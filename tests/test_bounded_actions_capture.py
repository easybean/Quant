from datetime import date

import pytest
from quant_data.bounded_actions_capture import validate

START, END = date(2026, 9, 1), date(2026, 9, 17)


def test_dividend_original_amount_and_payment_date_preserved():
    event = {"id": "one", "ticker": "GOOGL", "ex_dividend_date": "2026-09-08", "pay_date": "2026-09-21",
             "cash_amount": .22, "currency": "USD", "split_adjusted_cash_amount": .11}
    assert validate({"status": "OK", "results": [event]}, "GOOGL", "dividends", START, END) == [event]


def test_empty_response_is_evidence_not_qualification():
    assert validate({"status": "OK", "results": []}, "TSLA", "splits", START, END) == []


@pytest.mark.parametrize("payload", [{"status": "ERROR"}, {"status": "OK", "next_url": "https://example.com"},
                                      {"status": "OK", "results": {}},
                                      {"status": "OK", "results": [{"id": "one", "ticker": "OTHER"}]}])
def test_malformed_and_paginated_evidence_not_accepted(payload):
    with pytest.raises(ValueError): validate(payload, "TSLA", "splits", START, END)


@pytest.mark.parametrize("amount", [True, "NaN", "Infinity", -1, 0])
def test_invalid_original_dividend_amount_rejected(amount):
    event = {"id": "one", "ticker": "GOOGL", "ex_dividend_date": "2026-09-08", "cash_amount": amount, "currency": "USD"}
    with pytest.raises(ValueError): validate({"status": "OK", "results": [event]}, "GOOGL", "dividends", START, END)
