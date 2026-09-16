"""Versioned, read-only quality and coverage reporting for daily bars.

The structural cleaner already validates every raw file.  This module turns its
audit index into a report that researchers can consume without treating a
cleaned file as complete, adjusted market data.  It never writes raw or derived
bars; only an explicitly separate report directory is written.
"""

from __future__ import annotations

import json
import os
import uuid
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import pandas as pd


QUALITY_RULE_VERSION = "daily-quality-v1"
_REQUIRED = ("open", "high", "low", "close", "volume")


def _atomic_json(value: Any, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.parquet")
    frame.to_parquet(temporary, index=False)
    os.replace(temporary, destination)


def _symbol(symbol_key: object) -> str:
    return str(symbol_key).rsplit("-", 1)[0]


def _read_anomalies(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _manifest_gap_findings(paths: list[Path]) -> list[dict[str, Any]]:
    """Keep only latest manifest state per provider output path, never credentials."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for path in paths:
        for line in path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            key = (str(record.get("provider", "unknown")), str(record.get("path", record.get("symbol", ""))))
            prior = latest.get(key)
            if prior is None or str(record.get("finished_at", "")) >= str(prior.get("finished_at", "")):
                latest[key] = record
    findings: list[dict[str, Any]] = []
    for record in latest.values():
        if record.get("status") == "success":
            continue
        findings.append({
            "rule_version": QUALITY_RULE_VERSION, "kind": "supplier_download_gap", "severity": "warning",
            "symbol": str(record.get("symbol", record.get("provider_symbol", "unknown"))), "symbol_key": None,
            "provider": str(record.get("provider", "unknown")), "namespace": "manifest",
            "raw_relative_path": None, "date": record.get("finished_at"), "source": record.get("source"),
            "detail": json.dumps({"requested_start": record.get("requested_start"), "requested_end": record.get("requested_end"), "attempts": record.get("attempts"), "retryable": record.get("retryable"), "error_type": str(record.get("error", "")).split(":", 1)[0]}, ensure_ascii=False, default=str),
        })
    return findings


def _identity_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (str(row.get("provider", "unknown")), str(row.get("namespace", "unknown")), str(row["symbol_key"]))


def _finding(
    *, kind: str, severity: str, row: dict[str, Any], date: object = None,
    source: object = None, detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "rule_version": QUALITY_RULE_VERSION,
        "kind": kind,
        "severity": severity,
        "symbol": _symbol(row["symbol_key"]),
        "symbol_key": row["symbol_key"],
        "provider": row.get("provider", "unknown"),
        "namespace": row.get("namespace", "unknown"),
        "raw_relative_path": row.get("raw_relative_path"),
        "date": None if date is None or pd.isna(date) else str(pd.Timestamp(date).date()),
        "source": None if source is None or pd.isna(source) else str(source),
        "detail": json.dumps(detail or {}, ensure_ascii=False, sort_keys=True, default=str),
    }


def _audit_findings(
    coverage: pd.DataFrame, anomalies: list[dict[str, Any]], derived_root: Path
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Expand only files that existing full structural audit marked affected."""
    index = {_identity_key(record): record for record in coverage.to_dict("records")}
    findings: list[dict[str, Any]] = []
    scanned = 0
    relevant = {"missing_required_value", "ohlc_relation_invalid", "negative_volume", "negative_price", "duplicate_date_keep_last"}
    for anomaly in anomalies:
        if anomaly["kind"] not in relevant:
            continue
        record = index.get(_identity_key(anomaly))
        if record is None:
            findings.append(_finding(kind="audit_identity_not_found", severity="warning", row=anomaly, detail=anomaly))
            continue
        file_path = derived_root / record["raw_relative_path"]
        if not file_path.is_file():
            findings.append(_finding(kind="affected_derived_file_missing", severity="error", row=record, detail={"audit_kind": anomaly["kind"]}))
            continue
        frame = pd.read_parquet(file_path)
        scanned += 1
        source = frame["source"] if "source" in frame else pd.Series("unknown", index=frame.index)
        if anomaly["kind"] == "missing_required_value":
            for column in _REQUIRED:
                if column not in frame:
                    findings.append(_finding(kind="missing_required_column", severity="error", row=record, detail={"column": column}))
                    continue
                for position in frame.index[frame[column].isna()]:
                    findings.append(_finding(kind="missing_required_value", severity="error", row=record, date=frame.at[position, "date"], source=source.at[position], detail={"column": column}))
        elif anomaly["kind"] == "ohlc_relation_invalid":
            numeric = frame[["open", "high", "low", "close"]].apply(pd.to_numeric, errors="coerce")
            invalid = (numeric["high"] < numeric[["open", "close", "low"]].max(axis=1)) | (numeric["low"] > numeric[["open", "close", "high"]].min(axis=1))
            for position in frame.index[invalid.fillna(False)]:
                findings.append(_finding(kind="ohlc_relation_invalid", severity="error", row=record, date=frame.at[position, "date"], source=source.at[position]))
        elif anomaly["kind"] == "negative_volume":
            for position in frame.index[pd.to_numeric(frame["volume"], errors="coerce") < 0]:
                findings.append(_finding(kind="negative_volume", severity="error", row=record, date=frame.at[position, "date"], source=source.at[position]))
        elif anomaly["kind"] == "negative_price":
            column = anomaly.get("column")
            if column in frame:
                for position in frame.index[pd.to_numeric(frame[column], errors="coerce") < 0]:
                    findings.append(_finding(kind="negative_price", severity="error", row=record, date=frame.at[position, "date"], source=source.at[position], detail={"column": column}))
        # Duplicate dates were removed during structural cleaning. The original
        # full audit is authoritative for their count; they cannot be recreated
        # from a cleaned file without rereading every raw input.
        elif anomaly["kind"] == "duplicate_date_keep_last":
            findings.append(_finding(kind="duplicate_date_removed_in_structural_v1", severity="warning", row=record, detail={"count": anomaly.get("count"), "policy": anomaly.get("policy")}))
    return findings, {"affected_derived_files_read": scanned}


def _metadata_findings(coverage: pd.DataFrame, master: pd.DataFrame, derived_root: Path) -> tuple[list[dict[str, Any]], int]:
    findings: list[dict[str, Any]] = []
    conflict_files_read = 0
    # Same ticker has multiple lifecycle rows: this is a candidate, not proof
    # that two dates belong to a different issuer. P1-03 resolves identity.
    for symbol, group in master.groupby("symbol", dropna=True):
        if len(group) < 2:
            continue
        for record in group.to_dict("records"):
            findings.append({
                "rule_version": QUALITY_RULE_VERSION, "kind": "ticker_lifecycle_reuse_candidate", "severity": "warning",
                "symbol": str(symbol), "symbol_key": None, "provider": "security_master", "namespace": "consolidated",
                "raw_relative_path": None, "date": record.get("ipo_date") or record.get("delisting_date"),
                "source": record.get("source"),
                "detail": json.dumps({"lifecycle_rows_for_symbol": len(group), "ipo_date": record.get("ipo_date"), "delisting_date": record.get("delisting_date"), "status": record.get("status")}, ensure_ascii=False, default=str),
            })
    # Multiple bars sources for a ticker are not merged; flag them so that a
    # future curated layer cannot accidentally splice them.
    by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in coverage.to_dict("records"):
        by_symbol[_symbol(record["symbol_key"])].append(record)
    for symbol, records in by_symbol.items():
        source_keys = {(r["provider"], r["namespace"]) for r in records}
        if len(source_keys) > 1:
            for record in records:
                # These are the only derived files read for source conflicts:
                # identical ticker observations across provider/namespaces.
                path = derived_root / record["raw_relative_path"]
                if path.is_file():
                    bars = pd.read_parquet(path, columns=["date", "source"])
                    conflict_files_read += 1
                    source = ",".join(sorted(str(value) for value in bars["source"].dropna().unique())) or "unknown"
                    date = bars["date"].min() if len(bars) else None
                else:
                    source, date = "unknown", None
                findings.append(_finding(kind="source_conflict_unmerged", severity="warning", row=record, date=date, source=source, detail={"observations": len(records), "provider_namespaces": sorted("/".join(x) for x in source_keys)}))
    return findings, conflict_files_read


def build_quality_report(
    *, audit_root: Path, derived_root: Path, security_master: Path, report_root: Path,
    manifests: list[Path] | None = None,
) -> dict[str, Any]:
    """Build a read-only report from a completed structural audit.

    ``report_root`` must be separate from raw/derived roots. The report includes
    detailed findings for files marked affected by the prior all-file structural
    run and separate full-index metadata checks. It deliberately does not infer
    a trading halt from a missing bar: without an exchange session calendar and
    venue status, a missing bar remains a supplier/coverage candidate.
    """
    audit_root, derived_root, security_master, report_root = (Path(p).resolve() for p in (audit_root, derived_root, security_master, report_root))
    if report_root == derived_root or report_root in derived_root.parents or derived_root in report_root.parents:
        raise ValueError("report output must be separate from derived bars")
    manifest_paths = [Path(path).resolve() for path in (manifests or [])]
    coverage = pd.read_parquet(audit_root / "coverage.parquet")
    anomalies = _read_anomalies(audit_root / "anomalies.jsonl")
    master = pd.read_parquet(security_master)
    findings, scan = _audit_findings(coverage, anomalies, derived_root)
    metadata_findings, conflict_files_read = _metadata_findings(coverage, master, derived_root)
    findings.extend(metadata_findings)
    findings.extend(_manifest_gap_findings(manifest_paths))
    frame = pd.DataFrame(findings)
    if frame.empty:
        frame = pd.DataFrame(columns=["rule_version", "kind", "severity", "symbol", "symbol_key", "provider", "namespace", "raw_relative_path", "date", "source", "detail"])
    summary = {
        "format_version": 1, "rule_version": QUALITY_RULE_VERSION,
        "kind": "daily_bar_quality_and_gap_report", "inputs": {
            "audit_root": str(audit_root), "derived_root": str(derived_root), "security_master": str(security_master),
            "manifests": [str(path) for path in manifest_paths],
            "structural_audit_files": int(len(coverage)), "structural_anomalies": dict(sorted(Counter(item["kind"] for item in anomalies).items())),
        },
        "findings": dict(sorted(Counter(frame["kind"]).items())), "findings_rows": int(len(frame)),
        **scan, "source_conflict_derived_files_read": conflict_files_read,
        "rules": {
            "duplicate_dates": "Full structural audit ran stable_input_order_keep_last; original duplicate dates are not recoverable from structural-v1.",
            "ohlcv": "Affected structural-audit files are reread from structural-v1 and expanded to date/source-level findings.",
            "trading_days": "Not inferred from absent rows: weekends/holidays, halts and supplier omissions require a dated exchange calendar plus venue status. This report labels no absent day as a halt; latest manifest failures are supplier_download_gap candidates.",
            "ticker_reuse": "Multiple security-master lifecycle rows per ticker are candidates, not identity resolution.",
            "source_conflicts": "Multiple provider/namespace observations are flagged and remain unmerged; price conflict comparison is deferred until lifecycle mapping.",
        },
        "limitations": [
            "No raw or derived bar was changed.",
            "This report reuses the existing all-file structural audit; it does not claim a fresh all-file validation.",
            "Missing dates cannot be classified as normal closure, suspension, or supplier gap without an authoritative market calendar and security status source.",
        ],
    }
    _atomic_parquet(frame, report_root / "findings.parquet")
    _atomic_json(summary, report_root / "summary.json")
    return summary
