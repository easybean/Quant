"""Capture an immutable, current Massive US-stock ticker reference snapshot.

This is supplier reference evidence for code-format diagnosis.  It does not
map securities, alter a master, or qualify data for research.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

import requests

from .massive_rate import wait_for_slot


ENDPOINT = "https://api.massive.com/v3/reference/tickers"
NAMESPACE = "massive-ticker-reference-v1"
MAX_PAGES = 50
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_FIELDS = ("ticker", "name", "cik", "composite_figi", "share_class_figi", "primary_exchange", "type", "market", "locale", "active", "currency_name")


class MassiveReferenceError(ValueError):
    """Stable, non-sensitive reference-capture failure."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


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


def _load_key(path: Path) -> str:
    try:
        key = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise MassiveReferenceError("credential_file_unreadable") from exc
    if not key or "\n" in key or "\r" in key:
        raise MassiveReferenceError("credential_file_invalid")
    return key


def _new_snapshot(root: Path, observed: datetime) -> Path:
    snapshot = root / "reference" / NAMESPACE / f"{observed.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    snapshot.mkdir(parents=True, exist_ok=False, mode=0o700)
    return snapshot


def _safe_next_cursor(value: Any, seen: set[str]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise MassiveReferenceError("next_url_invalid")
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != "https" or parsed.hostname not in {"api.massive.com", "api.polygon.io"} or parsed.port is not None
                or parsed.username is not None or parsed.password is not None or parsed.fragment or parsed.path != "/v3/reference/tickers"):
            raise ValueError
        pairs = parse_qsl(parsed.query, keep_blank_values=True, strict_parsing=True)
        if any(key not in {"cursor", "apiKey"} for key, _ in pairs):
            raise ValueError
        cursors = [item for key, item in pairs if key == "cursor"]
        if len(cursors) != 1 or not cursors[0] or cursors[0] in seen:
            raise ValueError
    except (TypeError, ValueError):
        raise MassiveReferenceError("next_url_invalid") from None
    seen.add(cursors[0])
    return cursors[0]


