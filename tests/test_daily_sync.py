from datetime import date, datetime, timezone
import fcntl
import json
import sys
import types

import pandas as pd
import pytest

from quant_data import daily_sync
from quant_data.daily_sync import NAMESPACE, run_sync
from quant_data.pipeline import symbol_key


NOW = datetime(2024, 1, 10, 23, tzinfo=timezone.utc)


def test_empty_history_is_not_reported_as_completed_acquisition(tmp_path):
    master = _master(tmp_path, ("AAA",))
    result = run_sync(master, tmp_path / "data", now=NOW, downloader=lambda *_: pd.DataFrame())
    assert result["empty_history"] == 1 and result["status"] == "partial"


def _master(tmp_path, symbols=("AAA", "BBB")):
    path = tmp_path / "master.parquet"
    pd.DataFrame({"symbol": symbols, "status": ["active"] * len(symbols),
                  "asset_type": ["Stock"] * len(symbols)}).to_parquet(path, index=False)
    return path


def _bars(days):
    index = pd.to_datetime(days)
    index.name = "Date"
    return pd.DataFrame({"Open": [10.0] * len(index), "High": [11.0] * len(index),
                         "Low": [9.0] * len(index), "Close": [10.5] * len(index),
                         "Volume": [5] * len(index)}, index=index)


def _baseline(tmp_path, symbols):
    path = tmp_path / "baselines.json"
    path.write_text(json.dumps({"schema_version": "yahoo-sync-baselines-v1", "symbols": {
        symbol: {"last_date": value} for symbol, value in symbols.items()
    }}), encoding="utf-8")
    return path


