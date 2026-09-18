"""Capture one immutable, unqualified Massive US grouped-daily response.

This adapter is deliberately narrow: it makes one authenticated, read-only
request for one explicit session and never updates the project's current-bar
store or browser catalogue.  Its output is acquisition evidence, not a
research-qualified data set.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import tempfile
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .sync_scheduler import completed_session


ENDPOINT_TEMPLATE = "https://api.massive.com/v2/aggs/grouped/locale/us/market/stocks/{date}"
NAMESPACE = "massive-daily-v1"
MAX_RESPONSE_BYTES = 32 * 1024 * 1024
_NEW_YORK = ZoneInfo("America/New_York")
_BAR_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "available_at",
    "retrieved_at",
    "actions_status",
)


class MassiveDailyError(ValueError):
    """A static, non-sensitive adapter failure code."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("observed_at_must_be_timezone_aware")
    return value.astimezone(timezone.utc).isoformat()


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_parquet(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _load_api_key(credential_file: Path) -> str:
    """Load one plain API-key line without exposing its path or contents."""
    try:
        value = credential_file.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeDecodeError) as exc:
        raise MassiveDailyError("credential_file_unreadable") from exc
    if not value or "\n" in value or "\r" in value:
        raise MassiveDailyError("credential_file_invalid")
    return value


def _new_snapshot(data_root: Path, observed_at: datetime) -> Path:
    name = f"{observed_at.strftime('%Y%m%dT%H%M%SZ')}-{uuid.uuid4().hex}"
    snapshot = data_root / "reference" / NAMESPACE / name
    snapshot.mkdir(parents=True, exist_ok=False)
    return snapshot


def _base_manifest(
    *, snapshot: Path, data_root: Path, requested_date: date, observed_at: str, raw: bytes
) -> dict[str, Any]:
    endpoint = ENDPOINT_TEMPLATE.format(date=requested_date.isoformat())
    return {
        "schema_version": "massive-daily-reference-v1",
        "namespace": NAMESPACE,
        "snapshot_relative_path": snapshot.relative_to(data_root).as_posix(),
        "source": ENDPOINT_TEMPLATE,
        "request": {
            "url": endpoint,
            "date": requested_date.isoformat(),
            "adjusted": False,
            "include_otc": False,
            "method": "GET",
        },
        "observed_at": observed_at,
        "response_sha256": hashlib.sha256(raw).hexdigest(),
        "response_bytes": len(raw),
        "diagnostics_sha256": None,
        "normalized_sha256": None,
        "qualified": False,
        "research_qualified": False,
        "actions_status": "unknown",
        "warning": (
            "Unadjusted provider response captured at observation time only; "
            "corporate actions, historical identity, delistings, coverage and "
            "research qualification are unknown."
        ),
    }


def _reject(index: int, code: str) -> dict[str, Any]:
    return {"index": index, "code": code}


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        number = float(value)
    except (OverflowError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _valid_symbol(value: Any) -> bool:
    return isinstance(value, str) and bool(value) and value.strip() == value


def _row_to_bar(row: Any, requested_date: date, observed_at: str) -> tuple[dict[str, Any] | None, str | None]:
    """Validate one response row without repairing, filling, or coercing it."""
    if not isinstance(row, dict):
        return None, "row_not_object"
    symbol = row.get("T")
    if not _valid_symbol(symbol):
        return None, "invalid_symbol"
    timestamp = _finite_number(row.get("t"))
    if timestamp is None or not timestamp.is_integer():
        return None, "invalid_timestamp"
    try:
        session_date = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).astimezone(_NEW_YORK).date()
    except (OverflowError, OSError, ValueError):
        return None, "invalid_timestamp"
    if session_date != requested_date:
        return None, "timestamp_not_requested_ny_session"

    values = {name: _finite_number(row.get(source)) for name, source in {
        "open": "o", "high": "h", "low": "l", "close": "c", "volume": "v"
    }.items()}
    if any(value is None for value in values.values()):
        return None, "invalid_ohlcv_type_or_finiteness"
    assert all(value is not None for value in values.values())
    open_, high, low, close, volume = (
        values["open"], values["high"], values["low"], values["close"], values["volume"]
    )
    assert open_ is not None and high is not None and low is not None and close is not None and volume is not None
    if min(open_, high, low, close) <= 0:
        return None, "nonpositive_ohlc"
    if volume < 0:
        return None, "negative_volume"
    if high < max(open_, low, close) or low > min(open_, high, close):
        return None, "inconsistent_ohlc"
    return {
        "symbol": symbol,
        "date": requested_date.isoformat(),
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "available_at": observed_at,
        "retrieved_at": observed_at,
        "actions_status": "unknown",
    }, None


