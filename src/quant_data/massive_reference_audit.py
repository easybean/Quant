"""Read-only comparison of Massive current tickers and official listing aliases.

The report is diagnostic evidence, not a mapping.  In particular, current
reference absence never establishes a delisting or historical identity.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .symbol_mapping import _listing_rows
from .sync_scheduler import resolve_sync_master


SCHEMA = "massive-reference-alias-audit-v1"
_MIC = {"NASDAQ": "XNAS", "NYSE": "XNYS", "NYSE AMERICAN": "XASE", "NYSE ARCA": "ARCX", "CBOE BZX": "BATS", "IEX": "IEXG"}


class MassiveReferenceAuditError(ValueError):
    """Stable input-integrity failure for the read-only diagnostic."""


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
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _safe_file(snapshot: Path, relative: str) -> Path:
    part = Path(relative)
    if not relative or part.is_absolute() or ".." in part.parts:
        raise MassiveReferenceAuditError("reference_page_path_invalid")
    candidate = snapshot / part
    try:
        root, resolved = snapshot.resolve(strict=True), candidate.resolve(strict=True)
    except OSError as exc:
        raise MassiveReferenceAuditError("reference_page_missing") from exc
    if candidate.is_symlink() or root not in resolved.parents or not resolved.is_file():
        raise MassiveReferenceAuditError("reference_page_path_invalid")
    return resolved


def _load_reference(snapshot: Path) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    manifest_path, tickers_path = snapshot / "manifest.json", snapshot / "all-tickers.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tickers_payload = json.loads(tickers_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise MassiveReferenceAuditError("reference_snapshot_unreadable") from exc
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != "massive-ticker-reference-v1" or manifest.get("status") != "captured"
            or not isinstance(manifest.get("all_tickers_sha256"), str) or _sha256(tickers_path) != manifest["all_tickers_sha256"]
            or not isinstance(manifest.get("pages"), list) or not isinstance(tickers_payload, dict) or not isinstance(tickers_payload.get("tickers"), list)):
        raise MassiveReferenceAuditError("reference_snapshot_invalid")
    for page in manifest["pages"]:
        if not isinstance(page, dict) or not isinstance(page.get("raw_relative_path"), str) or not isinstance(page.get("sha256"), str):
            raise MassiveReferenceAuditError("reference_page_manifest_invalid")
        if _sha256(_safe_file(snapshot, page["raw_relative_path"])) != page["sha256"]:
            raise MassiveReferenceAuditError("reference_page_hash_invalid")
    index: dict[str, dict[str, Any]] = {}
    for row in tickers_payload["tickers"]:
        ticker = row.get("ticker") if isinstance(row, dict) else None
        if not isinstance(ticker, str) or not ticker or ticker in index:
            raise MassiveReferenceAuditError("reference_tickers_invalid")
        index[ticker] = row
    if not index:
        raise MassiveReferenceAuditError("reference_tickers_empty")
    return index, manifest


def _name_match(official: str, provider: Any) -> bool | None:
    return provider == official if isinstance(provider, str) else None


def classify_master_code(code: str, reference: dict[str, dict[str, Any]], listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Classify one raw master code without normalising provider spellings."""
    direct = reference.get(code)
    if direct is not None:
        return {"symbol": code, "classification": "exact_reference_present", "reference_ticker": code,
                "primary_exchange": direct.get("primary_exchange"), "research_qualified": False}
    candidates: list[dict[str, Any]] = []
    mismatch = False
    for listing in listings:
        if listing.get("primary_symbol") != code:
            continue
        exchange = listing.get("exchange")
        mic = _MIC.get(exchange)
        aliases = listing.get("aliases")
        if not isinstance(aliases, dict) or mic is None:
            continue
        for alias_field in ("CQS Symbol", "NASDAQ Symbol"):
            alias = aliases.get(alias_field)
            # Exact case is intentional: CQS's lower-case preferred marker is
            # semantic and Massive's raw ticker must never be guessed/cased.
            if not isinstance(alias, str) or not alias or alias not in reference:
                continue
            provider = reference[alias]
            if provider.get("primary_exchange") != mic:
                mismatch = True
                continue
            candidates.append({"reference_ticker": alias, "alias_field": alias_field, "official_exchange": exchange,
                               "required_primary_exchange_mic": mic, "primary_exchange": provider.get("primary_exchange"),
                               "official_security_name": listing.get("security_name"), "reference_name": provider.get("name"),
                               "name_exact_match": _name_match(str(listing.get("security_name", "")), provider.get("name"))})
    unique = {(item["reference_ticker"], item["official_exchange"]): item for item in candidates}
    candidates = [unique[key] for key in sorted(unique)]
    if len(candidates) == 1:
        return {"symbol": code, "classification": "authoritative_alias_candidate", "candidates": candidates, "research_qualified": False,
                "warning": "Candidate only; no mapping was applied and historical identity remains unknown."}
    if len(candidates) > 1:
        return {"symbol": code, "classification": "ambiguity", "candidates": candidates, "research_qualified": False,
                "warning": "More than one exact official-alias/MIC candidate; no mapping was applied."}
    return {"symbol": code, "classification": "absent_reference_unknown", "official_listing_found": any(row.get("primary_symbol") == code for row in listings),
            "alias_exact_exchange_mismatch": mismatch, "research_qualified": False,
            "warning": "Current reference absence does not establish delisting, historical identity, or provider unavailability."}