def test_incremental_window_archives_raw_and_keeps_revision(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    calls = []
    def download(symbol, start, end):
        calls.append((start, end))
        return _bars(["2024-01-02", "2024-01-03"])
    first = run_sync(master, root, start=date(2024, 1, 1), now=NOW, downloader=download, sleep=lambda _: None)
    assert first["success"] == 1
    second = run_sync(master, root, start=date(2024, 1, 1), now=NOW, downloader=download, sleep=lambda _: None)
    assert calls[1][0] == date(2024, 1, 1)  # max 1/3, floored by requested start
    archives = list((root / "reference" / NAMESPACE).glob("*/*.parquet"))
    assert len(archives) == 2
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    assert len(pd.read_parquet(current)) == 2


def test_selection_rotation_backfill_limit_and_failure_circuit(tmp_path):
    master, root = _master(tmp_path), tmp_path / "data"
    failed = run_sync(master, root, now=NOW, max_backfill_symbols=1, downloader=lambda *_: (_ for _ in ()).throw(RuntimeError()), sleep=lambda _: None)
    assert failed["failed"] == 1 and failed["deferred"] == 1
    records = root / "manifests" / NAMESPACE / "records.jsonl"
    first_symbol = json.loads(records.read_text().splitlines()[0])["symbol"]
    seen = []
    run_sync(master, root, now=NOW, max_symbols=1, downloader=lambda s, *_: seen.append(s) or _bars(["2024-01-02"]), sleep=lambda _: None)
    assert seen[0] != first_symbol


def test_rejects_response_outside_requested_window_and_future_end(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    result = run_sync(master, root, now=NOW, downloader=lambda *_: _bars(["2024-01-10"]), sleep=lambda _: None)
    assert result["failed"] == 1
    with pytest.raises(ValueError, match="conservative"):
        run_sync(master, root, end=date(2024, 1, 20), now=NOW)


def test_incremental_window_is_seven_days_not_original_start(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    later = datetime(2024, 2, 1, 23, tzinfo=timezone.utc)
    run_sync(master, root, start=date(2024, 1, 1), now=later,
             downloader=lambda *_: _bars(["2024-01-20"]), sleep=lambda _: None)
    calls = []
    run_sync(master, root, start=date(2024, 1, 1), now=later,
             downloader=lambda _s, a, b: calls.append((a, b)) or _bars(["2024-01-20"]), sleep=lambda _: None)
    assert calls[0][0] == date(2024, 1, 13)


def test_bootstrap_lookback_only_limits_cold_symbols(tmp_path):
    master, root = _master(tmp_path, ("COLD", "LIVE")), tmp_path / "data"
    base = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('LIVE')}" / "bars.parquet"
    base.parent.mkdir(parents=True)
    pd.DataFrame({"date": [pd.Timestamp("2024-01-05")], "source": ["yfinance"],
                  "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "volume": [1]}).to_parquet(base, index=False)
    calls = {}
    result = run_sync(master, root, start=date(2024, 1, 1), now=NOW, bootstrap_lookback_days=3,
                      downloader=lambda symbol, start, end: calls.setdefault(symbol, (start, end)) and _bars([start.isoformat()]),
                      sleep=lambda _: None)
    assert calls["COLD"][0] == date(2024, 1, 7)
    assert calls["LIVE"][0] == date(2024, 1, 1)  # established series retains seven-day correction probe
    assert result["bootstrap_lookback_days"] == 3
    assert "last 3 calendar days" in result["bootstrap_window"]
    assert result["success"] == 2 and result["status"] == "success"


def test_attempt_checkpoints_remain_running_until_final_status(tmp_path):
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    snapshots = []
    def download(*_):
        latest = json.loads((root / "manifests" / NAMESPACE / "latest.json").read_text())
        snapshots.append(latest)
        return _bars(["2024-01-02"])
    result = run_sync(master, root, now=NOW, downloader=download, sleep=lambda _: None)
    latest = json.loads((root / "manifests" / NAMESPACE / "latest.json").read_text())
    assert [snapshot["status"] for snapshot in snapshots] == ["running", "running"]
    assert snapshots[1]["attempted"] == 1 and snapshots[1]["success"] == 1
    assert result["status"] == latest["status"] == "success"


def test_failed_current_file_attempt_checkpoints_running_summary(tmp_path):
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    corrupt = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    corrupt.parent.mkdir(parents=True)
    corrupt.write_bytes(b"not parquet")
    seen = []
    def download(symbol, *_):
        if symbol == "BBB":
            seen.append(json.loads((root / "manifests" / NAMESPACE / "latest.json").read_text()))
        return _bars(["2024-01-02"])
    result = run_sync(master, root, now=NOW, downloader=download, sleep=lambda _: None)
    assert seen[0]["status"] == "running"
    assert seen[0]["attempted"] == seen[0]["failed"] == 1
    assert result["status"] == "failed"


def test_rejects_negative_bootstrap_lookback(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    with pytest.raises(ValueError, match="limits"):
        run_sync(master, root, now=NOW, bootstrap_lookback_days=-1)


def test_invalid_values_and_corrupt_current_fail_without_overwrite(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    invalid = _bars(["2024-01-02"])
    invalid.loc[invalid.index[0], "Open"] = float("inf")
    assert run_sync(master, root, now=NOW, downloader=lambda *_: invalid, sleep=lambda _: None)["failed"] == 1
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"not parquet")
    result = run_sync(master, root, now=NOW, downloader=lambda *_: _bars(["2024-01-02"]), sleep=lambda _: None)
    assert result["failed"] == 1 and current.read_bytes() == b"not parquet"


def test_cold_limit_keeps_existing_current_and_circuit_defers(tmp_path):
    master, root = _master(tmp_path, ("LIVE", "X1", "X2", "X3", "X4")), tmp_path / "data"
    base = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('LIVE')}" / "bars.parquet"
    base.parent.mkdir(parents=True)
    normalized = pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "symbol": ["LIVE"],
        "open": [1.0], "high": [2.0], "low": [0.5], "close": [1.5], "adj_close": [1.5], "volume": [1],
        "dividends": [0.0], "stock_splits": [0.0], "source": ["yfinance"],
        "adjustment_status": ["raw_ohlc_with_adjusted_close_and_actions"], "feed": [""]})
    normalized.to_parquet(base, index=False)
    seen = []
    def loader(symbol, *_):
        seen.append(symbol)
        if symbol == "LIVE":
            return _bars(["2024-01-03"])
        raise RuntimeError("provider denied")
    result = run_sync(master, root, now=NOW, max_backfill_symbols=4, max_consecutive_failures=3,
                      downloader=loader, sleep=lambda _: None)
    assert "LIVE" in seen and result["circuit_open"] and result["deferred"] >= 1


def test_archives_revision_and_main_exit_codes(tmp_path, monkeypatch):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    run_sync(master, root, now=NOW, downloader=lambda *_: _bars(["2024-01-02"]), sleep=lambda _: None)
    revised = _bars(["2024-01-02"])
    revised.loc[revised.index[0], "Close"] = 99.0
    revised.loc[revised.index[0], "High"] = 100.0
    run_sync(master, root, now=NOW, downloader=lambda *_: revised, sleep=lambda _: None)
    assert len(list((root / "reference" / NAMESPACE).glob("*/*.parquet"))) == 2
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    stored = pd.read_parquet(current).iloc[0]
    assert stored.close == 99.0 and pd.isna(stored.adj_close) and pd.isna(stored.dividends)
    assert stored.actions_status == "unknown" and stored.retrieved_at
    monkeypatch.setattr(daily_sync, "run_sync", lambda **_: {"status": "partial"})
    assert daily_sync.main(["--security-master", "x", "--data-root", "x", "--start", "2024-01-01"]) == 2
    monkeypatch.setattr(daily_sync, "run_sync", lambda **_: {"status": "failed"})
    assert daily_sync.main(["--security-master", "x", "--data-root", "x", "--start", "2024-01-01"]) == 1


def test_lock_held_skips_without_download(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    lock = root / "manifests" / NAMESPACE / "sync.lock"
    lock.parent.mkdir(parents=True)
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_sync(master, root, now=NOW, downloader=lambda *_: pytest.fail("must not download"))
    assert result["status"] == "skipped"


def test_malformed_attempt_records_do_not_block_sync(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    records = root / "manifests" / NAMESPACE / "records.jsonl"
    records.parent.mkdir(parents=True)
    records.write_text('null\n[]\n{"unfinished":\n', encoding="utf-8")
    result = run_sync(master, root, now=NOW, downloader=lambda *_: _bars(["2024-01-02"]), sleep=lambda _: None)
    assert result["status"] == "success"


def test_dedicated_yahoo_history_downloader_uses_single_ticker_raising_api(monkeypatch):
    calls = []
    frame = _bars(["2024-01-02"])
    class Ticker:
        def __init__(self, symbol):
            calls.append(("Ticker", symbol))

        def history(self, **kwargs):
            calls.append(("history", kwargs))
            return frame
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(Ticker=Ticker))

    assert daily_sync.yahoo_daily_history_downloader("BRK.B", date(2024, 1, 1), date(2024, 1, 2)) is frame
    assert calls == [("Ticker", "BRK-B"), ("history", {
        "start": "2024-01-01", "end": "2024-01-03", "interval": "1d",
        "auto_adjust": False, "actions": True, "timeout": 30, "raise_errors": True,
    })]


def test_confirmed_yahoo_missing_symbol_fails_without_opening_provider_circuit(tmp_path, monkeypatch):
    class YFPricesMissingError(Exception):
        pass
    class YFTzMissingError(Exception):
        pass
    fake_yf = types.SimpleNamespace(exceptions=types.SimpleNamespace(
        YFPricesMissingError=YFPricesMissingError, YFTzMissingError=YFTzMissingError,
    ))
    monkeypatch.setitem(sys.modules, "yfinance", fake_yf)
    class Response:
        status_code = 404

        @staticmethod
        def json():
            return {"chart": {"error": {"code": "Not Found"}}}
    calls = []
    def diagnostic_get(url, **kwargs):
        calls.append((url, kwargs))
        return Response()
    monkeypatch.setitem(sys.modules, "curl_cffi", types.SimpleNamespace(
        requests=types.SimpleNamespace(get=diagnostic_get),
    ))
    master, root = _master(tmp_path, ("BAD1", "BAD2", "GOOD")), tmp_path / "data"
    def download(symbol, *_):
        if symbol == "BAD1":
            raise YFPricesMissingError()
        if symbol == "BAD2":
            raise YFTzMissingError()
        return _bars(["2024-01-02"])

    result = run_sync(master, root, now=NOW, max_consecutive_failures=1, downloader=download, sleep=lambda _: None)
    assert result["failed"] == 2 and result["success"] == 1 and not result["circuit_open"]
    records = [json.loads(line) for line in (root / "manifests" / NAMESPACE / "records.jsonl").read_text().splitlines()]
    assert [record["error_code"] for record in records if record["status"] == "failed"] == [
        "symbol_unavailable", "symbol_unavailable"
    ]
    assert len(calls) == 2
    assert all(call[1]["params"] == {"interval": "1d", "range": "5d"} for call in calls)


def test_yahoo_rate_limit_error_remains_provider_failure(tmp_path, monkeypatch):
    class YFRateLimitError(Exception):
        pass
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        exceptions=types.SimpleNamespace(YFRateLimitError=YFRateLimitError),
    ))
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    result = run_sync(master, root, now=NOW, max_consecutive_failures=1,
                      downloader=lambda *_: (_ for _ in ()).throw(YFRateLimitError()), sleep=lambda _: None)
    assert result["failed"] == 1 and result["circuit_open"] and result["deferred"] == 1


def test_yahoo_missing_timezone_with_diagnostic_network_failure_still_opens_circuit(tmp_path, monkeypatch):
    class YFTzMissingError(Exception):
        pass
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        exceptions=types.SimpleNamespace(YFTzMissingError=YFTzMissingError),
    ))
    def diagnostic_get(*_, **__):
        raise OSError("network unavailable")
    monkeypatch.setitem(sys.modules, "curl_cffi", types.SimpleNamespace(
        requests=types.SimpleNamespace(get=diagnostic_get),
    ))
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    result = run_sync(master, root, now=NOW, max_consecutive_failures=1,
                      downloader=lambda *_: (_ for _ in ()).throw(YFTzMissingError()), sleep=lambda _: None)
    assert result["failed"] == 1 and result["circuit_open"] and result["deferred"] == 1


