import hashlib
import json
from datetime import datetime, timezone

import pandas as pd
import pytest

from quant_data.symbol_mapping import (
    MAPPING_NAMESPACE,
    _name_tokens,
    build_symbol_mapping,
    load_symbol_mapping,
    mapping_tail_tasks,
)


def _snapshots(tmp_path, *, asset_name="Arbor Realty Trust, Inc. 6.375% Series D Cumulative Redeemable Preferred Stock, Liquidation Preference $25.00 per Share", exchange="NYSE"):
    listing = tmp_path / "listing"; listing.mkdir()
    (listing / "nasdaqlisted.txt").write_text("Symbol|Security Name|Test Issue|ETF\nAAA|AAA - Common Stock|N|N\nFile Creation Time: x|||\n")
    (listing / "otherlisted.txt").write_text(
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "ABR$D|Arbor Realty Trust 6.375% Series D Cumulative Redeemable Preferred Stock, Liquidation Preference $25.00 per Share|N|ABRpD|N|100|N|ABR-D\n"
        "File Creation Time: x|||||||\n"
    )
    listing_hashes = {name: hashlib.sha256((listing / name).read_bytes()).hexdigest() for name in ("nasdaqlisted.txt", "otherlisted.txt")}
    (listing / "gap-evidence.json").write_text(json.dumps({
        "schema_version": "current-listing-gap-evidence-v1", "source_hashes": listing_hashes,
        "source_footers": {"nasdaqlisted.txt": "File Creation Time: x", "otherlisted.txt": "File Creation Time: x"},
    }))
    provider = tmp_path / "provider"; provider.mkdir()
    assets = [{"id": "ffaf81e6-4488-4e58-9e22-8088a4e4e4b1", "class": "us_equity", "status": "active", "symbol": "ABR.PRD", "exchange": exchange, "name": asset_name}]
    raw = json.dumps(assets).encode(); (provider / "assets.json").write_bytes(raw)
    (provider / "manifest.json").write_text(json.dumps({"schema_version": "provider-asset-reference-v1", "source": "https://paper-api.alpaca.markets/v2/assets", "sha256": hashlib.sha256(raw).hexdigest(), "observed_at": datetime.now(timezone.utc).isoformat()}))
    return listing, provider


def _refresh_listing_evidence(listing):
    hashes = {name: hashlib.sha256((listing / name).read_bytes()).hexdigest() for name in ("nasdaqlisted.txt", "otherlisted.txt")}
    (listing / "gap-evidence.json").write_text(json.dumps({
        "schema_version": "current-listing-gap-evidence-v1", "source_hashes": hashes,
        "source_footers": {"nasdaqlisted.txt": "File Creation Time: x", "otherlisted.txt": "File Creation Time: x"},
    }))


def _write_assets(provider, assets):
    raw = json.dumps(assets).encode(); (provider / "assets.json").write_bytes(raw)
    (provider / "manifest.json").write_text(json.dumps({"schema_version": "provider-asset-reference-v1", "source": "https://paper-api.alpaca.markets/v2/assets", "sha256": hashlib.sha256(raw).hexdigest(), "observed_at": datetime.now(timezone.utc).isoformat()}))


def _add_unit_listing(listing, name="Example Acquisition Holdings Unit"):
    path = listing / "otherlisted.txt"
    footer = "File Creation Time: x|||||||\n"
    text = path.read_text().replace(footer, f"UNIT.U|{name}|N|UNIT.U|N|100|N|UNIT-U\n" + footer)
    path.write_text(text)
    _refresh_listing_evidence(listing)


def test_current_directory_mapping_accepts_only_complete_current_identity(tmp_path):
    listing, provider = _snapshots(tmp_path)
    result = build_symbol_mapping(listing, provider, tmp_path / "data")
    mapping = load_symbol_mapping(result["path"])
    assert result["accepted"] == 1 and result["rejected"] == 0
    item = mapping["mappings"][0]
    assert item["original_symbol"] == "ABR$D" and item["provider_symbol"] == "ABR.PRD"
    assert item["provider_asset_id"] == "ffaf81e6-4488-4e58-9e22-8088a4e4e4b1"
    assert item["official_listing"]["symbology"] == {"ACT Symbol": "ABR$D", "CQS Symbol": "ABRpD", "NASDAQ Symbol": "ABR-D"}
    assert item["research_qualified"] is False and item["historical_identity"] == "unknown"
    assert set(mapping["inputs"]["listing_sha256"]) == {"nasdaqlisted.txt", "otherlisted.txt"}


