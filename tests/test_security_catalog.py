import sqlite3

import pandas as pd
import pytest

from quant_data.security_catalog import SecurityCatalogueInputError, SecurityCatalogueStore


def test_cli_uses_verified_acquisition_pointer_when_data_root_supplied(monkeypatch, tmp_path):
    from quant_data.security_catalog import main
    from quant_data import sync_scheduler
    master = tmp_path / "master.parquet"
    _master(master)
    calls = []
    def resolve(root, base):
        calls.append((root, base))
        return master
    monkeypatch.setattr(sync_scheduler, "resolve_sync_master", resolve)
    assert main(["--master", str(tmp_path / "old.parquet"), "--state-root", str(tmp_path / "state"), "--data-root", str(tmp_path)]) == 0
    assert calls == [(tmp_path, tmp_path / "old.parquet")]


def _master(path):
    frame = pd.DataFrame([
        {"symbol": "TSLA", "raw_symbol": "TSLA", "name": "Tesla, Inc.", "exchange": "NASDAQ", "asset_type": "Stock", "ipo_date": "2010-06-29", "delisting_date": None, "status": "active", "source": "nasdaq", "source_as_of": "2026-09-16", "sources": ["nasdaq"], "provenance": {"source": "nasdaq"}},
        {"symbol": "ABC", "raw_symbol": "ABC", "name": "ABC Old Corp", "exchange": "NYSE", "asset_type": "Stock", "ipo_date": "2000-01-01", "delisting_date": "2010-01-01", "status": "delisted", "source": "listing_status", "source_as_of": "2026-09-15", "sources": ["listing_status"], "provenance": {"period": "old"}},
        {"symbol": "ABC", "raw_symbol": "ABC", "name": "ABC New Corp", "exchange": "NYSE", "asset_type": "ETF", "ipo_date": "2020-01-01", "delisting_date": None, "status": "active", "source": "nasdaq", "source_as_of": "2026-09-16", "sources": ["nasdaq"], "provenance": {"period": "new"}},
        {"symbol": "ATEST", "raw_symbol": "ATEST", "name": "Exchange test", "exchange": None, "asset_type": "Stock", "ipo_date": None, "delisting_date": None, "status": "active", "source": "nasdaq", "source_as_of": "2026-09-16", "sources": ["nasdaq"], "provenance": {"flag": "exact"}},
        {"symbol": "FLAG", "raw_symbol": "FLAG", "name": "Flagged test", "exchange": "NYSE", "asset_type": "Stock", "ipo_date": None, "delisting_date": None, "status": "active", "source": "nasdaq", "source_as_of": "2026-09-16", "sources": ["nasdaq"], "provenance": {"flag": "field"}, "is_test": "Y"},
        {"symbol": "BOND", "raw_symbol": "BOND", "name": "Ignored", "exchange": "NYSE", "asset_type": "Bond", "ipo_date": None, "delisting_date": None, "status": "active", "source": "x", "source_as_of": "2026-09-16", "sources": [], "provenance": {}},
    ])
    frame.to_parquet(path, index=False)


def test_import_preserves_provisional_lifecycles_and_is_idempotent(tmp_path):
    master = tmp_path / "consolidated.parquet"; _master(master)
    store = SecurityCatalogueStore(tmp_path / "state"); store.initialize()
    first = store.import_master(master)
    second = store.import_master(master)
    assert first["imported_records"] == second["imported_records"] == 5
    visible = store.list(query="abc", limit=50)
    assert visible["total"] == 2 and visible["total_records"] == 5 and visible["quarantined_records"] == 2
    assert {item["name"] for item in visible["items"]} == {"ABC Old Corp", "ABC New Corp"}
    assert all(item["identity_status"] == "provisional" and item["research_qualified"] is False for item in visible["items"])
    tsla = store.list(query="tesLA", limit=1)["items"][0]
    assert tsla["source"] == "nasdaq" and tsla["sources"] == ["nasdaq"] and tsla["provenance"] == {"source": "nasdaq"}
    assert tsla["exchange"] == "NASDAQ"
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT count(*) FROM catalogue_records").fetchone()[0] == 5
        assert db.execute("SELECT count(*) FROM catalogue_import_manifests").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM catalogue_record_snapshots").fetchone()[0] == 5


