import json
from pathlib import Path

import pytest
from quant_data.backtest_readiness import CODES, SCHEMA, public_readiness, sha
from quant_data.bounded_backtest_data import SYMBOLS


def setup(root):
    path = root / "reference/backtest-readiness/fixed/report.json"
    path.parent.mkdir(parents=True)
    payload = {"schema_version": SCHEMA, "available": True, "qualified": False, "formal_backtest_available": False,
               "snapshot_id": "fixed", "assessed_at": "2026-09-18T12:00:00+00:00",
               "range": {"start": "2026-09-01", "end": "2026-09-17"}, "symbols": list(SYMBOLS),
               "checks": [{"code": c, "label": c, "status": "blocked", "detail": "needs independent evidence"} for c in CODES],
               "credential_file": "/secret/key", "evidence": {"private_path": "/secret/data"}}
    path.write_text(json.dumps(payload))
    pointer = root / "catalogue/backtest-data-readiness-v1.json"
    pointer.parent.mkdir()
    pointer.write_text(json.dumps({"schema_version": SCHEMA, "report_relative_path": str(path.relative_to(root)), "sha256": sha(path)}))
    return path, pointer, payload


def test_public_projection_redacts_paths_and_never_qualifies(tmp_path):
    setup(tmp_path)
    result = public_readiness(tmp_path)
    assert result["available"] and result["qualified"] is False and result["formal_backtest_available"] is False
    assert "/secret" not in json.dumps(result) and "evidence" not in result


@pytest.mark.parametrize("field,value", [("qualified", True), ("formal_backtest_available", True),
                                         ("symbols", ["AAPL"]), ("checks", [])])
def test_invalid_gate_scope_and_checks_fail_closed(tmp_path, field, value):
    path, pointer, payload = setup(tmp_path)
    payload[field] = value
    path.write_text(json.dumps(payload))
    data = json.loads(pointer.read_text()); data["sha256"] = sha(path); pointer.write_text(json.dumps(data))
    result = public_readiness(tmp_path)
    assert not result["available"] and not result["formal_backtest_available"]


def test_checksum_path_escape_and_missing_report_fail_closed(tmp_path):
    path, pointer, _ = setup(tmp_path)
    path.write_text("{}")
    assert not public_readiness(tmp_path)["available"]
    pointer.write_text(json.dumps({"schema_version": SCHEMA, "report_relative_path": "../outside", "sha256": "bad"}))
    assert not public_readiness(tmp_path)["available"]
    pointer.unlink()
    assert not public_readiness(tmp_path)["available"]


def test_api_is_readonly_and_does_not_accept_browser_file_paths(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from quant_data.api import create_app
    setup(tmp_path)
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    with TestClient(create_app()) as client:
        response = client.get("/api/v1/backtests/data-readiness?report_path=/secret/key")
        assert response.status_code == 200 and response.json()["qualified"] is False
        assert "/secret" not in response.text
        assert client.post("/api/v1/backtests/data-readiness", json={"qualified": True}).status_code == 405
        assert client.get("/api/v1/backtests/availability").json()["formal_backtest_available"] is False
