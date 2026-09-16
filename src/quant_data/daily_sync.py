"""Conservative Yahoo daily-bar synchronizer.

This is an acquisition/audit utility, not a qualification or research-data
publisher.  Yahoo responses may be retrospectively adjusted and do not carry
row-level availability times.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd

from .pipeline import default_yfinance_downloader, normalize_bars, symbol_key

NAMESPACE = "yahoo-daily-v1"
PROVIDER = "yfinance"
START_FLOOR = date(2016, 1, 1)


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, default=str)
            handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _atomic_parquet(frame: pd.DataFrame, path: Path, *, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=index)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_record(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True, default=str) + "\n")


def _read_master(path: Path) -> pd.DataFrame:
    if path.suffix.lower() in {".csv", ".tsv"}:
        return pd.read_csv(path, sep="\t" if path.suffix.lower() == ".tsv" else ",")
    return pd.read_parquet(path)


def _latest_attempts(records_path: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    if not records_path.exists():
        return result
    with records_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                row = json.loads(line)
                if not isinstance(row, dict):
                    continue
                symbol, attempted = str(row.get("symbol", "")), str(row.get("attempted_at", ""))
                if symbol and attempted >= result.get(symbol, ""):
                    result[symbol] = attempted
            except (TypeError, ValueError):
                continue
    return result


def _target_end(now: datetime) -> date:
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    ny = now.astimezone(ZoneInfo("America/New_York"))
    # Begin with UTC's previous calendar day.  Before the conservative 22:00
    # ET publication threshold, also clamp to New York's previous date.  This
    # deliberately does not infer trading sessions: weekends/holidays are only
    # harmless empty-request dates, never fabricated bars.
    utc_previous = now.astimezone(timezone.utc).date() - timedelta(days=1)
    return min(utc_previous, ny.date() - timedelta(days=1)) if ny.hour < 22 else utc_previous


def _raw_actions_status(frame: pd.DataFrame) -> str:
    columns = {str(column).strip().lower().replace(" ", "_") for column in frame.columns}
    return "included" if {"dividends", "stock_splits"}.issubset(columns) else "unknown"


def _raw_columns(frame: pd.DataFrame) -> set[str]:
    return {str(column).strip().lower().replace(" ", "_") for column in frame.columns}


def _validate_normalized(frame: pd.DataFrame, start: date, end: date) -> None:
    days = pd.to_datetime(frame["date"], errors="raise").dt.date
    if ((days < start) | (days > end)).any():
        raise ValueError("response_date_outside_requested_window")
    values = frame[["open", "high", "low", "close", "volume"]].apply(pd.to_numeric, errors="coerce")
    if not values.notna().all().all() or not values.map(lambda value: math.isfinite(float(value))).all().all():
        raise ValueError("invalid_non_finite_ohlcv")
    if (values[["open", "high", "low", "close"]] <= 0).any().any() or (values["volume"] < 0).any():
        raise ValueError("invalid_ohlcv_range")
    if (values["high"] < values[["open", "low", "close"]].max(axis=1)).any() or (
        values["low"] > values[["open", "high", "close"]].min(axis=1)
    ).any():
        raise ValueError("invalid_ohlc_consistency")


def _error_code(exc: Exception) -> str:
    if isinstance(exc, ValueError) and str(exc) in {
        "response_date_outside_requested_window", "invalid_non_finite_ohlcv",
        "invalid_ohlcv_range", "invalid_ohlc_consistency",
    }:
        return str(exc)
    if isinstance(exc, ValueError):
        return "invalid_response"
    return "download_or_storage_failed"


def _load_existing(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        return None
    try:
        frame = pd.read_parquet(path)
        required = {"date", "source", "open", "high", "low", "close", "volume"}
        if not required.issubset(frame.columns) or not (frame["source"] == PROVIDER).all():
            raise ValueError("current_bar_schema_or_source_invalid")
        dates = pd.to_datetime(frame["date"], errors="raise")
        if dates.empty or dates.isna().any():
            raise ValueError("current_bar_series_empty")
        return frame
    except Exception as exc:
        raise RuntimeError("current_bar_read_failed") from exc


def run_sync(
    security_master: Path,
    data_root: Path,
    start: date = START_FLOOR,
    end: date | None = None,
    max_symbols: int = 0,
    request_delay: float = 1.0,
    max_consecutive_failures: int = 3,
    max_backfill_symbols: int = 0,
    bootstrap_lookback_days: int = 0,
    downloader: Callable[[str, date, date], pd.DataFrame] = default_yfinance_downloader,
    now: datetime | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Synchronize active Stock/ETF symbols, preserving every raw response."""
    start = max(start, START_FLOOR)
    now = now or datetime.now(timezone.utc)
    allowed_end = _target_end(now)
    if end is not None and end > allowed_end:
        raise ValueError("end_exceeds_conservative_yahoo_cutoff")
    target = end or allowed_end
    if start > target:
        raise ValueError("start_after_target_end")
    if min(max_symbols, max_backfill_symbols, bootstrap_lookback_days) < 0 or max_consecutive_failures < 1:
        raise ValueError("limits must be non-negative and max_consecutive_failures positive")
    if not math.isfinite(request_delay) or request_delay < 0:
        raise ValueError("request_delay_must_be_finite_non_negative")

    manifest = data_root / "manifests" / NAMESPACE
    records_path = manifest / "records.jsonl"
    lock_path = manifest / "sync.lock"
    manifest.mkdir(parents=True, exist_ok=True)
    lock_handle = lock_path.open("a+")
    try:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "lock_held", "requested": 0, "attempted": 0,
                    "success": 0, "failed": 0, "deferred": 0, "circuit_open": False}

        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        summary: dict[str, Any] = {"run_id": run_id, "namespace": NAMESPACE, "status": "running",
            "started_at": datetime.now(timezone.utc).isoformat(), "requested": 0, "attempted": 0,
            "success": 0, "failed": 0, "deferred": 0, "max_data_date": None, "circuit_open": False,
            "bootstrap_lookback_days": bootstrap_lookback_days,
            "bootstrap_window": (
                "requested start for cold symbols" if bootstrap_lookback_days == 0
                else f"last {bootstrap_lookback_days} calendar days ending at target for cold symbols"
            ),
            "unqualified": True, "warning": "Yahoo data is not row-level PIT or research-qualified; price adjustment semantics require separate verification and may include historical split adjustments."}
        def write_progress() -> None:
            _atomic_json(manifest / f"run-{run_id}.json", summary)
            _atomic_json(manifest / "latest.json", summary)

        write_progress()

        master = _read_master(security_master)
        required = {"symbol", "status", "asset_type"}
        if not required.issubset(master.columns):
            raise ValueError("security_master_missing_required_columns")
        selected = master[
            master["status"].fillna("").astype(str).str.casefold().eq("active")
            & master["asset_type"].fillna("").astype(str).str.casefold().isin({"stock", "etf"})
        ]["symbol"].dropna().astype(str).str.strip().str.upper()
        symbols = list(dict.fromkeys(symbol for symbol in selected if symbol))
        attempts = _latest_attempts(records_path)
        symbols.sort(key=lambda symbol: (attempts.get(symbol, ""), symbol))
        requested = len(symbols)
        deferred = 0
        if max_symbols:
            deferred += max(0, len(symbols) - max_symbols)
            symbols = symbols[:max_symbols]

        summary.update({"requested": requested, "deferred": deferred})
        if not symbols:
            summary.update({"status": "failed", "error_code": "no_eligible_active_stock_or_etf_symbols"})
        consecutive = 0
        limited_backfills = 0
        for index, symbol in enumerate(symbols):
            destination = data_root / "bars" / "daily" / f"provider={PROVIDER}" / f"namespace={NAMESPACE}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
            try:
                existing = _load_existing(destination)
            except Exception as exc:
                # A malformed current series must never cause a full-history
                # overwrite.  Record it as a failed sync attempt and let the
                # circuit breaker protect the rest of the provider run.
                summary["attempted"] += 1
                _append_record(records_path, {"run_id": run_id, "symbol": symbol,
                    "attempted_at": datetime.now(timezone.utc).isoformat(), "requested_start": None,
                    "requested_end": target.isoformat(), "namespace": NAMESPACE, "source": PROVIDER,
                    "unqualified": True, "status": "failed", "error_type": type(exc).__name__,
                    "error_code": "current_bar_read_failed"})
                summary["failed"] += 1
                consecutive += 1
                if consecutive >= max_consecutive_failures:
                    summary["circuit_open"] = True
                    summary["deferred"] += len(symbols) - index - 1
                write_progress()
                if consecutive >= max_consecutive_failures:
                    break
                continue
            if existing is None:
                if max_backfill_symbols and limited_backfills >= max_backfill_symbols:
                    # Keep scanning: the bootstrap limit applies only to cold
                    # symbols, while established series must still receive
                    # their daily correction probes.
                    summary["deferred"] += 1
                    continue
                limited_backfills += 1
                request_start = start if bootstrap_lookback_days == 0 else max(
                    start, target - timedelta(days=bootstrap_lookback_days - 1)
                )
            else:
                current_max = pd.to_datetime(existing["date"], errors="raise").max().date()
                request_start = max(start, current_max - timedelta(days=7))
            if request_start > target:
                request_start = target  # still permits a one-day correction probe
            summary["attempted"] += 1
            record: dict[str, Any] = {"run_id": run_id, "symbol": symbol, "attempted_at": datetime.now(timezone.utc).isoformat(),
                "requested_start": request_start.isoformat(), "requested_end": target.isoformat(), "namespace": NAMESPACE,
                "source": PROVIDER, "unqualified": True}
            try:
                raw = downloader(symbol, request_start, target)
                if not isinstance(raw, pd.DataFrame) or raw.empty:
                    raise ValueError("invalid_response")
                archive = data_root / "reference" / NAMESPACE / run_id / f"{symbol_key(symbol)}.parquet"
                _atomic_parquet(raw, archive, index=True)  # archive before normalization/current mutation
                digest = hashlib.sha256(archive.read_bytes()).hexdigest()
                record.update({"archive": str(archive), "archive_sha256": digest,
                    "retrieved_at": datetime.now(timezone.utc).isoformat(), "row_count": int(len(raw)),
                    "actions_status": _raw_actions_status(raw)})
                normalized = normalize_bars(raw, symbol, PROVIDER, "raw_ohlc_with_adjusted_close_and_actions")
                raw_columns = _raw_columns(raw)
                # normalize_bars uses zeroes as a compatibility default.  For
                # this raw archive namespace, absent action columns are unknown,
                # not evidence that no action occurred.
                for optional in ("adj_close", "dividends", "stock_splits"):
                    if optional not in raw_columns:
                        normalized[optional] = pd.NA
                normalized["actions_status"] = record["actions_status"]
                normalized["retrieved_at"] = record["retrieved_at"]
                _validate_normalized(normalized, request_start, target)
                merged = normalized if existing is None else pd.concat([existing, normalized], ignore_index=True)
                merged["date"] = pd.to_datetime(merged["date"], errors="raise")
                merged = merged.drop_duplicates("date", keep="last").sort_values("date", ignore_index=True)
                _atomic_parquet(merged, destination)
                record.update({"status": "success"})
                summary["success"] += 1
                max_day = pd.to_datetime(merged["date"]).max().date().isoformat()
                summary["max_data_date"] = max(max_day, summary["max_data_date"] or max_day)
                consecutive = 0
            except Exception as exc:
                record.update({"status": "failed", "error_type": type(exc).__name__, "error_code": _error_code(exc)})
                summary["failed"] += 1
                consecutive += 1
            _append_record(records_path, record)
            if consecutive >= max_consecutive_failures:
                summary["circuit_open"] = True
                summary["deferred"] += len(symbols) - index - 1
            write_progress()
            if consecutive >= max_consecutive_failures:
                break
            if request_delay > 0 and index < len(symbols) - 1:
                sleep(request_delay)
        if summary["status"] != "failed":
            if summary["failed"] or summary["circuit_open"]:
                summary["status"] = "failed"
            elif summary["deferred"]:
                summary["status"] = "partial"
                summary["bootstrap_partial"] = bool(max_backfill_symbols)
            else:
                summary["status"] = "success"
        summary["finished_at"] = datetime.now(timezone.utc).isoformat()
        write_progress()
        return summary
    finally:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
        finally:
            lock_handle.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync unqualified Yahoo daily bars with immutable raw archives")
    parser.add_argument("--security-master", required=True, type=Path)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--max-symbols", type=int, default=0)
    parser.add_argument("--max-backfill-symbols", type=int, default=0)
    parser.add_argument("--bootstrap-lookback-days", type=int, default=0)
    parser.add_argument("--request-delay", type=float, default=1.0)
    parser.add_argument("--max-consecutive-failures", type=int, default=3)
    args = parser.parse_args(argv)
    try:
        result = run_sync(**vars(args))
    except (OSError, ValueError) as exc:
        # Do not expose provider/storage exception text, which can contain a
        # URL, token, or other environment detail.
        print(json.dumps({"status": "failed", "error_type": type(exc).__name__, "error_code": "sync_configuration_or_io_failed"}))
        return 1
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["status"] == "failed":
        return 1
    return 2 if result["status"] == "partial" else 0


if __name__ == "__main__":
    raise SystemExit(main())
