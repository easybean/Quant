import importlib.util
import json
import hashlib
from pathlib import Path

import pandas as pd
import pytest

spec = importlib.util.spec_from_file_location("bounded_inventory", Path(__file__).parents[1] / "scripts/capture_bounded_research_inventory.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_freezes_actual_rows_without_inventing_availability(tmp_path, monkeypatch):
    root = tmp_path / "data"
    monkeypatch.setattr(module, "SYMBOLS", ("AAPL",))
    path = root / f"bars/daily/provider=test/namespace=fixed-v1/symbol={module.symbol_key('AAPL')}/bars.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-08-31", "2026-09-01"], "close": [10, 11]}).to_parquet(path)
    original = path.read_bytes()
    out = tmp_path / "snapshot"
    reference = root / "reference/assets.json"
    reference.parent.mkdir()
    raw = json.dumps([{"symbol": "AAPL", "status": "active", "id": "provider-only-id"}]).encode()
    reference.write_bytes(raw)
    manifest = reference.with_name("manifest.json")
    manifest.write_text(json.dumps({"schema_version": "provider-asset-reference-v1", "sha256": hashlib.sha256(raw).hexdigest()}))
    result = module.capture(root, out, reference)
    assert result["members"][0]["provider_asset_id"] == "provider-only-id"
    assert not result["members"][0]["historical_identity_verified"]
    assert not result["qualified"]
    assert result["sources"][0]["rows_in_window"] == 1
    assert "available_at" not in result["sources"][0]["rows"][0]
    assert path.read_bytes() == original
    assert json.loads((out / "manifest.json").read_text())["qualified"] is False
    with pytest.raises(ValueError, match="new immutable"):
        module.capture(root, out)
    manifest.write_text('{}')
    with pytest.raises(ValueError, match="integrity mismatch"):
        module.capture(root, tmp_path / "invalid", reference)


def test_rejects_symlink_source(tmp_path):
    root = tmp_path / "data"
    path = root / f"bars/daily/provider=test/namespace=v1/symbol={module.symbol_key('AAPL')}/bars.parquet"
    path.parent.mkdir(parents=True)
    external = tmp_path / "external.parquet"
    pd.DataFrame({"date": ["2026-09-01"]}).to_parquet(external)
    path.symlink_to(external)
    with pytest.raises(ValueError, match="escapes"):
        module.capture(root, tmp_path / "snapshot")


def test_empty_capture_fails_closed(tmp_path):
    with pytest.raises(ValueError, match="missing coverage"):
        module.capture(tmp_path / "data", tmp_path / "snapshot")
    assert not (tmp_path / "snapshot").exists()
