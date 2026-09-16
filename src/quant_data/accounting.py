"""Small, deterministic accounting primitives used by P2-03 golden cases.

This is deliberately not an exchange adapter.  Every price, multiplier,
settlement price and funding cashflow is supplied by the caller; a missing
venue rule must remain blocked rather than being guessed here.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import Enum
from typing import Iterable


ZERO = Decimal("0")


class AccountingError(ValueError):
    """An input is incomplete or outside this intentionally small model."""


class OrderStatus(str, Enum):
    NEW = "new"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class OrderLifecycle:
    """Order quantity state; cancelling never invents a fill."""

    requested_quantity: Decimal
    filled_quantity: Decimal
    status: OrderStatus

    @property
    def remaining_quantity(self) -> Decimal:
        return self.requested_quantity - self.filled_quantity


@dataclass(frozen=True)
class CashFill:
    """A signed cash-product fill.  Positive quantity buys, negative sells."""

    quantity: Decimal
    price: Decimal
    fee: Decimal = ZERO


@dataclass(frozen=True)
class AccountSnapshot:
    """One-currency account view with cash and collateral kept separate.

    ``free_cash`` excludes ``frozen_margin``.  PnL buckets are accounting
    classifications, not additional cash balances.  ``total_pnl`` must never
    be added to cash a second time.
    """

    currency: str
    free_cash: Decimal
    frozen_margin: Decimal = ZERO
    position_quantity: Decimal = ZERO
    average_entry_price: Decimal = ZERO
    realized_pnl: Decimal = ZERO
    unrealized_pnl: Decimal = ZERO
    settlement_pnl: Decimal = ZERO
    funding_pnl: Decimal = ZERO
    fees: Decimal = ZERO
    cash_distributions: Decimal = ZERO

    @property
    def total_cash(self) -> Decimal:
        return self.free_cash + self.frozen_margin

    @property
    def total_pnl(self) -> Decimal:
        return (
            self.realized_pnl
            + self.unrealized_pnl
            + self.settlement_pnl
            + self.funding_pnl
            + self.cash_distributions
            - self.fees
        )


def order_lifecycle(
    requested_quantity: Decimal, fill_quantities: Iterable[Decimal], *, cancelled: bool = False
) -> OrderLifecycle:
    """Validate a small lifecycle from explicit fills and an optional cancel."""
    _positive(requested_quantity, "requested_quantity")
    fills = tuple(fill_quantities)
    if any(fill <= ZERO for fill in fills):
        raise AccountingError("fill quantities must be positive")
    filled = sum(fills, ZERO)
    if filled > requested_quantity:
        raise AccountingError("fills exceed requested quantity")
    if cancelled:
        status = OrderStatus.CANCELLED
    elif filled == requested_quantity:
        status = OrderStatus.FILLED
    elif filled:
        status = OrderStatus.PARTIALLY_FILLED
    else:
        status = OrderStatus.NEW
    return OrderLifecycle(requested_quantity, filled, status)


def apply_cash_fills(
    snapshot: AccountSnapshot, fills: Iterable[CashFill], *, mark_price: Decimal
) -> AccountSnapshot:
    """Apply long-only stock/spot fills and one explicit mark price.

    Short inventory and lot-selection rules are intentionally not inferred;
    callers requiring them must provide a separately verified implementation.
    """
    _non_negative(snapshot.position_quantity, "position_quantity")
    _non_negative(snapshot.average_entry_price, "average_entry_price")
    _positive(mark_price, "mark_price")
    current = snapshot
    for fill in fills:
        if fill.quantity == ZERO:
            raise AccountingError("fill quantity cannot be zero")
        _positive(fill.price, "fill price")
        _non_negative(fill.fee, "fill fee")
        if fill.quantity > ZERO:
            new_quantity = current.position_quantity + fill.quantity
            average = (
                current.position_quantity * current.average_entry_price + fill.quantity * fill.price
            ) / new_quantity
            current = replace(
                current,
                free_cash=current.free_cash - fill.quantity * fill.price - fill.fee,
                position_quantity=new_quantity,
                average_entry_price=average,
                fees=current.fees + fill.fee,
            )
            continue
        sold = -fill.quantity
        if sold > current.position_quantity:
            raise AccountingError("short inventory / lot selection is not verified by this model")
        remaining = current.position_quantity - sold
        current = replace(
            current,
            free_cash=current.free_cash + sold * fill.price - fill.fee,
            position_quantity=remaining,
            average_entry_price=current.average_entry_price if remaining else ZERO,
            realized_pnl=current.realized_pnl + sold * (fill.price - current.average_entry_price),
            fees=current.fees + fill.fee,
        )
    unrealized = current.position_quantity * (mark_price - current.average_entry_price)
    return replace(current, unrealized_pnl=unrealized)


def apply_stock_split(snapshot: AccountSnapshot, *, ratio: Decimal) -> AccountSnapshot:
    """Apply an explicit forward split ratio (e.g. ``2`` means 2-for-1)."""
    _positive(ratio, "split ratio")
    if snapshot.position_quantity == ZERO:
        return snapshot
    return replace(
        snapshot,
        position_quantity=snapshot.position_quantity * ratio,
        average_entry_price=snapshot.average_entry_price / ratio,
    )


def apply_cash_dividend(snapshot: AccountSnapshot, *, amount_per_share: Decimal) -> AccountSnapshot:
    """Credit an explicit cash entitlement; tax/withholding must be supplied elsewhere."""
    _non_negative(amount_per_share, "cash dividend per share")
    amount = snapshot.position_quantity * amount_per_share
    return replace(
        snapshot,
        free_cash=snapshot.free_cash + amount,
        cash_distributions=snapshot.cash_distributions + amount,
    )


def settle_linear_future(
    snapshot: AccountSnapshot,
    *,
    prior_settlement_price: Decimal,
    new_settlement_price: Decimal,
    multiplier: Decimal,
) -> AccountSnapshot:
    """Move one explicit futures variation settlement into free cash once.

    The returned ``unrealized_pnl`` is reset to zero because the new settlement
    price is the new cost basis.  ``settlement_pnl`` records the same cashflow
    for reporting only and therefore must not be credited again.
    """
    _positive(prior_settlement_price, "prior settlement price")
    _positive(new_settlement_price, "new settlement price")
    _positive(multiplier, "multiplier")
    variation = snapshot.position_quantity * multiplier * (new_settlement_price - prior_settlement_price)
    return replace(
        snapshot,
        free_cash=snapshot.free_cash + variation,
        unrealized_pnl=ZERO,
        settlement_pnl=snapshot.settlement_pnl + variation,
    )


def linear_unrealized_pnl(
    *, quantity: Decimal, multiplier: Decimal, entry_price: Decimal, mark_price: Decimal
) -> Decimal:
    """Linear derivative PnL in settlement currency, with signed quantity."""
    _positive(multiplier, "multiplier")
    _positive(entry_price, "entry price")
    _positive(mark_price, "mark price")
    return quantity * multiplier * (mark_price - entry_price)


def inverse_unrealized_pnl(
    *, contracts: Decimal, contract_value_quote: Decimal, entry_price: Decimal, mark_price: Decimal
) -> Decimal:
    """Inverse-contract PnL in base/settlement currency, with signed contracts."""
    _positive(contract_value_quote, "contract value")
    _positive(entry_price, "entry price")
    _positive(mark_price, "mark price")
    return contracts * contract_value_quote * (Decimal("1") / entry_price - Decimal("1") / mark_price)


def apply_funding_cashflow(snapshot: AccountSnapshot, *, payment: Decimal) -> AccountSnapshot:
    """Apply a venue-calculated funding payment; positive receives, negative pays.

    This module does not derive a payment from a rate because venue sign,
    notional basis and timestamp rules are product-specific and unverified.
    """
    return replace(
        snapshot,
        free_cash=snapshot.free_cash + payment,
        funding_pnl=snapshot.funding_pnl + payment,
    )


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
        raise AccountingError(f"{name} must be a finite positive Decimal")


def _non_negative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
        raise AccountingError(f"{name} must be a finite non-negative Decimal")
