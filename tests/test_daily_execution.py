from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from quant_data.accounting import AccountSnapshot
from quant_data.daily_execution import (
    CostModel, DailyBar, DailyExecutionError, Order, execute_daily_order,
)


D = Decimal
UTC = timezone.utc
OPEN = datetime(2024, 1, 2, 14, 30, tzinfo=UTC)  # 09:30 New York
CLOSE = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)


def bar(**changes):
    values = dict(session=date(2024, 1, 2), open_time=OPEN, close_time=CLOSE,
                  open=D("100"), high=D("110"), low=D("90"), close=D("105"), volume=D("1000"))
    values.update(changes)
    return DailyBar(**values)


def costs(**changes):
    values = dict(per_share_commission=D("0"), minimum_commission=D("0"),
                  spread_bps=D("0"), slippage_bps=D("0"), max_participation=D("1"))
    values.update(changes)
    return CostModel(**values)


def order(side="buy", **changes):
    values = dict(side=side, quantity=D("10"), limit_price=D("101"), submitted_at=OPEN - timedelta(seconds=1))
    values.update(changes)
    return Order(**values)


def test_buy_sell_round_trip_reconciles_cash_position_and_pnl():
    bought = execute_daily_order(AccountSnapshot("USD", D("2000")), bar(), order(), costs())
    assert bought.status == "filled" and bought.fill_price == D("100")
    assert bought.account.free_cash == D("1000") and bought.account.position_quantity == D("10")
    assert bought.account.unrealized_pnl == D("50")

    next_day = bar(session=date(2024, 1, 3), open_time=OPEN + timedelta(days=1), close_time=CLOSE + timedelta(days=1),
                   open=D("110"), high=D("115"), low=D("100"), close=D("108"))
    sold = execute_daily_order(bought.account, next_day,
                               order("sell", limit_price=D("109"), submitted_at=CLOSE + timedelta(seconds=1)), costs())
    assert sold.status == "filled" and sold.account.free_cash == D("2100")
    assert sold.account.position_quantity == D("0") and sold.account.realized_pnl == D("100")
    assert sold.account.total_pnl == D("100") and "synthetic_goldens_only" in sold.limitations


def test_per_share_and_minimum_commissions_are_separate_and_cash_is_inclusive():
    minimum = execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(quantity=D("2")), costs(per_share_commission=D("0.10"), minimum_commission=D("1")))
    per_share = execute_daily_order(AccountSnapshot("USD", D("3000")), bar(), order(quantity=D("20")), costs(per_share_commission=D("0.10"), minimum_commission=D("1")))
    assert minimum.commission == D("1") and minimum.account.free_cash == D("799")
    assert per_share.commission == D("2") and per_share.account.free_cash == D("998")


def test_costs_cannot_break_limit_or_escape_daily_range():
    result = execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(limit_price=D("100")), costs(spread_bps=D("20")))
    assert result.status == "unfilled" and result.reason == "cost_adjusted_price_breaks_limit"
    result = execute_daily_order(AccountSnapshot("USD", D("1000")), bar(high=D("100.05"), close=D("100")), order(limit_price=D("101")), costs(slippage_bps=D("10")))
    assert result.status == "unfilled" and result.reason == "cost_adjusted_price_outside_daily_range"


def test_opening_marketable_order_has_priority_over_later_limit_touch():
    result = execute_daily_order(AccountSnapshot("USD", D("2000")), bar(), order(limit_price=D("105")), costs())
    assert result.status == "filled" and result.base_price == D("100")
    assert "opening_marketable" in result.reason
    assert "after an order is not opening-marketable" in result.rules[2]


def test_same_bar_submission_is_not_eligible_and_equal_limit_does_not_touch():
    same_bar = execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(submitted_at=OPEN), costs())
    equality = execute_daily_order(AccountSnapshot("USD", D("1000")), bar(open=D("105"), high=D("110"), low=D("100")), order(limit_price=D("100")), costs())
    assert same_bar.reason == "not_day_eligible" and equality.reason == "limit_not_touched"
    assert same_bar.pending_cash_reserved == D("0")


@pytest.mark.parametrize("bad_bar", [
    {"volume": D("-1")}, {"open": D("NaN")}, {"high": D("99")},
])
def test_bad_prices_or_volume_fail_closed(bad_bar):
    with pytest.raises(DailyExecutionError):
        execute_daily_order(AccountSnapshot("USD", D("1000")), bar(**bad_bar), order(), costs())


def test_fractional_nan_and_non_utc_order_inputs_fail_closed():
    with pytest.raises(DailyExecutionError, match="whole shares"):
        execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(quantity=D("1.1")), costs())
    with pytest.raises(DailyExecutionError, match="finite positive"):
        execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(limit_price=D("NaN")), costs())
    with pytest.raises(DailyExecutionError, match="aware UTC"):
        execute_daily_order(AccountSnapshot("USD", D("1000")), bar(), order(submitted_at=OPEN.replace(tzinfo=None)), costs())


def test_partial_liquidity_and_cash_or_inventory_fail_closed():
    partial = execute_daily_order(AccountSnapshot("USD", D("2000")), bar(volume=D("31.5")), order(quantity=D("20")), costs(max_participation=D("0.5")))
    cash = execute_daily_order(AccountSnapshot("USD", D("250")), bar(), order(quantity=D("10")), costs(minimum_commission=D("1")))
    inventory = execute_daily_order(AccountSnapshot("USD", D("0"), position_quantity=D("3"), average_entry_price=D("90")), bar(), order("sell", quantity=D("5"), limit_price=D("99")), costs())
    assert partial.filled_quantity == D("15") and partial.status == "partially_filled"
    assert cash.filled_quantity == D("2") and cash.account.free_cash == D("49")
    assert inventory.filled_quantity == D("3") and inventory.account.position_quantity == D("0")


def test_unaffordable_buy_or_sell_fee_never_creates_negative_cash():
    buy = execute_daily_order(AccountSnapshot("USD", D("50")), bar(), order(), costs())
    sell = execute_daily_order(AccountSnapshot("USD", D("0"), position_quantity=D("1"), average_entry_price=D("90")), bar(), order("sell", quantity=D("1"), limit_price=D("99")), costs(minimum_commission=D("101")))
    assert buy.status == "unfilled" and buy.reason == "insufficient_cash"
    assert sell.status == "unfilled" and sell.reason == "insufficient_cash_for_sell_fee"


@pytest.mark.parametrize("snapshot", [
    AccountSnapshot("USD", D("1"), position_quantity=D("1"), average_entry_price=D("0")),
    AccountSnapshot("USD", D("1"), average_entry_price=D("1")),
    AccountSnapshot("USD", D("1"), fees=D("-1")),
    AccountSnapshot("EUR", D("1")),
])
def test_snapshot_cost_basis_and_fees_cannot_fabricate_pnl(snapshot):
    with pytest.raises(DailyExecutionError):
        execute_daily_order(snapshot, bar(), order(), costs())
