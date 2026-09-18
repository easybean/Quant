"""Read-only, bounded point-in-time checks for proposed US daily-bar rows.

This deliberately validates only a small JSON payload's row-level shape and
timestamp ordering.  It cannot establish historical identity, corporate-action
coverage, exchange-calendar correctness, licensing, or research eligibility.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import stat
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "bounded-pit-audit-v1"
MAX_JSON_BYTES = 16 * 1024 * 1024
_UTC_OFFSET = timedelta(0)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WARNINGS = (
    "Stable instrument identity was not independently verified.",
    "Corporate actions were not independently verified.",
    "Trading calendar and session validity were not independently verified.",
    "License and source permissions were not independently verified.",
)


def audit_rows(input_path: str | Path) -> dict[str, Any]:
    """Audit one local JSON payload without modifying data or any gate.

    The file is capped before parsing and symlink inputs are refused.  The
    returned ``qualified`` flag is always false, even when every bounded row
    check passes.
    """
    blockers: list[str] = []
    payload = _read_payload(Path(input_path), blockers)
    if not isinstance(payload, dict):
        blockers.append("input root must be an object")
        return _result(blockers)

    if payload.get("schema_version") != SCHEMA_VERSION:
        blockers.append(f"schema_version must be {SCHEMA_VERSION!r}")

    cohort_locked_at = _utc_timestamp(payload.get("cohort_locked_at"), "cohort_locked_at", blockers)
    decision_time = _utc_timestamp(payload.get("decision_time"), "decision_time", blockers)
    if cohort_locked_at is not None and decision_time is not None and decision_time < cohort_locked_at:
        blockers.append("decision_time must not precede cohort_locked_at")

    members = _members(payload.get("members"), blockers)
    _bars(payload.get("bars"), members, decision_time, blockers)
    return _result(blockers)


def _result(blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "row_checks_passed": not blockers,
        "qualified": False,
        "blockers": blockers,
        "warnings": list(_WARNINGS),
    }


def _read_payload(path: Path, blockers: list[str]) -> Any:
    try:
        path_stat = path.lstat()
        if stat.S_ISLNK(path_stat.st_mode):
            blockers.append("input path must not be a symlink")
            return None
        if not stat.S_ISREG(path_stat.st_mode):
            blockers.append("input path must be a regular file")
            return None
        if path_stat.st_size > MAX_JSON_BYTES:
            blockers.append("input JSON exceeds the 16 MiB limit")
            return None
        with path.open("rb") as handle:
            raw = handle.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            blockers.append("input JSON exceeds the 16 MiB limit")
            return None
        return json.loads(raw.decode("utf-8"), parse_constant=_reject_nonfinite_constant)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
        blockers.append("input cannot be read as valid JSON")
        return None


def _reject_nonfinite_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant: {value}")


def _utc_timestamp(value: Any, field: str, blockers: list[str]) -> datetime | None:
    if not isinstance(value, str):
        blockers.append(f"{field} must be an aware UTC timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        blockers.append(f"{field} must be an aware UTC timestamp")
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != _UTC_OFFSET:
        blockers.append(f"{field} must be an aware UTC timestamp")
        return None
    return parsed


def _date(value: Any, field: str, blockers: list[str]) -> date | None:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        blockers.append(f"{field} must be an ISO session date")
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        blockers.append(f"{field} must be an ISO session date")
        return None


def _instrument_id(value: Any, field: str, blockers: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        blockers.append(f"{field} must be a non-empty instrument_id without surrounding whitespace")
        return None
    return value


def _members(value: Any, blockers: list[str]) -> set[str]:
    if not isinstance(value, list):
        blockers.append("members must be a list")
        return set()
    if not value:
        blockers.append("members must contain at least one member")
    member_ids: set[str] = set()
    for index, member in enumerate(value):
        label = f"members[{index}]"
        if not isinstance(member, dict):
            blockers.append(f"{label} must be an object")
            continue
        instrument_id = _instrument_id(member.get("instrument_id"), f"{label}.instrument_id", blockers)
        if instrument_id is None:
            continue
        if instrument_id in member_ids:
            blockers.append(f"{label}.instrument_id duplicates a member")
            continue
        member_ids.add(instrument_id)
    return member_ids


def _bars(value: Any, members: set[str], decision_time: datetime | None, blockers: list[str]) -> None:
    if not isinstance(value, list):
        blockers.append("bars must be a list")
        return
    if not value:
        blockers.append("bars must contain at least one bar")
    seen_sessions: set[tuple[str, date]] = set()
    for index, bar in enumerate(value):
        label = f"bars[{index}]"
        if not isinstance(bar, dict):
            blockers.append(f"{label} must be an object")
            continue
        instrument_id = _instrument_id(bar.get("instrument_id"), f"{label}.instrument_id", blockers)
        session_date = _date(bar.get("session_date"), f"{label}.session_date", blockers)
        event_time = _utc_timestamp(bar.get("event_time"), f"{label}.event_time", blockers)
        available_at = _utc_timestamp(bar.get("available_at"), f"{label}.available_at", blockers)
        ingest_time = _utc_timestamp(bar.get("ingest_time"), f"{label}.ingest_time", blockers)

        if instrument_id is not None:
            if instrument_id not in members:
                blockers.append(f"{label}.instrument_id is not a member")
            if session_date is not None:
                key = (instrument_id, session_date)
                if key in seen_sessions:
                    blockers.append(f"{label} duplicates instrument_id/session_date")
                seen_sessions.add(key)
        if session_date is not None and event_time is not None and session_date > event_time.date():
            blockers.append(f"{label}.session_date must not be after event_time UTC date")
        if event_time is not None and available_at is not None and available_at < event_time:
            blockers.append(f"{label}.event_time must not be after available_at")
        if available_at is not None and ingest_time is not None and ingest_time < available_at:
            blockers.append(f"{label}.available_at must not be after ingest_time")
        if available_at is not None and decision_time is not None and available_at > decision_time:
            blockers.append(f"{label}.available_at must not be after decision_time")
        _ohlcv(bar, label, blockers)


def _ohlcv(bar: dict[str, Any], label: str, blockers: list[str]) -> None:
    values: dict[str, int | float] = {}
    for field in ("open", "high", "low", "close"):
        value = bar.get(field)
        if not _finite_number(value) or value <= 0:
            blockers.append(f"{label}.{field} must be a finite positive number")
        else:
            values[field] = value
    volume = bar.get("volume")
    if not _finite_number(volume) or volume < 0:
        blockers.append(f"{label}.volume must be a finite non-negative number")
    if len(values) == 4 and not (values["low"] <= values["open"] <= values["high"] and values["low"] <= values["close"] <= values["high"]):
        blockers.append(f"{label} OHLC values must satisfy low <= open/close <= high")


def _finite_number(value: Any) -> bool:
    """Accept only JSON numeric values representable as finite machine numbers."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        # JSON permits arbitrary-size integers, but this bounded audit uses
        # finite machine-number OHLCV inputs and must reject rather than crash.
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only bounded PIT row audit")
    parser.add_argument("input_path", type=Path)
    args = parser.parse_args(argv)
    result = audit_rows(args.input_path)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["row_checks_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