def _record(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        raise MassiveReferenceError("result_row_invalid")
    ticker = row.get("ticker")
    if not isinstance(ticker, str) or not ticker or ticker.strip() != ticker:
        raise MassiveReferenceError("result_ticker_invalid")
    output: dict[str, Any] = {"ticker": ticker}
    for field in ("name", "cik", "composite_figi", "share_class_figi", "primary_exchange", "type", "currency_name"):
        value = row.get(field)
        if value is not None and not isinstance(value, str):
            raise MassiveReferenceError("result_identity_field_invalid")
        output[field] = value
    if row.get("market") != "stocks" or row.get("locale") != "us":
        raise MassiveReferenceError("result_market_or_locale_invalid")
    if row.get("active") is not True:
        raise MassiveReferenceError("result_active_invalid")
    output.update({"market": "stocks", "locale": "us", "active": True})
    return output


def capture_massive_reference(
    data_root: Path,
    credential_file: Path,
    *,
    request_get: Callable[..., Any] = requests.get,
    wait_fn: Callable[[Path], None] | None = None,
    wait_for_lock: bool = False,
) -> dict[str, Any]:
    """Fetch at most 50 validated current-reference pages under shared limits."""
    root = Path(data_root)
    lock_path = root / "manifests" / "massive-network.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | (0 if wait_for_lock else fcntl.LOCK_NB))
        except BlockingIOError:
            return {"schema_version": "massive-ticker-reference-v1", "status": "skipped", "reason": "massive_network_lock_held"}
        key = _load_key(Path(credential_file))
        wait = wait_fn or wait_for_slot
        snapshot = _new_snapshot(root, _now())
        seen_tickers: set[str] = set()
        seen_cursors: set[str] = set()
        cursor: str | None = None
        pages: list[dict[str, Any]] = []
        records: list[dict[str, Any]] = []
        try:
            for page_number in range(1, MAX_PAGES + 1):
                params = ({"market": "stocks", "locale": "us", "active": "true", "limit": "1000", "sort": "ticker", "order": "asc"}
                          if cursor is None else {"cursor": cursor})
                try:
                    wait(root)
                    response = request_get(ENDPOINT, headers={"Authorization": f"Bearer {key}"}, params=params, timeout=30, allow_redirects=False)
                except Exception as exc:
                    raise MassiveReferenceError("transport_error") from exc
                status = getattr(response, "status_code", None)
                if isinstance(status, bool) or not isinstance(status, int):
                    raise MassiveReferenceError("http_invalid_status")
                if status != 200:
                    raise MassiveReferenceError(f"http_{status}")
                raw = getattr(response, "content", None)
                if not isinstance(raw, bytes):
                    raise MassiveReferenceError("response_content_invalid")
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise MassiveReferenceError("response_too_large")
                observed = _now().astimezone(timezone.utc).isoformat()
                page_name = f"pages/{page_number:03d}-response.json"
                page_path = snapshot / page_name
                page_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with page_path.open("xb") as handle:
                    handle.write(raw)
                os.chmod(page_path, 0o600)
                pages.append({"page": page_number, "raw_relative_path": page_name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "observed_at": observed, "cursor_present": cursor is not None})
                try:
                    payload = json.loads(raw)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise MassiveReferenceError("response_json_invalid") from exc
                if not isinstance(payload, dict) or payload.get("status") != "OK" or not isinstance(payload.get("results"), list):
                    raise MassiveReferenceError("response_results_invalid")
                if "count" in payload and (isinstance(payload["count"], bool) or not isinstance(payload["count"], int) or payload["count"] < 0 or payload["count"] != len(payload["results"])):
                    raise MassiveReferenceError("response_count_invalid")
                page_records = [_record(row) for row in payload["results"]]
                duplicates = seen_tickers.intersection(row["ticker"] for row in page_records)
                page_tickers = [row["ticker"] for row in page_records]
                if duplicates or len(page_tickers) != len(set(page_tickers)):
                    raise MassiveReferenceError("duplicate_ticker")
                seen_tickers.update(page_tickers)
                records.extend(page_records)
                cursor = _safe_next_cursor(payload.get("next_url"), seen_cursors)
                if cursor is None:
                    break
            else:
                raise MassiveReferenceError("max_pages_exceeded")
            if not records:
                raise MassiveReferenceError("response_results_empty")
        except MassiveReferenceError as exc:
            manifest = {"schema_version": "massive-ticker-reference-v1", "namespace": NAMESPACE, "status": "failed", "error_code": str(exc),
                        "snapshot_relative_path": snapshot.relative_to(root).as_posix(), "pages": pages, "research_qualified": False}
            _atomic_json(snapshot / "manifest.json", manifest)
            return manifest
        all_tickers = {"schema_version": "massive-ticker-reference-v1", "observed_start": pages[0]["observed_at"], "observed_end": pages[-1]["observed_at"],
                       "observation_model": "sequential_paginated_current_reference_not_atomic_pit_snapshot", "research_qualified": False, "tickers": records}
        _atomic_json(snapshot / "all-tickers.json", all_tickers)
        manifest = {
            "schema_version": "massive-ticker-reference-v1", "namespace": NAMESPACE, "status": "captured",
            "snapshot_relative_path": snapshot.relative_to(root).as_posix(), "source": ENDPOINT,
            "request": {"market": "stocks", "locale": "us", "active": True, "limit": 1000, "sort": "ticker", "order": "asc"},
            "pages": pages, "ticker_count": len(records), "all_tickers_sha256": hashlib.sha256((snapshot / "all-tickers.json").read_bytes()).hexdigest(),
            "identity_fields": list(_FIELDS),
            "observed_start": pages[0]["observed_at"], "observed_end": pages[-1]["observed_at"],
            "observation_model": "sequential_paginated_current_reference_not_atomic_pit_snapshot",
            "research_qualified": False, "warning": "Current supplier reference only; not historical identity, mapping, coverage, or research qualification.",
        }
        _atomic_json(snapshot / "manifest.json", manifest)
        return manifest


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path, help="Plain Massive API-key file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = capture_massive_reference(args.data_root, args.credential_file)
    except (MassiveReferenceError, OSError):
        print(json.dumps({"status": "failed", "error_code": "massive_reference_failed"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] in {"captured", "skipped"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
