"""Publish recent, current-only Massive alias bars into an isolated namespace."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import tempfile
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .massive_publish import _load_snapshot
from .massive_reference_audit import _load_reference, classify_master_code
from .pipeline import symbol_key
from .symbol_mapping import _listing_rows
from .sync_eligibility import select_sync_symbols
from .sync_scheduler import resolve_sync_master
from .sync_scheduler import completed_session


NAMESPACE = "massive-current-alias-daily-v1"
SCHEMA = "massive-current-alias-publication-v1"
_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "available_at", "retrieved_at", "actions_status", "source", "adjustment_status", "adjusted", "availability_policy", "historical_identity", "research_qualified", "provider_symbol", "mapping_report_sha256")


class MassiveAliasPublishError(ValueError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent); temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, sort_keys=True, indent=2, default=str); handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False); os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() in {".csv", ".tsv"} else pd.read_parquet(path)


def _parse_time(value: Any, code: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        if parsed is None or parsed.tzinfo is None: raise ValueError
        return parsed.astimezone(timezone.utc)
    except ValueError as exc:
        raise MassiveAliasPublishError(code) from exc


def _safe_input(root: Path, value: Path, code: str, *, directory: bool = False) -> Path:
    raw = Path(value); path = raw if raw.is_absolute() else root / raw
    try:
        resolved_root, resolved = root.resolve(strict=True), path.resolve(strict=True)
    except OSError as exc: raise MassiveAliasPublishError(code) from exc
    if path.is_symlink() or resolved_root not in resolved.parents or (not resolved.is_dir() if directory else not resolved.is_file()):
        raise MassiveAliasPublishError(code)
    cursor = path
    while cursor != root and cursor != cursor.parent:
        if cursor.is_symlink(): raise MassiveAliasPublishError(code)
        cursor = cursor.parent
    return resolved


def _resolve_report_path(root: Path, value: Path | None) -> Path:
    if value is not None: return _safe_input(root, value, "alias_report_path_invalid")
    pointer = _safe_input(root, root / "catalogue" / "current-massive-alias-report-v1.json", "alias_report_pointer_invalid")
    try: payload = json.loads(pointer.read_text()); relative, expected = payload["report_relative_path"], payload["report_sha256"]
    except (OSError, ValueError, TypeError, KeyError) as exc: raise MassiveAliasPublishError("alias_report_pointer_invalid") from exc
    report = _safe_input(root, root / str(relative), "alias_report_pointer_invalid")
    if not isinstance(expected, str) or _sha(report) != expected: raise MassiveAliasPublishError("alias_report_pointer_invalid")
    return report


def _load_alias_report(path: Path | None, root: Path, master_sha: str) -> tuple[dict[str, str], dict[str, Any], datetime, datetime]:
    path = _resolve_report_path(root, path)
    report_path = _safe_input(root, Path(path), "alias_report_path_invalid"); manifest_path = _safe_input(root, report_path.parent / "manifest.json", "alias_report_path_invalid")
    try:
        report, manifest = json.loads(report_path.read_text()), json.loads(manifest_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise MassiveAliasPublishError("alias_report_unreadable") from exc
    if (not isinstance(report, dict) or not isinstance(manifest, dict) or report.get("schema_version") != "massive-reference-alias-audit-v1"
            or manifest.get("status") != "captured" or manifest.get("report_sha256") != _sha(report_path)):
        raise MassiveAliasPublishError("alias_report_integrity_invalid")
    inputs = report.get("inputs")
    if not isinstance(inputs, dict) or inputs.get("security_master_sha256") != master_sha:
        raise MassiveAliasPublishError("alias_report_master_mismatch")
    reference_path = _safe_input(root, Path(str(inputs.get("reference_snapshot", ""))), "alias_report_reference_path_invalid", directory=True)
    reference, ref_manifest = _load_reference(reference_path)
    if inputs.get("reference_all_tickers_sha256") != ref_manifest.get("all_tickers_sha256") or inputs.get("reference_page_hashes") != {str(page["raw_relative_path"]): page["sha256"] for page in ref_manifest["pages"]}:
        raise MassiveAliasPublishError("alias_report_reference_mismatch")
    try:
        listing_path = _safe_input(root, Path(str(inputs.get("listing_snapshot", ""))), "alias_report_listing_path_invalid", directory=True)
        listings, listing_hashes, _ = _listing_rows(listing_path)
    except ValueError as exc:
        raise MassiveAliasPublishError("alias_report_listing_invalid") from exc
    if inputs.get("listing_sha256") != listing_hashes:
        raise MassiveAliasPublishError("alias_report_listing_mismatch")
    raw_aliases: list[tuple[str, str]] = []
    for item in report.get("items", []):
        if not isinstance(item, dict) or item.get("classification") != "authoritative_alias_candidate": continue
        symbol, candidates = item.get("symbol"), item.get("candidates")
        if not isinstance(symbol, str) or not isinstance(candidates, list) or len(candidates) != 1: continue
        provider = candidates[0].get("reference_ticker") if isinstance(candidates[0], dict) else None
        if not isinstance(provider, str) or not provider or provider not in reference: continue
        # Recompute the exact official CQS/NASDAQ alias + MIC predicate; the
        # report is evidence, never a substitute for validating its inputs.
        recomputed = classify_master_code(symbol, reference, listings)
        if recomputed.get("classification") != "authoritative_alias_candidate" or recomputed.get("candidates", [{}])[0].get("reference_ticker") != provider: continue
        raw_aliases.append((symbol, provider))
    provider_counts = {provider: sum(item_provider == provider for _, item_provider in raw_aliases) for _, provider in raw_aliases}
    symbol_counts = {symbol: sum(item_symbol == symbol for item_symbol, _ in raw_aliases) for symbol, _ in raw_aliases}
    aliases = {symbol: provider for symbol, provider in raw_aliases if provider_counts[provider] == 1 and symbol_counts[symbol] == 1}
    if not aliases: raise MassiveAliasPublishError("alias_report_no_unique_candidates")
    report_time = _parse_time(report.get("observed_at"), "alias_report_observation_invalid")
    ref_time = _parse_time(ref_manifest.get("observed_end"), "reference_observation_invalid")
    listing_time = _parse_time(json.loads((listing_path / "gap-evidence.json").read_text()).get("observed_at"), "listing_observation_invalid")
    report["validated_listing_observed_at"] = listing_time.isoformat()
    return aliases, report, report_time, ref_time


def _validate_window(target: date, clock: datetime, times: list[datetime]) -> None:
    if target > completed_session(clock) or (clock.date() - target).days > 31 or any(t > clock for t in times) or any(abs((target - t.date()).days) > 31 for t in times):
        raise MassiveAliasPublishError("alias_evidence_outside_31_day_window")


def load_current_aliases(root: Path, master_sha: str, target: date, now: datetime | None = None) -> tuple[dict[str, str], str]:
    """Load pointer-selected aliases only when target/report/reference are current."""
    clock = now or datetime.now(timezone.utc); aliases, report, report_time, ref_time = _load_alias_report(None, root, master_sha)
    _validate_window(target, clock, [report_time, ref_time, _parse_time(report["validated_listing_observed_at"], "listing_observation_invalid")])
    return aliases, _sha(_resolve_report_path(root, None))


def _safe_destination(root: Path, symbol: str) -> Path:
    base = root / "bars" / "daily"; path = base / "provider=massive" / f"namespace={NAMESPACE}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
    cursor = base
    if any(p.is_symlink() for p in (root, root / "bars", base)): raise MassiveAliasPublishError("publication_symlink_rejected")
    for part in path.relative_to(base).parts:
        cursor /= part
        if cursor.is_symlink(): raise MassiveAliasPublishError("publication_symlink_rejected")
    if base.resolve() not in path.parent.resolve().parents: raise MassiveAliasPublishError("publication_path_invalid")
    return path


def _published(frame: pd.DataFrame, symbol: str, provider: str, available: str, report_sha: str) -> pd.DataFrame:
    row = frame.loc[frame["symbol"] == provider].copy()
    row["symbol"] = symbol; row["available_at"] = available; row["source"] = "Massive current official-alias observation"
    row["adjustment_status"] = "raw_unadjusted"; row["adjusted"] = False; row["availability_policy"] = "observed_ingestion_and_current_alias_only"
    row["historical_identity"] = "unknown"; row["research_qualified"] = False; row["provider_symbol"] = provider; row["mapping_report_sha256"] = report_sha
    return row.loc[:, _COLUMNS]


def _validate_existing(frame: pd.DataFrame, symbol: str, provider: str) -> pd.DataFrame:
    if tuple(frame.columns) != _COLUMNS or frame.empty or not frame["symbol"].eq(symbol).all() or not frame["provider_symbol"].eq(provider).all(): raise MassiveAliasPublishError("existing_alias_view_invalid")
    if (not frame["source"].eq("Massive current official-alias observation").all() or not frame["adjustment_status"].eq("raw_unadjusted").all() or not frame["adjusted"].eq(False).all() or not frame["historical_identity"].eq("unknown").all() or not frame["research_qualified"].eq(False).all()): raise MassiveAliasPublishError("existing_alias_view_invalid")
    try:
        dates = pd.to_datetime(frame["date"], errors="raise"); availability = pd.to_datetime(frame["available_at"], errors="raise", utc=True); values = frame[["open","high","low","close","volume"]].apply(pd.to_numeric, errors="coerce")
    except (ValueError, TypeError, KeyError) as exc: raise MassiveAliasPublishError("existing_alias_view_invalid") from exc
    if dates.isna().any() or dates.duplicated().any() or availability.isna().any() or not values.map(math.isfinite).all().all() or (values[["open","high","low","close"]] <= 0).any().any() or (values["volume"] < 0).any() or (values["high"] < values[["open","low","close"]].max(axis=1)).any() or (values["low"] > values[["open","high","close"]].min(axis=1)).any(): raise MassiveAliasPublishError("existing_alias_view_invalid")
    return frame


def _update_catalogue(root: Path, successful: list[tuple[str, Path, pd.DataFrame]]) -> int:
    """Replace this isolated namespace in one locked catalogue write."""
    if not successful: return 0
    destination = root / "catalogue" / "us-daily-browser-v1.json"
    if not destination.exists() or destination.is_symlink(): raise MassiveAliasPublishError("browser_catalogue_missing")
    with (destination.parent / "us-daily-browser-refresh.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try: payload = json.loads(destination.read_text())
        except (OSError, ValueError, TypeError) as exc: raise MassiveAliasPublishError("browser_catalogue_invalid") from exc
        if payload.get("schema_version") != "us-daily-browser-v1" or not isinstance(payload.get("series"), list): raise MassiveAliasPublishError("browser_catalogue_invalid")
        changed = {symbol for symbol, _, _ in successful}; entries = [entry for entry in payload["series"] if not (isinstance(entry, dict) and entry.get("provider") == "massive" and entry.get("namespace") == NAMESPACE and entry.get("symbol") in changed)]
        raw_root = root / "bars" / "daily"
        for symbol, path, frame in successful:
            dates = pd.to_datetime(frame["date"], errors="raise")
            entries.append({"series_id": f"massive:{NAMESPACE}:{symbol_key(symbol)}", "symbol": symbol, "provider": "massive", "namespace": NAMESPACE,
                            "raw_relative_path": path.relative_to(raw_root).as_posix(), "first_date": dates.min().date().isoformat(), "last_date": dates.max().date().isoformat(), "rows": len(frame),
                            "quality_warning": "Massive current official-alias acquisition aid; raw unadjusted, historical identity unknown, not research/backtest qualified."})
        payload["series"] = sorted(entries, key=lambda x: (str(x.get("symbol", "")), str(x.get("last_date", ""))))
        _atomic_json(destination, payload)
    return len(successful)


def publish_massive_aliases(data_root: Path, security_master: Path, alias_report: Path | None, snapshots: list[Path], *, now: datetime | None = None) -> dict[str, Any]:
    """Apply verified current aliases to recent acquisition bars, never research identity."""
    root = Path(data_root); clock = now or datetime.now(timezone.utc)
    folder = root / "manifests" / NAMESPACE; folder.mkdir(parents=True, exist_ok=True)
    with (folder / "publish.lock").open("a+") as lock:
        try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: return {"schema_version": SCHEMA, "status": "skipped", "reason": "lock_held"}
        try:
            master_path = _safe_input(root, resolve_sync_master(root, Path(security_master)), "security_master_path_invalid"); master_sha = _sha(master_path); master = _read_master(master_path)
            if _sha(master_path) != master_sha: raise ValueError
            eligible = set(select_sync_symbols(master))
        except (OSError, ValueError, ImportError) as exc: raise MassiveAliasPublishError("security_master_invalid") from exc
        aliases, report, report_time, ref_time = _load_alias_report(alias_report, root, master_sha)
        aliases = {symbol: provider for symbol, provider in aliases.items() if symbol in eligible}
        if not aliases: raise MassiveAliasPublishError("no_eligible_unique_aliases")
        report_path = _resolve_report_path(root, alias_report); report_sha = _sha(report_path); inputs = {"alias_report_sha256": report_sha, "security_master_sha256": master_sha, "aliases": aliases}
        by_symbol: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in aliases}; summary = {"schema_version": SCHEMA, "status": "running", "matched": len(aliases), "returned": 0, "missing": 0, "published": 0, "skipped": 0, "failed": 0}
        records: list[dict[str, Any]] = []; requested_dates: list[str] = []
        for snapshot in snapshots:
            try:
                snapshot_path = _safe_input(root, Path(snapshot), "snapshot_path_invalid", directory=True); manifest_path = _safe_input(root, snapshot_path / "manifest.json", "snapshot_path_invalid"); manifest_raw = json.loads(manifest_path.read_text()); target = date.fromisoformat(manifest_raw["request"]["date"])
                manifest, frame = _load_snapshot(root, snapshot_path, target); observed = _parse_time(manifest["observed_at"], "snapshot_observation_invalid")
                listing_time = _parse_time(report["validated_listing_observed_at"], "listing_observation_invalid")
                _validate_window(target, clock, [observed, report_time, ref_time, listing_time])
                availability = max(observed, report_time, ref_time, listing_time).isoformat()
                requested_dates.append(target.isoformat())
                for symbol, provider in aliases.items():
                    incoming = _published(frame, symbol, provider, availability, report_sha)
                    if incoming.empty:
                        summary["missing"] += 1; records.append({"symbol": symbol, "provider_symbol": provider, "date": target.isoformat(), "status": "missing", "retirement_inference": "none"})
                    else:
                        by_symbol[symbol].append(incoming); summary["returned"] += 1
            except (MassiveAliasPublishError, ValueError, OSError, KeyError, ImportError) as exc:
                raise MassiveAliasPublishError(str(exc) if isinstance(exc, MassiveAliasPublishError) else "snapshot_invalid") from exc
        successful: list[tuple[str, Path, pd.DataFrame]] = []
        for symbol, frames in by_symbol.items():
            if not frames: continue
            destination = _safe_destination(root, symbol)
            try:
                incoming = pd.concat(frames, ignore_index=True)
                if destination.exists():
                    if destination.is_symlink(): raise MassiveAliasPublishError("publication_symlink_rejected")
                    existing = pd.read_parquet(destination)
                    try: existing = _validate_existing(existing, symbol, aliases[symbol])
                    except MassiveAliasPublishError:
                        quarantine = root / "quarantine" / NAMESPACE / f"{symbol_key(symbol)}-{uuid.uuid4().hex}.parquet"; quarantine.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(destination, quarantine)
                        raise MassiveAliasPublishError("existing_alias_view_invalid")
                    overlap = existing.merge(incoming, on="date", suffixes=("_old", "_new"))
                    for field in ("open", "high", "low", "close", "volume"):
                        if not overlap.empty and not overlap[f"{field}_old"].eq(overlap[f"{field}_new"]).all(): raise MassiveAliasPublishError("existing_alias_price_conflict")
                    incoming = pd.concat([existing, incoming], ignore_index=True)
                incoming = incoming.drop_duplicates("date", keep="first").sort_values("date").reset_index(drop=True)
                if destination.exists() and len(existing) == len(incoming) and existing.sort_values("date").reset_index(drop=True).equals(incoming):
                    successful.append((symbol, destination, existing))
                    summary["skipped"] += 1; records.append({"symbol": symbol, "provider_symbol": aliases[symbol], "status": "skipped", "rows": len(existing), "requested_start": str(existing.date.min()), "requested_end": str(existing.date.max()), "output_sha256": _sha(destination), "mapping_report_sha256": report_sha}); continue
                _atomic_parquet(incoming, destination); successful.append((symbol, destination, incoming)); summary["published"] += 1
                records.append({"symbol": symbol, "provider_symbol": aliases[symbol], "status": "success", "rows": len(incoming), "requested_start": str(incoming.date.min()), "requested_end": str(incoming.date.max()), "output_sha256": _sha(destination), "mapping_report_sha256": report_sha})
            except (OSError, ValueError, ImportError, MassiveAliasPublishError) as exc:
                summary["failed"] += 1; records.append({"symbol": symbol, "provider_symbol": aliases[symbol], "status": "failed", "error_code": str(exc) if isinstance(exc, MassiveAliasPublishError) else "storage_failed"})
        try: summary["catalogue_updated"] = _update_catalogue(root, successful)
        except MassiveAliasPublishError as exc: summary["catalogue_updated"] = 0; summary["failed"] += 1; summary["catalogue_error"] = str(exc)
        for record in records:
            request_range = {"requested_start": min(requested_dates), "requested_end": max(requested_dates)} if requested_dates else {}
            with (folder / "records.jsonl").open("a", encoding="utf-8") as handle: handle.write(json.dumps({**request_range, **record, "schema_version": SCHEMA, "research_qualified": False}, sort_keys=True) + "\n")
        summary["status"] = "success" if not summary["failed"] else "partial"; summary["finished_at"] = datetime.now(timezone.utc).isoformat(); summary["inputs"] = inputs
        source_snapshots = []
        for snapshot in snapshots:
            try:
                raw = json.loads((_safe_input(root, Path(snapshot), "snapshot_path_invalid", directory=True) / "manifest.json").read_text())
                source_snapshots.append({"snapshot_relative_path": _safe_input(root, Path(snapshot), "snapshot_path_invalid", directory=True).relative_to(root).as_posix(), "date": raw["request"]["date"], "observed_at": raw["observed_at"], "response_sha256": raw["response_sha256"], "normalized_sha256": raw["normalized_sha256"]})
            except (OSError, ValueError, KeyError, MassiveAliasPublishError): raise MassiveAliasPublishError("publication_snapshot_lineage_invalid")
        publication = {"schema_version": SCHEMA, "status": summary["status"], "created_at": summary["finished_at"], "inputs": {**inputs, "reference_sources": report.get("inputs", {}), "snapshots": source_snapshots}, "summary": {key: value for key, value in summary.items() if key != "inputs"}, "research_qualified": False}
        _atomic_json(folder / "publications" / f"{uuid.uuid4().hex}.json", publication)
        _atomic_json(folder / "latest.json", summary)
        return summary


def _recent_snapshots(root: Path, now: datetime) -> list[Path]:
    base = root / "reference" / "massive-daily-v1"; result = []
    for path in base.glob("*/manifest.json") if base.exists() else []:
        try:
            value = json.loads(path.read_text()); target = date.fromisoformat(value["request"]["date"])
            if value.get("status") == "captured" and target <= now.date() and (now.date() - target).days <= 31: result.append(path.parent)
        except (OSError, ValueError, MassiveAliasPublishError): continue
    return sorted(result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument("--data-root", required=True, type=Path); parser.add_argument("--security-master", required=True, type=Path); parser.add_argument("--alias-report", type=Path, help="Explicit verified report; defaults to current alias-report pointer")
    group = parser.add_mutually_exclusive_group(required=True); group.add_argument("--snapshot", action="append", type=Path); group.add_argument("--all-captured-recent", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv); now = datetime.now(timezone.utc); snapshots = _recent_snapshots(args.data_root, now) if args.all_captured_recent else args.snapshot
    try: result = publish_massive_aliases(args.data_root, args.security_master, args.alias_report, snapshots or [], now=now)
    except (MassiveAliasPublishError, OSError): print(json.dumps({"status": "failed", "error_code": "massive_alias_publish_failed"})); return 2
    print(json.dumps({k: v for k, v in result.items() if k != "inputs"}, sort_keys=True, default=str)); return 0 if result["status"] in {"success", "partial", "skipped"} else 2


if __name__ == "__main__": raise SystemExit(main())
