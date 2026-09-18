"""Explicit dividend-entitlement engineering goldens, isolated from backtests.

The caller supplies the shares entitled before the ex-date open.  This module
does not infer eligibility from a later account position or a market-data
source, and it deliberately has no tax, adjustment, or corporate-action feed.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal

from .accounting import AccountSnapshot


ZERO = Decimal("0")
LIMITATIONS = (
    "explicit entitlement engineering goldens only",
    "no tax, withholding, special distributions, price adjustment, or source qualification",
    "not connected to a backtest, market-data source, or broker account",
)


class DividendAccountingError(ValueError):
    """An explicit dividend entitlement is incomplete or internally inconsistent."""


@dataclass(frozen=True)
class DividendEntitlement:
    """A locked per-event right, independent of subsequent position changes."""

    event_id: str
    ex_date: date
    pay_date: date
    quantity_at_ex: Decimal
    original_amount_per_share: Decimal


@dataclass(frozen=True)
class DividendBook:
    """Append-only entitlement records and idempotent payment markers."""

    entitlements: tuple[DividendEntitlement, ...] = ()
    paid_event_ids: frozenset[str] = frozenset()


def record_entitlement(
    book: DividendBook,
    event_id: str,
    ex_date: date,
    pay_date: date,
    decision_date: date,
    quantity_at_ex: Decimal,
    rate: Decimal,
) -> DividendBook:
    """Record a caller-supplied ex-date entitlement, failing closed on conflict.

    Replaying precisely the same event is idempotent.  A reused event ID with
    any differing immutable field is rejected rather than silently revised.
    """
    _validate_book(book)
    _event_id(event_id)
    _plain_date(ex_date, "ex_date")
    _plain_date(pay_date, "pay_date")
    _plain_date(decision_date, "decision_date")
    if decision_date != ex_date:
        raise DividendAccountingError("decision_date must equal ex_date")
    if pay_date < ex_date:
        raise DividendAccountingError("pay_date must be on or after ex_date")
    _whole_nonnegative(quantity_at_ex, "quantity_at_ex")
    _positive(rate, "rate")
    entitlement = DividendEntitlement(event_id, ex_date, pay_date, quantity_at_ex, rate)
    for existing in book.entitlements:
        if existing.event_id == event_id:
            if existing == entitlement:
                return book
            raise DividendAccountingError("event_id conflicts with an existing entitlement")
    return replace(book, entitlements=book.entitlements + (entitlement,))


def unpaid_receivable(book: DividendBook) -> Decimal:
    """Return locked, unpaid gross dividend cash without making it available."""
    _validate_book(book)
    return sum(
        (entitlement.quantity_at_ex * entitlement.original_amount_per_share
         for entitlement in book.entitlements if entitlement.event_id not in book.paid_event_ids),
        ZERO,
    )


def pay_dividends(account: AccountSnapshot, book: DividendBook, as_of: date) -> tuple[AccountSnapshot, DividendBook]:
    """Credit due, unpaid entitlements once and return the reconciled account/book.

    ``as_of`` is a plain date: payment is due when it is on or after the
    recorded pay date.  This intentionally does not impose timezone rules.
    """
    _validate_account(account)
    _validate_book(book)
    _plain_date(as_of, "as_of")
    due = tuple(
        entitlement for entitlement in book.entitlements
        if entitlement.event_id not in book.paid_event_ids and entitlement.pay_date <= as_of
    )
    if not due:
        return account, book
    amount = sum((item.quantity_at_ex * item.original_amount_per_share for item in due), ZERO)
    paid = frozenset(set(book.paid_event_ids).union(item.event_id for item in due))
    return (
        replace(account, free_cash=account.free_cash + amount, cash_distributions=account.cash_distributions + amount),
        replace(book, paid_event_ids=paid),
    )


def _validate_book(book: DividendBook) -> None:
    if not isinstance(book, DividendBook):
        raise DividendAccountingError("book must be a DividendBook")
    if not isinstance(book.entitlements, tuple):
        raise DividendAccountingError("book.entitlements must be a tuple")
    seen: set[str] = set()
    for item in book.entitlements:
        if not isinstance(item, DividendEntitlement):
            raise DividendAccountingError("book entitlements must be DividendEntitlement values")
        _event_id(item.event_id)
        if item.event_id in seen:
            raise DividendAccountingError("book contains duplicate event_id values")
        seen.add(item.event_id)
        _plain_date(item.ex_date, "entitlement.ex_date")
        _plain_date(item.pay_date, "entitlement.pay_date")
        if item.pay_date < item.ex_date:
            raise DividendAccountingError("entitlement pay_date must be on or after ex_date")
        _whole_nonnegative(item.quantity_at_ex, "entitlement.quantity_at_ex")
        _positive(item.original_amount_per_share, "entitlement.original_amount_per_share")
    if not isinstance(book.paid_event_ids, frozenset):
        raise DividendAccountingError("paid_event_ids must be a frozenset of strings")
    for value in book.paid_event_ids:
        _event_id(value)
    if not book.paid_event_ids.issubset(seen):
        raise DividendAccountingError("paid_event_ids must refer to recorded entitlements")


def _validate_account(account: AccountSnapshot) -> None:
    if not isinstance(account, AccountSnapshot):
        raise DividendAccountingError("account must be an AccountSnapshot")
    if account.currency != "USD":
        raise DividendAccountingError("account.currency must be USD; FX conversion is not evidenced")
    for name in ("free_cash", "cash_distributions"):
        value = getattr(account, name)
        if not isinstance(value, Decimal) or not value.is_finite():
            raise DividendAccountingError(f"account.{name} must be a finite Decimal")


def _plain_date(value: date, name: str) -> None:
    if not isinstance(value, date) or isinstance(value, datetime):
        raise DividendAccountingError(f"{name} must be a date")


def _event_id(value: str) -> None:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise DividendAccountingError("event_id must be non-empty and stripped")


def _positive(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value <= ZERO:
        raise DividendAccountingError(f"{name} must be a finite positive Decimal")


def _whole_nonnegative(value: Decimal, name: str) -> None:
    if not isinstance(value, Decimal) or not value.is_finite() or value < ZERO:
        raise DividendAccountingError(f"{name} must be a finite non-negative Decimal")
    if value != value.to_integral_value():
        raise DividendAccountingError(f"{name} must be whole shares")
