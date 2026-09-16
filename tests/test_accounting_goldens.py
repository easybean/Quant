from decimal import Decimal

import pytest

from quant_data.accounting import (
    AccountingError, AccountSnapshot, CashFill, OrderStatus, apply_cash_dividend,
    apply_cash_fills, apply_funding_cashflow, apply_stock_split,
    inverse_unrealized_pnl, linear_unrealized_pnl, order_lifecycle,
    settle_linear_future,
)
from quant_data.accounting_goldens import GOLDEN_CASES, GOLDEN_VERSION, decimal_expected


D = Decimal


def test_goldens_are_versioned_complete_and_decimal_backed():
    assert GOLDEN_VERSION == "p2-03-v1"
    assert {case.case_id for case in GOLDEN_CASES} == {
        "equity-partial-fill-cancel-fee", "equity-split-and-dividend",
        "future-daily-settlement-no-double-count", "linear-perpetual-mark-pnl",
        "inverse-perpetual-mark-pnl", "linear-perpetual-funding-payment",
    }
    for case in GOLDEN_CASES:
        assert case.inputs and case.expected and case.convention
        for value in case.expected.values():
            D(value)


def test_partial_fill_cancel_fee_is_not_a_full_fill():
    lifecycle = order_lifecycle(D("10"), (D("4"),), cancelled=True)
    assert lifecycle.status is OrderStatus.CANCELLED
    assert lifecycle.filled_quantity == D("4") and lifecycle.remaining_quantity == D("6")
    end = apply_cash_fills(AccountSnapshot("USD", D("1000")), (CashFill(D("4"), D("100"), D("1")),), mark_price=D("102"))
    assert end.free_cash == decimal_expected("equity-partial-fill-cancel-fee", "free_cash")
    assert end.frozen_margin == D("0") and end.position_quantity == D("4")
    assert end.realized_pnl == D("0") and end.unrealized_pnl == D("8")
    assert end.fees == D("1") and end.total_pnl == decimal_expected("equity-partial-fill-cancel-fee", "total_pnl")


def test_stock_split_and_explicit_dividend_keep_cash_and_pnl_buckets_separate():
    start = AccountSnapshot("USD", D("500"), position_quantity=D("10"), average_entry_price=D("100"))
    split = apply_stock_split(start, ratio=D("2"))
    assert split.position_quantity == D("20") and split.average_entry_price == D("50")
    end = apply_cash_fills(apply_cash_dividend(split, amount_per_share=D("1")), (), mark_price=D("55"))
    assert end.free_cash == decimal_expected("equity-split-and-dividend", "free_cash")
    assert end.cash_distributions == D("20") and end.unrealized_pnl == D("100")
    assert end.total_pnl == decimal_expected("equity-split-and-dividend", "total_pnl")


def test_daily_future_settlement_moves_variation_once_and_resets_unrealized():
    start = AccountSnapshot("CNY", D("9000"), frozen_margin=D("1000"), position_quantity=D("2"), unrealized_pnl=D("100"))
    end = settle_linear_future(start, prior_settlement_price=D("100"), new_settlement_price=D("105"), multiplier=D("10"))
    assert end.free_cash == decimal_expected("future-daily-settlement-no-double-count", "free_cash")
    assert end.frozen_margin == D("1000") and end.total_cash == D("10100")
    assert end.settlement_pnl == D("100") and end.unrealized_pnl == D("0")
    assert end.total_pnl == decimal_expected("future-daily-settlement-no-double-count", "total_pnl")


def test_linear_and_inverse_pnl_keep_their_settlement_currencies_and_formulas_distinct():
    linear = linear_unrealized_pnl(quantity=D("0.01"), multiplier=D("1"), entry_price=D("50000"), mark_price=D("51000"))
    inverse = inverse_unrealized_pnl(contracts=D("100"), contract_value_quote=D("1"), entry_price=D("50000"), mark_price=D("40000"))
    assert linear == decimal_expected("linear-perpetual-mark-pnl", "unrealized_pnl")
    assert inverse == decimal_expected("inverse-perpetual-mark-pnl", "unrealized_pnl")


def test_funding_is_an_explicit_venue_cashflow_not_a_guessed_rate_formula():
    start = AccountSnapshot("USDT", D("900"), frozen_margin=D("100"), position_quantity=D("0.01"))
    end = apply_funding_cashflow(start, payment=D("-0.05"))
    assert end.free_cash == decimal_expected("linear-perpetual-funding-payment", "free_cash")
    assert end.frozen_margin == D("100") and end.funding_pnl == D("-0.05")
    assert end.total_pnl == decimal_expected("linear-perpetual-funding-payment", "total_pnl")


def test_unknown_or_unsupported_rules_fail_closed_instead_of_assuming_them():
    with pytest.raises(AccountingError, match="short inventory"):
        apply_cash_fills(AccountSnapshot("USD", D("100")), (CashFill(D("-1"), D("10")),), mark_price=D("10"))
    with pytest.raises(AccountingError, match="fills exceed"):
        order_lifecycle(D("1"), (D("2"),))
    with pytest.raises(AccountingError, match="positive Decimal"):
        linear_unrealized_pnl(quantity=D("1"), multiplier=D("1"), entry_price=D("0"), mark_price=D("1"))
