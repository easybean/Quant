"""Deterministic daily-bar execution goldens, deliberately not a backtest engine.

This module is an isolated engineering aid.  It only applies one long-only
cash order to caller-supplied synthetic daily OHLCV and has no order state,
market-data access, or connection to :mod:`quant_data.backtest`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, ROUND_FLOOR
from typing import Literal
from zoneinfo import ZoneInfo

from .accounting import AccountSnapshot, CashFill, apply_cash_fills


ZERO = Decimal("0")
_NY = ZoneInfo("America/New_York")


class DailyExecutionError(ValueError):
    """An input is outside the narrow, deterministic daily execution model."""


@dataclass(frozen=True)
class DailyBar:
    """One raw-price daily bar whose timestamps identify its New York session."""

    session: date
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class Order:
    """A one-shot, whole-share limit order; ``side`` is ``buy`` or ``sell``."""

    side: Literal["buy", "sell"]
    quantity: Decimal
    limit_price: Decimal
    submitted_at: datetime


@dataclass(frozen=True)
class CostModel:
    """Explicit, adverse per-fill costs expressed in raw-price currency."""

    per_share_commission: Decimal
    minimum_commission: Decimal
    spread_bps: Decimal
    slippage_bps: Decimal
    max_participation: Decimal


@dataclass(frozen=True)
class DailyExecutionResult:
    """A fully reconciled one-order result with no pending-cash reservation."""

    status: Literal["filled", "partially_filled", "unfilled"]
    reason: str
    requested_quantity: Decimal
    filled_quantity: Decimal
    remaining_quantity: Decimal
    base_price: Decimal | None
    fill_price: Decimal | None
    spread_cost_per_share: Decimal | None
    slippage_cost_per_share: Decimal | None
    commission: Decimal
    cash_fill: CashFill | None
    account: AccountSnapshot
    rules: tuple[str, ...]
    limitations: tuple[str, ...]
    pending_cash_reserved: Decimal = ZERO


RULES = (
    "one active order only; no pending-cash reservation is modeled",
    "an order is eligible only when submitted_at is strictly before the session open",
    "after an order is not opening-marketable, buy limits require low < limit and sell limits require high > limit; equality does not touch",
    "opening-marketable limits take priority and base at the raw open; otherwise a strictly touched limit bases at its limit",
    "spread half-width and slippage are adverse, and the resulting price must remain within both limit and raw daily range",
    "quantity is whole shares and capped by bar participation, available cash, or long inventory",
)
LIMITATIONS = (
    "synthetic_goldens_only",
    "not a real backtest, execution engine, market-data adapter, or broker integration",
    "no intraday path, queue priority, financing, shorting, corporate actions, or pending-order lifecycle",
)


def execute_daily_order(
    snapshot: AccountSnapshot, bar: DailyBar, order: Order, cost: CostModel
) -> DailyExecutionResult:
    """Apply a conservative daily-bar fill, then mark the account at raw close.

    A result is returned for legitimate no-fills; malformed inputs fail closed
    with :class:`DailyExecutionError`.  The returned account is always passed
    through ``apply_cash_fills`` so cash, position and PnL are reconciled at
    the supplied raw close.
    """
    _validate_snapshot(snapshot)
    _validate_bar(bar)
    _validate_order(order)
    _validate_cost(cost)

    if order.submitted_at >= bar.open_time:
        return _unfilled(snapshot, bar, order, "not_day_eligible")

    base, touch = _base_price(bar, order)
    if base is None:
        return _unfilled(snapshot, bar, order, "limit_not_touched")

    spread = base * (cost.spread_bps / Decimal("20000"))
    slippage = base * (cost.slippage_bps / Decimal("10000"))
    fill_price = base + spread + slippage if order.side == "buy" else base - spread - slippage
    limit_broken = (
        (order.side == "buy" and fill_price > order.limit_price)
        or (order.side == "sell" and fill_price < order.limit_price)
    )
    if limit_broken:
        return _unfilled(snapshot, bar, order, "cost_adjusted_price_breaks_limit")
    range_broken = fill_price < bar.low or fill_price > bar.high
    if range_broken:
        return _unfilled(snapshot, bar, order, "cost_adjusted_price_outside_daily_range")

    liquidity_cap = _floor_shares(bar.volume * cost.max_participation)
    requested = min(order.quantity, liquidity_cap)
    if order.side == "buy":
        filled = _affordable_shares(snapshot.free_cash, requested, fill_price, cost)
        reason = "insufficient_cash" if filled == ZERO else ("filled" if filled == order.quantity else "partial_liquidity_or_cash")
        signed_quantity = filled
    else:
        filled = min(requested, snapshot.position_quantity)
        reason = "insufficient_inventory" if filled == ZERO else ("filled" if filled == order.quantity else "partial_liquidity_or_inventory")
        signed_quantity = -filled
    if filled == ZERO:
        return _unfilled(snapshot, bar, order, reason)

    commission = _commission(filled, cost)
    if order.side == "sell" and snapshot.free_cash + filled * fill_price - commission < ZERO:
        return _unfilled(snapshot, bar, order, "insufficient_cash_for_sell_fee")
    cash_fill = CashFill(signed_quantity, fill_price, commission)
    account = apply_cash_fills(snapshot, (cash_fill,), mark_price=bar.close)
    return DailyExecutionResult(
        status="filled" if filled == order.quantity else "partially_filled",
        reason=reason if touch == "limit_touch" else f"{reason}_opening_marketable",
        requested_quantity=order.quantity,
        filled_quantity=filled,
        remaining_quantity=order.quantity - filled,
        base_price=base,
        fill_price=fill_price,
        spread_cost_per_share=spread,
        slippage_cost_per_share=slippage,
        commission=commission,
        cash_fill=cash_fill,
        account=account,
        rules=RULES,
        limitations=LIMITATIONS,
    )


def _base_price(bar: DailyBar, order: Order) -> tuple[Decimal | None, str | None]:
    if order.side == "buy":
        if order.limit_price >= bar.open:
            return bar.open, "opening_marketable"
        if bar.low < order.limit_price:
            return order.limit_price, "limit_touch"
    else:
        if order.limit_price <= bar.open:
            return bar.open, "opening_marketable"
        if bar.high > order.limit_price:
            return order.limit_price, "limit_touch"
    return None, None


def _unfilled(snapshot: AccountSnapshot, bar: DailyBar, order: Order, reason: str) -> DailyExecutionResult:
    return DailyExecutionResult(
        status="unfilled", reason=reason, requested_quantity=order.quantity,
        filled_quantity=ZERO, remaining_quantity=order.quantity, base_price=None,
        fill_price=None, spread_cost_per_share=None, slippage_cost_per_share=None,
        commission=ZERO, cash_fill=None,
        account=apply_cash_fills(snapshot, (), mark_price=bar.close), rules=RULES,
        limitations=LIMITATIONS,
    )


def _commission(quantity: Decimal, cost: CostModel) -> Decimal:
    return max(cost.minimum_commission, quantity * cost.per_share_commission)


def _affordable_shares(cash: Decimal, cap: Decimal, price: Decimal, cost: CostModel) -> Decimal:
    """Binary-search the greatest integer quantity whose commission is covered."""
    if cap <= ZERO or cash < price + _commission(Decimal("1"), cost):
        return ZERO
    low, high = Decimal("1"), cap
    answer = ZERO
    while low <= high:
        middle = _floor_shares((low + high) / Decimal("2"))
        if middle * price + _commission(middle, cost) <= cash:
            answer, low = middle, middle + Decimal("1")
        else:
            high = middle - Decimal("1")
    return answer


def _validate_snapshot(snapshot: AccountSnapshot) -> None:
    if not isinstance(snapshot, AccountSnapshot) or not isinstance(snapshot.currency, str) or not snapshot.currency:
        raise DailyExecutionError("snapshot must be an AccountSnapshot with currency")
    if snapshot.currency != "USD":
        raise DailyExecutionError("snapshot.currency must be USD; FX conversion is not evidenced")
    _nonnegative(snapshot.free_cash, "snapshot.free_cash")
    _nonnegative(snapshot.frozen_margin, "snapshot.frozen_margin")
    _whole_nonnegative(snapshot.position_quantity, "snapshot.position_quantity")
    if snapshot.position_quantity:
        _positive(snapshot.average_entry_price, "snapshot.average_entry_price")
    elif snapshot.average_entry_price != ZERO:
        raise DailyExecutionError("snapshot.average_entry_price must be zero with zero position")
    _nonnegative(snapshot.fees, "snapshot.fees")
    for name in ("realized_pnl", "unrealized_pnl", "settlement_pnl", "funding_pnl", "cash_distributions"):
        _finite(getattr(snapshot, name), f"snapshot.{name}")


def _validate_bar(bar: DailyBar) -> None:
    if not isinstance(bar, DailyBar) or not isinstance(bar.session, date) or isinstance(bar.session, datetime):
        raise DailyExecutionError("bar.session must be a date")
    _utc_time(bar.open_time, "bar.open_time")
    _utc_time(bar.close_time, "bar.close_time")
    if bar.open_time >= bar.close_time:
        raise DailyExecutionError("bar.open_time must be before bar.close_time")
    if bar.open_time.astimezone(_NY).date() != bar.session or bar.close_time.astimezone(_NY).date() != bar.session:
        raise DailyExecutionError("bar timestamps must be in the declared New York session")
    for name in ("open", "high", "low", "close"):
        _positive(getattr(bar, name), f"bar.{name}")
    _nonnegative(bar.volume, "bar.volume")
    if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
        raise DailyExecutionError("bar OHLC bounds are invalid")


def _validate_order(order: Order) -> None:
    if not isinstance(order, Order) or order.side not in ("buy", "sell"):
        raise DailyExecutionError("order.side must be buy or sell")
    _whole_positive(order.quantity, "order.quantity")
    _positive(order.limit_price, "order.limit_price")
    _utc_time(order.submitted_at, "order.submitted_at")


def _validate_cost(cost: CostModel) -> None:
    if not isinstance(cost, CostModel):
        raise DailyExecutionError("cost must be a CostModel")
    for name in ("per_share_commission", "minimum_commission", "spread_bps", "slippage_bps"):
        _nonnegative(getattr(cost, name), f"cost.{name}")
    _positive(cost.max_participation, "cost.max_participation")
    if cost.max_participation > Decimal("1"):
        raise DailyExecutionError("cost.max_participation cannot exceed one")


def _utc_time(value: datetime, name: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != timezone.utc.utcoffset(value):
        raise DailyExecutionError(f"{name} must be an aware UTC datetime")


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
        raise DailyExecutionError(f"{name} must be a finite positive Decimal")


def _nonnegative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
        raise DailyExecutionError(f"{name} must be a finite non-negative Decimal")


def _finite(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise DailyExecutionError(f"{name} must be a finite Decimal")


def _whole_positive(value: Decimal, name: str) -> None:
    _positive(value, name)
    if value != value.to_integral_value():
        raise DailyExecutionError(f"{name} must be whole shares")


def _whole_nonnegative(value: Decimal, name: str) -> None:
    _nonnegative(value, name)
    if value != value.to_integral_value():
        raise DailyExecutionError(f"{name} must be whole shares")


def _floor_shares(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)
