from datetime import date
from decimal import Decimal

import pytest

from quant_data.accounting import AccountSnapshot, CashFill, apply_cash_fills
from quant_data.dividend_accounting import (
    LIMITATIONS, DividendAccountingError, DividendBook, pay_dividends,
    record_entitlement, unpaid_receivable,
)


D = Decimal
EX = date(2026, 9, 4)
PAY = date(2026, 9, 14)


def googl_book() -> DividendBook:
    return record_entitlement(DividendBook(), "GOOGL-2026-09-04-regular", EX, PAY, EX, D("10"), D("0.22"))


def test_explicit_googl_golden_locks_ten_share_receivable_without_reading_market_prices():
    book = googl_book()
    assert unpaid_receivable(book) == D("2.20")
    assert LIMITATIONS[0] == "explicit entitlement engineering goldens only"


def test_sale_after_ex_date_does_not_remove_locked_dividend_and_payment_is_once():
    book = googl_book()
    account = AccountSnapshot("USD", D("100"), position_quantity=D("10"), average_entry_price=D("10"))
    sold = apply_cash_fills(account, (CashFill(D("-10"), D("11")),), mark_price=D("11"))
    paid_account, paid_book = pay_dividends(sold, book, PAY)
    replay_account, replay_book = pay_dividends(paid_account, paid_book, date(2026, 9, 15))
    assert sold.position_quantity == D("0")
    assert paid_account.free_cash == D("212.20") and paid_account.cash_distributions == D("2.20")
    assert paid_book.paid_event_ids == frozenset({"GOOGL-2026-09-04-regular"})
    assert replay_account == paid_account and replay_book == paid_book
    assert unpaid_receivable(paid_book) == D("0")


def test_payment_is_not_available_before_pay_date_and_empty_book_is_a_noop():
    account = AccountSnapshot("USD", D("100"))
    book = googl_book()
    early_account, early_book = pay_dividends(account, book, date(2026, 9, 13))
    empty_account, empty_book = pay_dividends(account, DividendBook(), PAY)
    assert early_account == account and early_book == book and unpaid_receivable(book) == D("2.20")
    assert empty_account == account and empty_book == DividendBook() and unpaid_receivable(empty_book) == D("0")


def test_same_event_replay_is_idempotent_but_conflicting_event_is_rejected():
    book = googl_book()
    assert record_entitlement(book, "GOOGL-2026-09-04-regular", EX, PAY, EX, D("10"), D("0.22")) == book
    with pytest.raises(DividendAccountingError, match="conflicts"):
        record_entitlement(book, "GOOGL-2026-09-04-regular", EX, PAY, EX, D("11"), D("0.22"))


@pytest.mark.parametrize("quantity,rate", [(D("1.1"), D("0.22")), (D("NaN"), D("0.22")), (D("1"), D("NaN"))])
def test_invalid_quantity_or_rate_fails_closed(quantity, rate):
    with pytest.raises(DividendAccountingError):
        record_entitlement(DividendBook(), "event", EX, PAY, EX, quantity, rate)


def test_dates_must_be_explicit_ex_date_decision_and_non_retrograde_payment():
    with pytest.raises(DividendAccountingError, match="decision_date"):
        record_entitlement(DividendBook(), "event", EX, PAY, date(2026, 9, 3), D("1"), D("0.22"))
    with pytest.raises(DividendAccountingError, match="pay_date"):
        record_entitlement(DividendBook(), "event", EX, date(2026, 9, 3), EX, D("1"), D("0.22"))


def test_usd_only_and_immutable_book_shape_fail_closed_without_fx_evidence():
    with pytest.raises(DividendAccountingError, match="USD"):
        pay_dividends(AccountSnapshot("EUR", D("100")), googl_book(), PAY)
    malformed = DividendBook(entitlements=[])
    with pytest.raises(DividendAccountingError, match="tuple"):
        unpaid_receivable(malformed)
    with pytest.raises(DividendAccountingError, match="stripped"):
        record_entitlement(DividendBook(), " event ", EX, PAY, EX, D("1"), D("0.22"))