def test_exact_ticker_precedes_related_funds_with_name_match(tmp_path):
    master = tmp_path / "master.parquet"; _master(master)
    frame = pd.read_parquet(master)
    related = frame.iloc[0].copy()
    related["symbol"] = related["raw_symbol"] = "AAAF"
    related["name"] = "TSLA related fund"
    frame = pd.concat([frame, related.to_frame().T], ignore_index=True)
    frame.to_parquet(master, index=False)
    store = SecurityCatalogueStore(tmp_path / "state"); store.initialize(); store.import_master(master)
    result = store.list(query="tsla", limit=1)
    assert result["total"] == 2
    assert result["items"][0]["symbol"] == "TSLA"


def test_quarantine_is_visible_only_when_explicitly_requested_and_pagination_is_stable(tmp_path):
    master = tmp_path / "master.parquet"; _master(master)
    store = SecurityCatalogueStore(tmp_path); store.initialize(); store.import_master(master)
    assert store.list(limit=50)["total"] == 3
    quarantined = store.list(limit=50, include_quarantined=True)
    flagged = {item["symbol"]: item for item in quarantined["items"] if item["quarantined"]}
    assert set(flagged) == {"ATEST", "FLAG"}
    assert all(item["quarantine_reason"] == "exchange_test_symbol_or_test_flag" for item in flagged.values())
    page = store.list(limit=1, offset=1)
    assert len(page["items"]) == 1 and page["total"] == 3 and page["research_qualified"] is False
    with pytest.raises(SecurityCatalogueInputError): store.list(limit=101)


def test_invalid_schema_fails_closed(tmp_path):
    path = tmp_path / "bad.parquet"
    pd.DataFrame({"symbol": ["AAA"]}).to_parquet(path, index=False)
    store = SecurityCatalogueStore(tmp_path); store.initialize()
    with pytest.raises(SecurityCatalogueInputError, match="missing_required_columns"):
        store.import_master(path)


def test_changed_sources_are_snapshotted_and_test_quarantine_cannot_be_cleared(tmp_path):
    master = tmp_path / "master.parquet"; _master(master)
    store = SecurityCatalogueStore(tmp_path); store.initialize(); store.import_master(master)
    changed = pd.read_parquet(master)
    changed.loc[changed.symbol == "TSLA", "source"] = "revised_source"
    changed.loc[changed.symbol == "FLAG", "is_test"] = None
    changed.to_parquet(master, index=False)
    store.import_master(master)
    all_rows = store.list(limit=50, include_quarantined=True)["items"]
    assert next(item for item in all_rows if item["symbol"] == "FLAG")["quarantined"] is True
    assert next(item for item in all_rows if item["symbol"] == "TSLA")["source"] == "revised_source"
    assert store.list(query="%", limit=50)["total"] == 0  # LIKE metacharacters are literals.
    with sqlite3.connect(store.db_path) as db:
        assert db.execute("SELECT count(*) FROM catalogue_import_manifests").fetchone()[0] == 2
        assert db.execute("SELECT count(*) FROM catalogue_record_snapshots").fetchone()[0] == 10


def test_api_returns_empty_without_import_and_safe_catalogue_metadata(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from quant_data.api import create_app

    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    empty = client.get("/api/v1/security-catalogue?query=TSLA&limit=50")
    assert empty.status_code == 200
    assert empty.json() == {"items": [], "total": 0, "total_records": 0, "quarantined_records": 0, "research_qualified": False}
    master = tmp_path / "master.parquet"; _master(master)
    store = SecurityCatalogueStore(tmp_path); store.initialize(); store.import_master(master)
    payload = client.get("/api/v1/security-catalogue?query=tesla&limit=1").json()
    assert payload["total"] == 1 and payload["items"][0]["symbol"] == "TSLA"
    assert client.get("/api/v1/security-catalogue?limit=101").status_code == 422
