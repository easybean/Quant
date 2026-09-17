"""Durable gap plans and bounded server-side resumes; no model calls/trading.

The queue is derived from fixed catalogue metadata and a rotating date audit.
Attempt state is the existing append-only provider journal, not service status.
Missing sessions remain acquisition candidates, never inferred trading facts.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .daily_sync import _target_end, _earliest_successful_request_starts, run_sync, _atomic_json
from .daily_quote_browser import (_canonical_catalogue, _base_priority,
                                 _is_append_source, _safe_raw_relative, refresh_yahoo_browser_catalogue)
from .sync_eligibility import select_sync_symbols, eligibility_report
from .recovery_sync import run_recovery

NAMESPACES = ("alpaca-sip-recovery-v1", "yahoo-daily-v1", "nasdaq-daily-recovery-v1")


def completed_session(now: datetime) -> date:
    import exchange_calendars as xc
    # Conservative provider publication cutoff retained; calendar additionally
    # excludes weekends, holidays and extraordinary closures.
    cutoff = _target_end(now)
    cal = xc.get_calendar("XNYS", start="2016-01-01", end=f"{now.year + 1}-12-31")
    return cal.date_to_session(cutoff.isoformat(), direction="previous").date()


def _load(path: Path, default):
    return json.loads(path.read_text()) if path.exists() else default


def build_gap_plan(root: Path, master_path: Path, *, now: datetime | None = None,
                   audit_limit: int = 300) -> dict:
    now = now or datetime.now(timezone.utc)
    if audit_limit < 0: raise ValueError("audit_limit_must_be_nonnegative")
    target = completed_session(now)
    import exchange_calendars as xc
    cal = xc.get_calendar("XNYS", start="2016-01-01", end=f"{now.year + 1}-12-31")
    master = pd.read_parquet(master_path)
    active = select_sync_symbols(master)
    baseline = _load(root / "manifests/yahoo-daily-v1/baselines.json", {"symbols": {}})["symbols"]
    catalogue = _load(root / "catalogue/us-daily-browser-v1.json", {})
    if catalogue.get("schema_version") != "us-daily-browser-v1" or not isinstance(catalogue.get("series"), list):
        raise ValueError("browser_catalogue_invalid")
    views = {entry["symbol"]: entry for entry in _canonical_catalogue(catalogue["series"])}
    folder = root / "manifests/us-daily-sync"; folder.mkdir(parents=True, exist_ok=True)
    audit = _load(folder / "session-audit.json", {"cursor": 0, "symbols": {}})
    cursor = int(audit["cursor"]) % max(1, len(active))
    batch = (active + active)[cursor:cursor + min(audit_limit, len(active))]
    for symbol in batch:
        view = views.get(symbol)
        if not view:
            audit["symbols"][symbol] = {"audited_at": now.isoformat(), "no_history": True, "target_date": target.isoformat()}
            continue
        base = min(view["_members"], key=_base_priority)
        members = [base] + [m for m in view["_members"] if m is not base and _is_append_source(m)]
        found = set(); errors = []
        try:
            base_end = date.fromisoformat(base["last_date"])
            for entry in members:
                relative = str(entry.get("raw_relative_path", ""))
                if not _safe_raw_relative(relative): raise ValueError("invalid_catalogue_path")
                path = root / "bars/daily" / relative
                if (root / "bars/daily").resolve() not in path.resolve().parents:
                    raise ValueError("invalid_catalogue_path")
                days = pd.to_datetime(pd.read_parquet(path, columns=["date"]).date, errors="raise")
                if days.isna().any(): raise ValueError("invalid_dates")
                found.update(d.date() for d in days if entry is base or d.date() > base_end)
            if found:
                first = max(min(found), date(2016, 1, 1))
                expected = {s.date() for s in cal.sessions_in_range(first.isoformat(), target.isoformat())} if first <= target else set()
                gaps = sorted(expected - found)
            else: gaps = []
            audit["symbols"][symbol] = {"audited_at": now.isoformat(), "first_missing_session": gaps[0].isoformat() if gaps else None,
                                        "missing_sessions": len(gaps), "target_date": target.isoformat()}
        except (ValueError, OSError, KeyError) as exc:
            errors.append(type(exc).__name__)
            audit["symbols"][symbol] = {"audited_at": now.isoformat(), "error": errors, "target_date": target.isoformat()}
    audit["cursor"] = (cursor + len(batch)) % max(1, len(active))
    _atomic_json(folder / "session-audit.json", audit)
    starts = {}
    for namespace in NAMESPACES:
        for symbol, day in _earliest_successful_request_starts(root / "manifests" / namespace / "records.jsonl").items():
            starts[symbol] = min(starts.get(symbol, day), day)
    tasks = []; current = 0
    for symbol in active:
        previous = baseline.get(symbol, {}).get("last_date")
        view = views.get(symbol)
        latest = view.get("last_date") if view else previous
        bridge = bool(previous and previous < target.isoformat() and starts.get(symbol, date.max) > date.fromisoformat(previous) + timedelta(days=1))
        candidates = []
        if not latest: candidates.append(date(2016, 1, 1))
        elif latest < target.isoformat():
            # Once acquired, retain the existing seven-calendar-day correction
            # probe. Legacy raw remains untouched and never becomes qualified.
            managed = bool(view and any(_is_append_source(m) for m in view["_members"]))
            candidates.append(date.fromisoformat(latest) - timedelta(days=6) if managed else date.fromisoformat(latest) + timedelta(days=1))
        if bridge: candidates.append(date.fromisoformat(previous) + timedelta(days=1))
        details = audit["symbols"].get(symbol, {})
        # Do not let a ten-year historical audit candidate postpone today's
        # update. Historical repair is a separate window once endpoint/bridge
        # work is complete; original raw is not overwritten to satisfy it.
        historical_candidate = not candidates and bool(details.get("first_missing_session"))
        if historical_candidate:
            candidates.append(date.fromisoformat(details["first_missing_session"]))
        if not candidates: current += 1; continue
        start = max(min(candidates), date(2016, 1, 1))
        identity = f"{symbol}|{start}|{target}"
        tasks.append({"symbol": symbol, "requested_start": start.isoformat(), "requested_end": target.isoformat(),
                      "task_id": hashlib.sha256(identity.encode()).hexdigest(), "latest_date": latest,
                      "reason": "missing_sessions_candidate" if historical_candidate else "endpoint_or_bridge_gap"})
    # Recent endpoint deficits first, then historical candidates; provider
    # journals enforce fairness between retries and never-attempted tasks.
    tasks.sort(key=lambda t: (t["latest_date"] is not None and t["latest_date"] >= target.isoformat(), t["latest_date"] is None, t["symbol"]))
    plan = {"schema_version": "us-daily-gap-queue-v1", "generated_at": now.isoformat(), "target_date": target.isoformat(),
            "calendar": "XNYS", "eligibility": eligibility_report(master), "eligible_symbols": len(active),
            "calendar_library_version": xc.__version__,
            "endpoint_without_known_gap": current, "pending": len(tasks), "tasks": tasks,
            "endpoint_or_bridge_pending": sum(t["reason"] == "endpoint_or_bridge_gap" for t in tasks),
            "historical_candidate_tasks": sum(t["reason"] == "missing_sessions_candidate" for t in tasks),
            "audit_symbols_this_cycle": len(batch), "audit_symbols_total": len(audit["symbols"]),
            "audit_errors": sum(bool(d.get("error")) for d in audit["symbols"].values()),
            "research_qualified": False, "warning": "Rotating session audit is incomplete until all symbols are checked; missing sessions may be halts/provider gaps, not confirmed tradable sessions. Historical identity and corporate actions remain unverified."}
    _atomic_json(folder / "queue.json", plan)
    pending_symbols = {t["symbol"] for t in tasks}
    historical_symbols = {t["symbol"] for t in tasks if t["reason"] == "missing_sessions_candidate"}
    _atomic_json(root / "catalogue/us-daily-sync-status-v1.json", {
        "schema_version": "us-daily-sync-status-v1", "generated_at": now.isoformat(),
        "target_date": target.isoformat(), "symbols": {
            symbol: {"state": "missing_sessions_candidate" if symbol in historical_symbols else "needs_update" if any_task else "endpoint_current",
                     "latest_date": views.get(symbol, {}).get("last_date")}
            for symbol, any_task in ((s, s in pending_symbols) for s in active)
        }, "research_qualified": False})
    return plan


def due_tasks(plan: dict, records: Path, now: datetime) -> list[dict]:
    attempts = {}
    if records.exists():
        for line in records.open():
            try:
                row = json.loads(line); marker = row.get("source_attempted_at")
                if marker:
                    attempts.setdefault(marker, []).append(row)
            except (ValueError, TypeError): continue
    ready = []
    for task in plan["tasks"]:
        history = attempts.get(task["task_id"], [])
        if len(history) >= 3: continue
        if history:
            # A successful but incomplete response remains unresolved. Retry
            # after cooldown, rather than treating HTTP success as coverage.
            stamp = datetime.fromisoformat(history[-1]["attempted_at"])
            if now - stamp < timedelta(minutes=30 * 2 ** (len(history) - 1)): continue
        ready.append(task)
    ready.sort(key=lambda t: (len(attempts.get(t["task_id"], [])), t["latest_date"] is not None and t["latest_date"] >= plan["target_date"], t["latest_date"] is None, t["symbol"]))
    return ready


def run_cycle(root: Path, master: Path, credential_file: Path, *, budget: float = 240) -> dict:
    if not math.isfinite(budget) or budget <= 0: raise ValueError("provider_budget_must_be_positive_finite")
    folder = root / "manifests/us-daily-sync"; folder.mkdir(parents=True, exist_ok=True)
    with (folder / "scheduler.lock").open("a+") as lock:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {"status": "skipped", "reason": "lock_held"}
        refresh_yahoo_browser_catalogue(root)
        plan = build_gap_plan(root, master)
        state = _load(folder / "latest.json", {"providers": {}})
        state.update({"status": "running", "target_date": plan["target_date"], "pending_before": plan["pending"]})
        state.pop("finished_at", None)
        _atomic_json(folder / "latest.json", state)
        for provider, namespace in zip(("alpaca", "yahoo", "nasdaq"), NAMESPACES):
            now = datetime.now(timezone.utc)
            previous = state["providers"].get(provider, {})
            resume = previous.get("resume_after")
            if resume and now < datetime.fromisoformat(resume): continue
            ready = due_tasks(plan, root / "manifests" / namespace / "records.jsonl", now)
            if not ready: continue
            provider_plan = {**plan, "tasks": ready}
            queue = folder / f"{provider}-due.json"; _atomic_json(queue, provider_plan)
            state["current_provider"] = provider
            _atomic_json(folder / "latest.json", state)
            try:
                if provider == "yahoo":
                    result = run_sync(master, root, gap_queue=queue, baseline_index=root / "manifests/yahoo-daily-v1/baselines.json",
                                      request_delay=2, max_runtime_seconds=budget)
                else:
                    result = run_recovery(master, root, gap_queue=queue, recovery_provider=provider,
                                          credential_file=credential_file if provider == "alpaca" else None,
                                          request_delay=0 if provider == "alpaca" else 2, max_runtime_seconds=budget * 3 if provider == "alpaca" else budget / 4)
                cooldown = 6 * 60 if result.get("last_error_code") in {"http_401", "http_403", "provider_authorization_failed"} else 30
                state["providers"][provider] = {**result, "resume_after": (datetime.now(timezone.utc) + timedelta(minutes=cooldown)).isoformat() if result.get("circuit_open") else None}
            except Exception as exc:
                state["providers"][provider] = {"status": "failed", "error_type": type(exc).__name__,
                    "resume_after": (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()}
            _atomic_json(folder / "latest.json", state)
            refresh_yahoo_browser_catalogue(root)
            plan = build_gap_plan(root, master, audit_limit=0)
        state.update({"status": "partial" if plan["pending"] else "endpoint_complete_audit_pending", "pending_after": plan["pending"],
                      "endpoint_or_bridge_pending": plan["endpoint_or_bridge_pending"], "historical_candidate_tasks": plan["historical_candidate_tasks"],
                      "finished_at": datetime.now(timezone.utc).isoformat()})
        state.pop("current_provider", None)
        _atomic_json(folder / "latest.json", state)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--security-master", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--provider-budget", type=float, default=240)
    args = parser.parse_args()
    print(json.dumps(run_cycle(args.data_root, args.security_master, args.credential_file, budget=args.provider_budget)))


if __name__ == "__main__": main()