def test_invalid_ohlcv_is_local_and_does_not_open_provider_circuit(tmp_path):
    master, root = _master(tmp_path, ("BAD", "GOOD")), tmp_path / "data"
    bad = _bars(["2024-01-02"])
    bad.loc[bad.index[0], "High"] = 1.0
    result = run_sync(master, root, now=NOW, max_consecutive_failures=1,
                      downloader=lambda symbol, *_: bad if symbol == "BAD" else _bars(["2024-01-02"]),
                      sleep=lambda _: None)
    assert result["failed"] == 1 and result["success"] == 1 and not result["circuit_open"]


def test_downloader_value_error_is_unknown_provider_failure_and_opens_circuit(tmp_path):
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    result = run_sync(master, root, now=NOW, max_consecutive_failures=1,
                      downloader=lambda *_: (_ for _ in ()).throw(ValueError("JSON decode failed")), sleep=lambda _: None)
    assert result["failed"] == 1 and result["circuit_open"] and result["deferred"] == 1
    assert result["last_error_code"] == "invalid_response"


def test_confirmed_symbol_outcome_resets_prior_provider_failure_count(tmp_path, monkeypatch):
    class YFPricesMissingError(Exception):
        pass
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        exceptions=types.SimpleNamespace(YFPricesMissingError=YFPricesMissingError),
    ))
    class Response:
        status_code = 404

        @staticmethod
        def json():
            return {"chart": {"error": {"code": "Not Found"}}}
    monkeypatch.setitem(sys.modules, "curl_cffi", types.SimpleNamespace(
        requests=types.SimpleNamespace(get=lambda *_, **__: Response()),
    ))
    master, root = _master(tmp_path, ("AAA", "BAD", "GOOD")), tmp_path / "data"
    def download(symbol, *_):
        if symbol == "AAA":
            raise RuntimeError("network fault")
        if symbol == "BAD":
            raise YFPricesMissingError()
        return _bars(["2024-01-02"])
    result = run_sync(master, root, now=NOW, max_consecutive_failures=2, downloader=download, sleep=lambda _: None)
    assert result["failed"] == 2 and result["success"] == 1 and not result["circuit_open"]


