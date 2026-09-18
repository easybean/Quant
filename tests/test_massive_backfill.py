from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from quant_data.massive_backfill import (
    MassiveBackfillError,
    build_massive_backfill_plan,
    main,
    run_massive_backfill,
)


NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
TARGET = date(2026, 9, 16)


@pytest.fixture(autouse=True)
def fixed_completed_session(monkeypatch):
    monkeypatch.setattr("quant_data.massive_backfill.completed_session", lambda _: TARGET)


def _master(root, symbols=("AAA", "BBB")):
    path = root / "master.csv"
    pd.DataFrame({"symbol": list(symbols), "status": ["active"] * len(symbols), "asset_type": ["stock"] * len(symbols)}).to_csv(path, index=False)
    return path


def _bars(root, provider, namespace, rows):
    path = root / "bars/daily" / f"provider={provider}" / f"namespace={namespace}" / "symbol=fixture" / "bars.parquet"
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_parquet(path, index=False)


def test_plan_scans_all_namespaces_and_retains_old_bridge_gap(tmp_path):
    master = _master(tmp_path)
    _bars(tmp_path, "legacy", "legacy-v1", [
        {"symbol": "AAA", "date": "2026-09-14"},
        {"symbol": "AAA", "date": "2026-09-16"},
        {"symbol": "BBB", "date": "2026-09-16"},
    ])
    plan = build_massive_backfill_plan(tmp_path, master, authorized_start=date(2026, 9, 14), now=NOW)
    assert plan["calendar"] == "XNYS"
    assert plan["completed_session"] == "2026-09-16"
    assert plan["tasks"] == [{"date": "2026-09-15", "symbols": ["AAA"]}]
    assert plan["research_qualified"] is False


def test_plan_preserves_explicit_pre_free_uncovered_range(tmp_path):
    master = _master(tmp_path, ("AAA",))
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2023-09-15"}])
    plan = build_massive_backfill_plan(tmp_path, master, authorized_start=date(2023, 9, 15), now=NOW)
    assert plan["free_history_start"] == "2024-09-17"
    assert plan["request_start"] == "2024-09-17"
    missing = plan["outside_free_window_uncovered"]
    assert missing[0]["symbol"] == "AAA"
    assert missing[0]["status"] == "outside_free_window_not_requested_not_assessed"
    assert plan["planned_sessions"] > 0


def test_run_groups_symbols_publishes_existing_snapshot_and_records_missing(tmp_path):
    master = _master(tmp_path)
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2026-09-15"}, {"symbol": "BBB", "date": "2026-09-15"}])
    target = TARGET
    snapshot = tmp_path / "reference/massive-daily-v1/fixed"
    snapshot.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "date": target.isoformat()}]).to_parquet(snapshot / "bars.parquet", index=False)
    (snapshot / "manifest.json").write_text(json.dumps({"schema_version": "massive-daily-reference-v1", "status": "captured", "request": {"date": target.isoformat()}}))
    calls = []

    def publish(*args, **kwargs):
        calls.append(kwargs)
        return {"status": "success"}

    result = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=target, max_sessions=1, now=NOW, publish=publish)
    assert result["status"] == "complete"
    assert result["http_requests"] == 0
    assert result["returned_symbol_sessions"] == 1
    assert result["missing_symbol_sessions"] == 1
    assert result["cumulative_returned_symbol_sessions"] == 1
    assert result["cumulative_missing_symbol_sessions"] == 1
    assert result["coverage_status"] == "unresolved"
    assert calls[0]["snapshot"] == snapshot
    records = [json.loads(line) for line in (tmp_path / "manifests/massive-backfill-v1/symbol-results.jsonl").read_text().splitlines()]
    assert {(row["symbol"], row["status"]) for row in records} == {("AAA", "returned"), ("BBB", "missing")}
    assert all(row["retirement_inference"] == "none" for row in records)


def test_429_is_persisted_and_resumed_without_blind_retry(tmp_path):
    master = _master(tmp_path, ("AAA",))
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2026-09-15"}])
    calls = []

    def capture(*args):
        calls.append(args)
        from quant_data.massive_daily import MassiveDailyError
        raise MassiveDailyError("http_429")

    first = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW, capture=capture)
    assert first["status"] == "partial" and first["last_error_code"] == "http_429"
    second = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW, capture=capture)
    assert second["status"] == "skipped" and second["reason"] == "resume_after"
    assert len(calls) == 1


