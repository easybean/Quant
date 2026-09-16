from datetime import date, datetime, timezone
import json

import pandas as pd

from quant_data.pipeline import PermanentDownloadError, symbol_key
from quant_data.recovery_sync import NAMESPACE, run_recovery

NOW = datetime(2024, 1, 10, 23, tzinfo=timezone.utc)


def _setup(tmp_path, yahoo_rows, master_rows=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    root = tmp_path / "data"; master = tmp_path / "master.parquet"
    pd.DataFrame(master_rows or {"symbol": ["AAA"], "status": ["active"], "asset_type": ["Stock"]}).to_parquet(master, index=False)
    records = root / "manifests/yahoo-daily-v1/records.jsonl"; records.parent.mkdir(parents=True)
    records.write_text("\n".join(json.dumps(x) for x in yahoo_rows) + "\n")
    catalogue = root / "catalogue/us-daily-browser-v1.json"; catalogue.parent.mkdir(parents=True)
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [{"symbol": "OTHER", "provider": "x", "namespace": "y", "series_id": "x:y:OTHER", "raw_relative_path": "provider=x/namespace=y/symbol=OTHER-1/bars.parquet"}]}))
    return master, root


def _failure(symbol="AAA", stamp="2024-01-10T00:00:00+00:00"):
    return {"symbol": symbol, "status": "failed", "attempted_at": stamp, "requested_start": "2024-01-02", "requested_end": "2024-01-09"}


def _bars(days=("2024-01-02",)):
    return pd.DataFrame({"date": pd.to_datetime(days), "open": [10.] * len(days), "high": [11.] * len(days), "low": [9.] * len(days), "close": [10.5] * len(days), "volume": [2] * len(days)})


def test_latest_yahoo_success_does_not_recover(tmp_path):
    master, root = _setup(tmp_path, [_failure(), {**_failure(), "status": "success", "attempted_at": "2024-01-11T00:00:00+00:00"}])
    result = run_recovery(master, root, now=NOW, downloader=lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert result["requested"] == result["attempted"] == 0


def test_deferred_does_not_cancel_failure_and_missing_volume_is_rejected(tmp_path):
    master, root = _setup(tmp_path, [_failure(), {**_failure(), "status": "deferred", "observed_at": "2024-01-11T00:00:00+00:00"}])
    frame = _bars()
    frame["volume"] = float("nan")
    result = run_recovery(master, root, now=NOW, downloader=lambda *_: frame)
    assert result["attempted"] == 1 and result["failed"] == 1
    assert not list((root / "bars").glob("**/bars.parquet"))


def test_verified_alias_keeps_original_identity_and_separate_yahoo_source(tmp_path, monkeypatch):
    master, root = _setup(tmp_path, [_failure("AAC.U")], {"symbol": ["AAC.U"], "status": ["active"], "asset_type": ["Stock"]})
    calls = []
    monkeypatch.setattr("quant_data.recovery_sync.yahoo_daily_history_downloader", lambda symbol, *_: calls.append(symbol) or _bars())
    result = run_recovery(master, root, now=NOW)
    assert calls == ["AAC-UN"] and result["success"] == 1
    record = json.loads((root / "manifests" / NAMESPACE / "records.jsonl").read_text())
    assert record["symbol"] == "AAC.U" and record["provider_symbol"] == "AAC-UN"
    assert record["provider"] == "yfinance" and record["namespace"] == "yahoo-symbol-recovery-v1"


def test_archives_hash_unknown_actions_and_preserves_catalogue(tmp_path):
    master, root = _setup(tmp_path, [_failure()])
    result = run_recovery(master, root, now=NOW, downloader=lambda *_: _bars())
    assert result["success"] == 1
    record = json.loads((root / "manifests" / NAMESPACE / "records.jsonl").read_text())
    assert len(record["archive_sha256"]) == 64
    saved = pd.read_parquet(root / "bars/daily/provider=nasdaq/namespace=nasdaq-daily-recovery-v1" / f"symbol={symbol_key('AAA')}" / "bars.parquet")
    assert saved["dividends"].isna().all() and saved["adj_close"].isna().all() and saved["actions_status"].eq("unknown").all()
    assert {x["symbol"] for x in json.loads((root / "catalogue/us-daily-browser-v1.json").read_text())["series"]} == {"AAA", "OTHER"}


def test_validation_failure_never_writes_current_series(tmp_path):
    master, root = _setup(tmp_path, [_failure()])
    result = run_recovery(master, root, now=NOW, downloader=lambda *_: pd.DataFrame({"date": ["2024-01-02"], "open": [10], "high": [9], "low": [8], "close": [10], "volume": [1]}))
    assert result["failed"] == 1
    assert not list((root / "bars").glob("**/bars.parquet"))


def test_only_failed_active_symbols_and_same_marker_is_idempotent(tmp_path):
    rows = [_failure("AAA"), _failure("INACTIVE")]
    master, root = _setup(tmp_path, rows, {"symbol": ["AAA", "INACTIVE"], "status": ["active", "inactive"], "asset_type": ["Stock", "Stock"]})
    calls = []
    run_recovery(master, root, now=NOW, downloader=lambda symbol, *_: calls.append(symbol) or _bars())
    again = run_recovery(master, root, now=NOW, downloader=lambda *_: (_ for _ in ()).throw(AssertionError()))
    assert calls == ["AAA"] and again["requested"] == 0


def test_permanent_error_does_not_open_circuit_but_network_errors_do(tmp_path):
    master, root = _setup(tmp_path, [_failure("AAA"), _failure("BBB")], {"symbol": ["AAA", "BBB"], "status": ["active", "active"], "asset_type": ["Stock", "Stock"]})
    result = run_recovery(master, root, now=NOW, downloader=lambda symbol, *_: (_ for _ in ()).throw(PermanentDownloadError()) if symbol == "AAA" else _bars(), request_delay=0)
    assert not result["circuit_open"] and result["attempted"] == 2
    master, root = _setup(tmp_path / "two", [_failure("AAA"), _failure("BBB"), _failure("CCC")], {"symbol": ["AAA", "BBB", "CCC"], "status": ["active"] * 3, "asset_type": ["Stock"] * 3})
    result = run_recovery(master, root, now=NOW, downloader=lambda *_: (_ for _ in ()).throw(RuntimeError("network")), request_delay=0)
    assert result["circuit_open"] and result["attempted"] == 3
