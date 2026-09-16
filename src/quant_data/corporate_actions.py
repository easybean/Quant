"""Narrow, auditable Alpaca corporate-actions capture for research data.

This module deliberately supports only the P1-04 representative windows.  It
never writes into bars/ or derived/ and refuses to replace an existing
snapshot.  Callers supply credentials through ``load_alpaca_credentials``;
credentials and pagination token values are never persisted or printed.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from .constituents import sha256_file
from .pipeline import load_alpaca_credentials


ENDPOINT = "https://data.alpaca.markets/v1/corporate-actions"
SOURCE = "alpaca"
DATA_QUALITY = "complete"
SCHEMA_VERSION = 1
# Intentionally narrow P1-04 coverage; this is not a general historical job.
REPRESENTATIVE_WINDOWS = (
    ("AAPL_2016", "AAPL", "2016-01-25", "2016-02-20"),
    ("AAPL_2020", "AAPL", "2020-08-01", "2020-09-15"),
    ("TSLA_2022", "TSLA", "2022-08-01", "2022-09-15"),
    ("TWTR_2022", "TWTR", "2022-10-01", "2022-11-15"),
    ("ATVI_2023", "ATVI", "2023-10-01", "2023-11-15"),
)
CURATED_FIELDS = (
    "source_event_id", "source", "retrieved_at", "data_quality", "action_type",
    "process_date", "announcement_date", "ex_date", "record_date", "payable_date",
    "effective_date", "symbol_as_reported", "instrument_id", "cik", "cusip", "isin",
    "old_symbol", "new_symbol", "ratio_numerator", "ratio_denominator", "cash_amount",
    "currency", "new_instrument_id", "source_url", "confidence", "notes",
)
_ACTION_TYPES = {
    "cash_dividends": "cash_dividend", "forward_splits": "forward_split",
    "reverse_splits": "reverse_split", "unit_splits": "unit_split",
    "cash_mergers": "cash_merger", "stock_mergers": "stock_merger",
    "stock_and_cash_mergers": "stock_and_cash_merger", "spin_offs": "spin_off",
    "stock_dividends": "stock_dividend", "redemptions": "redemption",
    "name_changes": "name_change", "worthless_removals": "worthless_removal",
    "rights_distributions": "rights_distribution", "reorganizations": "reorganization",
    "partial_calls": "partial_call", "capital_gains_distributions": "capital_gains_distribution",
}


def _atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(contents)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(path, (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())


def _token_hash(token: str | None) -> str | None:
    return hashlib.sha256(token.encode()).hexdigest() if token else None


def _canonical_event(action_type: str, raw: dict[str, Any], retrieved_at: str) -> dict[str, Any]:
    """Map only documented source fields; unknown fields remain null, never zero."""
    symbol = raw.get("symbol") or raw.get("acquiree_symbol")
    event = {field: None for field in CURATED_FIELDS}
    event.update({
        "source_event_id": raw.get("id"), "source": SOURCE, "retrieved_at": retrieved_at,
        "data_quality": DATA_QUALITY, "action_type": action_type,
        "process_date": raw.get("process_date"), "announcement_date": raw.get("announcement_date"),
        "ex_date": raw.get("ex_date"), "record_date": raw.get("record_date"),
        "payable_date": raw.get("payable_date"), "effective_date": raw.get("effective_date"),
        "symbol_as_reported": symbol, "cusip": raw.get("cusip") or raw.get("acquiree_cusip"),
        "isin": raw.get("isin"), "old_symbol": raw.get("old_symbol"),
        "new_symbol": raw.get("new_symbol"), "currency": raw.get("currency"),
        "source_url": ENDPOINT, "confidence": "source_complete_unverified",
        "notes": "Mapped from immutable raw API snapshot; instrument identity is not resolved.",
    })
    if action_type in {"forward_split", "reverse_split", "unit_split"}:
        event["ratio_numerator"] = raw.get("new_rate")
        event["ratio_denominator"] = raw.get("old_rate")
    if action_type in {"cash_dividend", "cash_merger", "redemption", "partial_call", "capital_gains_distribution"}:
        event["cash_amount"] = raw.get("rate")
    return event


def _events_from_response(response: dict[str, Any], retrieved_at: str) -> list[dict[str, Any]]:
    actions = response.get("corporate_actions", {})
    if not isinstance(actions, dict):
        raise ValueError("Alpaca response corporate_actions must be an object")
    events: list[dict[str, Any]] = []
    for collection, rows in actions.items():
        if collection not in _ACTION_TYPES:
            raise ValueError(f"unsupported Alpaca corporate action collection: {collection}")
        if not isinstance(rows, list):
            raise ValueError(f"Alpaca collection {collection} must be an array")
        for row in rows:
            if not isinstance(row, dict) or not row.get("id"):
                raise ValueError(f"Alpaca collection {collection} contains an event without id")
            events.append(_canonical_event(_ACTION_TYPES[collection], row, retrieved_at))
    return events


def _fetch_window(
    *, symbol: str, start: str, end: str, headers: dict[str, str], request_get: Callable[..., Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return raw pages and query metadata, retaining token hashes only."""
    token: str | None = None
    pages: list[dict[str, Any]] = []
    metadata: list[dict[str, Any]] = []
    while True:
        params: dict[str, Any] = {
            "symbols": symbol, "start": start, "end": end, "limit": 1000,
            "sort": "asc", "data_quality": DATA_QUALITY,
        }
        if token:
            params["page_token"] = token
        response = request_get(ENDPOINT, params=params, headers=headers, timeout=45)
        if response.status_code in {401, 403}:
            raise PermissionError(f"Alpaca corporate-actions authorization failed (HTTP {response.status_code})")
        response.raise_for_status()
        body = response.json()
        if not isinstance(body, dict) or "corporate_actions" not in body:
            raise ValueError("Alpaca corporate-actions response has no corporate_actions object")
        pages.append(body)
        next_token = body.get("next_page_token")
        if next_token is not None and not isinstance(next_token, str):
            raise ValueError("Alpaca next_page_token must be string or null")
        metadata.append({
            "request": {"symbols": symbol, "start": start, "end": end, "limit": 1000,
                        "sort": "asc", "data_quality": DATA_QUALITY, "has_page_token": bool(token)},
            "http_status": response.status_code, "next_page_token_hash": _token_hash(next_token),
            "response_top_level_keys": sorted(body),
        })
        if not next_token:
            return pages, metadata
        if next_token == token:
            raise ValueError("Alpaca pagination token did not advance")
        token = next_token


