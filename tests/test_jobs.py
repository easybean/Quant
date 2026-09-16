from __future__ import annotations

import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient

from quant_data.api import create_app
from quant_data.jobs import JobStore, Submission


def _body(**extra: object) -> dict[str, object]:
    body: dict[str, object] = {"kind": "research", "operation": "record_metadata", "strategy": {"template": "buy-and-hold-v1"}, "parameters": {"symbol": "ABC"}, "data_snapshot": "curated-us-v1", "code_version": "git-abc123"}
    body.update(extra)
    return body


def test_idempotent_research_submission_publishes_reproducibility_record(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    app = create_app()
    client = TestClient(app)
    headers = {"Idempotency-Key": "research-001"}
    first = client.post("/api/v1/jobs", json=_body(), headers=headers)
    again = client.post("/api/v1/jobs", json=_body(), headers=headers)
    assert first.status_code == 202 and first.json()["created"] is True
    assert again.status_code == 202 and again.json()["created"] is False
    job_id = first.json()["job"]["id"]
    assert app.state.job_store.run_once() is True
    job = client.get(f"/api/v1/jobs/{job_id}").json()
    assert job["status"] == "succeeded"
    assert job["artifacts"] == [{"name": "experiment.json", "kind": "reproducibility_record", "job_id": job_id}]
    experiment = client.get("/api/v1/experiments").json()["items"]
    assert experiment[0]["data_snapshot"] == "curated-us-v1"
    record = json.loads((tmp_path / "artifacts" / job_id / "experiment.json").read_text())
    assert record["code_version"] == "git-abc123"
    assert "path" not in json.dumps(job)


def test_formal_backtest_and_unfixed_metadata_are_blocked(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    headers = {"Idempotency-Key": "blocked-001"}
    blocked = client.post("/api/v1/jobs", json=_body(kind="backtest"), headers=headers)
    latest = client.post("/api/v1/jobs", json=_body(data_snapshot="latest"), headers={"Idempotency-Key": "blocked-002"})
    assert blocked.status_code == 422 and "formal backtest" in blocked.json()["detail"]
    assert latest.status_code == 422 and "fixed" in latest.json()["detail"]


def test_only_synthetic_daily_limit_backtest_uses_durable_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    body = {
        "kind": "backtest", "operation": "synthetic_daily_limit", "data_snapshot": "synthetic-us-daily-v1", "code_version": "git-p3-03",
        "strategy": {"template": "buy_and_hold", "parameters": {"symbol": "ACME", "start_date": "2024-01-02"}, "data_requirements": {"dataset_version": "synthetic-us-daily-v1", "universe_version": "synthetic-us-equity-pool-v1", "calendar_version": "synthetic-nyse-calendar-v1", "price_basis": "raw"}},
        "parameters": {"asset_pool": {"version": "synthetic-us-equity-pool-v1", "kind": "synthetic", "symbols": ["ACME"], "corporate_actions": "not_applicable"}, "calendar_version": "synthetic-nyse-calendar-v1", "nautilus_version": "1.221.0", "initial_cash": "1000", "limit_price": "100", "requested_quantity": "10", "bars": [{"day": "2024-01-02", "available_time": "2024-01-02T21:00:00Z", "close": "100", "limit_fill_price": "100", "fill_quantity": "0"}, {"day": "2024-01-03", "available_time": "2024-01-03T21:00:00Z", "close": "101", "limit_fill_price": "100", "fill_quantity": "4"}, {"day": "2024-01-04", "available_time": "2024-01-04T21:00:00Z", "close": "102", "limit_fill_price": "100", "fill_quantity": "0"}]},
    }
    reply = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": "synthetic-p3-03"})
    assert reply.status_code == 202
    job_id = reply.json()["job"]["id"]
    assert client.app.state.job_store.run_once()
    report = json.loads((tmp_path / "artifacts" / job_id / "backtest-report.json").read_text())
    assert report["ledger"]["total_pnl"] == "7"
    assert client.get(f"/api/v1/jobs/{job_id}").json()["artifacts"][0]["kind"] == "synthetic_backtest_report"


def test_cancel_and_restart_recovery_do_not_republish_artifacts(tmp_path):
    store = JobStore(tmp_path)
    job, _ = store.submit(Submission.parse(_body(), "recover-001"))
    assert store.cancel(job["id"])["status"] == "cancelled"
    assert store.run_once() is False

    publish, _ = store.submit(Submission.parse(_body(code_version="git-def456"), "recover-002"))
    assert store.run_once() is True
    artifact = tmp_path / "artifacts" / publish["id"] / "experiment.json"
    original = artifact.read_text()
    with store._connect() as db:  # simulates process loss after atomic publication
        db.execute("UPDATE jobs SET status='running', finished_at=NULL WHERE id=?", (publish["id"],))
    JobStore(tmp_path).initialize()
    assert JobStore(tmp_path).get_job(publish["id"])["status"] == "succeeded"
    assert artifact.read_text() == original


def test_factor_evaluation_requires_qualified_fixed_snapshot_and_publishes_result(monkeypatch, tmp_path):
    import pandas as pd
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    root = tmp_path / "derived"
    for number in range(5):
        path = root / f"provider=p" / "namespace=n" / f"symbol=S{number}" / "bars.parquet"
        path.parent.mkdir(parents=True)
        dates = pd.date_range("2024-01-01", periods=12, freq="D")
        close = [100 + number + day * (number + 1) for day in range(12)]
        pd.DataFrame({"date": dates, "symbol": f"S{number}", "open": close, "high": [x + 1 for x in close], "low": [x - 1 for x in close], "close": close, "volume": 1000}).to_parquet(path, index=False)
    body = {"kind": "research", "operation": "factor_evaluation", "strategy": {"template": "single_factor_evaluation-v1"}, "parameters": {"factor_id": "return_5", "symbols": [f"S{x}" for x in range(5)], "holding_period": 2, "quantiles": 5, "min_cross_section": 5}, "data_snapshot": "curated-us-qualified-v1", "code_version": "git-p4-02"}
    client = TestClient(create_app())
    blocked = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": "factor-blocked"})
    assert blocked.status_code == 422 and "P3-03A" in blocked.json()["detail"]
    monkeypatch.setenv("QUANT_FACTOR_RESEARCH_SNAPSHOTS", json.dumps({"curated-us-qualified-v1": {"derived_root": str(root), "p3_03a_qualified": True, "factor_formula_version": "p4-01-v1"}}))
    reply = client.post("/api/v1/jobs", json=body, headers={"Idempotency-Key": "factor-qualified"})
    assert reply.status_code == 202
    job_id = reply.json()["job"]["id"]
    assert client.app.state.job_store.run_once()
    result = client.get(f"/api/v1/jobs/{job_id}/result")
    assert result.status_code == 200
    assert result.json()["factor_formula_version"] == "p4-01-v1"
    assert result.json()["result"]["metrics"].keys() >= {"平均日 IC", "IC IR", "平均单向换手"}
