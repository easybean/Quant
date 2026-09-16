from datetime import date, datetime, timezone
import fcntl
import json

import pandas as pd
import pytest

from quant_data import daily_sync
from quant_data.daily_sync import NAMESPACE, run_sync
from quant_data.pipeline import symbol_key


NOW = datetime(2024, 1, 10, 23, tzinfo=timezone.utc)


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
