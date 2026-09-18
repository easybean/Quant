from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from quant_data.massive_publish import MassivePublishError, publish_massive_daily


DAY = date(2026, 9, 16)
NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
OBSERVED = "2026-09-17T12:00:00+00:00"


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _snapshot(root, rows):
    snapshot = root / "reference" / "massive-daily-v1" / "fixed"
    snapshot.mkdir(parents=True)
    raw = b'{"status":"OK","adjusted":false}'
    (snapshot / "response.json").write_bytes(raw)
    frame = pd.DataFrame(rows, columns=["symbol", "date", "open", "high", "low", "close", "volume", "available_at", "retrieved_at", "actions_status"])
    frame.to_parquet(snapshot / "bars.parquet", index=False)
    diagnostics = {"schema_version": "massive-daily-validation-v1", "accepted_count": len(rows), "accepted_rows": list(range(len(rows))), "rejected_count": 0, "rejected_rows": [], "response_error": None}
    (snapshot / "diagnostics.json").write_text(json.dumps(diagnostics))
    manifest = {"schema_version": "massive-daily-reference-v1", "namespace": "massive-daily-v1", "snapshot_relative_path": "reference/massive-daily-v1/fixed",
                "source": "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{date}",
                "request": {"url": "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/2026-09-16", "date": DAY.isoformat(), "adjusted": False, "include_otc": False, "method": "GET"},
                "status": "captured", "qualified": False, "research_qualified": False, "actions_status": "unknown", "observed_at": OBSERVED,
                "response_sha256": _sha(snapshot / "response.json"), "diagnostics_sha256": _sha(snapshot / "diagnostics.json"), "normalized_sha256": _sha(snapshot / "bars.parquet")}
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    return snapshot


def _setup(root, rows):
    snapshot = _snapshot(root, rows)
    master = root / "master.csv"
    pd.DataFrame({"symbol": ["AAA", "BBB"], "status": ["active", "active"], "asset_type": ["stock", "etf"]}).to_csv(master, index=False)
    catalogue = root / "catalogue" / "us-daily-browser-v1.json"
    catalogue.parent.mkdir()
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": []}))
    return snapshot, master


def _row(symbol):
    return [symbol, DAY.isoformat(), 10.0, 11.0, 9.0, 10.5, 100, OBSERVED, OBSERVED, "unknown"]


def test_publishes_exact_active_symbols_with_observation_lineage_and_idempotency(tmp_path):
    snapshot, master = _setup(tmp_path, [_row("AAA"), _row("NOTINMASTER")])
    first = publish_massive_daily(master, tmp_path, snapshot=snapshot, date_=DAY, now=NOW)
    assert first["status"] == "success"
    assert first["success"] == 1 and first["unmatched"] == 1 and first["missing"] == 1
    output = next((tmp_path / "bars").rglob("bars.parquet"))
    frame = pd.read_parquet(output)
    assert frame.loc[0, "symbol"] == "AAA"
    assert frame.loc[0, "available_at"] == OBSERVED and frame.loc[0, "adjusted"] == False
    records = tmp_path / "manifests/massive-daily-v1/records.jsonl"
    count = len(records.read_text().splitlines())
    second = publish_massive_daily(master, tmp_path, snapshot=snapshot, date_=DAY, now=NOW)
    assert second["skipped"] == 1 and len(records.read_text().splitlines()) == count
    assert len(list((tmp_path / "manifests/massive-daily-v1/publications").rglob("*.json"))) == 2


def test_existing_invalid_view_is_copied_to_quarantine_without_overwrite(tmp_path):
    snapshot, master = _setup(tmp_path, [_row("AAA")])
    path = tmp_path / "bars/daily/provider=massive/namespace=massive-daily-v1/symbol=AAA-cb1ad211/bars.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"bad": [1]}).to_parquet(path)
    result = publish_massive_daily(master, tmp_path, snapshot=snapshot, date_=DAY, now=NOW)
    assert result["failed"] == 1
    assert pd.read_parquet(path).columns.tolist() == ["bad"]
    assert list((tmp_path / "quarantine/massive-daily-v1").glob("*.parquet"))


def test_snapshot_integrity_and_link_escape_are_rejected(tmp_path):
    snapshot, master = _setup(tmp_path, [_row("AAA")])
    (snapshot / "response.json").write_bytes(b"changed")
    with pytest.raises(MassivePublishError, match="snapshot_integrity_invalid"):
        publish_massive_daily(master, tmp_path, snapshot=snapshot, date_=DAY, now=NOW)