@pytest.mark.parametrize("kwargs", [
    {"asset_name": "Arbor Realty Trust, Inc. Series D Preferred Stock"},
    {"exchange": "NASDAQ"},
])
def test_mapping_rejects_description_or_exchange_mismatch(tmp_path, kwargs):
    listing, provider = _snapshots(tmp_path, **kwargs)
    result = build_symbol_mapping(listing, provider, tmp_path / "data")
    mapping = load_symbol_mapping(result["path"])
    assert result["accepted"] == 0
    assert mapping["rejected"][0]["reason"] in {"description_mismatch", "exchange_mismatch"}


def test_description_exchange_fallback_maps_non_preferred_symbol_only_on_exact_current_metadata(tmp_path):
    listing, provider = _snapshots(tmp_path)
    _add_unit_listing(listing)
    assets = json.loads((provider / "assets.json").read_text()) + [{
        "id": "unit-uuid", "class": "us_equity", "status": "active", "symbol": "UNIT.UN",
        "exchange": "NYSE", "name": "Example Acquisition Holdings, Inc. Unit",
    }]
    _write_assets(provider, assets)
    mapping = load_symbol_mapping(build_symbol_mapping(listing, provider, tmp_path / "data")["path"])
    item = {row["original_symbol"]: row for row in mapping["mappings"]}["UNIT.U"]
    assert item["provider_symbol"] == "UNIT.UN"
    assert item["candidate_basis"] == "exact_current_description_exchange"


def test_description_exchange_fallback_rejects_uuid_collision_and_preserves_security_tokens(tmp_path):
    listing, provider = _snapshots(tmp_path)
    _add_unit_listing(listing, name="Example Acquisition Holdings Series 2 Unit")
    assets = json.loads((provider / "assets.json").read_text()) + [
        {"id": "unit-one", "class": "us_equity", "status": "active", "symbol": "UNIT.UN", "exchange": "NYSE", "name": "Example Acquisition Holdings, Inc. Series 2 Unit"},
        {"id": "unit-two", "class": "us_equity", "status": "active", "symbol": "UNIT.UT", "exchange": "NYSE", "name": "Example Acquisition Holdings, Inc. Series 2 Unit"},
    ]
    _write_assets(provider, assets)
    mapping = load_symbol_mapping(build_symbol_mapping(listing, provider, tmp_path / "data")["path"])
    rejected = {row["original_symbol"]: row for row in mapping["rejected"]}["UNIT.U"]
    assert rejected["reason"] == "provider_candidate_not_unique"
    assert rejected["candidate_basis"] == "exact_current_description_exchange"
    assert _name_tokens("Limited Duration Income Fund") != _name_tokens("Duration Income Fund")
    assert _name_tokens("Example, Incorporated Series 2 Unit") != _name_tokens("Example Series 3 Unit")


def test_mapping_tail_is_current_only_and_preserves_earlier_history(tmp_path):
    listing, provider = _snapshots(tmp_path)
    mapping = load_symbol_mapping(build_symbol_mapping(listing, provider, tmp_path / "data")["path"])
    task = {"task_id": "old", "symbol": "ABR$D", "requested_start": "2016-01-01", "requested_end": "2026-09-16"}
    mapped = mapping_tail_tasks([task], mapping, current_target="2026-09-16")
    assert len(mapped) == 1
    assert mapped[0]["requested_start"] == "2026-08-17"
    assert mapped[0]["provider_symbol"] == "ABR.PRD"
    assert mapped[0]["remaining_history_unverified"] == {"requested_start": "2016-01-01", "requested_end": "2026-08-16", "status": "unverified"}
    assert mapping_tail_tasks([{**task, "requested_end": "2026-08-16"}], mapping, current_target="2026-09-16") == []


