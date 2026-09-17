from datetime import date, datetime, timezone

import pytest
import requests

from quant_data.pipeline import FatalProviderError
from quant_data.sip_batch import make_sip_batch_downloader


NOW = datetime(2024, 1, 10, 23, tzinfo=timezone.utc)


class Response:
    def __init__(self, status=200, payload=None):
        self.status_code = status
        self._payload = payload if payload is not None else {"bars": {}, "next_page_token": None}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


def _tasks(*symbols, start="2024-01-02", end="2024-01-09"):
    return [{"symbol": symbol, "requested_start": start, "requested_end": end, "task_id": symbol} for symbol in symbols]


def _downloader(monkeypatch, tasks, responses, **kwargs):
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls = []
    iterator = iter(responses)
    def get(*args, **params):
        calls.append((args, params))
        return next(iterator)
    return make_sip_batch_downloader(tasks, credential_file=None, now=NOW, request_get=get,
                                     sleep=lambda _: None, monotonic=lambda: 10.0, **kwargs), calls


def test_groups_exact_windows_and_exposes_cached_symbol_frames(monkeypatch):
    downloader, calls = _downloader(monkeypatch, _tasks("AAA", "BBB"), [Response(payload={"bars": {
        "AAA": [{"t": "2024-01-02T05:00:00Z", "o": 1, "h": 2, "l": .5, "c": 1.5, "v": 3}],
        "BBB": [{"t": "2024-01-02T05:00:00Z", "o": 4, "h": 5, "l": 3, "c": 4.5, "v": 6}],
    }, "next_page_token": None})])
    assert downloader("AAA", date(2024, 1, 2), date(2024, 1, 9)).columns.tolist() == ["date", "open", "high", "low", "close", "volume"]
    downloader("BBB", date(2024, 1, 2), date(2024, 1, 9))
    assert len(calls) == 1
    assert downloader.batch_request_count == 1 and downloader.single_request_count == 0
    params = calls[0][1]["params"]
    assert params == {"symbols": "AAA,BBB", "timeframe": "1Day", "feed": "sip", "adjustment": "raw", "asof": "-",
                      "start": "2024-01-02T05:00:00Z", "end": "2024-01-10T04:59:59.999999Z", "limit": 10000, "sort": "asc"}


def test_mapped_provider_symbol_is_requested_and_returned_under_original_symbol(monkeypatch):
    tasks = [{"symbol": "ABR$D", "provider_symbol": "ABR.PRD", "requested_start": "2024-01-02", "requested_end": "2024-01-09", "task_id": "mapped"}]
    downloader, calls = _downloader(monkeypatch, tasks, [Response(payload={"bars": {"ABR.PRD": [
        {"t": "2024-01-02T05:00:00Z", "o": 1, "h": 2, "l": .5, "c": 1.5, "v": 3}],
    }, "next_page_token": None})])
    frame = downloader("ABR$D", date(2024, 1, 2), date(2024, 1, 9))
    assert len(frame) == 1 and calls[0][1]["params"]["symbols"] == "ABR.PRD"
    with pytest.raises(ValueError, match="tasks_invalid"):
        make_sip_batch_downloader(tasks + [{**tasks[0], "symbol": "OTHER"}], None, NOW, request_get=lambda *_a, **_k: Response())


def test_dst_window_uses_new_york_midnight(monkeypatch):
    now = datetime(2024, 3, 12, 23, tzinfo=timezone.utc)
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls = []
    downloader = make_sip_batch_downloader(_tasks("AAA", start="2024-03-10", end="2024-03-11"), None, now,
        request_get=lambda *a, **k: calls.append(k) or Response(payload={"bars": {"AAA": [{"t": "x"}]}}), sleep=lambda _: None)
    downloader("AAA", date(2024, 3, 10), date(2024, 3, 11))
    assert calls[0]["params"]["start"] == "2024-03-10T05:00:00Z"
    assert calls[0]["params"]["end"] == "2024-03-12T03:59:59.999999Z"


def test_inclusive_provider_excludes_following_session(monkeypatch):
    """Simulate inclusive timestamp selection, then retain strict validation."""
    from quant_data.daily_sync import _validate_normalized
    from quant_data.pipeline import normalize_bars
    import pandas as pd
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    rows = [{"t": t, "o": 10, "h": 11, "l": 9, "c": 10, "v": 5}
            for t in ("2024-01-09T05:00:00Z", "2024-01-10T05:00:00Z")]
    def get(*_args, **kwargs):
        end = pd.Timestamp(kwargs["params"]["end"])
        return Response(payload={"bars": {"AAA": [r for r in rows if pd.Timestamp(r["t"]) <= end]}})
    downloader = make_sip_batch_downloader(_tasks("AAA"), None, NOW, request_get=get)
    frame = normalize_bars(downloader("AAA", date(2024, 1, 2), date(2024, 1, 9)), "AAA", "alpaca_stock_historical_v2", "raw")
    _validate_normalized(frame, date(2024, 1, 2), date(2024, 1, 9))
    assert len(frame) == 1


