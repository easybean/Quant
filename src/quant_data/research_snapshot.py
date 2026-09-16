"""Read-only readiness checks for versioned research-snapshot manifests.

This module deliberately validates only the manifest's declared shape and the
integrity of its local files.  It is not a data-quality, PIT, or backtest
approval mechanism.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Any


MANIFEST_SCHEMA_VERSION = "research-snapshot-v1"
_MUTABLE_VERSION_IDS = {"latest", "current", "unknown"}
_FILE_ROLES = {"bars", "identity", "membership", "corporate_actions", "delistings", "calendar"}
_REQUIRED_FILE_ROLES = {"bars", "identity", "membership", "calendar"}
_HEX_SHA256 = re.compile(r"^[0-9a-fA-F]{64}$")
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_WARNINGS = [
    "Readiness checks validate only the manifest structure and local file integrity.",
    "Declared source, identity, membership, corporate-action, and delisting claims are not independently verified.",
    "PIT and row-level data availability are not validated by this checker.",
    "This result does not open factor jobs or any backtest capability.",
]


def inspect_manifest(manifest_path: Path) -> dict[str, Any]:
    """Inspect one JSON manifest without writing, downloading, or following escapes.

    The returned ``qualified`` value is intentionally always false: passing this
    static check makes a snapshot ready for human review, never eligible for
    research returns or backtesting by itself.
    """
    blockers: list[str] = []
    snapshot_id: str | None = None
    manifest: Any = None
    try:
        with Path(manifest_path).open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        blockers.append("manifest cannot be read as a JSON object")

    if not isinstance(manifest, dict):
        blockers.append("manifest root must be an object")
        return _result(snapshot_id, blockers)

    snapshot_id = _string(manifest.get("snapshot_id"))
    _check_equal(manifest, "schema_version", MANIFEST_SCHEMA_VERSION, blockers)
    if snapshot_id is None or snapshot_id.lower() in _MUTABLE_VERSION_IDS:
        blockers.append("snapshot_id must be a fixed non-empty value, not latest/current/unknown")
    _check_equal(manifest, "market", "us_equity", blockers)
    _check_equal(manifest, "frequency", "1d", blockers)
    _check_equal(manifest, "price_basis", "raw", blockers)
    _check_fixed_version(manifest, "calendar_version", blockers)
    _check_source(manifest.get("source"), blockers)
    snapshot_range = _check_range(manifest.get("range"), blockers)
    _check_instruments(manifest.get("instruments"), snapshot_range, blockers)
    _check_availability(manifest.get("availability"), blockers)
    action_status = _check_coverage(manifest.get("corporate_actions"), "corporate_actions", blockers)
    delisting_status = _check_coverage(manifest.get("delistings"), "delistings", blockers)
    _check_files(manifest.get("files"), Path(manifest_path), action_status, delisting_status, blockers)
    return _result(snapshot_id, blockers)


def _result(snapshot_id: str | None, blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "snapshot_id": snapshot_id,
        "ready_for_review": not blockers,
        "qualified": False,
        "blockers": blockers,
        "warnings": list(_WARNINGS),
    }


def _string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _check_equal(manifest: dict[str, Any], field: str, expected: str, blockers: list[str]) -> None:
    if manifest.get(field) != expected:
        blockers.append(f"{field} must be {expected!r}")


def _check_fixed_version(manifest: dict[str, Any], field: str, blockers: list[str]) -> None:
    value = _string(manifest.get(field))
    if value is None or value.lower() in _MUTABLE_VERSION_IDS:
        blockers.append(f"{field} must be a fixed non-empty value, not latest/current/unknown")


def _check_source(source: Any, blockers: list[str]) -> None:
    if not isinstance(source, dict):
        blockers.append("source must be an object")
        return
    for field in ("provider", "namespace", "license_evidence"):
        if _string(source.get(field)) is None:
            blockers.append(f"source.{field} must be non-empty")


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _check_range(value: Any, blockers: list[str]) -> tuple[date, date] | None:
    if not isinstance(value, dict):
        blockers.append("range must be an object with ISO start and end dates")
        return None
    start, end = _parse_date(value.get("start")), _parse_date(value.get("end"))
    if start is None or end is None or start > end:
        blockers.append("range.start and range.end must be ordered ISO dates")
        return None
    return start, end


def _check_instruments(value: Any, snapshot_range: tuple[date, date] | None, blockers: list[str]) -> None:
    if not isinstance(value, list) or not value:
        blockers.append("instruments must be a non-empty list")
        return
    seen: set[str] = set()
    for index, item in enumerate(value):
        label = f"instruments[{index}]"
        if not isinstance(item, dict):
            blockers.append(f"{label} must be an object")
            continue
        instrument_id = _string(item.get("instrument_id"))
        if instrument_id is None:
            blockers.append(f"{label}.instrument_id must be non-empty")
        elif instrument_id in seen:
            blockers.append(f"{label}.instrument_id must be unique")
        else:
            seen.add(instrument_id)
        for field in ("symbol", "identity_evidence"):
            if _string(item.get(field)) is None:
                blockers.append(f"{label}.{field} must be non-empty")
        start, end = _parse_date(item.get("membership_start")), _parse_date(item.get("membership_end"))
        if start is None or end is None or start > end:
            blockers.append(f"{label}.membership_start and membership_end must be ordered ISO dates")
        elif snapshot_range is not None and (end < snapshot_range[0] or start > snapshot_range[1]):
            blockers.append(f"{label} membership period must overlap the snapshot range")


def _check_availability(value: Any, blockers: list[str]) -> None:
    if not isinstance(value, dict):
        blockers.append("availability must be an object")
        return
    if value.get("policy") != "explicit_available_time":
        blockers.append("availability.policy must be 'explicit_available_time'")
    if _string(value.get("evidence")) is None:
        blockers.append("availability.evidence must be non-empty")


def _check_coverage(value: Any, field: str, blockers: list[str]) -> str | None:
    if not isinstance(value, dict):
        blockers.append(f"{field} must be an object")
        return None
    status = value.get("status")
    if not isinstance(status, str) or status not in {"covered", "not_applicable"}:
        blockers.append(f"{field}.status must be 'covered' or 'not_applicable'")
        return None
    if _string(value.get("evidence")) is None:
        blockers.append(f"{field}.evidence must be non-empty")
    if status == "not_applicable" and _string(value.get("rationale")) is None:
        blockers.append(f"{field}.rationale must be non-empty when status is not_applicable")
    return status


def _check_files(value: Any, manifest_path: Path, action_status: str | None, delisting_status: str | None, blockers: list[str]) -> None:
    if not isinstance(value, list) or not value:
        blockers.append("files must be a non-empty list")
        return
    try:
        root = manifest_path.parent.resolve(strict=True)
    except OSError:
        blockers.append("manifest directory cannot be resolved")
        return
    roles: set[str] = set()
    for index, item in enumerate(value):
        label = f"files[{index}]"
        if not isinstance(item, dict):
            blockers.append(f"{label} must be an object")
            continue
        role = item.get("role")
        if not isinstance(role, str) or role not in _FILE_ROLES:
            blockers.append(f"{label}.role is invalid")
        else:
            roles.add(role)
        declared_hash = item.get("sha256")
        if not isinstance(declared_hash, str) or not _HEX_SHA256.fullmatch(declared_hash):
            blockers.append(f"{label}.sha256 must be a 64-character hexadecimal digest")
        candidate = _safe_file(root, item.get("path"), label, blockers)
        if candidate is not None and isinstance(declared_hash, str) and _HEX_SHA256.fullmatch(declared_hash):
            try:
                actual_hash = _sha256(candidate)
            except OSError:
                blockers.append(f"{label}.path cannot be read for integrity verification")
                continue
            if actual_hash != declared_hash.lower():
                blockers.append(f"{label}.sha256 does not match the local file")
    for role in sorted(_REQUIRED_FILE_ROLES - roles):
        blockers.append(f"files must include role {role!r}")
    if action_status == "covered" and "corporate_actions" not in roles:
        blockers.append("files must include role 'corporate_actions' when corporate_actions are covered")
    if delisting_status == "covered" and "delistings" not in roles:
        blockers.append("files must include role 'delistings' when delistings are covered")


def _safe_file(root: Path, declared_path: Any, label: str, blockers: list[str]) -> Path | None:
    if not isinstance(declared_path, str) or not declared_path:
        blockers.append(f"{label}.path must be a non-empty relative path")
        return None
    path = Path(declared_path)
    if path.is_absolute() or PureWindowsPath(declared_path).is_absolute() or ".." in path.parts:
        blockers.append(f"{label}.path must stay within the manifest directory")
        return None
    try:
        resolved = (root / path).resolve(strict=True)
        resolved.relative_to(root)
    except (OSError, RuntimeError, ValueError):
        blockers.append(f"{label}.path is missing or escapes the manifest directory")
        return None
    if not resolved.is_file():
        blockers.append(f"{label}.path must refer to a regular file")
        return None
    return resolved


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Inspect a local research snapshot manifest")
    parser.add_argument("manifest_path", type=Path)
    args = parser.parse_args(argv)
    result = inspect_manifest(args.manifest_path)
    print(json.dumps(result, sort_keys=True))
    return 0 if not result["blockers"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
