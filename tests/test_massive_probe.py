from datetime import date, datetime, timezone
from quant_data.massive_probe import assess, summarize

DAY = date(2026, 9, 17)

def test_empty_is_unknown_not_delisted():
    result = assess({"status": "OK", "ticker": "AAA", "adjusted": False, "resultsCount": 0}, "AAA", DAY, DAY, "2026-09-18T08:00:00Z")
    assert result["status"] == "empty_unknown" and result["target_returned"] is False

def test_identity_adjustment_and_invalid_prices_rejected():
    assert assess({"status": "OK", "ticker": "BBB", "adjusted": False}, "AAA", DAY, DAY, "now")["status"] == "identity_or_adjustment_invalid"
    assert assess({"status": "OK", "ticker": "AAA", "adjusted": True}, "AAA", DAY, DAY, "now")["status"] == "identity_or_adjustment_invalid"

def test_transient_failure_is_not_completed_test():
    result = summarize(["AAA", "BBB"], {"AAA": {"status": "grouped_returned", "target_returned": True}, "BBB": {"status": "http_429"}}, DAY, "sha")
    assert result["tested"] == 1 and result["remaining"] == 1 and result["status"] == "partial"


def test_real_shape_row_is_validated_and_duplicate_rejected():
    row = {"t": int(datetime(2026, 9, 17, 4, tzinfo=timezone.utc).timestamp() * 1000), "o": 10, "h": 12, "l": 9, "c": 11, "v": 10.5}
    payload = {"status": "OK", "ticker": "AAA", "adjusted": False, "resultsCount": 1, "results": [row]}
    assert assess(payload, "AAA", DAY, DAY, "observed")["target_returned"] is True
    payload.update(resultsCount=2, results=[row, row])
    assert assess(payload, "AAA", DAY, DAY, "observed")["status"] == "bars_invalid"