def classify_unmatched_reference_ticker(ticker: str, provider: dict[str, Any], master_codes: set[str], listings: list[dict[str, Any]]) -> dict[str, Any]:
    """Attribute a non-exact provider ticker only to exact official aliases."""
    candidates: list[dict[str, Any]] = []
    for listing in listings:
        primary = listing.get("primary_symbol")
        if not isinstance(primary, str) or primary not in master_codes:
            continue
        exchange, aliases = listing.get("exchange"), listing.get("aliases")
        mic = _MIC.get(exchange)
        if mic is None or provider.get("primary_exchange") != mic or not isinstance(aliases, dict):
            continue
        for alias_field in ("CQS Symbol", "NASDAQ Symbol"):
            if aliases.get(alias_field) == ticker:
                candidates.append({"master_symbol": primary, "alias_field": alias_field, "official_exchange": exchange,
                                   "required_primary_exchange_mic": mic, "reference_ticker": ticker,
                                   "official_security_name": listing.get("security_name"), "reference_name": provider.get("name"),
                                   "name_exact_match": _name_match(str(listing.get("security_name", "")), provider.get("name"))})
    unique = {item["master_symbol"]: item for item in candidates}
    candidates = [unique[key] for key in sorted(unique)]
    classification = "authoritative_alias_candidate" if len(candidates) == 1 else "ambiguity" if len(candidates) > 1 else "absent_reference_unknown"
    return {"reference_ticker": ticker, "classification": classification, "candidates": candidates, "research_qualified": False,
            "warning": "Exact current alias evidence only; no mapping was applied and absence never implies delisting."}


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() in {".csv", ".tsv"} else pd.read_parquet(path)


def audit_massive_reference(
    data_root: Path,
    security_master: Path,
    reference_snapshot: Path,
    listing_snapshot: Path,
) -> dict[str, Any]:
    """Write an immutable all-master-code diagnostic from fixed input evidence."""
    root = Path(data_root)
    reference_path = Path(reference_snapshot)
    reference, reference_manifest = _load_reference(reference_path)
    try:
        listings, listing_hashes, listing_footer = _listing_rows(Path(listing_snapshot))
        master_path = resolve_sync_master(root, Path(security_master))
        master_sha = _sha256(master_path)
        master = _read_master(master_path)
        if _sha256(master_path) != master_sha or "symbol" not in master.columns:
            raise ValueError
    except (OSError, ValueError, ImportError) as exc:
        raise MassiveReferenceAuditError("listing_or_master_invalid") from exc
    codes = sorted({value.strip() for value in master["symbol"].dropna().astype(str) if value.strip()})
    if not codes:
        raise MassiveReferenceAuditError("master_symbols_empty")
    items = [classify_master_code(code, reference, listings) for code in codes]
    counts = dict(sorted(Counter(item["classification"] for item in items).items()))
    master_set = set(codes)
    unmatched_reference = [classify_unmatched_reference_ticker(ticker, row, master_set, listings) for ticker, row in sorted(reference.items()) if ticker not in master_set]
    unmatched_reference_counts = dict(sorted(Counter(item["classification"] for item in unmatched_reference).items()))
    observed = datetime.now(timezone.utc)
    destination = root / "reference" / "massive-reference-audits" / f"{observed.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    destination.mkdir(parents=True, exist_ok=False)
    report = {"schema_version": SCHEMA, "observed_at": observed.isoformat(), "research_qualified": False,
              "inputs": {"reference_snapshot": str(reference_path), "reference_manifest_sha256": _sha256(reference_path / "manifest.json"),
                         "reference_all_tickers_sha256": reference_manifest["all_tickers_sha256"], "reference_page_hashes": {str(page["raw_relative_path"]): page["sha256"] for page in reference_manifest["pages"]},
                         "listing_snapshot": str(Path(listing_snapshot)), "listing_sha256": listing_hashes, "listing_footer": listing_footer,
                         "security_master_sha256": master_sha},
              "master_code_count": len(codes), "classification_counts": counts, "items": items,
              "unmatched_reference_ticker_count": len(unmatched_reference), "unmatched_reference_classification_counts": unmatched_reference_counts,
              "unmatched_reference_tickers": unmatched_reference,
              "warning": "Current reference/listing evidence only. Alias candidates are not applied automatically and absence never implies delisting or historical identity.",}
    _atomic_json(destination / "report.json", report)
    manifest = {"schema_version": SCHEMA, "status": "captured", "report_sha256": _sha256(destination / "report.json"), "master_code_count": len(codes),
                "classification_counts": counts, "unmatched_reference_ticker_count": len(unmatched_reference), "unmatched_reference_classification_counts": unmatched_reference_counts,
                "observed_at": observed.isoformat(), "research_qualified": False}
    _atomic_json(destination / "manifest.json", manifest)
    return {**manifest, "report_relative_path": (destination / "report.json").relative_to(root).as_posix()}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--security-master", required=True, type=Path)
    parser.add_argument("--reference-snapshot", required=True, type=Path)
    parser.add_argument("--listing-snapshot", required=True, type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = audit_massive_reference(args.data_root, args.security_master, args.reference_snapshot, args.listing_snapshot)
    except (MassiveReferenceAuditError, OSError):
        print(json.dumps({"status": "failed", "error_code": "massive_reference_audit_failed"}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
