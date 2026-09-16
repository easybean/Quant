"""Read-only structural validation and versioned cleaning for daily-bar Parquet.

This module intentionally does not calculate adjusted prices or delisting
returns.  It operates on one raw provider/symbol file at a time, so a clean
run cannot silently splice price histories from different providers.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


REQUIRED_COLUMNS = ("date", "symbol", "open", "high", "low", "close", "volume")
PRICE_COLUMNS = ("open", "high", "low", "close")
CLEANING_STATUS = "structural_cleaned_not_adjusted_or_delisting_processed"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.parquet")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, destination)


def _atomic_json(value: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _provider_identity(raw_root: Path, path: Path) -> dict[str, str]:
    relative = path.relative_to(raw_root)
    parts = relative.parts
    provider = next((part.partition("=")[2] for part in parts if part.startswith("provider=")), "unknown")
    namespace = next((part.partition("=")[2] for part in parts if part.startswith("namespace=")), "unknown")
    symbol_key = next((part.partition("=")[2] for part in parts if part.startswith("symbol=")), "unknown")
    return {"provider": provider, "namespace": namespace, "symbol_key": symbol_key}


def _anomaly(path: Path, identity: dict[str, str], kind: str, count: int = 1, **detail: Any) -> dict[str, Any]:
    return {
        "raw_relative_path": str(path), "provider": identity["provider"],
        "namespace": identity["namespace"], "symbol_key": identity["symbol_key"],
        "kind": kind, "count": int(count), **detail,
    }


def _clean_one(raw_root: Path, source_path: Path) -> tuple[pd.DataFrame | None, dict[str, Any], list[dict[str, Any]]]:
    """Validate one file and return a separate cleaned frame; never write raw."""
    relative = source_path.relative_to(raw_root)
    identity = _provider_identity(raw_root, source_path)
    raw_hash = _sha256(source_path)
    anomalies: list[dict[str, Any]] = []
    record: dict[str, Any] = {
        **identity, "raw_relative_path": str(relative), "raw_sha256": raw_hash,
        "status": "ok", "input_rows": 0, "output_rows": 0, "first_date": None, "last_date": None,
    }
    try:
        frame = pd.read_parquet(source_path)
    except Exception as exc:
        record["status"] = "read_failed"
        anomalies.append(_anomaly(relative, identity, "parquet_read_failed", error=f"{type(exc).__name__}: {exc}"))
        return None, record, anomalies
    record["input_rows"] = len(frame)
    missing = sorted(set(REQUIRED_COLUMNS).difference(frame.columns))
    if missing:
        record["status"] = "schema_failed"
        anomalies.append(_anomaly(relative, identity, "missing_required_columns", columns=missing))
        return None, record, anomalies

    cleaned = frame.copy()
    for metadata_column in ("source", "feed", "adjustment_status"):
        if metadata_column not in cleaned.columns:
            anomalies.append(_anomaly(relative, identity, "missing_provenance_metadata", column=metadata_column))
    if "dividends" not in cleaned.columns or "stock_splits" not in cleaned.columns:
        anomalies.append(_anomaly(relative, identity, "missing_action_metadata"))
    parsed_dates = pd.to_datetime(cleaned["date"], errors="coerce", utc=True)
    invalid_dates = int(parsed_dates.isna().sum())
    if invalid_dates:
        anomalies.append(_anomaly(relative, identity, "invalid_date_dropped", invalid_dates))
        cleaned = cleaned.loc[parsed_dates.notna()].copy()
        parsed_dates = parsed_dates.loc[parsed_dates.notna()]
    cleaned["date"] = parsed_dates.dt.tz_localize(None).dt.normalize()

    duplicates = int(cleaned["date"].duplicated(keep=False).sum())
    if duplicates:
        anomalies.append(_anomaly(relative, identity, "duplicate_date_keep_last", duplicates, policy="stable_input_order_keep_last"))
        cleaned = cleaned.drop_duplicates("date", keep="last")
    cleaned = cleaned.sort_values("date", kind="stable").reset_index(drop=True)

    missing_numeric = int(cleaned[list(REQUIRED_COLUMNS[2:])].isna().sum().sum())
    if missing_numeric:
        anomalies.append(_anomaly(relative, identity, "missing_required_value", missing_numeric))
    for column in PRICE_COLUMNS:
        negative = int((pd.to_numeric(cleaned[column], errors="coerce") < 0).sum())
        if negative:
            anomalies.append(_anomaly(relative, identity, "negative_price", negative, column=column))
    negative_volume = int((pd.to_numeric(cleaned["volume"], errors="coerce") < 0).sum())
    if negative_volume:
        anomalies.append(_anomaly(relative, identity, "negative_volume", negative_volume))
    numeric = cleaned[list(PRICE_COLUMNS)].apply(pd.to_numeric, errors="coerce")
    ohlc_invalid = ((numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)) | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))).fillna(False)
    if int(ohlc_invalid.sum()):
        anomalies.append(_anomaly(relative, identity, "ohlc_relation_invalid", int(ohlc_invalid.sum())))

    # Preserve source/feed/action information even for older raw files, and make
    # the absence explicit rather than pretending that prices are adjusted.
    defaults = {
        "source": "unknown_raw_source", "feed": "unknown", "adjustment_status": "unknown",
        "dividends": 0.0, "stock_splits": 0.0,
    }
    for column, default in defaults.items():
        if column not in cleaned:
            cleaned[column] = default
    if "actions_status" not in cleaned:
        adjustment = cleaned["adjustment_status"].astype(str).str.lower()
        cleaned["actions_status"] = adjustment.map(
            lambda value: "actions_not_available_or_unverified"
            if value in {"raw", "unadjusted"} else "actions_unknown_unapplied"
        )
    cleaned["cleaning_status"] = CLEANING_STATUS
    cleaned["raw_relative_path"] = str(relative)
    cleaned["raw_sha256"] = raw_hash
    record.update({
        "output_rows": len(cleaned),
        "first_date": cleaned["date"].min().date().isoformat() if len(cleaned) else None,
        "last_date": cleaned["date"].max().date().isoformat() if len(cleaned) else None,
        "adjustment_processed": False, "delisting_return_processed": False,
    })
    return cleaned, record, anomalies


def validate_clean_paths(raw_root: Path, output_root: Path | None, audit_root: Path | None) -> None:
    raw = raw_root.resolve()
    if not raw.is_dir():
        raise ValueError(f"raw root does not exist or is not a directory: {raw}")
    for label, candidate in (("output", output_root), ("audit output", audit_root)):
        if candidate is None:
            continue
        resolved = candidate.resolve()
        if resolved == raw or raw in resolved.parents or resolved in raw.parents:
            raise ValueError(f"{label} must be separate from raw root; raw inputs are read-only")


def run_structural_clean(
    raw_root: Path,
    *,
    output_root: Path | None = None,
    audit_root: Path | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Scan raw bars and optionally write a versioned derived copy plus audit files."""
    validate_clean_paths(raw_root, output_root, audit_root)
    if not dry_run and output_root is None:
        raise ValueError("--output is required unless --dry-run is selected")
    raw_root = raw_root.resolve()
    raw_files = sorted(raw_root.rglob("bars.parquet"))
    records: list[dict[str, Any]] = []
    anomalies: list[dict[str, Any]] = []
    outputs = 0
    for source_path in raw_files:
        cleaned, record, file_anomalies = _clean_one(raw_root, source_path)
        records.append(record)
        anomalies.extend(file_anomalies)
        if cleaned is not None and not dry_run:
            destination = output_root.resolve() / source_path.relative_to(raw_root)
            _atomic_parquet(cleaned, destination)
            outputs += 1
    status_counts = Counter(record["status"] for record in records)
    coverage = pd.DataFrame(records)
    summary = {
        "format_version": 1,
        "kind": "structural_cleaning_quality_summary",
        "raw_root": str(raw_root), "output_root": str(output_root.resolve()) if output_root else None,
        "dry_run": dry_run, "raw_files": len(raw_files), "derived_files_written": outputs,
        "file_status_counts": dict(sorted(status_counts.items())), "anomaly_counts": dict(sorted(Counter(item["kind"] for item in anomalies).items())),
        "limitations": ["Structural cleaning only; adjusted prices were not calculated.", "Delisting returns and merger/OTC outcomes were not processed.", "Each provider/namespace/symbol file is processed independently; no cross-provider price series are merged."],
        "cleaning_status": CLEANING_STATUS,
    }
    if audit_root is not None:
        audit = audit_root.resolve()
        _atomic_json(summary, audit / "quality-summary.json")
        _atomic_json({"run_at": datetime.now(timezone.utc).isoformat(), **summary}, audit / "run-metadata.json")
        _atomic_parquet(coverage, audit / "coverage.parquet")
        anomaly_path = audit / "anomalies.jsonl"
        anomaly_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = anomaly_path.with_suffix(f".{uuid.uuid4().hex}.tmp.jsonl")
        temporary.write_text("".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in anomalies), encoding="utf-8")
        os.replace(temporary, anomaly_path)
    return summary
