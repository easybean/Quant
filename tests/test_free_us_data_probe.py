from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location(
    "probe_free_us_data", Path(__file__).parents[1] / "scripts" / "probe_free_us_data.py"
)
probe = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(probe)


class Response:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self.payload = {} if payload is None else payload

    def json(self):
        return self.payload


NOW = datetime(2026, 9, 16, 12, tzinfo=timezone.utc)


def _probe(get, **kwargs):
    return probe.probe_free_us_data(
        end=date(2026, 9, 1), request_get=get, now=NOW,
        credential_loader=lambda _: ("test-key", "test-secret"), **kwargs
    )


def test_rejects_end_boundary_that_is_not_fifteen_minutes_old():
    with pytest.raises(ValueError, match="15 minutes"):
        probe.probe_free_us_data(
            end=date(2026, 9, 16), now=datetime(2026, 9, 17, 3, 50, tzinfo=timezone.utc),
            credential_loader=lambda _: ("key", "secret"),
        )


def test_permission_response_stops_immediately_without_a_retry_or_fallback():
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(403)

    result = _probe(get)
    assert result["probe_status"] == "blocked_http_403"
    assert result["results"] == [{"symbol": "AAPL", "sample": "recent", "http_status": 403,
                                   "bar_count": 0, "date_range": None, "ohlcv_valid": "unknown",
                                   "pagination_complete": "unknown", "status": "http_403_blocked"}]
    assert len(calls) == 1
    assert calls[0][1]["params"]["feed"] == "sip"
    assert calls[0][1]["params"]["asof"] == "-"
    assert calls[0][1]["timeout"] == 30


@pytest.mark.parametrize("bars", [[], None])
def test_empty_response_is_reported_as_unknown_not_as_qualified_data(bars):
    result = _probe(lambda *args, **kwargs: Response(200, {"bars": bars, "next_page_token": None}))
    assert result["probe_status"] == "completed"
    assert len(result["results"]) == 5
    assert all(row["status"] == "empty_unknown" for row in result["results"])
    assert result["qualified"] is False


def test_invalid_ohlcv_is_not_accepted():
    result = _probe(lambda *args, **kwargs: Response(200, {"bars": [
        {"t": "2026-08-20T04:00:00Z", "o": -10, "h": 9, "l": 8, "c": 9, "v": 100}
    ], "next_page_token": None}))
    assert result["results"][0]["status"] == "invalid_ohlcv"
    assert result["results"][0]["ohlcv_valid"] is False
    assert result["probe_status"] == "completed_with_invalid"


@pytest.mark.parametrize(
    "timestamp",
    [None, "not-a-timestamp", "2026-08-20T04:00:00", "2026-08-01T04:00:00Z"],
)
def test_missing_or_out_of_window_timestamp_is_invalid(timestamp):
    bar = {"o": 10, "h": 11, "l": 9, "c": 10, "v": 100}
    if timestamp is not None:
        bar["t"] = timestamp
    result = _probe(lambda *args, **kwargs: Response(200, {"bars": [bar], "next_page_token": None}))
    assert result["results"][0]["status"] == "invalid_ohlcv"
    assert result["probe_status"] == "completed_with_invalid"


def test_end_before_fixed_aapl_2016_window_is_rejected_before_credentials():
    with pytest.raises(ValueError, match="AAPL 2016"):
        probe.probe_free_us_data(
            end=date(2015, 12, 31), now=NOW,
            credential_loader=lambda _: (_ for _ in ()).throw(AssertionError("credentials loaded")),
        )


def test_cli_returns_nonzero_for_invalid_probe(monkeypatch):
    monkeypatch.setattr(probe, "probe_free_us_data", lambda **_: {"probe_status": "completed_with_invalid"})
    monkeypatch.setattr(sys, "argv", ["probe_free_us_data.py", "--end", "2026-09-01"])
    assert probe.main() == 2


def test_partial_pagination_is_blocked_without_fetching_a_second_page():
    calls = []

    def get(*args, **kwargs):
        calls.append((args, kwargs))
        return Response(200, {"bars": [], "next_page_token": "more"})

    result = _probe(get)
    assert result["probe_status"] == "blocked_partial_pagination"
    assert result["results"][0]["status"] == "pagination_blocked"
    assert result["results"][0]["pagination_complete"] is False
    assert len(calls) == 1