def test_mapping_pointer_and_acquisition_master_resolvers_are_hash_checked(tmp_path):
    from quant_data.sync_scheduler import resolve_current_symbol_mapping, resolve_sync_master
    root = tmp_path / "data"; (root / "catalogue").mkdir(parents=True)
    base = tmp_path / "base.parquet"; pd.DataFrame({"symbol": ["BASE"]}).to_parquet(base)
    master = root / "reference/master.parquet"; master.parent.mkdir(); pd.DataFrame({"symbol": ["NEW"]}).to_parquet(master)
    raw = master.read_bytes()
    (root / "catalogue/current-acquisition-master-v1.json").write_text(json.dumps({"schema_version": "current-acquisition-master-v1", "relative_path": "reference/master.parquet", "sha256": hashlib.sha256(raw).hexdigest()}))
    assert resolve_sync_master(root, base) == master.resolve()
    mapping = root / "reference/mapping.json"; mapping.write_text("{}")
    (root / "catalogue/current-symbol-mapping-v1.json").write_text(json.dumps({"schema_version": "current-symbol-mapping-v1", "relative_path": "reference/mapping.json", "sha256": hashlib.sha256(mapping.read_bytes()).hexdigest()}))
    assert resolve_current_symbol_mapping(root) == mapping.resolve()
    (root / "catalogue/current-acquisition-master-v1.json").write_text(json.dumps({"schema_version": "current-acquisition-master-v1", "relative_path": "reference/master.parquet", "sha256": "0" * 64}))
    with pytest.raises(ValueError, match="pointer"):
        resolve_sync_master(root, base)
    (root / "catalogue/current-acquisition-master-v1.json").write_text(json.dumps({"schema_version": "current-acquisition-master-v1", "relative_path": str(master.resolve()), "sha256": hashlib.sha256(raw).hexdigest()}))
    with pytest.raises(ValueError, match="pointer"):
        resolve_sync_master(root, base)


def test_mapped_recovery_requests_provider_symbol_but_archives_original_identity(tmp_path):
    from quant_data.recovery_sync import run_recovery
    root = tmp_path / "data"; master = tmp_path / "master.parquet"
    pd.DataFrame({"symbol": ["ABR$D"], "status": ["active"], "asset_type": ["Stock"]}).to_parquet(master, index=False)
    catalogue = root / "catalogue/us-daily-browser-v1.json"; catalogue.parent.mkdir(parents=True)
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": []}))
    queue = tmp_path / "mapped.json"
    queue.write_text(json.dumps({"schema_version": "us-daily-gap-queue-v1", "tasks": [{
        "task_id": "mapped", "original_task_id": "old", "symbol": "ABR$D", "provider_symbol": "ABR.PRD", "requested_start": "2024-01-02", "requested_end": "2024-01-09",
        "symbol_mapping": {"mapping_id": "id", "mapping_version": "version", "provider_asset_id": "uuid", "historical_identity": "unknown", "research_qualified": False},
        "remaining_history_unverified": {"requested_start": "2016-01-01", "requested_end": "2024-01-01", "status": "unverified"},
    }]}))
    calls = []
    bars = pd.DataFrame({"date": ["2024-01-02"], "open": [1.], "high": [2.], "low": [.5], "close": [1.5], "volume": [3]})
    result = run_recovery(master, root, gap_queue=queue, recovery_provider="alpaca", now=datetime(2024, 1, 10, 23, tzinfo=timezone.utc), downloader=lambda symbol, *_: calls.append(symbol) or bars, request_delay=0)
    assert result["success"] == 1 and result["namespace"] == MAPPING_NAMESPACE and calls == ["ABR.PRD"]
    record = json.loads((root / "manifests" / MAPPING_NAMESPACE / "records.jsonl").read_text())
    assert record["symbol"] == "ABR$D" and record["provider_symbol"] == "ABR.PRD"
    assert record["symbol_mapping_version"] == "version" and record["research_qualified"] is False
    assert (root / "bars/daily/provider=alpaca" / f"namespace={MAPPING_NAMESPACE}").exists()
    remaining = json.loads((root / "manifests" / MAPPING_NAMESPACE / "remaining-history.json").read_text())
    assert remaining["items"][0]["requested_start"] == "2016-01-01"
