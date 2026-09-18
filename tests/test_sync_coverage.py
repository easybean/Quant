import json
import hashlib

import pandas as pd

from quant_data.sync_coverage import build_sync_coverage


def test_massive_endpoint_is_counted_but_does_not_erase_bridge_gap(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr("quant_data.sync_scheduler.completed_session", lambda _: date(2026, 9, 17))
    manifest = tmp_path / "manifests/yahoo-daily-v1"; manifest.mkdir(parents=True)
    (manifest / "baselines.json").write_text(json.dumps({"symbols": {"TSLA": {"last_date": "2026-08-31"}}}))
    source = tmp_path / "manifests/massive-daily-v1"; source.mkdir()
    (source / "records.jsonl").write_text(json.dumps({"symbol": "TSLA", "status": "success", "requested_start": "2026-09-17"}) + "\n")
    catalogue = tmp_path / "catalogue"; catalogue.mkdir()
    (catalogue / "us-daily-browser-v1.json").write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [{"symbol": "TSLA", "provider": "massive", "namespace": "massive-daily-v1", "last_date": "2026-09-17"}]}))
    master = tmp_path / "master.parquet"
    pd.DataFrame({"symbol": ["TSLA"], "status": ["active"], "asset_type": ["Stock"]}).to_parquet(master)
    result = build_sync_coverage(tmp_path, master)
    assert result["needs_update"] == 1
    details = json.loads((manifest / "coverage.json").read_text())["symbols"]
    assert details[0]["latest_date"] == "2026-09-17" and details[0]["bridge_needed"] is True


def test_historical_only_is_excluded_and_bridge_gap_is_not_current(tmp_path, monkeypatch):
    from datetime import date
    monkeypatch.setattr("quant_data.sync_scheduler.completed_session", lambda _: date(2026, 9, 15))
    manifest = tmp_path / "manifests/yahoo-daily-v1"
    manifest.mkdir(parents=True)
    (manifest / "baselines.json").write_text(json.dumps({"symbols": {"OLD": {"last_date": "2020-01-01"}, "TSLA": {"last_date": "2026-08-31"}}}))
    path = tmp_path / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1/symbol=TSLA-2ff6985a/bars.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-09-15"]}).to_parquet(path, index=False)
    catalogue = tmp_path / "catalogue/us-daily-browser-v1.json"
    catalogue.parent.mkdir(parents=True)
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [{"symbol": "TSLA", "provider": "yfinance", "namespace": "yahoo-daily-v1", "last_date": "2026-09-15"}]}))
    master = tmp_path / "master.parquet"
    pd.DataFrame({"symbol": ["OLD", "TSLA"], "status": ["delisted", "active"], "asset_type": ["Stock", "Stock"]}).to_parquet(master, index=False)
    result = build_sync_coverage(tmp_path, master)
    assert result["historical_only_symbols"] == 1 and result["needs_update"] == 1
    (manifest / "records.jsonl").write_text(json.dumps({"symbol": "TSLA", "status": "success", "requested_start": "2026-09-01"}) + "\n")
    assert build_sync_coverage(tmp_path, master)["endpoint_current"] == 1
    derived = tmp_path / "reference/acquisition.parquet"
    derived.parent.mkdir()
    pd.DataFrame({"symbol": ["OLD", "TSLA", "NEW"], "status": ["delisted", "active", "active"], "asset_type": ["Stock"] * 3}).to_parquet(derived, index=False)
    (tmp_path / "catalogue/current-acquisition-master-v1.json").write_text(json.dumps({"schema_version": "current-acquisition-master-v1", "relative_path": "reference/acquisition.parquet", "sha256": hashlib.sha256(derived.read_bytes()).hexdigest()}))
    result = build_sync_coverage(tmp_path, master)
    assert result["active_symbols"] == 2 and result["needs_update"] == 1
    alpaca_manifest = tmp_path / "manifests/alpaca-sip-recovery-v1"
    alpaca_manifest.mkdir(parents=True)
    (alpaca_manifest / "records.jsonl").write_text(json.dumps({"symbol": "TSLA", "status": "success", "requested_start": "2026-09-01"}) + "\n")
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [{"symbol": "TSLA", "provider": "alpaca", "namespace": "alpaca-sip-recovery-v1", "last_date": "2026-09-15"}]}))
    assert build_sync_coverage(tmp_path, master)["endpoint_current"] == 1
    (manifest / "records.jsonl").unlink()
    recovery = tmp_path / "manifests/nasdaq-daily-recovery-v1"
    recovery.mkdir(parents=True)
    (recovery / "records.jsonl").write_text(json.dumps({"symbol": "TSLA", "status": "success", "requested_start": "2026-09-01"}) + "\n")
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [
        {"symbol": "TSLA", "provider": "nasdaq", "namespace": "nasdaq-daily-recovery-v1", "last_date": "2026-09-15"},
        {"symbol": "TSLA", "provider": "yfinance", "namespace": "yahoo-daily-v1", "last_date": "2026-09-10"}]}))
    assert build_sync_coverage(tmp_path, master)["endpoint_current"] == 1