def _golden_checks(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Small public-event checks; result is evidence, not a coverage assertion."""
    checks = (
        ("AAPL 2020 cash dividend", "AAPL", "cash_dividend", "cash_amount", 0.82),
        ("AAPL 2020 four-for-one split", "AAPL", "forward_split", "ratio_numerator", 4),
        ("TSLA 2022 three-for-one split", "TSLA", "forward_split", "ratio_numerator", 3),
        ("TWTR 2022 cash merger", "TWTR", "cash_merger", "cash_amount", 54.20),
        ("ATVI 2023 cash merger", "ATVI", "cash_merger", "cash_amount", 95.00),
    )
    results: list[dict[str, Any]] = []
    for label, symbol, action_type, field, expected in checks:
        candidates = [x for x in events if x["symbol_as_reported"] == symbol and x["action_type"] == action_type]
        observed = [x.get(field) for x in candidates]
        passed = any(float(value) == float(expected) for value in observed if value is not None)
        results.append({"sample": label, "expected": expected, "observed": observed, "passed": passed})
    return results


def capture_representative_snapshot(
    *, data_root: Path, snapshot_id: str, credential_file: Path | None = None,
    request_get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Capture only the fixed representative windows into immutable raw+curated roots."""
    if not snapshot_id or "/" in snapshot_id or "\\" in snapshot_id:
        raise ValueError("snapshot_id must be a single non-empty path component")
    raw_root = data_root / "reference" / "corporate-actions" / SOURCE / snapshot_id
    curated_root = data_root / "curated" / "reference" / "corporate-actions" / SOURCE / snapshot_id
    if raw_root.exists() or curated_root.exists():
        raise FileExistsError("refusing to overwrite existing corporate-actions snapshot")
    key, secret = load_alpaca_credentials(credential_file)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    retrieved_at = datetime.now(timezone.utc).isoformat()
    raw_pages: list[tuple[str, dict[str, Any]]] = []
    query_metadata: list[dict[str, Any]] = []
    for window_id, symbol, start, end in REPRESENTATIVE_WINDOWS:
        pages, metadata = _fetch_window(symbol=symbol, start=start, end=end, headers=headers, request_get=request_get)
        raw_pages.extend((window_id, page) for page in pages)
        query_metadata.extend({"window_id": window_id, **page_metadata} for page_metadata in metadata)
    events = [event for _, page in raw_pages for event in _events_from_response(page, retrieved_at)]
    if not any(x["symbol_as_reported"] == "AAPL" and x["process_date"] and str(x["process_date"])[:4] == "2016" for x in events):
        raise ValueError("narrow historical-start probe did not return a 2016 AAPL event")
    golden = _golden_checks(events)
    # Raw acquisition is still kept if an expected public sample fails, but the
    # report makes the snapshot ineligible for the researched asset pool.
    raw_root.mkdir(parents=True, exist_ok=False)
    curated_root.mkdir(parents=True, exist_ok=False)
    raw_files: list[dict[str, Any]] = []
    for number, (window_id, body) in enumerate(raw_pages, start=1):
        destination = raw_root / "pages" / f"{number:03d}-{window_id}.json"
        _atomic_json(destination, body)
        raw_files.append({"path": str(destination.relative_to(raw_root)), "sha256": sha256_file(destination),
                          "bytes": destination.stat().st_size, "window_id": window_id})
    _atomic_json(raw_root / "manifest.json", {
        "schema_version": SCHEMA_VERSION, "source": SOURCE, "endpoint": ENDPOINT,
        "retrieved_at": retrieved_at, "data_quality": DATA_QUALITY,
        "scope": "P1-04 representative narrow windows only; not a full-market corporate-actions download.",
        "queries": query_metadata, "files": raw_files,
        "credential_fingerprint": "not recorded", "limitations": [
            "Alpaca documents that corporate-action availability may be delayed; absence is not evidence of no event.",
            "Provider and account terms govern use; this local research snapshot is not licensed for redistribution.",
        ],
    })
    events_path = curated_root / "events.jsonl"
    _atomic_bytes(events_path, b"".join((json.dumps(event, sort_keys=True) + "\n").encode() for event in events))
    schema_hash = hashlib.sha256(json.dumps(CURATED_FIELDS).encode()).hexdigest()
    _atomic_json(curated_root / "schema.json", {"schema_version": SCHEMA_VERSION, "fields": CURATED_FIELDS,
                                                   "schema_hash": schema_hash, "mapping": "raw API fields are mapped by action type; unresolved identity and delisting fields are null"})
    _atomic_json(curated_root / "verification-report.json", {
        "snapshot_id": snapshot_id, "raw_snapshot": str(raw_root), "event_count": len(events),
        "golden_checks": golden, "eligible_for_actions_complete_scope": all(x["passed"] for x in golden),
        "delisting_resolution": "unknown; final return remains null/unknown and is excluded from total-return eligibility.",
    })
    return {"raw_root": str(raw_root), "curated_root": str(curated_root), "event_count": len(events),
            "golden_checks_passed": all(x["passed"] for x in golden)}
