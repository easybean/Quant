from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from quant_data.api import create_app


def _body(*, initial_cash: str = "1000", code_version: str = "git-p3-05", last_close: str = "102") -> dict[str, object]:
    return {
        "kind": "backtest", "operation": "synthetic_daily_limit", "data_snapshot": "synthetic-us-daily-v1", "code_version": code_version,
        "strategy": {"template": "buy_and_hold", "parameters": {"symbol": "ACME", "start_date": "2024-01-02"}, "data_requirements": {"dataset_version": "synthetic-us-daily-v1", "universe_version": "synthetic-us-equity-pool-v1", "calendar_version": "synthetic-nyse-calendar-v1", "price_basis": "raw"}},
        "parameters": {"asset_pool": {"version": "synthetic-us-equity-pool-v1", "kind": "synthetic", "symbols": ["ACME"], "corporate_actions": "not_applicable"}, "calendar_version": "synthetic-nyse-calendar-v1", "nautilus_version": "1.221.0", "initial_cash": initial_cash, "limit_price": "100", "requested_quantity": "10", "bars": [{"day": "2024-01-02", "available_time": "2024-01-02T21:00:00Z", "close": "100", "limit_fill_price": "100", "fill_quantity": "0"}, {"day": "2024-01-03", "available_time": "2024-01-03T21:00:00Z", "close": "101", "limit_fill_price": "100", "fill_quantity": "4"}, {"day": "2024-01-04", "available_time": "2024-01-04T21:00:00Z", "close": last_close, "limit_fill_price": "100", "fill_quantity": "0"}]},
    }


def _publish(client: TestClient, body: dict[str, object], key: str) -> str:
    reply = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": key})
    assert reply.status_code == 202
    job_id = reply.json()["job"]["id"]
    assert client.app.state.job_store.run_once()
    return job_id


