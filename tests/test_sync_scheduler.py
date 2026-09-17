from datetime import datetime, timezone
import json

import pandas as pd

from quant_data.sync_scheduler import build_gap_plan, completed_session, due_tasks
from quant_data.sync_eligibility import select_sync_symbols
from quant_data.pipeline import symbol_key

NOW = datetime(2026, 9, 17, 3, tzinfo=timezone.utc)


def test_shared_eligibility_blocks_tests_from_any_source_without_name_guessing():
    master = pd.DataFrame({"symbol": ["ATEST", "ATEST-A", "TESTCO", "AAA", "AAA", "OLD"],
        "status": ["active"] * 5 + ["delisted"], "asset_type": ["Stock"] * 6,
        "Test Issue": ["", "", "N", "N", "Y", "N"]})
    assert select_sync_symbols(master) == ["TESTCO"]


def test_completed_session_holiday_weekend_and_dst():
    assert completed_session(datetime(2026, 9, 8, 12, tzinfo=timezone.utc)).isoformat() == "2026-09-04"
    assert completed_session(datetime(2026, 9, 6, 12, tzinfo=timezone.utc)).isoformat() == "2026-09-04"
    assert completed_session(NOW).isoformat() == "2026-09-16"
    assert completed_session(datetime(2026, 3, 9, 12, tzinfo=timezone.utc)).isoformat() == "2026-03-06"


def setup(root, symbols=("SUPX", "COLD", "ATEST", "OLD")):
    master = root / "master.parquet"
    pd.DataFrame({"symbol": symbols, "status": ["delisted" if s == "OLD" else "active" for s in symbols],
                  "asset_type": ["Stock"] * len(symbols)}).to_parquet(master)
    folder = root / "manifests/yahoo-daily-v1"; folder.mkdir(parents=True)
    (folder / "baselines.json").write_text(json.dumps({"symbols": {"SUPX": {"last_date": "2026-08-31"}}}))
    path = root / "bars/daily" / f"symbol={symbol_key('SUPX')}/bars.parquet"; path.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["2026-08-31"]}).to_parquet(path)
    catalogue = root / "catalogue/us-daily-browser-v1.json"; catalogue.parent.mkdir()
    catalogue.write_text(json.dumps({"schema_version": "us-daily-browser-v1", "series": [
        {"symbol": "SUPX", "series_id": f"unknown:unknown:{symbol_key('SUPX')}", "provider": "unknown", "namespace": "unknown",
         "raw_relative_path": f"symbol={symbol_key('SUPX')}/bars.parquet", "first_date": "2026-08-31", "last_date": "2026-08-31", "rows": 1}]}))
    return master


def test_never_attempted_and_cold_symbols_have_durable_tasks(tmp_path):
    master = setup(tmp_path)
    plan = build_gap_plan(tmp_path, master, now=NOW)
    assert plan["eligible_symbols"] == 2
    tasks = {x["symbol"]: x for x in plan["tasks"]}
    assert set(tasks) == {"SUPX", "COLD"}
    assert tasks["SUPX"]["requested_start"] == "2026-09-01"
    assert tasks["COLD"]["requested_start"] == "2016-01-01"
    assert tasks["SUPX"]["requested_end"] == "2026-09-16"
    assert build_gap_plan(tmp_path, master, now=NOW)["tasks"] == plan["tasks"]
    assert (tmp_path / "manifests/us-daily-sync/queue.json").exists()


def test_due_journal_cooldown_fairness_and_three_attempt_cap(tmp_path):
    tasks = [{"task_id": s, "symbol": s, "latest_date": "2026-08-31"} for s in ("AAA", "BBB", "CCC")]
    plan = {"tasks": tasks, "target_date": "2026-09-16"}
    records = tmp_path / "records.jsonl"
    rows = [{"source_attempted_at": "AAA", "attempted_at": NOW.isoformat(), "status": "failed"}]
    rows += [{"source_attempted_at": "BBB", "attempted_at": "2026-09-16T00:00:00+00:00", "status": "failed"}] * 3
    records.write_text("\n".join(json.dumps(r) for r in rows))
    assert [t["symbol"] for t in due_tasks(plan, records, NOW)] == ["CCC"]
    later = datetime(2026, 9, 17, 5, tzinfo=timezone.utc)
    assert [t["symbol"] for t in due_tasks(plan, records, later)] == ["CCC", "AAA"]


