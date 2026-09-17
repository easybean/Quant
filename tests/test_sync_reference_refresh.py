import hashlib
import json

import pytest

from quant_data import sync_reference_refresh as module


def test_reference_refresh_publishes_only_after_mapping_success(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "capture_and_reconcile", lambda root: {"snapshot_relative_path": "listing", "observed_at": "now", "acquisition_master_added": 103})
    monkeypatch.setattr(module, "capture_asset_reference", lambda root, credentials: {"snapshot_relative_path": "assets", "records": 10})
    mapping = tmp_path / "reference/mapping.json"
    mapping.parent.mkdir()
    mapping.write_text("{}")
    monkeypatch.setattr(module, "build_symbol_mapping", lambda listings, assets, root: {"path": str(mapping), "mapping_sha256": hashlib.sha256(mapping.read_bytes()).hexdigest(), "accepted": 2, "rejected": 3})
    assert module.refresh(tmp_path, tmp_path / "secret")["mapping_accepted"] == 2
    pointer = tmp_path / "catalogue/current-symbol-mapping-v1.json"
    before = pointer.read_bytes()
    assert json.loads(before)["relative_path"] == "reference/mapping.json"
    def fail(*args):
        raise ValueError("failed")
    monkeypatch.setattr(module, "build_symbol_mapping", fail)
    with pytest.raises(ValueError):
        module.refresh(tmp_path, tmp_path / "secret")
    assert pointer.read_bytes() == before
