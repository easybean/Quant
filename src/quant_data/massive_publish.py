"""Publish a validated immutable Massive grouped-daily capture as raw views.

The Massive capture adapter deliberately has no current-bar side effects.  This
module is the equally narrow second step: it can publish one *already captured*
session into a provider-isolated browser view.  It is not a history repair,
identity mapping service, or a research/backtest data source.
"""
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
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from .massive_daily import ENDPOINT_TEMPLATE, NAMESPACE, MassiveDailyError, capture_massive_daily
from .pipeline import symbol_key
from .sync_eligibility import select_sync_symbols
from .sync_scheduler import completed_session, resolve_sync_master

PROVIDER = "massive"
SOURCE = "Massive"
PUBLICATION_SCHEMA = "massive-daily-publication-v1"
_CAPTURE_SCHEMA = "massive-daily-reference-v1"
_DIAGNOSTIC_SCHEMA = "massive-daily-validation-v1"
_CAPTURE_COLUMNS = ("symbol", "date", "open", "high", "low", "close", "volume", "available_at", "retrieved_at", "actions_status")
_PUBLISHED_COLUMNS = _CAPTURE_COLUMNS + ("source", "adjustment_status", "adjusted", "availability_policy", "historical_identity", "research_qualified")


class MassivePublishError(ValueError):
    """A non-sensitive, fail-closed publication error."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, default=str)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, sort_keys=True, default=str) + "\n")


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)


def _safe_file(path: Path, root: Path) -> Path:
    """Return a regular file below root without accepting link/path escapes."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise MassivePublishError("snapshot_path_outside_reference") from exc
    if not relative.parts or ".." in relative.parts:
        raise MassivePublishError("snapshot_path_invalid")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise MassivePublishError("snapshot_symlink_rejected")
    try:
        resolved_root, resolved = root.resolve(strict=True), path.resolve(strict=True)
    except OSError as exc:
        raise MassivePublishError("snapshot_file_missing") from exc
    if resolved_root not in resolved.parents or not resolved.is_file():
        raise MassivePublishError("snapshot_path_invalid")
    return resolved


def _safe_destination(path: Path, root: Path) -> None:
    """Reject links in an output path before either reading or writing it."""
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("publication_path_outside_root") from exc
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError("publication_symlink_rejected")
    if root.resolve() not in path.parent.resolve().parents and path.parent.resolve() != root.resolve():
        raise ValueError("publication_path_outside_root")


def _snapshot_path(data_root: Path, value: Path) -> Path:
    root = data_root / "reference" / NAMESPACE
    candidate = value if value.is_absolute() else data_root / value
    # A relative snapshot name is convenient, but it never means cwd.
    if not candidate.exists() and not value.is_absolute():
        candidate = root / value
    try:
        relative = candidate.relative_to(root)
    except ValueError as exc:
        raise MassivePublishError("snapshot_path_outside_reference") from exc
    if not relative.parts or ".." in relative.parts:
        raise MassivePublishError("snapshot_path_invalid")
    cursor = root
    for part in relative.parts:
        cursor /= part
        if cursor.is_symlink():
            raise MassivePublishError("snapshot_symlink_rejected")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise MassivePublishError("snapshot_missing") from exc
    if root.resolve() not in resolved.parents or not resolved.is_dir():
        raise MassivePublishError("snapshot_path_invalid")
    return resolved