def _validate_payload(payload: Any) -> tuple[list[Any] | None, str | None]:
    if not isinstance(payload, dict):
        return None, "response_not_object"
    if payload.get("status") not in {"OK", "DELAYED"}:
        return None, "response_status_invalid"
    if payload.get("adjusted") is not False:
        return None, "response_adjusted_not_false"
    rows = payload.get("results")
    count = payload.get("resultsCount")
    if not isinstance(rows, list) or not rows:
        return None, "response_results_empty_or_invalid"
    if isinstance(count, bool) or not isinstance(count, int) or count != len(rows):
        return None, "response_results_count_invalid"
    return rows, None


def capture_massive_daily(
    data_root: Path,
    requested_date: date,
    credential_file: Path,
    *,
    now: datetime | None = None,
    request_get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Capture one response, retaining raw evidence even if JSON validation fails.

    No current bars, catalogue pointers, retry records, or credentials are
    written.  All non-200 and transport failures occur before a snapshot is
    created because their response bodies are intentionally never inspected or
    stored.
    """
    if not isinstance(requested_date, date) or isinstance(requested_date, datetime):
        raise MassiveDailyError("requested_date_invalid")
    clock = now or _utc_now()
    if clock.tzinfo is None:
        raise MassiveDailyError("now_must_be_timezone_aware")
    if requested_date > completed_session(clock):
        raise MassiveDailyError("requested_date_after_completed_session")

    api_key = _load_api_key(Path(credential_file))
    endpoint = ENDPOINT_TEMPLATE.format(date=requested_date.isoformat())
    try:
        response = request_get(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            params={"adjusted": "false", "include_otc": "false"},
            timeout=30,
            allow_redirects=False,
        )
    except Exception as exc:
        # Transport implementations may include request headers in their error
        # text, so expose only a stable code.
        raise MassiveDailyError("transport_error") from exc
    status_code = getattr(response, "status_code", None)
    if isinstance(status_code, bool) or not isinstance(status_code, int):
        raise MassiveDailyError("http_invalid_status")
    if status_code != 200:
        raise MassiveDailyError(f"http_{status_code}")
    raw = getattr(response, "content", None)
    if not isinstance(raw, bytes):
        raise MassiveDailyError("response_content_invalid")
    if len(raw) > MAX_RESPONSE_BYTES:
        raise MassiveDailyError("response_too_large")

    observed_at = _iso_utc(_utc_now())
    snapshot = _new_snapshot(Path(data_root), datetime.fromisoformat(observed_at))
    with (snapshot / "response.json").open("xb") as handle:
        handle.write(raw)
    manifest = _base_manifest(
        snapshot=snapshot,
        data_root=Path(data_root),
        requested_date=requested_date,
        observed_at=observed_at,
        raw=raw,
    )

    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        diagnostics = {
            "schema_version": "massive-daily-validation-v1",
            "accepted_count": 0,
            "accepted_rows": [],
            "rejected_count": 0,
            "rejected_rows": [],
            "response_error": "response_json_invalid",
        }
        manifest.update({"status": "validation_failed", "error_code": "response_json_invalid", "accepted_rows": 0, "rejected_rows": 0})
        _atomic_json(snapshot / "diagnostics.json", diagnostics)
        manifest["diagnostics_sha256"] = hashlib.sha256((snapshot / "diagnostics.json").read_bytes()).hexdigest()
        _atomic_json(snapshot / "manifest.json", manifest)
        return manifest

    rows, response_error = _validate_payload(payload)
    if response_error is not None:
        diagnostics = {
            "schema_version": "massive-daily-validation-v1",
            "accepted_count": 0,
            "accepted_rows": [],
            "rejected_count": 0,
            "rejected_rows": [],
            "response_error": response_error,
        }
        manifest.update({"status": "validation_failed", "error_code": response_error, "accepted_rows": 0, "rejected_rows": 0})
        _atomic_json(snapshot / "diagnostics.json", diagnostics)
        manifest["diagnostics_sha256"] = hashlib.sha256((snapshot / "diagnostics.json").read_bytes()).hexdigest()
        _atomic_json(snapshot / "manifest.json", manifest)
        return manifest

    assert rows is not None
    accepted: list[dict[str, Any]] = []
    accepted_indices: list[int] = []
    rejected: list[dict[str, Any]] = []
    # A repeated provider ticker makes every occurrence ambiguous.  Retaining
    # the first row would be an undocumented repair, so quarantine them all.
    symbol_counts: dict[str, int] = {}
    for row in rows:
        symbol = row.get("T") if isinstance(row, dict) else None
        if _valid_symbol(symbol):
            assert isinstance(symbol, str)
            symbol_counts[symbol] = symbol_counts.get(symbol, 0) + 1
    for index, row in enumerate(rows):
        symbol = row.get("T") if isinstance(row, dict) else None
        if _valid_symbol(symbol) and symbol_counts[symbol] > 1:
            rejected.append(_reject(index, "duplicate_symbol"))
            continue
        bar, error = _row_to_bar(row, requested_date, observed_at)
        if error is not None:
            rejected.append(_reject(index, error))
            continue
        assert bar is not None
        accepted.append(bar)
        accepted_indices.append(index)

    diagnostics = {
        "schema_version": "massive-daily-validation-v1",
        "accepted_count": len(accepted),
        "accepted_rows": accepted_indices,
        "rejected_count": len(rejected),
        "rejected_rows": rejected,
        "response_error": None,
    }
    _atomic_json(snapshot / "diagnostics.json", diagnostics)
    manifest["diagnostics_sha256"] = hashlib.sha256((snapshot / "diagnostics.json").read_bytes()).hexdigest()
    manifest.update({"accepted_rows": len(accepted), "rejected_rows": len(rejected)})
    if not accepted:
        manifest.update({"status": "validation_failed", "error_code": "no_accepted_rows"})
        _atomic_json(snapshot / "manifest.json", manifest)
        return manifest

    frame = pd.DataFrame(accepted, columns=_BAR_COLUMNS)
    _atomic_parquet(frame, snapshot / "bars.parquet")
    manifest["normalized_sha256"] = hashlib.sha256((snapshot / "bars.parquet").read_bytes()).hexdigest()
    manifest.update({"status": "captured", "error_code": None, "normalized_file": "bars.parquet"})
    _atomic_json(snapshot / "manifest.json", manifest)
    return manifest


def _parse_date(value: str) -> date:
    try:
        if len(value) != 10 or value[4] != "-" or value[7] != "-":
            raise ValueError
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must use YYYY-MM-DD") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, type=_parse_date, help="NYSE session date (YYYY-MM-DD)")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path, help="Plain Massive API-key file")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = capture_massive_daily(args.data_root, args.date, args.credential_file)
    except MassiveDailyError as exc:
        print(json.dumps({"status": "failed", "error_code": str(exc)}))
        return 2
    except OSError:
        print(json.dumps({"status": "failed", "error_code": "local_output_error"}))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "captured" else 2


if __name__ == "__main__":
    raise SystemExit(main())
