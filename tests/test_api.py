import json

import pytest

pytest.importorskip("fastapi", reason="install the API extra with pip install -e '.[api]'")

from fastapi.testclient import TestClient

from quant_data.api import create_app
from quant_data.factor_catalogue import CATALOGUE_CATEGORIES
from quant_data.factors import list_factors


def _client() -> TestClient:
    return TestClient(create_app())


def test_health_is_small_and_read_only():
    response = _client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "schema_version": "v1"}
    assert _client().post("/api/v1/factors").status_code == 405


def test_backtest_availability_is_explicitly_fail_closed_for_real_data():
    payload = _client().get("/api/v1/backtests/availability").json()
    assert payload["formal_backtest_available"] is False
    assert "P3-03A" in payload["formal_backtest_reason"]
    assert payload["synthetic_acceptance"]["operation"] == "synthetic_daily_limit"
    assert "path" not in json.dumps(payload)


def test_strategy_drafts_are_validated_and_versioned(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = _client()
    assert [item["version"] for item in client.get("/api/v1/strategy-templates").json()["items"]] == ["buy-and-hold-v1", "dual-moving-average-v1"]
    assert client.post("/api/v1/strategy-drafts", json={"name": "bad", "template": "buy_and_hold", "parameters": {"symbol": "SPY"}}).status_code == 422
    created = client.post("/api/v1/strategy-drafts", json={"name": "SPY hold", "template": "buy_and_hold", "parameters": {"symbol": "SPY", "start_date": "2020-01-02"}})
    assert created.status_code == 201
    draft = created.json()
    assert client.put(f"/api/v1/strategy-drafts/{draft['id']}", json={"name": "SPY hold", "template": "buy_and_hold", "parameters": {"symbol": "SPY", "start_date": "2021-01-02", "target_weight": "0.8"}}).json()["version"] == 2
    assert [item["version"] for item in client.get(f"/api/v1/strategy-drafts/{draft['id']}/history").json()["items"]] == [2, 1]


def test_instrument_and_asset_pool_drafts_are_separate_and_versioned(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = _client()
    invalid = {"name": "bad", "instrument": {"product": "CRYPTO_DELIVERY"}}
    assert client.post("/api/v1/instrument-drafts", json=invalid).status_code == 422
    instrument = {"instrument_id": "NYSE:US-EXAMPLE-001", "identity_key": "US-EXAMPLE-001", "asset": "equity", "product": "US_EQUITY", "venue": "NYSE", "venue_symbol": "EXM", "timezone": "America/New_York", "calendar_id": "US-DRAFT", "linear": False, "inverse": False, "symbol_history": [{"venue_symbol": "EXM", "valid_from": "2024-01-01"}], "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "share", "tick_size": "0.01", "lot_size": "1"}]}
    created = client.post("/api/v1/instrument-drafts", json={"name": "美股", "instrument": instrument})
    assert created.status_code == 201
    draft = created.json()
    assert client.put(f"/api/v1/instrument-drafts/{draft['id']}", json={"name": "美股 v2", "instrument": instrument}).json()["version"] == 2
    assert [row["version"] for row in client.get(f"/api/v1/instrument-drafts/{draft['id']}/history").json()["items"]] == [2, 1]
    reference = {"instrument_draft_id": draft["id"], "instrument_version": 1}
    assert client.post("/api/v1/asset-pool-drafts", json={"name": "空池", "purpose": "研究", "instruments": []}).status_code == 422
    assert client.post("/api/v1/asset-pool-drafts", json={"name": "错误引用", "purpose": "研究", "instruments": [{"instrument_draft_id": draft["id"], "instrument_version": 9}]}).status_code == 422
    assert client.post("/api/v1/asset-pool-drafts", json={"name": "重复引用", "purpose": "研究", "instruments": [reference, {"instrument_draft_id": draft["id"], "instrument_version": 2}]}).status_code == 422
    pool = client.post("/api/v1/asset-pool-drafts", json={"name": "美股研究池", "purpose": "因子研究候选池", "instruments": [reference]})
    assert pool.status_code == 201
    assert pool.json()["instruments"] == [reference]
    assert "instrument" not in pool.json()


def test_visual_strategy_drafts_are_versioned_and_non_executable(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path)); client = _client()
    instrument = {"instrument_id": "NYSE:VS-001", "identity_key": "VS-001", "asset": "equity", "product": "US_EQUITY", "venue": "NYSE", "venue_symbol": "VS", "timezone": "America/New_York", "calendar_id": "US-DRAFT", "linear": False, "inverse": False, "symbol_history": [{"venue_symbol": "VS", "valid_from": "2024-01-01"}], "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "share", "tick_size": "0.01", "lot_size": "1"}]}
    saved = client.post("/api/v1/instrument-drafts", json={"name": "Visual sample", "instrument": instrument}).json()
    pool = client.post("/api/v1/asset-pool-drafts", json={"name": "Strategy pool", "purpose": "test", "instruments": [{"instrument_draft_id": saved["id"], "instrument_version": 1}]}).json()
    definition = {"name": "动量候选", "asset_pool": {"asset_pool_draft_id": pool["id"], "asset_pool_version": 1}, "signal_rules": [{"factor_id": "return_20", "operator": "gt", "threshold": 0, "direction": "long"}], "combination": "all", "rebalance_frequency": "monthly", "allocation": {"mode": "equal_weight"}, "constraints": {"max_position_weight": 0.3, "max_holdings": 10}}
    assert client.post("/api/v1/visual-strategy-drafts", json={**definition, "unexpected": True}).status_code == 422
    created = client.post("/api/v1/visual-strategy-drafts", json=definition)
    assert created.status_code == 201
    draft = created.json()
    assert "formula" not in str(draft)
    updated = client.put(f"/api/v1/visual-strategy-drafts/{draft['id']}", json={**definition, "rebalance_frequency": "weekly"})
    assert updated.json()["version"] == 2
    assert [row["version"] for row in client.get(f"/api/v1/visual-strategy-drafts/{draft['id']}/history").json()["items"]] == [2, 1]


def test_global_risk_policy_drafts_are_validated_and_versioned(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = _client()
    policy = {"name": "全局限额", "scope": "global", "max_instrument_exposure_pct": "10", "max_market_exposure_pct": "30", "max_gross_leverage": "1.5", "max_daily_loss_pct": "2", "max_drawdown_pct": "10", "max_orders_per_minute": 10, "trading_halted": False}
    assert client.post("/api/v1/risk-policy-drafts", json={**policy, "scope": "strategy"}).status_code == 422
    created = client.post("/api/v1/risk-policy-drafts", json=policy)
    assert created.status_code == 201
    draft = created.json()
    updated = client.put(f"/api/v1/risk-policy-drafts/{draft['id']}", json={**policy, "trading_halted": True})
    assert updated.json()["version"] == 2
    assert updated.json()["policy"]["trading_halted"] is True
    assert [row["version"] for row in client.get(f"/api/v1/risk-policy-drafts/{draft['id']}/history").json()["items"]] == [2, 1]


def test_data_source_drafts_are_declared_only_and_versioned(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = _client()
    source = {"name": "美股数据声明", "provider": "alpaca", "market": "US", "product": "US_EQUITY", "frequency": "1d", "coverage_declaration": "待确认。", "license_declaration": "待确认。", "credential_reference": "ALPACA_DATA_REF", "verification_status": "declared"}
    assert client.post("/api/v1/data-source-drafts", json={**source, "verification_status": "verified"}).status_code == 422
    created = client.post("/api/v1/data-source-drafts", json=source)
    assert created.status_code == 201
    draft = created.json()
    assert draft["source"]["verification_status"] == "declared"
    assert client.put(f"/api/v1/data-source-drafts/{draft['id']}", json={**source, "frequency": "1h"}).json()["version"] == 2
    history = client.get(f"/api/v1/data-source-drafts/{draft['id']}/history").json()["items"]
    assert [row["version"] for row in history] == [2, 1]
    assert "secret" not in str(history).lower()


def test_paper_accounts_are_created_but_ledger_is_read_only_and_empty(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_WORKBENCH_STATE_DIR", str(tmp_path))
    client = _client()
    invalid = {"name": "bad", "base_currency": "usd", "initial_cash": "0", "margin_mode": "cash"}
    assert client.post("/api/v1/paper-accounts", json=invalid).status_code == 422
    created = client.post("/api/v1/paper-accounts", json={"name": "Paper USD", "base_currency": "USD", "initial_cash": "1000", "margin_mode": "cash"})
    assert created.status_code == 201
    account = created.json()
    assert account["account_type"] == "paper"
    ledger = client.get(f"/api/v1/paper-accounts/{account['id']}/ledger")
    assert ledger.status_code == 200
    assert ledger.json()["items"] == []
    assert ledger.json()["reconciliation"]["reconciled_cash"] == "1000"
    assert client.post(f"/api/v1/paper-accounts/{account['id']}/ledger", json={}).status_code == 405


def test_factors_match_visible_registry_and_have_safe_documentation():
    response = _client().get("/api/v1/factors")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "v1"
    assert payload["count"] == len(list_factors())
    assert payload["generated_at"].endswith("Z")
    assert all(item["category"] in CATALOGUE_CATEGORIES for item in payload["items"])
    assert all(not item["id"].startswith("qlib360_") for item in payload["items"])
    assert payload["categories"] == [category for category in payload["categories"] if category["count"] > 0]
    ids = {item["id"]: item for item in payload["items"]}
    assert ids["qlib158_VWAP0"]["status"] == "不可运行"
    assert ids["qlib158_IMXD5"]["status"] == "可计算"
    assert ids["rsi_14"]["status"] == "可计算"
    sensitive = {"path", "file_path", "derived_root", "traceback", "exception", "secret", "token", "password"}
    for item in payload["items"]:
        assert sensitive.isdisjoint(item)
        assert {"formula", "purpose", "data_requirements", "limitations", "availability_note"}.issubset(item)
        assert item["runnable"] is (item["status"] == "可计算")


def test_cors_only_allows_configured_origins():
    client = _client()
    allowed = client.get("/api/v1/health", headers={"Origin": "http://127.0.0.1:5173"})
    unknown = client.get("/api/v1/health", headers={"Origin": "https://untrusted.example"})
    assert allowed.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
    assert "access-control-allow-origin" not in unknown.headers


def test_data_catalogue_is_unavailable_without_fixed_snapshots(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path))
    response = _client().get("/api/v1/data-catalogue")
    assert response.status_code == 503
    assert response.json() == {
        "status": "unavailable",
        "message": "数据目录快照尚不可用",
        "missing": ["数据盘点", "质量报告", "结构清洗摘要", "公司行动验证快照"],
    }


def test_data_catalogue_reads_only_pre_generated_json(monkeypatch, tmp_path):
    root = tmp_path
    files = {
        "catalogue/inventory-v1.json": {
            "generated_at": "2026-09-08T00:00:00Z", "market": "美国股票", "frequency": "日线",
            "raw_files": 10643, "derived_files": 10640, "unique_symbols": 10327, "lifecycle_records": 24516,
            "rows": 13560261, "date_range": {"start": "2016-08-29", "end": "2026-08-31"}, "benchmarks": ["SPY"],
            "sources": [{"name": "Alpaca", "files": 3444, "symbols": 3444, "date_range": {"start": "2017-09-19", "end": "2026-08-31"}, "adjustment_status": "raw"}],
            "versions": [{"name": "盘点", "version": "inventory-v1", "scope": "仅索引"}],
            "private_path": "/not-exposed",
        },
        "audit/quality-v1/summary.json": {"rule_version": "daily-quality-v1", "findings_rows": 3, "findings": {"supplier_download_gap": 2}, "limitations": ["不修改行情"]},
        "audit/structural-v1/run-metadata.json": {"run_at": "2026-09-01T00:00:00Z", "cleaning_status": "structural", "raw_files": 1, "derived_files_written": 1},
        "curated/reference/corporate-actions/alpaca/20260908-narrow-v1/verification-report.json": {"snapshot_id": "narrow-v1", "event_count": 6, "golden_checks": [{"passed": True}, {"passed": False}], "delisting_resolution": "unknown"},
    }
    for name, payload in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setenv("QUANT_DATA_ROOT", str(root))
    response = _client().get("/api/v1/data-catalogue")
    assert response.status_code == 200
    payload = response.json()
    assert payload["inventory"]["raw_files"] == 10643
    assert payload["quality"]["findings"]["supplier_download_gap"] == 2
    assert payload["corporate_actions"]["golden_checks_passed"] == 1
    assert "path" not in json.dumps(payload)
    assert "private_path" not in json.dumps(payload)