def _load_snapshot(data_root: Path, snapshot: Path, target: date) -> tuple[dict[str, Any], pd.DataFrame]:
    root = data_root / "reference" / NAMESPACE
    snapshot = _snapshot_path(data_root, snapshot)
    manifest_path = _safe_file(snapshot / "manifest.json", root)
    raw_path = _safe_file(snapshot / "response.json", root)
    diagnostics_path = _safe_file(snapshot / "diagnostics.json", root)
    bars_path = _safe_file(snapshot / "bars.parquet", root)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        diagnostics = json.loads(diagnostics_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MassivePublishError("snapshot_manifest_or_diagnostics_invalid") from exc
    request = manifest.get("request") if isinstance(manifest, dict) else None
    if not isinstance(request, dict) or manifest.get("schema_version") != _CAPTURE_SCHEMA or manifest.get("namespace") != NAMESPACE:
        raise MassivePublishError("snapshot_manifest_schema_invalid")
    if (manifest.get("status") != "captured" or manifest.get("qualified") is not False or manifest.get("research_qualified") is not False
            or manifest.get("actions_status") != "unknown" or request.get("date") != target.isoformat()
            or request.get("adjusted") is not False or request.get("include_otc") is not False
            or request.get("method") != "GET" or manifest.get("source") != ENDPOINT_TEMPLATE
            or request.get("url") != ENDPOINT_TEMPLATE.format(date=target.isoformat())):
        raise MassivePublishError("snapshot_manifest_contract_invalid")
    if not isinstance(diagnostics, dict) or diagnostics.get("schema_version") != _DIAGNOSTIC_SCHEMA:
        raise MassivePublishError("snapshot_diagnostics_schema_invalid")
    hashes = (("response_sha256", raw_path), ("diagnostics_sha256", diagnostics_path), ("normalized_sha256", bars_path))
    for field, path in hashes:
        value = manifest.get(field)
        if not isinstance(value, str) or len(value) != 64 or _sha256(path) != value:
            raise MassivePublishError("snapshot_integrity_invalid")
    observed_at = manifest.get("observed_at")
    try:
        observed = datetime.fromisoformat(observed_at) if isinstance(observed_at, str) else None
    except ValueError:
        observed = None
    if observed is None or observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
        raise MassivePublishError("snapshot_observation_time_invalid")
    try:
        frame = pd.read_parquet(bars_path)
    except (OSError, ValueError, ImportError) as exc:
        raise MassivePublishError("snapshot_bars_unreadable") from exc
    if tuple(frame.columns) != _CAPTURE_COLUMNS or frame.empty:
        raise MassivePublishError("snapshot_bars_schema_invalid")
    accepted = diagnostics.get("accepted_rows")
    if (not isinstance(accepted, list) or len(accepted) != len(frame)
            or diagnostics.get("accepted_count") != len(frame) or not isinstance(diagnostics.get("rejected_rows"), list)):
        raise MassivePublishError("snapshot_diagnostics_contract_invalid")
    numeric_columns = ["open", "high", "low", "close", "volume"]
    try:
        dates = pd.to_datetime(frame["date"], errors="raise").dt.date
        values = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    except (KeyError, ValueError, TypeError) as exc:
        raise MassivePublishError("snapshot_bars_schema_invalid") from exc
    if (dates.ne(target).any() or not frame["symbol"].map(lambda value: isinstance(value, str) and bool(value) and value.strip() == value).all()
            or frame["symbol"].duplicated().any() or not values.notna().all().all()
            or not frame[numeric_columns].apply(lambda column: column.map(lambda value: not isinstance(value, bool))).all().all()
            or not values.apply(lambda column: column.map(lambda x: math.isfinite(float(x)))).all().all()
            or (values[["open", "high", "low", "close"]] <= 0).any().any() or (values["volume"] < 0).any()
            or (values["high"] < values[["open", "low", "close"]].max(axis=1)).any()
            or (values["low"] > values[["open", "high", "close"]].min(axis=1)).any()
            or not frame["actions_status"].eq("unknown").all()
            or not frame["available_at"].eq(observed_at).all() or not frame["retrieved_at"].eq(observed_at).all()):
        raise MassivePublishError("snapshot_bars_contract_invalid")
    return manifest, frame


def _publication_pointer(manifest_root: Path, target: date) -> Path:
    return manifest_root / "published" / f"{target.isoformat()}.json"


def _reusable_snapshot(data_root: Path, target: date, security_master_sha256: str) -> Path | None:
    pointer = _publication_pointer(data_root / "manifests" / NAMESPACE, target)
    if not pointer.exists() or pointer.is_symlink():
        return None
    try:
        payload = json.loads(pointer.read_text(encoding="utf-8"))
        rel = payload["snapshot_relative_path"]
        if (payload.get("schema_version") != PUBLICATION_SCHEMA or payload.get("status") != "success"
                or payload.get("target_date") != target.isoformat() or not isinstance(rel, str)):
            return None
        inputs = payload.get("inputs")
        if not isinstance(inputs, dict) or inputs.get("security_master_sha256") != security_master_sha256:
            return None
        snapshot = data_root / rel
        manifest, _ = _load_snapshot(data_root, snapshot, target)
        if payload.get("snapshot_response_sha256") != manifest.get("response_sha256") or payload.get("snapshot_normalized_sha256") != manifest.get("normalized_sha256"):
            return None
        return snapshot
    except (MassivePublishError, OSError, ValueError, TypeError, KeyError):
        return None


def _published_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    result = frame.loc[frame["symbol"] == symbol].copy()
    result["source"] = SOURCE
    result["adjustment_status"] = "raw_unadjusted"
    result["adjusted"] = False
    result["availability_policy"] = "observed_ingestion_only"
    result["historical_identity"] = "unknown"
    result["research_qualified"] = False
    return result.loc[:, _PUBLISHED_COLUMNS]


def _validate_existing(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if tuple(frame.columns) != _PUBLISHED_COLUMNS or frame.empty:
        raise ValueError("existing_massive_schema_invalid")
    if (not frame["source"].eq(SOURCE).all() or not frame["adjustment_status"].eq("raw_unadjusted").all()
            or not frame["adjusted"].eq(False).all() or not frame["availability_policy"].eq("observed_ingestion_only").all()
            or not frame["historical_identity"].eq("unknown").all() or not frame["research_qualified"].eq(False).all()):
        raise ValueError("existing_massive_source_invalid")
    dates = pd.to_datetime(frame["date"], errors="raise").dt.normalize()
    numeric_columns = ["open", "high", "low", "close", "volume"]
    values = frame[numeric_columns].apply(pd.to_numeric, errors="coerce")
    observations = pd.to_datetime(frame["available_at"], errors="raise", utc=True)
    retrieved = pd.to_datetime(frame["retrieved_at"], errors="raise", utc=True)
    if (dates.isna().any() or dates.duplicated().any() or not frame["symbol"].eq(symbol).all()
            or not frame[numeric_columns].apply(lambda column: column.map(lambda value: not isinstance(value, bool))).all().all()
            or not values.notna().all().all() or not values.apply(lambda column: column.map(lambda x: math.isfinite(float(x)))).all().all()
            or (values[["open", "high", "low", "close"]] <= 0).any().any() or (values["volume"] < 0).any()
            or (values["high"] < values[["open", "low", "close"]].max(axis=1)).any()
            or (values["low"] > values[["open", "high", "close"]].min(axis=1)).any()
            or observations.isna().any() or retrieved.isna().any()):
        raise ValueError("existing_massive_dates_invalid")
    return frame


def _quarantine(data_root: Path, symbol: str, path: Path) -> str:
    quarantine = data_root / "quarantine" / NAMESPACE / f"{symbol_key(symbol)}-{uuid.uuid4().hex}.parquet"
    quarantine.parent.mkdir(parents=True, exist_ok=True)
    # Keep the original provider-isolated view in place.  This is a distinct,
    # exclusive forensic copy (not a hard link), so later changes cannot alter
    # the quarantined evidence.
    with path.open("rb") as source, quarantine.open("xb") as destination:
        shutil.copyfileobj(source, destination)
    return quarantine.relative_to(data_root).as_posix()


def _update_catalogue(root: Path, successful: list[tuple[str, Path, pd.DataFrame]]) -> int:
    if not successful:
        return 0
    destination = root / "catalogue" / "us-daily-browser-v1.json"
    if not destination.exists() or destination.is_symlink():
        raise MassivePublishError("browser_catalogue_missing")
    with (destination.parent / "us-daily-browser-refresh.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            payload = json.loads(destination.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError) as exc:
            raise MassivePublishError("browser_catalogue_invalid") from exc
        if not isinstance(payload, dict) or payload.get("schema_version") != "us-daily-browser-v1" or not isinstance(payload.get("series"), list):
            raise MassivePublishError("browser_catalogue_invalid")
        changed = {symbol for symbol, _, _ in successful}
        entries = [x for x in payload["series"] if not (isinstance(x, dict) and x.get("provider") == PROVIDER and x.get("namespace") == NAMESPACE and x.get("symbol") in changed)]
        raw_root = root / "bars" / "daily"
        for symbol, path, frame in successful:
            dates = pd.to_datetime(frame["date"], errors="raise")
            entries.append({"series_id": f"{PROVIDER}:{NAMESPACE}:{symbol_key(symbol)}", "symbol": symbol,
                            "provider": PROVIDER, "namespace": NAMESPACE,
                            "raw_relative_path": path.relative_to(raw_root).as_posix(),
                            "first_date": dates.min().date().isoformat(), "last_date": dates.max().date().isoformat(), "rows": len(frame),
                            "quality_warning": "Massive 非复权观测快照；公司行为与历史身份未知，仅供浏览，不可用于研究或回测。"})
        payload["series"] = sorted(entries, key=lambda x: (str(x.get("symbol", "")), str(x.get("last_date", ""))))
        _atomic_json(destination, payload)
    return len(successful)


def _previous_successes(records: Path, completion_key: str, root: Path) -> set[str]:
    if not records.exists():
        return set()
    result: set[str] = set()
    for line in records.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("completion_key") != completion_key or row.get("status") != "success" or not isinstance(row.get("symbol"), str):
            continue
        symbol = row["symbol"]
        destination = root / "bars" / "daily" / f"provider={PROVIDER}" / f"namespace={NAMESPACE}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
        try:
            _safe_destination(destination, root / "bars" / "daily")
            if not destination.is_file() or destination.is_symlink() or not isinstance(row.get("output_sha256"), str):
                continue
            _validate_existing(pd.read_parquet(destination), symbol)
            if _sha256(destination) != row["output_sha256"]:
                continue
        except (OSError, ValueError, ImportError):
            continue
        result.add(symbol)
    return result


def _previous_unmatched(records: Path, completion_key: str) -> set[str]:
    if not records.exists():
        return set()
    result: set[str] = set()
    for line in records.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if row.get("completion_key") == completion_key and row.get("status") == "unmatched" and isinstance(row.get("symbol"), str):
            result.add(row["symbol"])
    return result


def publish_massive_daily(security_master: Path, data_root: Path, credential_file: Path | None = None, *, date_: date | None = None,
                          snapshot: Path | None = None, now: datetime | None = None,
                          capture: Callable[[Path, date, Path], dict[str, Any]] = capture_massive_daily) -> dict[str, Any]:
    """Capture at most once, then publish exact active-symbol matches from one day."""
    clock = now or _now()
    if clock.tzinfo is None:
        raise MassivePublishError("now_must_be_timezone_aware")
    target = date_ or completed_session(clock)
    if not isinstance(target, date) or isinstance(target, datetime) or target > completed_session(clock):
        raise MassivePublishError("requested_date_after_completed_session")
    root = Path(data_root)
    try:
        master_path = resolve_sync_master(root, Path(security_master))
    except (OSError, ValueError) as exc:
        raise MassivePublishError("security_master_invalid") from exc
    manifest_root = root / "manifests" / NAMESPACE
    records = manifest_root / "records.jsonl"
    manifest_root.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"schema_version": PUBLICATION_SCHEMA, "provider": PROVIDER, "namespace": NAMESPACE,
                               "target_date": target.isoformat(), "status": "running", "requested": 0, "attempted": 0,
                               "success": 0, "failed": 0, "unmatched": 0, "missing": 0, "skipped": 0,
                               "captured": False, "last_error_code": None}
    with (manifest_root / "publish.lock").open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**summary, "status": "skipped", "reason": "lock_held"}
        try:
            master_sha = _sha256(master_path)
            master = _read_master(master_path)
            if _sha256(master_path) != master_sha:
                raise ValueError("security_master_changed_during_read")
            active = set(select_sync_symbols(master))
        except (OSError, ValueError, ImportError) as exc:
            raise MassivePublishError("security_master_invalid") from exc
        if snapshot is None:
            snapshot = _reusable_snapshot(root, target, master_sha)
            if snapshot is None:
                if credential_file is None:
                    raise MassivePublishError("credential_file_required")
                try:
                    captured = capture(root, target, Path(credential_file))
                except MassiveDailyError as exc:
                    summary.update({"status": "failed", "last_error_code": str(exc), "finished_at": _now().isoformat()})
                    _atomic_json(manifest_root / "latest.json", summary)
                    return summary
                if captured.get("status") != "captured" or not isinstance(captured.get("snapshot_relative_path"), str):
                    summary.update({"status": "failed", "last_error_code": "capture_not_publishable", "finished_at": _now().isoformat()})
                    _atomic_json(manifest_root / "latest.json", summary)
                    return summary
                snapshot = root / captured["snapshot_relative_path"]
                summary["captured"] = True
        manifest, captured_frame = _load_snapshot(root, Path(snapshot), target)
        completion_input = {"target_date": target.isoformat(), "snapshot_response_sha256": manifest["response_sha256"],
                            "snapshot_normalized_sha256": manifest["normalized_sha256"], "security_master_sha256": master_sha,
                            "publisher_schema": PUBLICATION_SCHEMA}
        completion_key = hashlib.sha256(json.dumps(completion_input, sort_keys=True).encode()).hexdigest()
        already = _previous_successes(records, completion_key, root)
        unmatched_already = _previous_unmatched(records, completion_key)
        source_symbols = set(captured_frame["symbol"])
        unmatched = sorted(source_symbols - active)
        summary["unmatched"] = len(unmatched)
        summary["missing"] = len(active - source_symbols)
        summary["requested"] = len(active)
        successful: list[tuple[str, Path, pd.DataFrame]] = []
        for symbol in sorted(source_symbols & active):
            if symbol in already:
                summary["skipped"] += 1
                continue
            summary["attempted"] += 1
            record: dict[str, Any] = {"schema_version": PUBLICATION_SCHEMA, "symbol": symbol, "provider": PROVIDER, "namespace": NAMESPACE,
                                       "source": SOURCE, "status": "failed", "requested_start": target.isoformat(), "requested_end": target.isoformat(),
                                       "actual_last_date": target.isoformat(), "adjusted": False, "actions_status": "unknown", "historical_identity": "unknown",
                                       "research_qualified": False, "availability_policy": "observed_ingestion_only", "snapshot_relative_path": manifest["snapshot_relative_path"],
                                       "archive": f"{manifest['snapshot_relative_path']}/response.json",
                                       "archive_sha256": manifest["response_sha256"], "normalized_sha256": manifest["normalized_sha256"], "completion_key": completion_key}
            destination = root / "bars" / "daily" / f"provider={PROVIDER}" / f"namespace={NAMESPACE}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
            try:
                _safe_destination(destination, root / "bars" / "daily")
                incoming = _published_frame(captured_frame, symbol)
                if len(incoming) != 1:
                    raise ValueError("source_symbol_not_unique")
                if destination.exists() or destination.is_symlink():
                    try:
                        existing = _validate_existing(pd.read_parquet(destination), symbol)
                    except Exception:
                        try:
                            record["quarantine_path"] = _quarantine(root, symbol, destination)
                        except OSError:
                            record["quarantine_path"] = None
                        raise ValueError("existing_massive_quarantined")
                    merged = pd.concat([existing, incoming], ignore_index=True)
                    merged["_date"] = pd.to_datetime(merged["date"], errors="raise")
                    merged = merged.drop_duplicates("_date", keep="last").sort_values("_date").drop(columns="_date").reset_index(drop=True)
                else:
                    merged = incoming
                _atomic_parquet(merged, destination)
                record.update({"status": "success", "rows": len(merged), "published_relative_path": destination.relative_to(root).as_posix(),
                               "output_sha256": _sha256(destination)})
                successful.append((symbol, destination, merged)); summary["success"] += 1
            except Exception as exc:
                record["error_code"] = str(exc) if isinstance(exc, ValueError) else "publish_storage_failed"
                summary["failed"] += 1; summary["last_error_code"] = record["error_code"]
            _append(records, record)
            if summary["attempted"] % 100 == 0:
                _atomic_json(manifest_root / "latest.json", summary)
        for symbol in unmatched:
            if symbol in unmatched_already:
                continue
            _append(records, {"schema_version": PUBLICATION_SCHEMA, "symbol": symbol, "provider": PROVIDER, "namespace": NAMESPACE,
                              "status": "unmatched", "error_code": "symbol_not_active_exact_match", "requested_start": target.isoformat(),
                              "requested_end": target.isoformat(), "completion_key": completion_key, "snapshot_relative_path": manifest["snapshot_relative_path"]})
        try:
            # Include verified idempotent members, so a previous catalogue
            # write failure is repairable without rewriting any bar evidence.
            for symbol in sorted(already):
                destination = root / "bars" / "daily" / f"provider={PROVIDER}" / f"namespace={NAMESPACE}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
                successful.append((symbol, destination, _validate_existing(pd.read_parquet(destination), symbol)))
            summary["catalogue_updated"] = _update_catalogue(root, successful)
        except MassivePublishError:
            # Bar evidence is retained; a catalogue problem is visible and never
            # converted into per-symbol retry traffic or a false success.
            summary["catalogue_updated"] = 0; summary["failed"] += 1; summary["catalogue_error"] = "browser_catalogue_invalid"
        summary["status"] = "success" if not summary["failed"] else "partial"
        summary["finished_at"] = _now().isoformat()
        publication = {**summary, "snapshot_relative_path": manifest["snapshot_relative_path"], "inputs": completion_input,
                       "completion_key": completion_key, "snapshot_response_sha256": manifest["response_sha256"],
                       "snapshot_normalized_sha256": manifest["normalized_sha256"]}
        _atomic_json(manifest_root / "publications" / target.isoformat() / f"{completion_key}-{uuid.uuid4().hex}.json", publication)
        _atomic_json(manifest_root / "latest.json", summary)
        if summary["status"] == "success":
            _atomic_json(_publication_pointer(manifest_root, target), publication)
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
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--date", type=_parse_date, help="Defaults to the completed XNYS session")
    parser.add_argument("--snapshot", type=Path, help="Existing immutable Massive capture beneath data-root/reference")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = publish_massive_daily(args.security_master, args.data_root, args.credential_file, date_=args.date, snapshot=args.snapshot)
    except (MassivePublishError, OSError):
        print(json.dumps({"status": "failed", "error_code": "massive_publication_failed"}))
        return 2
    print(json.dumps(result, sort_keys=True, default=str))
    return 0 if result["status"] == "success" else 2


if __name__ == "__main__":
    raise SystemExit(main())
