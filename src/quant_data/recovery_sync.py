"""Source-isolated Nasdaq or delayed Alpaca SIP recovery for Yahoo failures.

This namespace is deliberately separate from Yahoo and is an acquisition view,
not a repair of Yahoo data nor a research-qualified total-return series.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import fcntl
import hashlib
import json
import math
import os
import tempfile
import time
import uuid
from datetime import date, datetime, time as daytime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests
from zoneinfo import ZoneInfo

from .daily_sync import _target_end, _validate_normalized, yahoo_daily_history_downloader
from .sync_eligibility import select_sync_symbols
from .pipeline import FatalProviderError, PermanentDownloadError, make_alpaca_downloader, nasdaq_web_downloader, normalize_bars, symbol_key

NAMESPACE = "nasdaq-daily-recovery-v1"
PROVIDER = "nasdaq"
SOURCE = "nasdaq_web_unadjusted"
YAHOO_NAMESPACE = "yahoo-daily-v1"
START_FLOOR = date(2016, 1, 1)
# Explicit mappings verified against issuer names and live Yahoo metadata.
# Never infer preferred/unit/warrant identity from punctuation alone.
VERIFIED_YAHOO_ALIASES = {"AAC-U": "AAC-UN", "AAC.U": "AAC-UN", "ABR$D": "ABR-PD", "ABR-P-D": "ABR-PD"}
ALIAS_NAMESPACE = "yahoo-symbol-recovery-v1"
ALPACA_NAMESPACE = "alpaca-sip-recovery-v1"
ALPACA_SOURCE = "alpaca_stock_historical_v2"


class SymbolHistoryUnknown(RuntimeError):
    """A healthy HTTP response has no usable history for this symbol/window."""


class SymbolRequestRejected(RuntimeError):
    """Provider rejects this symbol query; this does not establish delisting."""
_NY = ZoneInfo("America/New_York")


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".tmp-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2, default=str)
            handle.write("\n")
        os.replace(name, path)
    finally:
        if os.path.exists(name): os.unlink(name)


def _atomic_parquet(frame: pd.DataFrame, path: Path, *, index: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=index)
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")


def _read_master(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.suffix.lower() == ".csv" else pd.read_parquet(path)


def _latest_yahoo_failures(path: Path) -> dict[str, dict[str, Any]]:
    """Last Yahoo terminal record wins: a later Yahoo success cancels recovery."""
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists(): return latest
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
            if row.get("status") not in {"success", "failed"}:
                continue
            symbol = str(row.get("symbol", "")).strip().upper()
            stamp = str(row.get("attempted_at", row.get("observed_at", "")))
            if symbol and stamp >= str(latest.get(symbol, {}).get("_stamp", "")):
                row["_stamp"] = stamp
                latest[symbol] = row
        except (ValueError, TypeError):
            continue
    return {symbol: row for symbol, row in latest.items() if row.get("status") == "failed" and row.get("requested_start")}


def _recovery_state(path: Path) -> tuple[dict[str, str], dict[str, str], dict[str, date]]:
    attempts, markers, recovered_end = {}, {}, {}
    if not path.exists(): return attempts, markers, recovered_end
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line); symbol = str(row.get("symbol", "")); stamp = str(row.get("attempted_at", ""))
            if symbol and stamp >= attempts.get(symbol, ""): attempts[symbol] = stamp
            marker = str(row.get("source_attempted_at", ""))
            if symbol and marker and row.get("status") in {"success", "failed", "unavailable"}: markers[symbol] = marker
            if symbol and row.get("status") == "success":
                try: recovered_end[symbol] = max(recovered_end.get(symbol, date.min), date.fromisoformat(str(row["actual_last_date"])))
                except (KeyError, TypeError, ValueError): pass
        except (ValueError, TypeError): continue
    return attempts, markers, recovered_end


def _error_code(exc: Exception) -> str:
    text = str(exc).lower()
    if "429" in text: return "http_429"
    if "401" in text: return "http_401"
    if "403" in text: return "http_403"
    if isinstance(exc, FatalProviderError): return "provider_authorization_failed"
    if isinstance(exc, ValueError) and str(exc) in {"response_date_outside_requested_window", "invalid_non_finite_ohlcv", "invalid_ohlcv_range", "invalid_ohlc_consistency"}: return str(exc)
    return "download_or_validation_failed"


def _alpaca_sip_request_wrapper(now: datetime, request_get: Callable[..., Any] = requests.get) -> Callable[..., Any]:
    """Force the free SIP recovery request into explicit NY daily semantics.

    ``make_alpaca_downloader`` emits date-only daily endpoints at UTC midnight.
    The recovery policy instead makes the day boundary explicit as New York
    midnight converted to UTC, supplies ``asof=-``, and refuses an end later
    than the documented delayed-data safety boundary before any HTTP call.
    """
    now_utc = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    cutoff = now_utc - pd.Timedelta(minutes=15)

    def request(url: str, *, params: dict[str, Any], headers: dict[str, str], timeout: float, **kwargs: Any) -> Any:
        adjusted = dict(params)
        try:
            start_day = date.fromisoformat(str(adjusted["start"])[:10])
            end_day = date.fromisoformat(str(adjusted["end"])[:10])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("alpaca_daily_window_invalid") from exc
        start_utc = datetime.combine(start_day, daytime.min, tzinfo=_NY).astimezone(timezone.utc)
        # make_alpaca_downloader supplies the following date as an exclusive
        # endpoint; the HTTP API's end is inclusive, so subtract one microsecond.
        end_utc = datetime.combine(end_day, daytime.min, tzinfo=_NY).astimezone(timezone.utc) - pd.Timedelta(microseconds=1)
        if end_utc > cutoff:
            # The completed day's bar is timestamped at NY midnight. Before
            # the *following* midnight, its exclusive day boundary is still
            # in the future; use the legal delayed cutoff, but only when that
            # entire requested day passed the conservative publication gate.
            if end_day - pd.Timedelta(days=1) > _target_end(now_utc) or start_utc >= cutoff:
                raise ValueError("alpaca_end_within_15_minute_delay_window")
            end_utc = cutoff
        adjusted.update({"start": start_utc.isoformat().replace("+00:00", "Z"),
                         "end": end_utc.isoformat().replace("+00:00", "Z"), "asof": "-"})
        response = request_get(url, params=adjusted, headers=headers, timeout=min(float(timeout), 30.0), **kwargs)
        if getattr(response, "status_code", None) in {400, 422}:
            raise SymbolRequestRejected("symbol_request_rejected")
        if getattr(response, "status_code", None) == 200:
            payload = response.json()
            if isinstance(payload, dict) and "bars" in payload and payload["bars"] in (None, []):
                raise SymbolHistoryUnknown("empty_history_unknown")
        return response

    return request


def _publish_catalogue(root: Path, symbol: str, path: Path, frame: pd.DataFrame, *, provider: str = PROVIDER, namespace: str = NAMESPACE) -> None:
    destination = root / "catalogue" / "us-daily-browser-v1.json"
    if not destination.exists(): raise ValueError("browser_catalogue_missing")
    lock_path = destination.parent / "us-daily-browser-refresh.lock"
    with lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        payload = json.loads(destination.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("schema_version") != "us-daily-browser-v1" or not isinstance(payload.get("series"), list):
            raise ValueError("browser_catalogue_invalid")
        key = symbol_key(symbol); relative = path.relative_to(root / "bars" / "daily").as_posix()
        entries = [x for x in payload["series"] if not (isinstance(x, dict) and x.get("provider") == provider and x.get("namespace") == namespace and x.get("symbol") == symbol)]
        dates = pd.to_datetime(frame["date"], errors="raise")
        entries.append({"series_id": f"{provider}:{namespace}:{key}", "symbol": symbol, "provider": provider, "namespace": namespace,
                        "raw_relative_path": relative, "first_date": dates.min().date().isoformat(), "last_date": dates.max().date().isoformat(), "rows": len(frame),
                        "quality_warning": "独立补数来源；公司行为未验证，仅可拼接用于浏览，不可用于研究回测。"})
        payload["series"] = sorted(entries, key=lambda x: (str(x.get("symbol", "")), str(x.get("last_date", ""))), reverse=False)
        _atomic_json(destination, payload)


def run_recovery(security_master: Path, data_root: Path, *, max_runtime_seconds: float = 64800, request_delay: float = 2.0,
                 max_consecutive_failures: int = 3, downloader: Callable[[str, date, date], pd.DataFrame] | None = None,
                 recovery_provider: str = "nasdaq", credential_file: Path | None = None,
                 now: datetime | None = None, sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                 gap_queue: Path | None = None) -> dict[str, Any]:
    if max_runtime_seconds < 0 or request_delay < 0 or max_consecutive_failures < 1: raise ValueError("invalid_limits")
    now = now or datetime.now(timezone.utc)
    default_downloader = downloader is None
    if recovery_provider not in {"nasdaq", "alpaca"}: raise ValueError("invalid_recovery_provider")
    is_alpaca = recovery_provider == "alpaca"
    if downloader is None:
        # SIP is explicit.  request_retries=0 guarantees this scheduler never
        # changes proxy, backs off, or silently switches to IEX after an error.
        downloader = make_alpaca_downloader(feed="sip", credential_file=credential_file, request_retries=0,
                                             request_get=_alpaca_sip_request_wrapper(now)) if is_alpaca else nasdaq_web_downloader
    base_provider, base_namespace, base_source, base_adjustment = (
        ("alpaca", ALPACA_NAMESPACE, ALPACA_SOURCE, "raw") if is_alpaca else (PROVIDER, NAMESPACE, SOURCE, "unadjusted")
    )
    target = _target_end(now)
    manifest = data_root / "manifests" / base_namespace; records = manifest / "records.jsonl"; manifest.mkdir(parents=True, exist_ok=True)
    summary: dict[str, Any] = {"namespace": base_namespace, "source": base_source, "recovery_provider": recovery_provider, "status": "running", "requested": 0, "attempted": 0, "success": 0, "failed": 0, "deferred": 0, "circuit_open": False, "target_end": target.isoformat()}
    with ExitStack() as resources, (manifest / "sync.lock").open("a+") as lock:
      try: fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
      except BlockingIOError: return {**summary, "status": "skipped", "reason": "lock_held"}
      def progress(): _atomic_json(manifest / "latest.json", summary)
      progress(); started = monotonic()
      catalogue = data_root / "catalogue" / "us-daily-browser-v1.json"
      if not catalogue.exists(): raise ValueError("browser_catalogue_missing")
      try:
          catalogue_payload = json.loads(catalogue.read_text(encoding="utf-8"))
      except (OSError, ValueError, TypeError) as exc:
          raise ValueError("browser_catalogue_invalid") from exc
      if not isinstance(catalogue_payload, dict) or catalogue_payload.get("schema_version") != "us-daily-browser-v1" or not isinstance(catalogue_payload.get("series"), list):
          raise ValueError("browser_catalogue_invalid")
      master = _read_master(security_master)
      required = {"symbol", "status", "asset_type"}
      if not required.issubset(master.columns): raise ValueError("security_master_missing_required_columns")
      active = set(select_sync_symbols(master))
      failures = _latest_yahoo_failures(data_root / "manifests" / YAHOO_NAMESPACE / "records.jsonl")
      if gap_queue is not None:
          plan = json.loads(gap_queue.read_text(encoding="utf-8"))
          if plan.get("schema_version") != "us-daily-gap-queue-v1" or not isinstance(plan.get("tasks"), list):
              raise ValueError("gap_queue_invalid")
          failures = {}
          for task in plan["tasks"]:
              symbol = str(task["symbol"])
              start_day = date.fromisoformat(str(task["requested_start"]))
              end_day = date.fromisoformat(str(task["requested_end"]))
              if start_day < START_FLOOR or start_day > end_day or end_day > target:
                  raise ValueError("gap_queue_window_invalid")
              if symbol in failures: raise ValueError("duplicate_gap_queue_symbol")
              failures[symbol] = {**task, "attempted_at": str(task["task_id"])}
      attempts, markers, recovered_end = _recovery_state(records)
      def needs_recovery(symbol: str, row: dict[str, Any]) -> bool:
          if gap_queue is not None:
              return symbol in active  # Scheduler owns cooldown/attempt limits, including internal gaps.
          if symbol not in active or markers.get(symbol) == str(row.get("attempted_at", "")): return False
          try: return date.fromisoformat(str(row.get("requested_end") or target)) > recovered_end.get(symbol, date.min)
          except ValueError: return False
      queue = [(s, row) for s, row in failures.items() if needs_recovery(s, row)]
      if gap_queue is None:
          queue.sort(key=lambda item: (attempts.get(item[0], ""), item[0]))
      summary["requested"] = len(queue)
      if queue and gap_queue is not None and is_alpaca and default_downloader:
          from .sip_batch import make_sip_batch_downloader
          downloader = make_sip_batch_downloader([{**row, "symbol": symbol} for symbol, row in queue], credential_file, now)
          resources.callback(downloader.close)
          summary["acquisition_mode"] = "batch_sip"
      consecutive = 0
      for i, (symbol, failure) in enumerate(queue):
        if max_runtime_seconds and monotonic() - started >= max_runtime_seconds:
            summary["deferred"] += len(queue) - i; break
        start = max(START_FLOOR, date.fromisoformat(str(failure["requested_start"])))
        end = min(target, date.fromisoformat(str(failure.get("requested_end") or target)))
        marker = str(failure.get("attempted_at", "")); record = {"symbol": symbol, "namespace": base_namespace, "provider": base_provider, "source": base_source, "attempted_at": datetime.now(timezone.utc).isoformat(), "source_attempted_at": marker, "requested_start": start.isoformat(), "requested_end": end.isoformat()}
        if is_alpaca:
          # Explicitly record the endpoint's latest-data view.  This is not a
          # corporate-action as-of reconstruction and no symbol aliasing occurs.
          record["asof"] = "-"
          record["request_window_version"] = "inclusive-end-v2"
        summary["attempted"] += 1
        response_received = False
        try:
          alias = VERIFIED_YAHOO_ALIASES.get(symbol) if not is_alpaca and downloader is nasdaq_web_downloader else None
          provider, namespace, source, adjustment = ("yfinance", ALIAS_NAMESPACE, "yfinance", "raw_ohlc_with_adjusted_close_and_actions") if alias else (base_provider, base_namespace, base_source, base_adjustment)
          record.update({"provider": provider, "namespace": namespace, "source": source, "provider_symbol": alias or symbol})
          raw = yahoo_daily_history_downloader(alias, start, end) if alias else downloader(symbol, start, end)
          response_received = isinstance(raw, pd.DataFrame)
          if not isinstance(raw, pd.DataFrame) or raw.empty: raise ValueError("invalid_response")
          archive = data_root / "reference" / namespace / uuid.uuid4().hex / f"{symbol_key(symbol)}.parquet"
          _atomic_parquet(raw, archive, index=True)
          record.update({"archive": str(archive), "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "row_count": len(raw)})
          normalized = normalize_bars(raw, symbol, source, adjustment)
          normalized["date"] = pd.to_datetime(normalized["date"], errors="raise").dt.normalize()
          normalized[["adj_close", "dividends", "stock_splits"]] = pd.NA; normalized["actions_status"] = "unknown"
          _validate_normalized(normalized, start, end)
          record["actual_last_date"] = normalized["date"].max().date().isoformat()
          destination = data_root / "bars" / "daily" / f"provider={provider}" / f"namespace={namespace}" / f"symbol={symbol_key(symbol)}" / "bars.parquet"
          existing = pd.read_parquet(destination) if destination.exists() else None
          if existing is not None:
            if not {"date", "source", "adjustment_status"}.issubset(existing.columns) or not existing["source"].eq(source).all() or not existing["adjustment_status"].eq(adjustment).all():
                raise ValueError("existing_recovery_source_invalid")
            existing["date"] = pd.to_datetime(existing["date"], errors="raise").dt.normalize()
            _validate_normalized(existing, existing["date"].min().date(), existing["date"].max().date())
          merged = normalized if existing is None else pd.concat([existing, normalized], ignore_index=True).drop_duplicates("date", keep="last").sort_values("date", ignore_index=True)
          _atomic_parquet(merged, destination); _publish_catalogue(data_root, symbol, destination, merged, provider=provider, namespace=namespace)
          record["status"] = "success"; summary["success"] += 1; consecutive = 0
        except (SymbolHistoryUnknown, SymbolRequestRejected) as exc:
          record.update({"status": "failed", "error_code": str(exc), "http_response_received": True})
          summary["failed"] += 1
          consecutive = 0  # Healthy provider response, but no eligible data.
        except PermanentDownloadError:
          record.update({"status": "unavailable", "error_code": "symbol_unavailable"}); summary["failed"] += 1
          consecutive = 0
        except Exception as exc:
          code = _error_code(exc); record.update({"status": "failed", "error_type": type(exc).__name__, "error_code": code}); summary["failed"] += 1
          summary["last_error_code"] = code
          if code in {"http_429", "http_401", "http_403", "provider_authorization_failed"}: consecutive = max_consecutive_failures
          elif response_received and isinstance(exc, ValueError): consecutive = 0
          else: consecutive += 1
        _append(records, record); progress()
        if consecutive >= max_consecutive_failures:
          summary["circuit_open"] = True; summary["deferred"] += len(queue)-i-1; break
        if i < len(queue)-1 and request_delay: sleep(request_delay)
      if summary.get("acquisition_mode") == "batch_sip":
          summary["batch_http_attempts"] = getattr(downloader, "batch_request_count", 0)
          summary["single_fallback_http_attempts"] = getattr(downloader, "single_request_count", 0)
      summary["status"] = "failed" if summary["failed"] or summary["circuit_open"] else ("partial" if summary["deferred"] else "success")
      summary["finished_at"] = datetime.now(timezone.utc).isoformat(); progress(); return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recover Yahoo failures with isolated Nasdaq or historical Alpaca SIP data")
    parser.add_argument("--security-master", required=True, type=Path); parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--max-runtime-seconds", type=float, default=64800); parser.add_argument("--request-delay", type=float, default=2.0); parser.add_argument("--max-consecutive-failures", type=int, default=3)
    parser.add_argument("--recovery-provider", choices=("nasdaq", "alpaca"), default="nasdaq")
    parser.add_argument("--credential-file", type=Path)
    args = parser.parse_args(argv)
    try: result = run_recovery(**vars(args))
    except (OSError, ValueError): print(json.dumps({"status":"failed", "error_code":"recovery_configuration_or_io_failed"})); return 1
    print(json.dumps(result, sort_keys=True)); return 1 if result["status"] == "failed" else (2 if result["status"] == "partial" else 0)


if __name__ == "__main__": raise SystemExit(main())