def test_chart_diagnostic_distinguishes_healthy_empty_from_unknown(tmp_path, monkeypatch):
    class YFPricesMissingError(Exception):
        pass
    monkeypatch.setitem(sys.modules, "yfinance", types.SimpleNamespace(
        exceptions=types.SimpleNamespace(YFPricesMissingError=YFPricesMissingError),
    ))
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"chart": {"result": [{"timestamp": []}], "error": None}}
    monkeypatch.setitem(sys.modules, "curl_cffi", types.SimpleNamespace(
        requests=types.SimpleNamespace(get=lambda *_, **__: Response()),
    ))
    master, root = _master(tmp_path, ("EMPTY", "GOOD")), tmp_path / "data"
    result = run_sync(master, root, now=NOW, max_consecutive_failures=1,
                      downloader=lambda symbol, *_: (_ for _ in ()).throw(YFPricesMissingError()) if symbol == "EMPTY" else _bars(["2024-01-02"]),
                      sleep=lambda _: None)
    assert result["empty_history"] == 1 and result["success"] == 1 and not result["circuit_open"]
    records = [json.loads(line) for line in (root / "manifests" / NAMESPACE / "records.jsonl").read_text().splitlines()]
    assert next(row for row in records if row["symbol"] == "EMPTY")["chart_classification"] == "healthy_empty"


