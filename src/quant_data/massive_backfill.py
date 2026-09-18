"""Durably backfill eligible US daily-bar gaps with Massive grouped sessions.

The Massive grouped endpoint is requested once per XNYS session, never once per
symbol.  A plan is frozen before downloads begin, so a recent Massive bar can
not make an older bridge gap disappear on a later resume.  This is acquisition
evidence only: every published bar remains raw, unadjusted and explicitly not
research qualified.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .massive_daily import NAMESPACE, MassiveDailyError, capture_massive_daily
from .massive_publish import MassivePublishError, publish_massive_daily
from .sync_eligibility import select_sync_symbols
from .sync_scheduler import completed_session, resolve_sync_master


PLAN_SCHEMA = "massive-grouped-backfill-plan-v1"
RUN_SCHEMA = "massive-grouped-backfill-run-v1"
MIN_HTTP_INTERVAL_SECONDS = 13.0
MAX_SESSION_ATTEMPTS = 3
_FREE_HISTORY_YEARS = 2


class MassiveBackfillError(ValueError):
    """Stable, non-sensitive backfill input or local-state failure."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True, default=str)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() in {".csv", ".tsv"} else pd.read_parquet(path)


def _two_year_floor(value: date) -> date:
    """Calendar-year floor for the stated free-history policy, including Feb 29."""
    try:
        return value.replace(year=value.year - _FREE_HISTORY_YEARS)
    except ValueError:
        return value.replace(year=value.year - _FREE_HISTORY_YEARS, day=28)


def _sessions(start: date, end: date) -> list[date]:
    if start > end:
        return []
    try:
        import exchange_calendars as xc

        calendar = xc.get_calendar("XNYS", start=f"{start.year - 1}-01-01", end=f"{end.year + 1}-12-31")
        return [session.date() for session in calendar.sessions_in_range(start.isoformat(), end.isoformat())]
    except Exception as exc:
        raise MassiveBackfillError("xnys_calendar_unavailable") from exc


def _existing_dates(root: Path, active: set[str], window_sessions: list[date]) -> tuple[dict[str, int], dict[str, date], dict[str, date], list[str]]:
    """Read actual bars without retaining a multi-year date set per symbol.

    The free-window sessions become one bit per symbol.  This preserves every
    in-window bridge hole while keeping the 25M-row, multi-namespace store out
    of process memory.  Dates outside that window contribute only first/last
    observed provenance; they are explicitly not assessed as coverage.
    """
    masks = {symbol: 0 for symbol in active}
    first_seen: dict[str, date] = {}
    last_seen: dict[str, date] = {}
    index = {session: position for position, session in enumerate(window_sessions)}
    errors: list[str] = []
    bars_root = root / "bars" / "daily"
    if not bars_root.exists():
        return masks, first_seen, last_seen, errors
    try:
        resolved_root = bars_root.resolve(strict=True)
    except OSError as exc:
        raise MassiveBackfillError("bars_root_unreadable") from exc
    for path in bars_root.rglob("bars.parquet"):
        try:
            if path.is_symlink() or resolved_root not in path.resolve(strict=True).parents:
                raise ValueError("path")
            frame = pd.read_parquet(path, columns=["symbol", "date"])
            if not {"symbol", "date"}.issubset(frame.columns):
                raise ValueError("columns")
            symbols = frame["symbol"].fillna("").astype(str).str.strip().str.upper()
            parsed = pd.to_datetime(frame["date"], errors="coerce").dt.date
            usable = pd.DataFrame({"symbol": symbols, "date": parsed})
            usable = usable.loc[usable["symbol"].isin(active) & usable["date"].notna()]
            for symbol, values in usable.groupby("symbol")["date"]:
                low, high = min(values), max(values)
                first_seen[symbol] = min(first_seen.get(symbol, low), low)
                last_seen[symbol] = max(last_seen.get(symbol, high), high)
                mask = masks[symbol]
                for observed in set(values):
                    position = index.get(observed)
                    if position is not None:
                        mask |= 1 << position
                masks[symbol] = mask
        except (OSError, ValueError, TypeError, ImportError, KeyError):
            # Do not include raw paths in reports; a report might be shared
            # outside the data host.  The count remains auditable in the plan.
            errors.append("invalid_local_bar_view")
    return masks, first_seen, last_seen, errors


