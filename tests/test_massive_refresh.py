import json
from pathlib import Path

import pytest
from quant_data import massive_refresh


def test_refresh_publishes_only_successful_fixed_audit(tmp_path, monkeypatch):
    (tmp_path / "catalogue").mkdir()
    (tmp_path / "reference/listing").mkdir(parents=True)
    (tmp_path / "catalogue/current-listing-gap-evidence-v1.json").write_text(json.dumps({"snapshot_relative_path": "reference/listing"}))
    monkeypatch.setattr(massive_refresh, "capture_massive_reference", lambda *a, **k: {"status": "captured", "snapshot_relative_path": "reference/captured", "ticker_count": 4})
    audit = {"report_relative_path": "reference/audit/report.json", "report_sha256": "sha", "classification_counts": {"authoritative_alias_candidate": 1}}
    monkeypatch.setattr(massive_refresh, "audit_massive_reference", lambda *a: audit)
    assert massive_refresh.refresh(tmp_path, Path("master"), Path("key"))["ticker_count"] == 4
    assert json.loads((tmp_path / "catalogue/current-massive-alias-report-v1.json").read_text()) == audit


def test_refresh_failure_never_replaces_pointer(tmp_path, monkeypatch):
    monkeypatch.setattr(massive_refresh, "capture_massive_reference", lambda *a, **k: {"status": "failed"})
    with pytest.raises(ValueError, match="refresh_failed"):
        massive_refresh.refresh(tmp_path, Path("master"), Path("key"))
    assert not (tmp_path / "catalogue/current-massive-alias-report-v1.json").exists()