def _rehash_legacy_artifact(path, mutate) -> None:
    """Build an old immutable-format fixture only; production never rewrites it."""
    payload = json.loads(path.read_text())
    mutate(payload)
    hash_input = dict(payload)
    for key in ("job_id", "code_version", "created_at", "report_hash"):
        hash_input.pop(key, None)
    import hashlib
    payload["report_hash"] = hashlib.sha256(json.dumps(hash_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_synthetic_report_is_read_only_reconciled_and_comparable(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    first = _publish(client, _body(code_version="git-one"), "report-one")
    second = _publish(client, _body(code_version="git-two"), "report-two")

    listing = client.get("/api/v1/backtests/reports")
    assert listing.status_code == 200
    assert {item["job_id"] for item in listing.json()["items"]} == {first, second}
    report = client.get(f"/api/v1/backtests/reports/{first}")
    assert report.status_code == 200
    payload = report.json()
    assert payload["label"].startswith("合成验收")
    assert payload["artifact"]["report_hash_verified"] is True
    assert payload["artifact"]["sha256_status"] == "computed_read_time_only"
    assert len(payload["contract"]["bars_sha256"]) == 64
    assert len(payload["versions"]["input_fingerprint"]) == 64
    assert payload["versions"]["engine"] == {
        "name": "参考账务验收程序", "version": "reference-accounting-v1",
        "execution_implementation": "reference-accounting-v1",
        "reference_runner": "NautilusTrader", "reference_runner_version": "1.221.0",
        "runner_invoked": False, "verified_scope": "P2-05 synthetic daily buy-limit partial-fill/cancel",
    }
    assert payload["contract"]["engine_contract"]["execution_implementation"] == "reference-accounting-v1"
    assert payload["metrics"] == {"initial_cash": "1000", "free_cash": "599", "terminal_equity": "1007", "total_pnl": "7", "total_return_pct": "0.7", "fees": "1", "unrealized_pnl": "8"}
    assert payload["holdings"] == [{"symbol": "ACME", "quantity": "4", "mark_price": "102", "market_value": "408", "price_basis": "raw"}]
    assert "最大回撤" in "".join(payload["warnings"])
    assert "path" not in json.dumps(payload)

    comparison = client.get("/api/v1/backtests/reports/compare", params={"left_job_id": first, "right_job_id": second})
    assert comparison.status_code == 200
    assert comparison.json()["comparable"] is True
    assert comparison.json()["differences"] == {"terminal_equity": "0", "total_pnl": "0", "total_return_pct": "0", "fees": "0"}
    assert comparison.json()["version_differences"]["code_version"] == {"left": "git-one", "right": "git-two"}


def test_report_comparison_refuses_different_initial_cash_and_tampered_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    first = _publish(client, _body(initial_cash="1000"), "cash-one")
    second = _publish(client, _body(initial_cash="2000"), "cash-two")
    comparison = client.get("/api/v1/backtests/reports/compare", params={"left_job_id": first, "right_job_id": second})
    assert comparison.status_code == 200
    assert comparison.json()["comparable"] is False
    assert comparison.json()["non_comparable_reasons"] == ["初始资金"]

    artifact = tmp_path / "artifacts" / first / "backtest-report.json"
    modified = json.loads(artifact.read_text())
    modified["ledger"]["total_pnl"] = "999"
    artifact.write_text(json.dumps(modified), encoding="utf-8")
    assert client.get(f"/api/v1/backtests/reports/{first}").status_code == 404
    items = client.get("/api/v1/backtests/reports").json()["items"]
    failed = next(item for item in items if item["job_id"] == first)
    assert failed == {"job_id": first, "available": False, "reason": "artifact report hash verification failed"}
    assert client.get("/api/v1/backtests/reports/not-a-path").status_code == 404


def test_report_comparison_refuses_same_dates_with_different_synthetic_bars(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    first = _publish(client, _body(last_close="102"), "bars-one")
    changed_close = _publish(client, _body(last_close="103"), "bars-two")
    comparison = client.get("/api/v1/backtests/reports/compare", params={"left_job_id": first, "right_job_id": changed_close})
    assert comparison.status_code == 200
    assert comparison.json()["comparable"] is False
    assert comparison.json()["non_comparable_reasons"] == ["合成日线输入"]


def test_report_reader_rejects_symlink_and_oversized_artifacts(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    job_id = _publish(client, _body(), "report-path-guard")
    artifact = tmp_path / "artifacts" / job_id / "backtest-report.json"
    outside = tmp_path / "outside-report.json"
    outside.write_text("{}", encoding="utf-8")
    artifact.unlink()
    artifact.symlink_to(outside)
    response = client.get(f"/api/v1/backtests/reports/{job_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "report artifact symlinks are not allowed"

    artifact.unlink()
    artifact.write_bytes(b"0" * (512 * 1024 + 1))
    response = client.get(f"/api/v1/backtests/reports/{job_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "report artifact is missing or exceeds the read-only size limit"


def test_old_reports_are_truthfully_projected_and_cannot_compare_to_new_reports(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    old_job = _publish(client, _body(), "legacy-report")
    new_job = _publish(client, _body(), "explicit-report")
    artifact = tmp_path / "artifacts" / old_job / "backtest-report.json"

    def make_legacy(payload):
        for key in ("execution_implementation", "reference_runner", "reference_runner_version", "runner_invoked"):
            payload["engine"].pop(key)
    _rehash_legacy_artifact(artifact, make_legacy)

    old_engine = client.get(f"/api/v1/backtests/reports/{old_job}").json()["versions"]["engine"]
    assert old_engine["name"] == "参考账务验收程序（旧报告）"
    assert old_engine["version"] == "unknown"
    assert old_engine["reference_runner"] == "NautilusTrader"
    assert old_engine["reference_runner_version"] == "1.221.0"
    assert old_engine["runner_invoked"] is False
    comparison = client.get("/api/v1/backtests/reports/compare", params={"left_job_id": old_job, "right_job_id": new_job}).json()
    assert comparison["comparable"] is False
    assert comparison["non_comparable_reasons"] == ["回测引擎合同"]


def test_report_reader_rejects_malformed_engine_provenance(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    job_id = _publish(client, _body(), "bad-engine-provenance")
    artifact = tmp_path / "artifacts" / job_id / "backtest-report.json"
    _rehash_legacy_artifact(artifact, lambda payload: payload["engine"].update({"execution_implementation": "actual-nautilus-v1"}))
    response = client.get(f"/api/v1/backtests/reports/{job_id}")
    assert response.status_code == 404
    assert response.json()["detail"] == "artifact engine provenance is not accepted"
