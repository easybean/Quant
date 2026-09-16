import hashlib
import json
import os
import subprocess
import sys

from quant_data.research_snapshot import inspect_manifest


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(tmp_path):
    files = {}
    for role in ("bars", "identity", "membership", "calendar"):
        path = tmp_path / f"{role}.json"
        path.write_text(role, encoding="utf-8")
        files[role] = {"path": path.name, "sha256": _digest(path), "role": role}
    return {
        "schema_version": "research-snapshot-v1", "snapshot_id": "us-daily-2026-09-16-v1",
        "market": "us_equity", "frequency": "1d", "price_basis": "raw", "calendar_version": "nyse-2026-v1",
        "source": {"provider": "example", "namespace": "licensed", "license_evidence": "record-1"},
        "range": {"start": "2024-01-01", "end": "2024-12-31"},
        "instruments": [{"instrument_id": "US:ABC:1", "symbol": "ABC", "membership_start": "2024-01-01", "membership_end": "2024-12-31", "identity_evidence": "figi-record"}],
        "availability": {"policy": "explicit_available_time", "evidence": "vendor field mapping"},
        "corporate_actions": {"status": "not_applicable", "evidence": "narrow sample", "rationale": "single unaffected fixture"},
        "delistings": {"status": "not_applicable", "evidence": "narrow sample", "rationale": "single listed fixture"},
        "files": list(files.values()),
    }


def _write_manifest(tmp_path, manifest):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_valid_narrow_manifest_is_ready_for_review_but_never_qualified(tmp_path):
    result = inspect_manifest(_write_manifest(tmp_path, _manifest(tmp_path)))
    assert result["ready_for_review"] is True
    assert result["qualified"] is False
    assert result["blockers"] == []
    assert len(result["warnings"]) == 4


def test_unknown_state_and_missing_evidence_are_blockers(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["snapshot_id"] = "latest"
    manifest["calendar_version"] = "current"
    manifest["source"]["license_evidence"] = ""
    manifest["availability"]["evidence"] = ""
    manifest["corporate_actions"] = {"status": "unknown", "evidence": ""}
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert not result["ready_for_review"]
    assert any("snapshot_id" in item for item in result["blockers"])
    assert any("calendar_version" in item for item in result["blockers"])
    assert any("license_evidence" in item for item in result["blockers"])
    assert any("corporate_actions.status" in item for item in result["blockers"])


def test_non_object_roots_and_whitespace_mutable_versions_fail_closed(tmp_path):
    path = tmp_path / "manifest.json"
    for root in (None, []):
        path.write_text(json.dumps(root), encoding="utf-8")
        result = inspect_manifest(path)
        assert result["ready_for_review"] is False
        assert result["blockers"] == ["manifest root must be an object"]
    manifest = _manifest(tmp_path)
    manifest["snapshot_id"] = " latest "
    manifest["calendar_version"] = " current "
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("snapshot_id" in item for item in result["blockers"])
    assert any("calendar_version" in item for item in result["blockers"])


def test_hash_and_required_role_fail_closed(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["files"][0]["sha256"] = "0" * 64
    manifest["files"] = [item for item in manifest["files"] if item["role"] != "calendar"]
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("role 'calendar'" in item for item in result["blockers"])
    assert any("sha256" in item for item in result["blockers"])


def test_path_traversal_and_symlink_escape_are_blocked_without_exposing_paths(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["files"][0]["path"] = "../outside"
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("stay within" in item for item in result["blockers"])
    outside = tmp_path.parent / "outside-data"
    outside.write_text("outside", encoding="utf-8")
    link = tmp_path / "escape"
    try:
        os.symlink(outside, link)
    except OSError as error:
        import pytest
        pytest.skip(f"symlinks unavailable: {error}")
    manifest = _manifest(tmp_path)
    manifest["files"][0]["path"] = link.name
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("escapes" in item for item in result["blockers"])
    assert str(outside) not in " ".join(result["blockers"])


def test_malicious_field_types_and_symlink_loop_become_blockers(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["files"][0]["role"] = []
    manifest["corporate_actions"]["status"] = {}
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("files[0].role" in item for item in result["blockers"])
    assert any("corporate_actions.status" in item for item in result["blockers"])
    cycle = tmp_path / "cycle"
    try:
        os.symlink(cycle.name, cycle)
    except OSError as error:
        import pytest
        pytest.skip(f"symlinks unavailable: {error}")
    manifest = _manifest(tmp_path)
    manifest["files"][0]["path"] = cycle.name
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("missing or escapes" in item for item in result["blockers"])


def test_dates_duplicate_identity_and_membership_overlap_are_checked(tmp_path):
    manifest = _manifest(tmp_path)
    item = dict(manifest["instruments"][0])
    item["membership_start"] = "2025-01-01"
    item["membership_end"] = "2024-01-01"
    manifest["instruments"].append(item)
    manifest["range"] = {"start": "not-date", "end": "2024-01-01"}
    result = inspect_manifest(_write_manifest(tmp_path, manifest))
    assert any("range.start" in item for item in result["blockers"])
    assert any("unique" in item for item in result["blockers"])
    assert any("membership_start" in item for item in result["blockers"])


def test_cli_outputs_json_and_nonzero_for_blockers(tmp_path):
    manifest = _manifest(tmp_path)
    manifest["market"] = "other"
    path = _write_manifest(tmp_path, manifest)
    completed = subprocess.run([sys.executable, "-m", "quant_data.research_snapshot", str(path)], capture_output=True, text=True, check=False)
    assert completed.returncode == 1
    assert json.loads(completed.stdout)["ready_for_review"] is False