def test_historical_audit_candidate_does_not_delay_latest_day_update(tmp_path):
    master = setup(tmp_path, ("SUPX",))
    folder = tmp_path / "manifests/us-daily-sync"; folder.mkdir()
    (folder / "session-audit.json").write_text(json.dumps({"cursor": 0, "symbols": {
        "SUPX": {"first_missing_session": "2020-01-02", "missing_sessions": 1}}}))
    task = build_gap_plan(tmp_path, master, now=NOW, audit_limit=0)["tasks"][0]
    assert task["requested_start"] == "2026-09-01"
    assert task["reason"] == "endpoint_or_bridge_gap"


def test_internal_missing_session_detected_even_when_endpoint_current(tmp_path):
    master = setup(tmp_path, ("SUPX",))
    path = tmp_path / "bars/daily" / f"symbol={symbol_key('SUPX')}/bars.parquet"
    pd.DataFrame({"date": ["2026-09-14", "2026-09-16"]}).to_parquet(path)
    catalogue = tmp_path / "catalogue/us-daily-browser-v1.json"
    data = json.loads(catalogue.read_text()); data["series"][0].update(first_date="2026-09-14", last_date="2026-09-16")
    catalogue.write_text(json.dumps(data))
    (tmp_path / "manifests/yahoo-daily-v1/baselines.json").write_text(json.dumps({"symbols": {}}))
    plan = build_gap_plan(tmp_path, master, now=NOW)
    assert plan["tasks"][0]["requested_start"] == "2026-09-15"
    assert plan["tasks"][0]["reason"] == "missing_sessions_candidate"


def test_audit_rotates_and_zero_budget_does_not_read_more(tmp_path):
    master = setup(tmp_path)
    first = build_gap_plan(tmp_path, master, now=NOW, audit_limit=1)
    second = build_gap_plan(tmp_path, master, now=NOW, audit_limit=1)
    assert second["audit_symbols_total"] == 2
    assert build_gap_plan(tmp_path, master, now=NOW, audit_limit=0)["audit_symbols_this_cycle"] == 0


def test_cycle_persists_provider_cooldown_and_continues_other_sources(tmp_path, monkeypatch):
    from quant_data import sync_scheduler as scheduler
    plan = {"schema_version": "us-daily-gap-queue-v1", "target_date": "2026-09-16", "pending": 1, "endpoint_or_bridge_pending": 1, "historical_candidate_tasks": 0,
        "tasks": [{"task_id": "x", "symbol": "AAA", "latest_date": None, "requested_start": "2026-09-01", "requested_end": "2026-09-16"}]}
    monkeypatch.setattr(scheduler, "build_gap_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(scheduler, "refresh_yahoo_browser_catalogue", lambda *_: None)
    called = []
    def recovery(*_args, **kwargs):
        provider = kwargs["recovery_provider"]; called.append(provider)
        return {"circuit_open": provider == "alpaca", "status": "failed", "last_error_code": "provider_authorization_failed"}
    monkeypatch.setattr(scheduler, "run_recovery", recovery)
    monkeypatch.setattr(scheduler, "run_sync", lambda *_args, **_kwargs: called.append("yahoo") or {"status": "success"})
    first = scheduler.run_cycle(tmp_path, tmp_path / "m", tmp_path / "k", budget=1)
    assert called == ["alpaca", "yahoo", "nasdaq"] and first["status"] == "partial"
    assert first["providers"]["alpaca"]["resume_after"]
    called.clear()
    scheduler.run_cycle(tmp_path, tmp_path / "m", tmp_path / "k", budget=1)
    assert called == ["yahoo", "nasdaq"]