def _gap_queue(tmp_path, tasks, target="2024-01-09"):
    path = tmp_path / "gap-queue.json"
    path.write_text(json.dumps({"schema_version": "us-daily-gap-queue-v1", "target_date": target, "tasks": tasks}), encoding="utf-8")
    return path


def test_gap_queue_attempts_planned_gap_even_when_unattempted_or_baseline_current(tmp_path):
    master, root = _master(tmp_path, ("AAA", "INACTIVE")), tmp_path / "data"
    # The inactive task is ignored; the explicit active gap cannot be skipped
    # just because the baseline says its endpoint is current.
    queue = _gap_queue(tmp_path, [
        {"symbol": "AAA", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "gap-aaa"},
        {"symbol": "INACTIVE", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "gap-inactive"},
    ])
    # Mark the second symbol inactive after the common helper has constructed
    # the fixture's initial active rows.
    frame = pd.read_parquet(master); frame.loc[frame.symbol == "INACTIVE", "status"] = "inactive"; frame.to_parquet(master, index=False)
    baseline = _baseline(tmp_path, {"AAA": "2024-01-09"})
    calls = []
    result = run_sync(master, root, now=NOW, gap_queue=queue, baseline_index=baseline,
                      downloader=lambda symbol, start, end: calls.append((symbol, start, end)) or _bars(["2024-01-03"]),
                      sleep=lambda _: None)
    assert calls == [("AAA", date(2024, 1, 3), date(2024, 1, 9))]
    assert result["requested"] == result["attempted"] == 1 and result["target_end"] == "2024-01-09"
    record = json.loads((root / "manifests" / NAMESPACE / "records.jsonl").read_text())
    assert record["source_attempted_at"] == "gap-aaa"


