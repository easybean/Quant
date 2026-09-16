"""Synthetic-only P2-05 NautilusTrader US-equity daily golden harness.

Run this file only with NautilusTrader 1.221.0.  It creates a cash account,
one equity, and three in-memory daily bars; it never reads market data or
connects to a venue/account.
"""

from __future__ import annotations

import argparse
import json
from decimal import Decimal

from nautilus_trader.backtest.engine import BacktestEngine
from nautilus_trader.backtest.models import FillModel, FixedFeeModel
from nautilus_trader.config import StrategyConfig
from nautilus_trader.model.currencies import USD
from nautilus_trader.model.data import Bar, BarType
from nautilus_trader.model.enums import AccountType, BookType, OmsType, OrderSide
from nautilus_trader.model.identifiers import InstrumentId, Venue
from nautilus_trader.model.objects import Money, Price, Quantity
from nautilus_trader.test_kit.providers import TestInstrumentProvider
from nautilus_trader.trading.strategy import Strategy


GOLDEN = {
    "free_cash": "599",
    "position": "4",
    "unrealized_pnl": "8",
    "fees": "1",
    "total_pnl": "7",
}


class FourShareBook(FillModel):
    """Expose exactly four shares of synthetic offer liquidity once."""

    def __init__(self) -> None:
        super().__init__()
        self._consumed = False

    def get_orderbook_for_fill_simulation(self, instrument, order, best_bid, best_ask):
        from nautilus_trader.model.book import OrderBook
        from nautilus_trader.model.data import BookOrder
        from nautilus_trader.model.enums import OrderSide

        book = OrderBook(instrument.id, BookType.L2_MBP)
        if not self._consumed:
            book.add(BookOrder(OrderSide.BUY, best_bid, Quantity.from_int(4), 1), 0, 0)
            book.add(BookOrder(OrderSide.SELL, best_ask, Quantity.from_int(4), 2), 0, 0)
            self._consumed = True
        return book


class GoldenStrategyConfig(StrategyConfig, frozen=True):
    instrument_id: InstrumentId
    bar_type: BarType
    order_kind: str


class GoldenStrategy(Strategy):
    def __init__(self, config: GoldenStrategyConfig) -> None:
        super().__init__(config)
        self.order = None
        self.events: list[dict[str, str]] = []

    def on_start(self) -> None:
        self.subscribe_bars(self.config.bar_type)

    def on_bar(self, bar: Bar) -> None:
        if self.order is not None:
            return
        if self.config.order_kind == "market":
            self.order = self.order_factory.market(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(10),
            )
        else:
            self.order = self.order_factory.limit(
                instrument_id=self.config.instrument_id,
                order_side=OrderSide.BUY,
                quantity=Quantity.from_int(10),
                price=Price.from_str("100.00"),
            )
        self.submit_order(self.order)

    def on_order_filled(self, event) -> None:
        self.events.append({"type": type(event).__name__, "quantity": str(event.last_qty), "price": str(event.last_px), "commission": str(event.commission)})
        self.cancel_order(self.order)

    def on_order_canceled(self, event) -> None:
        self.events.append({"type": type(event).__name__})


def _bar(instrument_id: InstrumentId, day: int, close: str) -> Bar:
    bar_type = BarType.from_str(f"{instrument_id}-1-DAY-LAST-EXTERNAL")
    price = Price.from_str(close)
    return Bar(bar_type, price, price, price, price, Quantity.from_int(100), day * 86_400_000_000_000, day * 86_400_000_000_000)


def _number(value) -> str:
    return format(Decimal(str(value).split()[0]).normalize(), "f")


def run(order_kind: str) -> dict[str, object]:
    if order_kind not in {"market", "limit"}:
        raise ValueError("order_kind must be market or limit")
    engine = BacktestEngine()
    venue = Venue("XNAS")
    instrument = TestInstrumentProvider.equity("P205", venue.value)
    engine.add_venue(
        venue=venue, oms_type=OmsType.NETTING, account_type=AccountType.CASH,
        starting_balances=[Money(1000, USD)], base_currency=USD,
        fill_model=FourShareBook(), fee_model=FixedFeeModel(Money(1, USD)),
        book_type=BookType.L1_MBP, bar_execution=True,
    )
    engine.add_instrument(instrument)
    bar_type = BarType.from_str(f"{instrument.id}-1-DAY-LAST-EXTERNAL")
    strategy = GoldenStrategy(GoldenStrategyConfig(instrument_id=instrument.id, bar_type=bar_type, order_kind=order_kind))
    engine.add_strategy(strategy)
    engine.add_data([_bar(instrument.id, 1, "100.00"), _bar(instrument.id, 2, "100.00"), _bar(instrument.id, 3, "102.00")])
    engine.run()
    account = engine.cache.account_for_venue(venue)
    result = {
        "framework_version": "1.221.0",
        "order_kind": order_kind,
        "events": strategy.events,
        "order": {"status": str(strategy.order.status), "filled_qty": str(strategy.order.filled_qty), "leaves_qty": str(strategy.order.leaves_qty)},
        "account": {
            "free_cash": _number(account.balances_total()[USD]),
            "position": _number(engine.portfolio.net_position(instrument.id)),
            "unrealized_pnl": _number(engine.portfolio.unrealized_pnl(instrument.id, Price.from_str("102.00"))),
            "fees": _number(sum((Decimal(event["commission"].split()[0]) for event in strategy.events if "commission" in event), Decimal())),
        },
    }
    result["account"]["total_pnl"] = _number(Decimal(result["account"]["unrealized_pnl"]) - Decimal(result["account"]["fees"]))
    result["expected"] = GOLDEN
    result["passed"] = result["account"] == GOLDEN and [event["type"] for event in strategy.events] == ["OrderFilled", "OrderCanceled"] and result["order"] == {"status": "8", "filled_qty": "4", "leaves_qty": "6"}
    engine.dispose()
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--order-kind", choices=("market", "limit"), required=True)
    report = run(parser.parse_args().order_kind)
    print(json.dumps(report, sort_keys=True))
    raise SystemExit(0 if report["passed"] else 2)
