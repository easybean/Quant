import json

import pytest
from fastapi.testclient import TestClient

from quant_data.api import create_app


def body(client, template="dual_moving_average"):
    fixture = client.get("/api/v1/backtests/availability").json()["signal_acceptance"]
    return {"kind": "backtest", "operation": "synthetic_signal_daily", "data_snapshot": fixture["dataset_version"], "code_version": "test-signal-v1",
            "parameters": fixture["parameters"],
            "strategy": {"template": template, "parameters": {"symbol": "ACME", "fast_window": 2, "slow_window": 3} if template == "dual_moving_average" else {"symbol": "ACME", "start_date": "2024-01-02"},
                         "data_requirements": {"dataset_version": fixture["dataset_version"], "universe_version": fixture["asset_pool_version"], "calendar_version": fixture["calendar_version"], "price_basis": "raw"}}}


def publish(client, value, key):
    reply = client.post("/api/v1/jobs", json=value, headers={"Idempotency-Key": key})
    assert reply.status_code == 202, reply.text
    job = reply.json()["job"]
    retry = client.post("/api/v1/jobs", json=value, headers={"Idempotency-Key": key})
    assert retry.json()["job"]["id"] == job["id"]
    assert client.app.state.job_store.run_once()
    assert client.get(f"/api/v1/jobs/{job['id']}").json()["status"] == "succeeded"
    response = client.get(f"/api/v1/backtests/reports/{job['id']}")
    assert response.status_code == 200, response.text
    return response.json()


def test_signal_jobs_reports_comparison_and_tamper(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    hold = publish(client, body(client, "buy_and_hold"), "hold")
    dma = publish(client, body(client), "dma")
    assert hold["metrics"]["total_pnl"] != dma["metrics"]["total_pnl"]
    assert hold["fills"] != dma["fills"]
    assert dma["equity_curve"] and dma["signals"] and dma["orders"]
    assert dma["artifact"]["report_hash_verified"]
    comparison = client.get("/api/v1/backtests/reports/compare", params={"left_job_id": hold["job_id"], "right_job_id": dma["job_id"]}).json()
    assert comparison["comparable"] is True
    assert comparison["differences"]["total_pnl"] != "0"
    path = tmp_path / "artifacts" / dma["job_id"] / "backtest-report.json"
    payload = json.loads(path.read_text())
    payload["equity_curve"][0]["equity"] = "999999"
    path.write_text(json.dumps(payload))
    assert client.get(f"/api/v1/backtests/reports/{dma['job_id']}").status_code == 404
    assert client.get("/api/v1/backtests/availability").json()["formal_backtest_available"] is False


@pytest.mark.parametrize("mutation", ["real", "bars", "path", "unknown_strategy", "bad_cash"])
def test_signal_job_request_fail_closed(monkeypatch, tmp_path, mutation):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = TestClient(create_app())
    value = body(client)
    if mutation == "real": value["data_snapshot"] = "massive-real-v1"
    if mutation == "bars": value["parameters"]["bars"] = []
    if mutation == "path": value["parameters"]["data_path"] = "/secret/key"
    if mutation == "unknown_strategy": value["strategy"]["template"] = "user_python"
    if mutation == "bad_cash": value["parameters"]["initial_cash"] = "0"
    response = client.post("/api/v1/jobs", json=value, headers={"Idempotency-Key": mutation})
    assert response.status_code == 422, response.text
    assert not client.app.state.job_store.list_jobs()
