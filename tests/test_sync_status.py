import json

from quant_data.sync_status import sync_status_payload


def test_missing_or_invalid_summary_is_not_complete(tmp_path):
    assert sync_status_payload(tmp_path)["available"] is False
    path = tmp_path / "manifests/yahoo-daily-v1/coverage.json"
    path.parent.mkdir(parents=True)
    path.write_text("[]")
    assert sync_status_payload(tmp_path)["available"] is False


def test_allowlist_counts_and_no_paths_credentials(tmp_path):
    folder = tmp_path / "manifests/yahoo-daily-v1"
    folder.mkdir(parents=True)
    coverage = {"schema_version": "yahoo-sync-coverage-v1", "active_symbols": 10, "endpoint_current": 7, "needs_update": 3, "historical_only_symbols": 4, "target_date": "2026-09-16", "generated_at": "2026-09-17T00:00:00Z", "credential_file": "secret"}
    (folder / "coverage.json").write_text(json.dumps(coverage))
    status = sync_status_payload(tmp_path)
    assert status["available"] and status["endpoint_current"] == 7 and not status["research_qualified"]
    assert "secret" not in json.dumps(status)
    assert not status["run_available"]
    coverage["endpoint_current"] = 11
    (folder / "coverage.json").write_text(json.dumps(coverage))
    assert not sync_status_payload(tmp_path)["available"]


def test_provider_projection_does_not_expose_runtime_configuration(tmp_path):
    folder = tmp_path / "manifests/yahoo-daily-v1"
    folder.mkdir(parents=True)
    (folder / "coverage.json").write_text(json.dumps({"schema_version": "yahoo-sync-coverage-v1", "active_symbols": 1, "endpoint_current": 0, "needs_update": 1, "historical_only_symbols": 0}))
    run = tmp_path / "manifests/us-daily-sync"
    run.mkdir()
    row = {"status": "failed", "attempted": 3, "success": 0, "failed": 3, "last_error_code": "http_403", "credential_file": "/secret", "finished_at": "2026-09-17T00:00:00Z"}
    (run / "latest.json").write_text(json.dumps({"status": "partial", "providers": {"alpaca": row, "unknown": row}}))
    result = sync_status_payload(tmp_path)
    assert result["run_available"]
    assert result["providers"][0]["last_error_code"] == "http_403"
    assert len(result["providers"]) == 1
    assert "/secret" not in json.dumps(result)
    row["failed"] = -1
    (run / "latest.json").write_text(json.dumps({"providers": {"alpaca": row}}))
    assert not sync_status_payload(tmp_path)["run_available"]


def test_listing_absence_remains_unknown_and_does_not_reduce_pending(tmp_path):
    folder = tmp_path / "manifests/yahoo-daily-v1"
    folder.mkdir(parents=True)
    (folder / "coverage.json").write_text(json.dumps({"schema_version": "yahoo-sync-coverage-v1", "active_symbols": 2, "endpoint_current": 0, "needs_update": 2, "historical_only_symbols": 0}))
    catalogue = tmp_path / "catalogue"
    catalogue.mkdir()
    (catalogue / "current-listing-gap-evidence-v1.json").write_text(json.dumps({"schema_version": "current-listing-gap-evidence-v1", "items": [{"reason": "endpoint_or_bridge_gap", "listing_evidence": "present_in_current_directory"}, {"reason": "endpoint_or_bridge_gap", "listing_evidence": "absent_from_current_directory_unknown"}]}))
    result = sync_status_payload(tmp_path)
    assert result["needs_update"] == 2
    assert result["listing_evidence"]["present_endpoint_gaps"] == 1
    assert result["listing_evidence"]["absent_endpoint_gaps_unknown"] == 1


def test_provider_phase_exception_preserves_other_evidence_without_inventing_counts(tmp_path):
    folder = tmp_path / "manifests/yahoo-daily-v1"; folder.mkdir(parents=True)
    (folder / "coverage.json").write_text(json.dumps({"schema_version": "yahoo-sync-coverage-v1", "active_symbols": 1, "endpoint_current": 0, "needs_update": 1, "historical_only_symbols": 0}))
    run = tmp_path / "manifests/us-daily-sync"; run.mkdir()
    (run / "latest.json").write_text(json.dumps({"status": "partial", "providers": {"yahoo": {"status": "failed", "error_type": "ValueError"}, "alpaca": {"status": "success", "attempted": 1, "success": 1, "failed": 0}}}))
    result = sync_status_payload(tmp_path)
    assert result["run_available"]
    assert result["providers"][0]["success"] == 1
    yahoo = result["providers"][1]
    assert yahoo["counts_available"] is False and yahoo["attempted"] is None
    assert yahoo["last_error_code"] == "provider_phase_failed"