def test_gap_queue_repairs_earlier_internal_gap_for_existing_series(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    current.parent.mkdir(parents=True)
    pd.DataFrame({"date": [pd.Timestamp("2024-01-09")], "source": ["yfinance"], "open": [1.], "high": [2.], "low": [.5], "close": [1.5], "volume": [1]}).to_parquet(current, index=False)
    queue = _gap_queue(tmp_path, [{"symbol": "AAA", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "older-gap"}])
    calls = []
    run_sync(master, root, now=NOW, gap_queue=queue,
             downloader=lambda _s, start, end: calls.append((start, end)) or _bars(["2024-01-03"]), sleep=lambda _: None)
    assert calls == [(date(2024, 1, 3), date(2024, 1, 9))]


def test_gap_queue_current_read_failure_keeps_task_marker(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    current.parent.mkdir(parents=True)
    current.write_bytes(b"corrupt")
    queue = _gap_queue(tmp_path, [{"symbol": "AAA", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "corrupt-gap"}])
    run_sync(master, root, now=NOW, gap_queue=queue, downloader=lambda *_: pytest.fail("must not download"), sleep=lambda _: None)
    record = json.loads((root / "manifests" / NAMESPACE / "records.jsonl").read_text())
    assert record["error_code"] == "current_bar_read_failed" and record["source_attempted_at"] == "corrupt-gap"


def test_gap_queue_rejects_future_plan_target(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    queue = _gap_queue(tmp_path, [{"symbol": "AAA", "requested_start": "2024-01-03", "requested_end": "2024-01-10", "task_id": "future"}], target="2024-01-10")
    with pytest.raises(ValueError, match="gap_queue_invalid"):
        run_sync(master, root, now=NOW, gap_queue=queue, sleep=lambda _: None)


def test_gap_queue_runtime_budget_writes_one_run_event_not_every_pending_task(tmp_path):
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    queue = _gap_queue(tmp_path, [
        {"symbol": "AAA", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "one"},
        {"symbol": "BBB", "requested_start": "2024-01-03", "requested_end": "2024-01-09", "task_id": "two"},
    ])
    ticks = iter((0.0, 5.0))
    result = run_sync(master, root, now=NOW, gap_queue=queue, max_runtime_seconds=1, monotonic=lambda: next(ticks),
                      downloader=lambda *_: pytest.fail("must not download"), sleep=lambda _: None)
    records = [json.loads(line) for line in (root / "manifests" / NAMESPACE / "records.jsonl").read_text().splitlines()]
    assert result["deferred"] == 2 and len(records) == 1
    assert records[0]["scope"] == "run" and records[0]["deferred_count"] == 2


def test_runtime_budget_defers_without_claiming_attempts(tmp_path):
    master, root = _master(tmp_path, ("AAA", "BBB")), tmp_path / "data"
    ticks = iter((0.0, 5.0))
    result = run_sync(master, root, now=NOW, max_runtime_seconds=1, monotonic=lambda: next(ticks),
                      downloader=lambda *_: pytest.fail("must not download"), sleep=lambda _: None)
    assert result["status"] == "partial" and result["runtime_budget_exhausted"]
    assert result["attempted"] == 0 and result["deferred"] == 2
    records = [json.loads(line) for line in (root / "manifests" / NAMESPACE / "records.jsonl").read_text().splitlines()]
    assert {record["symbol"] for record in records} == {"AAA", "BBB"}
    assert all(record["status"] == "deferred" and record["error_code"] == "runtime_budget_exhausted" for record in records)


def test_baseline_cold_symbol_starts_at_legacy_gap_and_never_mixes_series(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    calls = []
    baseline = _baseline(tmp_path, {"AAA": "2024-01-05"})
    run_sync(master, root, start=date(2024, 1, 1), now=NOW, baseline_index=baseline,
             downloader=lambda _s, a, b: calls.append((a, b)) or _bars([a.isoformat()]), sleep=lambda _: None)
    assert calls == [(date(2024, 1, 6), date(2024, 1, 9))]


def test_baseline_bridges_existing_yahoo_series_until_success_record_covers_gap(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    baseline = _baseline(tmp_path, {"AAA": "2024-01-05"})
    run_sync(master, root, start=date(2024, 1, 13), now=datetime(2024, 1, 25, 23, tzinfo=timezone.utc),
             downloader=lambda *_: _bars(["2024-01-20"]), sleep=lambda _: None)
    calls = []
    run_sync(master, root, start=date(2024, 1, 1), now=datetime(2024, 1, 25, 23, tzinfo=timezone.utc),
             baseline_index=baseline,
             downloader=lambda _s, a, b: calls.append((a, b)) or _bars(["2024-01-20"]), sleep=lambda _: None)
    assert calls[0][0] == date(2024, 1, 6)
    calls.clear()
    run_sync(master, root, start=date(2024, 1, 1), now=datetime(2024, 1, 25, 23, tzinfo=timezone.utc),
             baseline_index=baseline,
             downloader=lambda _s, a, b: calls.append((a, b)) or _bars(["2024-01-20"]), sleep=lambda _: None)
    assert calls[0][0] == date(2024, 1, 13)


def test_baseline_at_target_is_up_to_date_without_yahoo_attempt(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    baseline = _baseline(tmp_path, {"AAA": "2024-01-09"})
    result = run_sync(master, root, now=NOW, baseline_index=baseline,
                      downloader=lambda *_: pytest.fail("must not download"), sleep=lambda _: None)
    assert result["status"] == "success" and result["already_current"] == 1 and result["attempted"] == 0
    record = json.loads((root / "manifests" / NAMESPACE / "records.jsonl").read_text())
    assert record["status"] == "up_to_date"


def test_baseline_future_or_invalid_schema_fails_closed(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    future = _baseline(tmp_path, {"AAA": "2024-01-11"})
    with pytest.raises(ValueError, match="future"):
        run_sync(master, root, now=NOW, baseline_index=future, sleep=lambda _: None)
    invalid = tmp_path / "invalid.json"
    invalid.write_text('{"schema_version":"other","symbols":{}}', encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        run_sync(master, root, now=NOW, baseline_index=invalid, sleep=lambda _: None)


def test_timezone_shifted_yahoo_day_revises_legacy_midnight_row_without_duplication(tmp_path):
    master, root = _master(tmp_path, ("AAA",)), tmp_path / "data"
    current = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet"
    current.parent.mkdir(parents=True)
    pd.DataFrame({"date": [pd.Timestamp("2024-01-02")], "symbol": ["AAA"],
                  "open": [10.0], "high": [11.0], "low": [9.0], "close": [10.5],
                  "adj_close": [10.5], "volume": [5], "dividends": [0.0], "stock_splits": [0.0],
                  "source": ["yfinance"], "adjustment_status": ["legacy"], "feed": [""]}).to_parquet(current, index=False)
    raw = _bars(["2024-01-02"])
    raw.index = pd.DatetimeIndex(raw.index, tz="America/New_York", name="Date")
    raw.loc[:, "Close"] = 99.0
    raw.loc[:, "High"] = 100.0
    result = run_sync(master, root, start=date(2024, 1, 1), now=NOW,
                      downloader=lambda *_: raw, sleep=lambda _: None)
    stored = pd.read_parquet(current)
    archives = list((root / "reference" / NAMESPACE).glob("*/*.parquet"))
    assert result["success"] == 1 and len(stored) == 1
    assert stored.iloc[0].date == pd.Timestamp("2024-01-02") and stored.iloc[0].close == 99.0
    assert len(archives) == 1 and len(pd.read_parquet(archives[0])) == 1