def _validate_window(authorized_start: date, clock: datetime) -> tuple[date, date, date]:
    if not isinstance(authorized_start, date) or isinstance(authorized_start, datetime):
        raise MassiveBackfillError("authorized_start_invalid")
    if clock.tzinfo is None:
        raise MassiveBackfillError("now_must_be_timezone_aware")
    target = completed_session(clock)
    if authorized_start > target:
        raise MassiveBackfillError("authorized_start_after_completed_session")
    # Provider entitlement is measured from the current UTC calendar date, not
    # from the prior completed exchange session (which can be one to four days
    # earlier over weekends/holidays).
    free_start = _two_year_floor(clock.astimezone(timezone.utc).date())
    return target, free_start, max(authorized_start, free_start)


def build_massive_backfill_plan(
    data_root: Path,
    security_master: Path,
    *,
    authorized_start: date,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build an immutable grouped-session plan from actual multi-namespace bars.

    ``authorized_start`` is an explicit user-authorized historical boundary.
    The endpoint plan is additionally restricted to the stated free two-year
    window.  Earlier missing XNYS sessions are retained as *uncovered* evidence
    rather than silently shortened or claimed to be delisted.
    """
    root = Path(data_root)
    clock = now or _utc_now()
    target, free_start, request_start = _validate_window(authorized_start, clock)
    try:
        master_path = resolve_sync_master(root, Path(security_master))
        master_sha = _sha256(master_path)
        master = _read_master(master_path)
        if _sha256(master_path) != master_sha:
            raise MassiveBackfillError("security_master_changed_during_read")
        active = set(select_sync_symbols(master))
    except MassiveBackfillError:
        raise
    except (OSError, ValueError, ImportError) as exc:
        raise MassiveBackfillError("security_master_invalid") from exc

    in_window_sessions = _sessions(request_start, target)
    masks, first_seen, last_seen, coverage_errors = _existing_dates(root, active, in_window_sessions)
    grouped: dict[str, list[str]] = {}
    uncovered: list[dict[str, Any]] = []
    unknown_history: list[dict[str, str]] = []
    for symbol in sorted(active):
        observed_first = first_seen.get(symbol)
        if observed_first is None:
            # No actual local bar means the listing/lifecycle start is unknown.
            # Do not manufacture pre-IPO missing sessions or schedule a blind
            # two-year provider request for it.
            unknown_history.append({"symbol": symbol, "status": "history_start_unknown_not_planned"})
            continue
        symbol_start = max(request_start, observed_first)
        missing = [session for position, session in enumerate(in_window_sessions) if session >= symbol_start and not (masks[symbol] & (1 << position))]
        for session in missing:
            grouped.setdefault(session.isoformat(), []).append(symbol)
        if authorized_start < free_start and observed_first < free_start:
            uncovered.append({
                "symbol": symbol,
                "start": max(authorized_start, observed_first).isoformat(),
                "end": min(target, free_start - timedelta(days=1)).isoformat(),
                "first_observed_date": observed_first.isoformat(),
                "last_observed_date": last_seen[symbol].isoformat(),
                "status": "outside_free_window_not_requested_not_assessed",
            })

    # Recent bridge gaps are visible to users first; the complete immutable
    # plan still contains every eligible free-window session.
    tasks = [{"date": session, "symbols": symbols} for session, symbols in sorted(grouped.items(), reverse=True)]
    plan_input = {
        "schema_version": PLAN_SCHEMA,
        "security_master_sha256": master_sha,
        "authorized_start": authorized_start.isoformat(),
        "free_history_start": free_start.isoformat(),
        "request_start": request_start.isoformat(),
        "completed_session": target.isoformat(),
        "tasks": tasks,
    }
    plan_id = hashlib.sha256(json.dumps(plan_input, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return {
        **plan_input,
        "plan_id": plan_id,
        "calendar": "XNYS",
        "coverage_evidence": "actual_local_bars_all_provider_namespaces",
        "coverage_read_errors": len(coverage_errors),
        "eligible_symbols": len(active),
        "history_start_unknown_symbols": unknown_history,
        "planned_sessions": len(tasks),
        "planned_symbol_sessions": sum(len(task["symbols"]) for task in tasks),
        "outside_free_window_uncovered": uncovered,
        "research_qualified": False,
        "warning": (
            "Missing is an acquisition/coverage result, not delisting evidence. "
            "Massive responses are unadjusted and raw only."
        ),
    }


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else default
    except (OSError, ValueError):
        return default


def _freeze_plan(folder: Path, plan: dict[str, Any]) -> Path:
    path = folder / "plans" / f"{plan['plan_id']}.json"
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing.get("plan_id") != plan["plan_id"]:
                raise MassiveBackfillError("plan_path_conflict")
        except (OSError, ValueError, AttributeError) as exc:
            raise MassiveBackfillError("plan_unreadable") from exc
    else:
        _atomic_json(path, plan)
    _atomic_json(folder / "current-plan.json", {"schema_version": PLAN_SCHEMA, "plan_id": plan["plan_id"], "relative_path": path.relative_to(folder).as_posix()})
    return path


def _load_frozen_plan(folder: Path, root: Path, security_master: Path, authorized_start: date, clock: datetime) -> dict[str, Any] | None:
    """Reuse a matching plan so completed missing responses are not re-queried.

    Rebuilding from newly published bars would otherwise produce a different
    plan id after every slice and turn a provider-confirmed missing symbol into
    endless work.  A changed acquisition master deliberately starts a new plan.
    """
    pointer = _load_json(folder / "current-plan.json", {})
    relative = pointer.get("relative_path")
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute() or ".." in Path(relative).parts:
        return None
    path = folder / relative
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
        master_path = resolve_sync_master(root, Path(security_master))
        master_sha = _sha256(master_path)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(plan, dict):
        return None
    if (plan.get("schema_version") != PLAN_SCHEMA or plan.get("authorized_start") != authorized_start.isoformat()
            or not isinstance(plan.get("completed_session"), str) or plan["completed_session"] > completed_session(clock).isoformat() or plan.get("security_master_sha256") != master_sha
            or not isinstance(plan.get("plan_id"), str) or not isinstance(plan.get("tasks"), list)):
        return None
    return plan


def _terminal_latest(plan: dict[str, Any], journal_path: Path) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    latest = _latest_session_records(journal_path, plan["plan_id"])
    pending = [task for task in plan["tasks"] if latest.get(task["date"], {}).get("status") not in {"completed", "unavailable"}]
    return latest, pending


def _reidentify_plan(plan: dict[str, Any]) -> dict[str, Any]:
    source = {key: plan[key] for key in ("schema_version", "security_master_sha256", "authorized_start", "free_history_start", "request_start", "completed_session", "tasks")}
    plan["plan_id"] = hashlib.sha256(json.dumps(source, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    plan["planned_sessions"] = len(plan["tasks"])
    plan["planned_symbol_sessions"] = sum(len(task["symbols"]) for task in plan["tasks"])
    return plan


def _exclude_terminal_prior_tasks(plan: dict[str, Any], prior_plan: dict[str, Any], prior_latest: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Extend a frozen plan without re-requesting terminal earlier sessions."""
    terminal = {task["date"] for task in prior_plan["tasks"] if prior_latest.get(task["date"], {}).get("status") in {"completed", "unavailable"}}
    if not terminal:
        return plan
    prior_symbols = {task["date"]: set(task["symbols"]) for task in prior_plan["tasks"] if task["date"] in terminal}
    tasks = []
    for task in plan["tasks"]:
        symbols = [symbol for symbol in task["symbols"] if symbol not in prior_symbols.get(task["date"], set())]
        if symbols:
            tasks.append({"date": task["date"], "symbols": symbols})
    plan["tasks"] = tasks
    return _reidentify_plan(plan)


def _latest_session_records(path: Path, plan_id: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            session = row.get("date") if isinstance(row, dict) else None
            if row.get("plan_id") == plan_id and isinstance(session, str):
                latest[session] = row
        except (ValueError, AttributeError):
            continue
    return latest


def _snapshot_for_date(root: Path, target: date) -> Path | None:
    reference = root / "reference" / NAMESPACE
    if not reference.exists():
        return None
    candidates: list[Path] = []
    for manifest_path in reference.glob("*/manifest.json"):
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            request = manifest.get("request", {})
            if (manifest.get("schema_version") == "massive-daily-reference-v1" and manifest.get("status") == "captured"
                    and request.get("date") == target.isoformat() and (manifest_path.parent / "bars.parquet").is_file()):
                candidates.append(manifest_path.parent)
        except (OSError, ValueError, AttributeError):
            continue
    return sorted(candidates)[-1] if candidates else None


def _returned_symbols(snapshot: Path, target: date) -> set[str]:
    try:
        frame = pd.read_parquet(snapshot / "bars.parquet", columns=["symbol", "date"])
        returned: set[str] = set()
        for symbol, raw_date in frame.loc[:, ["symbol", "date"]].itertuples(index=False, name=None):
            if pd.Timestamp(raw_date).date() == target:
                returned.add(str(symbol).strip().upper())
        return returned
    except (OSError, ValueError, TypeError, ImportError, KeyError) as exc:
        raise MassiveBackfillError("snapshot_bars_invalid") from exc


def _attempt_count(previous: dict[str, Any] | None) -> int:
    value = previous.get("attempt", 0) if previous else 0
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _parse_resume(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed if parsed and parsed.tzinfo else None
    except ValueError:
        return None


def run_massive_backfill(
    data_root: Path,
    security_master: Path,
    credential_file: Path,
    *,
    authorized_start: date,
    max_sessions: int = 1,
    now: datetime | None = None,
    capture: Callable[[Path, date, Path], dict[str, Any]] = capture_massive_daily,
    publish: Callable[..., dict[str, Any]] = publish_massive_daily,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    """Run a bounded, resumable slice of a frozen grouped-session plan.

    The process owns ``manifests/massive-network.lock`` from plan execution
    through publication.  A concurrent daily capture or probe therefore gets a
    clear ``lock_held`` skip instead of breaking the shared free-tier limit.
    """
    if not isinstance(max_sessions, int) or isinstance(max_sessions, bool) or max_sessions <= 0:
        raise MassiveBackfillError("max_sessions_must_be_positive_integer")
    root = Path(data_root)
    folder = root / "manifests" / "massive-backfill-v1"
    folder.mkdir(parents=True, exist_ok=True)
    network_lock_path = root / "manifests" / "massive-network.lock"
    network_lock_path.parent.mkdir(parents=True, exist_ok=True)
    with network_lock_path.open("a+") as network_lock:
        try:
            fcntl.flock(network_lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"schema_version": RUN_SCHEMA, "status": "skipped", "reason": "massive_network_lock_held"}
        clock = now or _utc_now()
        previous_state = _load_json(folder / "latest.json", {})
        _atomic_json(folder / "latest.json", {"schema_version": RUN_SCHEMA, "status": "running", "phase": "building_plan", "authorized_start": authorized_start.isoformat(), "started_at": _utc_now().isoformat(), "research_qualified": False})
        prior_plan = _load_frozen_plan(folder, root, security_master, authorized_start, clock)
        prior_latest: dict[str, dict[str, Any]] = {}
        if prior_plan is not None:
            prior_latest, prior_pending = _terminal_latest(prior_plan, journal_path := folder / "session-records.jsonl")
            current_target = completed_session(clock).isoformat()
            # A still-open plan remains authoritative even when a new trading
            # session completes.  Once terminal, extend it only with newer
            # work, retaining old returned/missing outcomes as evidence.
            if prior_pending or prior_plan["completed_session"] == current_target:
                plan = prior_plan
            else:
                plan = build_massive_backfill_plan(root, security_master, authorized_start=authorized_start, now=clock)
                plan = _exclude_terminal_prior_tasks(plan, prior_plan, prior_latest)
        else:
            plan = build_massive_backfill_plan(root, security_master, authorized_start=authorized_start, now=clock)
        plan_path = _freeze_plan(folder, plan)
        journal_path = folder / "session-records.jsonl"
        latest = _latest_session_records(journal_path, plan["plan_id"])
        resume_after = _parse_resume(previous_state.get("resume_after")) if previous_state.get("plan_id") == plan["plan_id"] else None
        actual_now = _utc_now()
        if resume_after and actual_now < resume_after:
            _atomic_json(folder / "latest.json", previous_state)
            return {"schema_version": RUN_SCHEMA, "status": "skipped", "reason": "resume_after", "plan_id": plan["plan_id"], "resume_after": resume_after.isoformat()}

        pending = [task for task in plan["tasks"] if latest.get(task["date"], {}).get("status") not in {"completed", "unavailable"}]
        chosen = pending[:max_sessions]
        summary: dict[str, Any] = {
            "schema_version": RUN_SCHEMA,
            "status": "running",
            "plan_id": plan["plan_id"],
            "plan_relative_path": plan_path.relative_to(root).as_posix(),
            "authorized_start": plan["authorized_start"],
            "completed_session": plan["completed_session"],
            "planned_sessions": plan["planned_sessions"],
            "pending_before": len(pending),
            "processed_sessions": 0,
            "http_requests": 0,
            "returned_symbol_sessions": 0,
            "missing_symbol_sessions": 0,
            "failed_symbol_sessions": 0,
            "last_error_code": None,
            "research_qualified": False,
        }
        last_http = None
        summary["phase"] = "executing_frozen_plan"
        _atomic_json(folder / "latest.json", summary)
        for task in chosen:
            target = date.fromisoformat(task["date"])
            symbols = list(task["symbols"])
            prior = latest.get(task["date"])
            prior_attempts = _attempt_count(prior)
            if prior_attempts >= MAX_SESSION_ATTEMPTS:
                record = {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "status": "unavailable",
                          "error_code": "max_attempts_exhausted", "attempt": prior_attempts, "symbol_count": len(symbols), "recorded_at": _utc_now().isoformat()}
                _append_jsonl(journal_path, record)
                for symbol in symbols:
                    _append_jsonl(folder / "symbol-results.jsonl", {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "symbol": symbol, "status": "failed", "error_code": "max_attempts_exhausted", "retirement_inference": "none"})
                summary["failed_symbol_sessions"] += len(symbols); summary["processed_sessions"] += 1
                continue
            snapshot = _snapshot_for_date(root, target)
            downloaded = False
            if snapshot is None:
                if last_http is not None:
                    remaining = MIN_HTTP_INTERVAL_SECONDS - (monotonic() - last_http)
                    if remaining > 0:
                        sleep(remaining)
                try:
                    captured = capture(root, target, Path(credential_file))
                    last_http = monotonic(); downloaded = True; summary["http_requests"] += 1
                    if captured.get("status") != "captured" or not isinstance(captured.get("snapshot_relative_path"), str):
                        raise MassiveBackfillError("capture_not_publishable")
                    snapshot = root / captured["snapshot_relative_path"]
                except (MassiveDailyError, MassiveBackfillError) as exc:
                    code = str(exc)
                    attempts = prior_attempts + 1
                    retry_seconds = min(15 * (2 ** (attempts - 1)), 300) if code == "http_429" else min(30 * (2 ** (attempts - 1)), 300)
                    resume = _utc_now() + timedelta(seconds=retry_seconds)
                    record = {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "status": "failed", "error_code": code,
                              "attempt": attempts, "symbol_count": len(symbols), "resume_after": resume.isoformat(), "recorded_at": _utc_now().isoformat()}
                    _append_jsonl(journal_path, record)
                    for symbol in symbols:
                        _append_jsonl(folder / "symbol-results.jsonl", {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "symbol": symbol, "status": "failed", "error_code": code, "retirement_inference": "none"})
                    summary.update({"status": "partial", "last_error_code": code, "failed_symbol_sessions": summary["failed_symbol_sessions"] + len(symbols), "processed_sessions": summary["processed_sessions"] + 1, "resume_after": resume.isoformat()})
                    break
            assert snapshot is not None
            returned: set[str] = set()
            try:
                returned = _returned_symbols(snapshot, target) & set(symbols)
                publication = publish(Path(security_master), root, Path(credential_file), date_=target, snapshot=snapshot, now=clock, symbols=set(symbols))
                publication_status = str(publication.get("status", "unknown"))
                if publication_status != "success":
                    raise MassiveBackfillError("publication_not_success")
                for symbol in symbols:
                    status = "returned" if symbol in returned else "missing"
                    _append_jsonl(folder / "symbol-results.jsonl", {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "symbol": symbol,
                                                                      "status": status, "publication_status": publication_status, "snapshot_relative_path": snapshot.relative_to(root).as_posix(), "retirement_inference": "none"})
                summary["returned_symbol_sessions"] += len(returned)
                summary["missing_symbol_sessions"] += len(symbols) - len(returned)
                record = {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "status": "completed", "attempt": prior_attempts,
                          "snapshot_relative_path": snapshot.relative_to(root).as_posix(), "downloaded": downloaded, "returned": len(returned), "missing": len(symbols) - len(returned),
                          "publication_status": publication_status, "recorded_at": _utc_now().isoformat()}
                _append_jsonl(journal_path, record)
                summary["processed_sessions"] += 1
                _atomic_json(folder / "latest.json", summary)
            except (MassiveBackfillError, MassivePublishError, OSError, ValueError, ImportError) as exc:
                code = str(exc) if isinstance(exc, (MassiveBackfillError, MassivePublishError)) else "publication_or_snapshot_failed"
                record = {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "status": "failed", "error_code": code,
                          "attempt": prior_attempts + 1, "symbol_count": len(symbols), "recorded_at": _utc_now().isoformat()}
                _append_jsonl(journal_path, record)
                for symbol in symbols:
                    status = "returned_not_published" if symbol in returned else "failed"
                    _append_jsonl(folder / "symbol-results.jsonl", {"schema_version": RUN_SCHEMA, "plan_id": plan["plan_id"], "date": task["date"], "symbol": symbol, "status": status, "error_code": code, "retirement_inference": "none"})
                summary.update({"status": "partial", "last_error_code": code, "failed_symbol_sessions": summary["failed_symbol_sessions"] + len(symbols), "processed_sessions": summary["processed_sessions"] + 1})
                break
        final_latest, final_pending = _terminal_latest(plan, journal_path)
        terminal_unavailable = sum(row.get("status") == "unavailable" for row in final_latest.values())
        cumulative_returned = sum(int(row.get("returned", 0)) for row in final_latest.values() if row.get("status") == "completed")
        cumulative_missing = sum(int(row.get("missing", 0)) for row in final_latest.values() if row.get("status") == "completed")
        cumulative_failed = sum(int(row.get("symbol_count", 0)) for row in final_latest.values() if row.get("status") in {"failed", "unavailable"})
        unknown_history = plan.get("history_start_unknown_symbols", [])
        outside_unassessed = plan.get("outside_free_window_uncovered", [])
        if summary["status"] == "running":
            summary["status"] = "complete" if not final_pending else "partial"
        summary["pending_after"] = len(final_pending)
        summary["terminal_unavailable_sessions"] = terminal_unavailable
        summary["cumulative_returned_symbol_sessions"] = cumulative_returned
        summary["cumulative_missing_symbol_sessions"] = cumulative_missing
        summary["cumulative_failed_symbol_sessions"] = cumulative_failed
        summary["history_start_unknown_symbols"] = len(unknown_history) if isinstance(unknown_history, list) else -1
        summary["outside_free_window_unassessed_symbols"] = len(outside_unassessed) if isinstance(outside_unassessed, list) else -1
        summary["coverage_status"] = "complete" if not (final_pending or terminal_unavailable or cumulative_missing or cumulative_failed or summary["history_start_unknown_symbols"] or summary["outside_free_window_unassessed_symbols"]) else "unresolved"
        summary["finished_at"] = _utc_now().isoformat()
        _atomic_json(folder / "latest.json", summary)
        return summary


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--security-master", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path, help="Plain Massive API-key file")
    parser.add_argument("--authorized-start", required=True, type=_parse_date, help="Explicit historical boundary authorized for acquisition")
    parser.add_argument("--max-sessions", type=int, default=1, help="Bounded XNYS sessions per invocation; resume by rerunning")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_massive_backfill(args.data_root, args.security_master, args.credential_file, authorized_start=args.authorized_start, max_sessions=args.max_sessions)
    except (MassiveBackfillError, OSError):
        print(json.dumps({"status": "failed", "error_code": "massive_backfill_failed"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, default=str))
    return 0 if result["status"] in {"complete", "partial", "skipped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
