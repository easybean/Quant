#!/usr/bin/env python3
"""Read-only probe for historical US SIP daily-bar access.

This is an entitlement diagnostic, not an ingestion path.  It neither writes
market data nor qualifies a dataset or backtest for research use.
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

from quant_data.pipeline import load_alpaca_credentials


DATA_URL = "https://data.alpaca.markets/v2/stocks"
NY = ZoneInfo("America/New_York")
BASE_SYMBOLS = ("AAPL", "TSLA", "AACPW", "ABNG")
AAPL_2016_START = date(2016, 1, 4)
AAPL_2016_END = date(2016, 1, 8)


def _ny_midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=NY).astimezone(timezone.utc)


def _windows(end: date, start: date | None) -> list[tuple[str, str, date, date]]:
    base_start = start or end - timedelta(days=20)
    if base_start > end:
        raise ValueError("start must not be after end")
    if end < AAPL_2016_END:
        raise ValueError("end must include the fixed AAPL 2016 validation window")
    return [
        *( (symbol, "recent", base_start, end) for symbol in BASE_SYMBOLS ),
        ("AAPL", "aapl_2016_narrow", AAPL_2016_START, AAPL_2016_END),
    ]


def _iso_boundary(day: date) -> str:
    return _ny_midnight_utc(day).isoformat().replace("+00:00", "Z")


def _bar_timestamp(bar: Any) -> datetime | None:
    if not isinstance(bar, dict) or not isinstance(bar.get("t"), str):
        return None
    try:
        parsed = datetime.fromisoformat(bar["t"].replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _valid_ohlcv(bar: Any, *, start: date, end: date) -> bool:
    if not isinstance(bar, dict):
        return False
    try:
        o, h, l, c, v = (float(bar[key]) for key in ("o", "h", "l", "c", "v"))
    except (KeyError, TypeError, ValueError):
        return False
    timestamp = _bar_timestamp(bar)
    return (
        timestamp is not None
        and start <= timestamp.astimezone(NY).date() <= end
        and all(math.isfinite(value) for value in (o, h, l, c, v))
        and min(o, h, l, c) > 0
        and v >= 0
        and l <= min(o, c) <= max(o, c) <= h
    )


def _date_range(bars: list[Any]) -> list[str] | None:
    timestamps = [bar["t"] for bar in bars if _bar_timestamp(bar) is not None]
    return [min(timestamps), max(timestamps)] if timestamps else None


def probe_free_us_data(
    *,
    end: date,
    start: date | None = None,
    credential_file: Path | None = None,
    request_get: Callable[..., Any] = requests.get,
    now: datetime | None = None,
    credential_loader: Callable[[Path | None], tuple[str, str]] = load_alpaca_credentials,
) -> dict[str, Any]:
    """Probe fixed historical SIP windows once, returning sanitized diagnostics.

    Alpaca's FAQ says historical SIP can be queried without a subscription when
    ``end`` is at least 15 minutes old.  This function validates that boundary,
    but never infers subscription or research eligibility from a successful call.
    """
    end_boundary = _ny_midnight_utc(end + timedelta(days=1))
    clock = now or datetime.now(timezone.utc)
    if clock.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if end_boundary > clock.astimezone(timezone.utc) - timedelta(minutes=15):
        raise ValueError("end boundary must be at least 15 minutes old")

    windows = _windows(end, start)
    key, secret = credential_loader(credential_file)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    results: list[dict[str, Any]] = []
    stopped_early = False
    probe_status = "completed"
    for symbol, sample, window_start, window_end in windows:
        # Quote the path segment explicitly; symbols are never interpolated as a URL token.
        url = f"{DATA_URL}/{quote(symbol, safe='')}/bars"
        params = {
            "feed": "sip",
            "timeframe": "1Day",
            "adjustment": "raw",
            "asof": "-",  # Disable Alpaca's historical ticker-name mapping.
            "start": _iso_boundary(window_start),
            "end": _iso_boundary(window_end + timedelta(days=1)),
            "limit": 10000,
            "sort": "asc",
        }
        try:
            response = request_get(url, params=params, headers=headers, timeout=30)
            http_status = int(response.status_code)
        except Exception:
            results.append({"symbol": symbol, "sample": sample, "status": "request_error", "bar_count": 0,
                            "date_range": None, "ohlcv_valid": "unknown", "pagination_complete": "unknown"})
            probe_status, stopped_early = "blocked_request_error", True
            break
        result: dict[str, Any] = {"symbol": symbol, "sample": sample, "http_status": http_status,
                                  "bar_count": 0, "date_range": None, "ohlcv_valid": "unknown",
                                  "pagination_complete": "unknown"}
        if http_status in {401, 403, 429}:
            result["status"] = f"http_{http_status}_blocked"
            results.append(result)
            probe_status, stopped_early = f"blocked_http_{http_status}", True
            break
        if not 200 <= http_status < 300:
            result["status"] = "http_error"
            results.append(result)
            probe_status, stopped_early = "blocked_http_error", True
            break
        try:
            payload = response.json()
            bars = payload.get("bars") if isinstance(payload, dict) else None
            if isinstance(payload, dict) and "bars" in payload and bars is None:
                bars = []  # Alpaca represents some empty histories as null.
        except Exception:
            bars = None
            payload = None
        if not isinstance(bars, list):
            result["status"] = "response_unknown"
            results.append(result)
            probe_status, stopped_early = "blocked_response_unknown", True
            break
        result["bar_count"] = len(bars)
        result["date_range"] = _date_range(bars)
        result["ohlcv_valid"] = (
            all(_valid_ohlcv(bar, start=window_start, end=window_end) for bar in bars)
            if bars else "unknown"
        )
        if isinstance(payload, dict) and payload.get("next_page_token"):
            result["status"] = "pagination_blocked"
            result["pagination_complete"] = False
            results.append(result)
            probe_status, stopped_early = "blocked_partial_pagination", True
            break
        result["pagination_complete"] = True
        result["status"] = "empty_unknown" if not bars else ("ok" if result["ohlcv_valid"] else "invalid_ohlcv")
        results.append(result)
    if probe_status == "completed" and any(row["status"] == "invalid_ohlcv" for row in results):
        probe_status = "completed_with_invalid"
    return {
        "probe_status": probe_status,
        "stopped_early": stopped_early,
        "feed": "sip",
        "adjustment": "raw",
        "asof": "-",
        "qualified": False,
        "qualification_note": "license and free-plan eligibility remain unreviewed; this is not a research or backtest qualification",
        "results": results,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", type=Path)
    parser.add_argument("--end", type=date.fromisoformat, required=True, help="inclusive fixed NY trading-date endpoint (YYYY-MM-DD)")
    parser.add_argument("--start", type=date.fromisoformat, help="inclusive fixed start date; defaults to end minus 20 calendar days")
    args = parser.parse_args()
    try:
        result = probe_free_us_data(end=args.end, start=args.start, credential_file=args.credential_file)
    except Exception:
        print(json.dumps({"probe_status": "configuration_or_boundary_error", "qualified": False}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0 if result["probe_status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