def test_completed_target_uses_delayed_cutoff_and_batch_sends_are_throttled(monkeypatch):
    now = datetime(2024, 1, 10, 3, 30, tzinfo=timezone.utc)
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls, sleeps, ticks = [], [], iter((0.0, 0.1, 0.5))
    def get(*_args, **kwargs):
        calls.append(kwargs)
        return Response(payload={"bars": {kwargs["params"]["symbols"]: [{"t": "x"}]}})
    downloader = make_sip_batch_downloader(_tasks("AAA", "BBB", end="2024-01-09"), None, now, batch_size=1,
        request_get=get, sleep=sleeps.append, monotonic=lambda: next(ticks))
    downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))
    downloader("BBB", date(2024, 1, 2), date(2024, 1, 9))
    assert calls[0]["params"]["end"] == "2024-01-10T03:15:00Z"
    assert sleeps == [0.4]


def test_pagination_is_complete_before_frames_are_published(monkeypatch):
    responses = [Response(payload={"bars": {"AAA": [{"t": "one"}]}, "next_page_token": "next"}),
                 Response(payload={"bars": {"AAA": [{"t": "two"}]}, "next_page_token": None})]
    downloader, calls = _downloader(monkeypatch, _tasks("AAA"), responses)
    assert len(downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))) == 2
    assert len(calls) == 2 and calls[1][1]["params"]["page_token"] == "next"


def test_bad_later_page_and_repeat_token_cache_batch_failure(monkeypatch):
    downloader, calls = _downloader(monkeypatch, _tasks("AAA", "BBB"), [
        Response(payload={"bars": {"AAA": [{"t": "one"}]}, "next_page_token": "next"}), Response(payload={"oops": True}),
    ])
    with pytest.raises(ValueError, match="response_invalid"):
        downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))
    with pytest.raises(ValueError, match="response_invalid"):
        downloader("BBB", date(2024, 1, 2), date(2024, 1, 9))
    assert len(calls) == 2
    repeat, _ = _downloader(monkeypatch, _tasks("AAA"), [
        Response(payload={"bars": {}, "next_page_token": "same"}), Response(payload={"bars": {}, "next_page_token": "same"}),
    ])
    with pytest.raises(ValueError, match="repeated_page_token"):
        repeat("AAA", date(2024, 1, 2), date(2024, 1, 9))


def test_duplicate_tasks_and_malformed_expected_rows_fail_closed(monkeypatch):
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    with pytest.raises(ValueError, match="tasks_invalid"):
        make_sip_batch_downloader(_tasks("AAA", "AAA"), None, NOW, request_get=lambda *_a, **_k: Response())
    downloader, calls = _downloader(monkeypatch, _tasks("AAA", "BBB"), [Response(payload={
        "bars": {"AAA": "not-a-list", "BBB": [{"t": "x"}]}, "next_page_token": None,
    })])
    with pytest.raises(ValueError, match="expected_symbol_rows_invalid"):
        downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))
    with pytest.raises(ValueError, match="expected_symbol_rows_invalid"):
        downloader("BBB", date(2024, 1, 2), date(2024, 1, 9))
    assert len(calls) == 1


def test_explicit_missing_symbol_is_unknown_not_delisted(monkeypatch):
    downloader, _ = _downloader(monkeypatch, _tasks("AAA"), [Response(payload={"bars": {}, "next_page_token": None})])
    from quant_data.recovery_sync import SymbolHistoryUnknown
    with pytest.raises(SymbolHistoryUnknown):
        downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))


def test_auth_and_rate_never_fallback(monkeypatch):
    for status, expected in ((401, FatalProviderError), (429, requests.HTTPError)):
        downloader, calls = _downloader(monkeypatch, _tasks("AAA"), [Response(status=status)])
        with pytest.raises(expected):
            downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))
        assert len(calls) == 1


def test_bad_batch_request_falls_back_to_one_symbol_request(monkeypatch):
    monkeypatch.setattr("quant_data.sip_batch.load_alpaca_credentials", lambda _: ("key", "secret"))
    monkeypatch.setattr("quant_data.pipeline.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls = []
    def get(*args, **kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return Response(400)
        return Response(payload={"bars": [{"t": "2024-01-02T05:00:00Z", "o": 1, "h": 2, "l": .5, "c": 1.5, "v": 3}]})
    sleeps, ticks = [], iter((0.0, 0.1, 0.5))
    downloader = make_sip_batch_downloader(_tasks("AAA"), None, NOW, request_get=get, sleep=sleeps.append,
                                            monotonic=lambda: next(ticks))
    assert len(downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))) == 1
    assert len(calls) == 2 and "symbols" not in calls[1]["params"]
    assert sleeps == [0.4]
    # A healthy fallback response is memoized; it does not make another call.
    downloader("AAA", date(2024, 1, 2), date(2024, 1, 9))
    assert len(calls) == 2