def test_returned_bar_is_not_completed_when_scoped_publication_fails(tmp_path):
    master = _master(tmp_path, ("AAA",))
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2026-09-15"}])
    snapshot = tmp_path / "reference/massive-daily-v1/fixed"
    snapshot.mkdir(parents=True)
    pd.DataFrame([{"symbol": "AAA", "date": TARGET.isoformat()}]).to_parquet(snapshot / "bars.parquet", index=False)
    (snapshot / "manifest.json").write_text(json.dumps({"schema_version": "massive-daily-reference-v1", "status": "captured", "request": {"date": TARGET.isoformat()}}))
    result = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW, publish=lambda *args, **kwargs: {"status": "partial"})
    assert result["status"] == "partial" and result["pending_after"] == 1
    journal = [json.loads(line) for line in (tmp_path / "manifests/massive-backfill-v1/session-records.jsonl").read_text().splitlines()]
    assert journal[-1]["status"] == "failed"
    symbols = [json.loads(line) for line in (tmp_path / "manifests/massive-backfill-v1/symbol-results.jsonl").read_text().splitlines()]
    assert symbols[-1]["status"] == "returned_not_published"


def test_frozen_plan_does_not_requery_a_completed_provider_missing_session(tmp_path):
    master = _master(tmp_path, ("AAA",))
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2026-09-14"}])
    for session in (date(2026, 9, 15), date(2026, 9, 16)):
        snapshot = tmp_path / "reference/massive-daily-v1" / session.isoformat()
        snapshot.mkdir(parents=True)
        # Empty relative to the requested code remains a completed, explicit
        # provider result, never a signal to retry it after a later slice.
        pd.DataFrame([{"symbol": "OTHER", "date": session.isoformat()}]).to_parquet(snapshot / "bars.parquet", index=False)
        (snapshot / "manifest.json").write_text(json.dumps({"schema_version": "massive-daily-reference-v1", "status": "captured", "request": {"date": session.isoformat()}}))
    published = []

    def publish(*args, **kwargs):
        published.append(kwargs["date_"])
        return {"status": "success"}

    first = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=date(2026, 9, 15), max_sessions=1, now=NOW, publish=publish)
    second = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=date(2026, 9, 15), max_sessions=1, now=NOW, publish=publish)
    assert first["status"] == "partial" and second["status"] == "complete"
    assert published == [date(2026, 9, 16), date(2026, 9, 15)]


def test_terminal_frozen_plan_extends_to_newer_session_without_replaying_old_work(tmp_path, monkeypatch):
    master = _master(tmp_path, ("AAA",))
    _bars(tmp_path, "legacy", "legacy-v1", [{"symbol": "AAA", "date": "2026-09-15"}])
    for session in (date(2026, 9, 16), date(2026, 9, 17)):
        snapshot = tmp_path / "reference/massive-daily-v1" / f"extension-{session.isoformat()}"
        snapshot.mkdir(parents=True)
        pd.DataFrame([{"symbol": "OTHER", "date": session.isoformat()}]).to_parquet(snapshot / "bars.parquet", index=False)
        (snapshot / "manifest.json").write_text(json.dumps({"schema_version": "massive-daily-reference-v1", "status": "captured", "request": {"date": session.isoformat()}}))
    published = []
    publish = lambda *args, **kwargs: (published.append(kwargs["date_"]) or {"status": "success"})
    first = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW, publish=publish)
    monkeypatch.setattr("quant_data.massive_backfill.completed_session", lambda _: date(2026, 9, 17))
    second = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW, publish=publish)
    assert first["status"] == second["status"] == "complete"
    assert published == [date(2026, 9, 16), date(2026, 9, 17)]


def test_network_lock_and_cli_help(tmp_path, capsys):
    master = _master(tmp_path, ("AAA",))
    lock = tmp_path / "manifests/massive-network.lock"
    lock.parent.mkdir(parents=True)
    import fcntl
    with lock.open("a+") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = run_massive_backfill(tmp_path, master, tmp_path / "key", authorized_start=TARGET, now=NOW)
    assert result == {"schema_version": "massive-grouped-backfill-run-v1", "status": "skipped", "reason": "massive_network_lock_held"}
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--authorized-start" in capsys.readouterr().out


def test_rejects_invalid_authorized_window(tmp_path):
    master = _master(tmp_path)
    with pytest.raises(MassiveBackfillError, match="authorized_start_after_completed_session"):
        build_massive_backfill_plan(tmp_path, master, authorized_start=date(2026, 9, 17), now=NOW)
