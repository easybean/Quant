"""Freeze five-stock Massive evidence, with explicit non-qualification blockers."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import uuid4

import pandas as pd
import exchange_calendars as calendars

from .massive_publish import _load_snapshot
from .massive_reference_audit import _load_reference
from .sync_scheduler import completed_session

SYMBOLS = ("AAPL", "MSFT", "AMZN", "GOOGL", "TSLA")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def safe(root: Path, path: Path) -> Path:
    if root.resolve() not in path.resolve().parents:
        raise ValueError("input_outside_root")
    cursor = path
    while cursor != root and cursor != cursor.parent:
        if cursor.is_symlink(): raise ValueError("input_symlink")
        cursor = cursor.parent
    return path


def build(root: Path, output: Path, start: date, end: date, *, now: datetime | None = None) -> dict:
    clock = now or datetime.now(timezone.utc)
    if output.exists() or start > end or end > completed_session(clock):
        raise ValueError("new_output_and_completed_ordered_range_required")
    if clock.tzinfo is None or clock.utcoffset().total_seconds() != 0:
        raise ValueError("clock_must_be_utc")
    calendar = calendars.get_calendar("XNYS")
    sessions = calendar.sessions_in_range(start.isoformat(), end.isoformat())
    if not len(sessions): raise ValueError("range_has_no_sessions")
    schedule = [{"session_date": s.date().isoformat(), "scheduled_open": calendar.session_open(s).isoformat(),
                 "scheduled_close": calendar.session_close(s).isoformat()} for s in sessions]
    dates = {s["session_date"] for s in schedule}
    by_date = {s["session_date"]: s for s in schedule}
    inputs, selected = [], {}
    for manifest_path in sorted((root / "reference/massive-daily-v1").glob("*/manifest.json")):
        safe(root, manifest_path)
        manifest = json.loads(manifest_path.read_text())
        day = manifest.get("request", {}).get("date")
        if day not in dates or manifest.get("status") != "captured": continue
        before = sha(manifest_path)
        manifest, frame = _load_snapshot(root, manifest_path.parent, date.fromisoformat(day))
        observed = datetime.fromisoformat(manifest["observed_at"])
        if observed.tzinfo is None or observed > clock: raise ValueError("invalid_capture_observation")
        inputs.append({"relative_path": manifest_path.parent.relative_to(root).as_posix(), "manifest_sha256": before,
                       "response_sha256": manifest["response_sha256"], "normalized_sha256": manifest["normalized_sha256"],
                       "session_date": day, "observed_at": observed.isoformat()})
        for symbol in SYMBOLS:
            rows = frame.loc[frame.symbol.eq(symbol)]
            if rows.empty: continue
            if len(rows) != 1: raise ValueError("ambiguous_symbol_date")
            record = rows.iloc[0].to_dict()
            record.update(session_date=day, source_snapshot=inputs[-1]["relative_path"],
                          scheduled_close_reference=by_date[day]["scheduled_close"], event_time=None,
                          event_time_status="scheduled_close_is_not_independent_event_time_evidence",
                          available_at=observed.isoformat(), ingest_time=observed.isoformat())
            key = (symbol, day)
            if key in selected:
                if any(record[k] != selected[key][k] for k in ("open", "high", "low", "close", "volume")):
                    raise ValueError("source_revision_conflict_requires_review")
                if record["available_at"] >= selected[key]["available_at"]: continue
            selected[key] = record
        if sha(manifest_path) != before: raise ValueError("manifest_changed_during_capture")
    if not selected: raise ValueError("no_massive_rows_for_cohort")
    pointer_path = safe(root, root / "catalogue/current-massive-reference-v1.json")
    pointer_sha = sha(pointer_path)
    pointer = json.loads(pointer_path.read_text())
    reference_path = safe(root, root / pointer["snapshot_relative_path"])
    reference_sha = sha(reference_path / "manifest.json")
    reference, reference_manifest = _load_reference(reference_path)
    if sha(pointer_path) != pointer_sha or sha(reference_path / "manifest.json") != reference_sha:
        raise ValueError("reference_changed")
    members = []
    for symbol in SYMBOLS:
        row = reference.get(symbol)
        if not row or row.get("active") is not True or row.get("type") != "CS" or row.get("locale") != "us":
            raise ValueError("missing_or_invalid_current_common_stock_identity")
        members.append({"symbol": symbol, "current_reference": row, "historical_identity_verified": False})
    gaps = [{"symbol": symbol, "missing_sessions": sorted(dates - {d for s, d in selected if s == symbol})} for symbol in SYMBOLS]
    bars = [selected[k] for k in sorted(selected)]
    historical_unavailable = sum(datetime.fromisoformat(b["available_at"]) > datetime.fromisoformat(by_date[b["session_date"]]["scheduled_close"]) for b in bars)
    prospective = calendar.sessions_in_range(clock.date().isoformat(), (clock.date() + pd.Timedelta(days=60)).isoformat())
    future = [s for s in prospective if calendar.session_open(s).to_pydatetime() > clock][:20]
    report = {"schema_version": "bounded-backtest-candidate-v1", "snapshot_id": output.name,
              "frozen_at": clock.isoformat(), "qualified": False, "formal_backtest_enabled": False,
              "provider": "massive", "price_basis": "raw_unadjusted", "range": {"start": start.isoformat(), "end": end.isoformat()},
              "symbols": list(SYMBOLS), "rows": len(bars), "expected_rows": len(dates) * len(SYMBOLS), "coverage": gaps,
              "historical_close_decision_unavailable_rows": historical_unavailable,
              "reference": {"relative_path": reference_path.relative_to(root).as_posix(), "manifest_sha256": reference_sha,
                            "all_tickers_sha256": reference_manifest["all_tickers_sha256"]},
              "calendar": {"name": "XNYS", "library": "exchange_calendars", "version": calendars.__version__,
                           "scheduled_times_only": True},
              "prospective_observation": {"cohort_locked_at": clock.isoformat(), "sessions": [s.date().isoformat() for s in future],
                                          "completed": False, "purpose": "engineering evidence, not alpha validation"},
              "blockers": ["historical identity lifecycle unverified", "event_time evidence unverified",
                           "corporate actions and delisting applicability require independent review", "license evidence unreviewed",
                           "real execution/cost engine not accepted"] + (["missing stock sessions"] if any(g["missing_sessions"] for g in gaps) else [])
                          + (["historical bars were observed after historical decisions; no PIT backdating permitted"] if historical_unavailable else []),
              "inputs": inputs}
    output.mkdir(parents=True, exist_ok=False)
    for name, payload in (("bars.json", bars), ("calendar.json", schedule), ("members.json", members), ("report.json", report)):
        with (output / name).open("x") as handle: json.dump(payload, handle, indent=2, allow_nan=False, default=str)
    with (output / "manifest.json").open("x") as handle:
        json.dump({"schema_version": report["schema_version"], "qualified": False, "frozen_at": clock.isoformat(),
                   "files": {name: sha(output / name) for name in ("bars.json", "calendar.json", "members.json", "report.json")}}, handle, indent=2)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    args = parser.parse_args()
    report = build(args.data_root, args.output, args.start, args.end)
    print(json.dumps({k: report[k] for k in ("snapshot_id", "rows", "expected_rows", "coverage", "historical_close_decision_unavailable_rows", "qualified", "blockers")}))
