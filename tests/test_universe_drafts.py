import pandas as pd
import pytest

from quant_data.security_catalog import SecurityCatalogueStore
from quant_data.universe_drafts import AssetPoolDraftStore, InstrumentDraftStore, UniverseDraftInputError


def _instrument(product="US_EQUITY"):
    item = {"instrument_id": "NYSE:US-EXAMPLE-001", "identity_key": "US-EXAMPLE-001", "asset": "equity", "product": product, "venue": "NYSE", "venue_symbol": "EXM", "timezone": "America/New_York", "calendar_id": "US-DRAFT", "linear": False, "inverse": False, "symbol_history": [{"venue_symbol": "EXM", "valid_from": "2024-01-01"}], "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "share", "tick_size": "0.01", "lot_size": "1"}]}
    if product == "CRYPTO_SPOT":
        item.update({"instrument_id": "BINANCE:BTC-USDT", "identity_key": "BTC-USDT", "asset": "crypto", "venue": "BINANCE", "venue_symbol": "BTCUSDT", "timezone": "UTC", "calendar_id": "CRYPTO-24X7", "base_currency": "BTC", "quote_currency": "USDT", "settlement_currency": "USDT", "symbol_history": [{"venue_symbol": "BTCUSDT", "valid_from": "2024-01-01"}]})
    if product in {"CRYPTO_PERPETUAL", "CRYPTO_DELIVERY", "CN_FUTURE", "GLOBAL_FUTURE"}:
        item.update({"asset": "crypto" if product.startswith("CRYPTO") else "future", "linear": True, "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "contract", "multiplier": "1", "tick_size": "0.01", "lot_size": "1"}]})
    if product in {"CRYPTO_PERPETUAL", "CRYPTO_DELIVERY"}: item.update({"base_currency": "BTC", "quote_currency": "USDT", "settlement_currency": "USDT"})
    if product == "CRYPTO_DELIVERY": item["expiry"] = "2025-12-31"
    return item


def test_saves_versions_with_stable_identity(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    first = store.save({"name": "美股示例", "instrument": _instrument()})
    second = store.save({"name": "美股示例 v2", "instrument": _instrument()}, first["id"])
    assert second["version"] == 2
    assert [row["version"] for row in store.history(first["id"])] == [2, 1]
    assert store.list()[0]["instrument"]["instrument_id"] == "NYSE:US-EXAMPLE-001"


def test_rejects_invalid_product_contract_combinations(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    invalid = _instrument("CRYPTO_DELIVERY"); invalid.pop("expiry")
    try: store.save({"name": "bad", "instrument": invalid})
    except UniverseDraftInputError as exc: assert "expiry" in str(exc)
    else: raise AssertionError("delivery without expiry must fail")
    assert store.list() == []


def test_accepts_spot_and_derivative_draft_shapes(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    spot = store.save({"name": "现货", "instrument": _instrument("CRYPTO_SPOT")})
    delivery = store.save({"name": "交割", "instrument": _instrument("CRYPTO_DELIVERY")})
    assert spot["instrument"]["product"] == "CRYPTO_SPOT"
    assert delivery["instrument"]["product"] == "CRYPTO_DELIVERY"


def test_asset_pool_only_persists_saved_immutable_references(tmp_path):
    instruments = InstrumentDraftStore(tmp_path); instruments.initialize()
    definition = instruments.save({"name": "美股", "instrument": _instrument()})
    pools = AssetPoolDraftStore(tmp_path, instruments); pools.initialize()
    reference = {"instrument_draft_id": definition["id"], "instrument_version": 1}
    created = pools.save({"name": "研究池", "purpose": "研究用途", "instruments": [reference]})
    assert created["instruments"] == [reference]
    assert pools.history(created["id"])[0]["purpose"] == "研究用途"


def _catalogue_master(path, *, test_flag=None):
    pd.DataFrame([{"symbol": "OLD", "raw_symbol": "OLD", "name": "Old Corp", "exchange": "NYSE", "asset_type": "Stock", "ipo_date": "2000-01-01", "delisting_date": "2010-01-01", "status": "delisted", "source": "listing_status", "source_as_of": "2026-09-15", "sources": ["listing_status"], "provenance": {"lifecycle": "old"}, "is_test": test_flag}]).to_parquet(path, index=False)


def test_asset_pool_persists_catalogue_manifest_and_record_snapshot(tmp_path):
    master = tmp_path / "master.parquet"; _catalogue_master(master)
    catalogue = SecurityCatalogueStore(tmp_path); catalogue.initialize(); imported = catalogue.import_master(master)
    record = catalogue.list(query="old")["items"][0]
    pools = AssetPoolDraftStore(tmp_path, security_catalogue=catalogue); pools.initialize()
    created = pools.save({"name": "历史研究池", "purpose": "允许退市历史名单", "instruments": [{"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": imported["checksum"]}]})
    member = created["instruments"][0]
    assert member["source_checksum"] == imported["checksum"]
    assert member["record_snapshot"]["status"] == "delisted"
    assert member["record_snapshot"]["identity_status"] == "provisional"
    assert member["record_snapshot"]["research_qualified"] is False
    assert created["catalogue_member_count"] == 1 and created["research_qualified"] is False


def test_catalogue_members_reject_quarantine_and_unknown_manifest(tmp_path):
    master = tmp_path / "master.parquet"; _catalogue_master(master)
    catalogue = SecurityCatalogueStore(tmp_path); catalogue.initialize(); imported = catalogue.import_master(master)
    record = catalogue.list(query="old")["items"][0]
    pools = AssetPoolDraftStore(tmp_path, security_catalogue=catalogue); pools.initialize()
    base = {"name": "pool", "purpose": "test", "instruments": [{"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": "0" * 64}]}
    with pytest.raises(UniverseDraftInputError, match="manifest"):
        pools.save(base)
    _catalogue_master(master, test_flag="Y"); catalogue.import_master(master)
    base["instruments"][0]["source_checksum"] = imported["checksum"]
    with pytest.raises(UniverseDraftInputError, match="quarantined"):
        pools.save(base)


def test_identical_reimport_upgrades_old_catalogue_manifest_without_guessing(tmp_path):
    master = tmp_path / "master.parquet"; _catalogue_master(master)
    catalogue = SecurityCatalogueStore(tmp_path); catalogue.initialize(); imported = catalogue.import_master(master)
    with catalogue._connect() as db:
        db.execute("DELETE FROM catalogue_record_snapshots")
        db.execute("ALTER TABLE catalogue_records DROP COLUMN current_checksum")
    # A database created before P2-06B has neither the linkage column nor
    # snapshots.  initialize may add only the column; the byte-identical
    # reimport below is what supplies the missing immutable evidence.
    catalogue.initialize()
    catalogue.import_master(master)
    record = catalogue.list(query="old")["items"][0]
    assert record["source_checksum"] == imported["checksum"]
    pools = AssetPoolDraftStore(tmp_path, security_catalogue=catalogue); pools.initialize()
    assert pools.save({"name": "upgraded", "purpose": "test", "instruments": [{"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": imported["checksum"]}]})["instrument_count"] == 1


def test_catalogue_reference_rejects_forged_snapshots_and_duplicate_identity_across_versions(tmp_path):
    master = tmp_path / "master.parquet"; _catalogue_master(master)
    catalogue = SecurityCatalogueStore(tmp_path); catalogue.initialize(); first = catalogue.import_master(master)
    record = catalogue.list(query="old")["items"][0]
    frame = pd.read_parquet(master); frame.loc[0, "source"] = "revised"; frame.loc[0, "source_as_of"] = "2026-09-16"; frame.to_parquet(master, index=False)
    second = catalogue.import_master(master)
    pools = AssetPoolDraftStore(tmp_path, security_catalogue=catalogue); pools.initialize()
    forged = {"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": first["checksum"], "record_snapshot": {"research_qualified": True}}
    with pytest.raises(UniverseDraftInputError, match="security_catalogue_v1 manifest reference"):
        pools.save({"name": "forged", "purpose": "test", "instruments": [forged]})
    references = [
        {"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": first["checksum"]},
        {"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": second["checksum"]},
    ]
    with pytest.raises(UniverseDraftInputError, match="duplicate"):
        pools.save({"name": "duplicate", "purpose": "test", "instruments": references})


def test_catalogue_pool_keeps_old_snapshot_after_source_update(tmp_path):
    master = tmp_path / "master.parquet"; _catalogue_master(master)
    catalogue = SecurityCatalogueStore(tmp_path); catalogue.initialize(); first = catalogue.import_master(master)
    record = catalogue.list(query="old")["items"][0]
    pools = AssetPoolDraftStore(tmp_path, security_catalogue=catalogue); pools.initialize()
    pool = pools.save({"name": "immutable", "purpose": "test", "instruments": [{"member_type": "security_catalogue_v1", "catalog_id": record["catalog_id"], "source_checksum": first["checksum"]}]})
    frame = pd.read_parquet(master); frame.loc[0, "source"] = "revised"; frame.to_parquet(master, index=False)
    catalogue.import_master(master)
    saved = pools.history(pool["id"])[0]["instruments"][0]
    assert saved["record_snapshot"]["name"] == "Old Corp" and saved["record_snapshot"]["source"] == "listing_status"
    assert catalogue.list(query="old")["items"][0]["source"] == "revised"
