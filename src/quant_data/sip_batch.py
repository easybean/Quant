"""Bounded, read-only Alpaca SIP daily-bar batch downloader.

This module deliberately exposes the same single-symbol callable contract as
``make_alpaca_downloader`` while sharing one complete multi-symbol request
across planned gap tasks.  It neither retries failed HTTP requests nor exposes
partial pagination results.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, time as daytime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .daily_sync import _target_end
from .pipeline import FatalProviderError, load_alpaca_credentials, make_alpaca_downloader

_NY = ZoneInfo("America/New_York")
_URL = "https://data.alpaca.markets/v2/stocks/bars"
_MAX_PAGES = 64


def _unknown_history() -> Exception:
    # recovery_sync imports this module during integration, so importing its
    # symbol only when needed avoids a module-level cycle.
    from .recovery_sync import SymbolHistoryUnknown
    return SymbolHistoryUnknown("empty_history_unknown")


def _sip_window(start: date, end: date, now: datetime) -> tuple[str, str]:
    now_utc = (now if now.tzinfo else now.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)
    if end > _target_end(now_utc):
        raise ValueError("alpaca_end_exceeds_conservative_daily_cutoff")
    start_utc = datetime.combine(start, daytime.min, tzinfo=_NY).astimezone(timezone.utc)
    following_ny_midnight = datetime.combine(end + timedelta(days=1), daytime.min, tzinfo=_NY).astimezone(timezone.utc)
    cutoff = now_utc - timedelta(minutes=15)
    return (start_utc.isoformat().replace("+00:00", "Z"),
            min(following_ny_midnight, cutoff).isoformat().replace("+00:00", "Z"))


class _SIPBatchDownloader:
    def __init__(self, tasks: list[dict[str, Any]], credential_file: Path, now: datetime, batch_size: int,
                 request_get: Callable[..., Any] | None, sleep: Callable[[float], None],
                 monotonic: Callable[[], float], min_request_interval: float):
        if batch_size < 1:
            raise ValueError("batch_size_must_be_positive")
        self.now, self.sleep, self.monotonic = now, sleep, monotonic
        self.credential_file = credential_file
        self.min_request_interval = float(min_request_interval)
        if self.min_request_interval < 0:
            raise ValueError("min_request_interval_must_be_non_negative")
        key, secret = load_alpaca_credentials(credential_file)
        self.headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
        self.session = requests.Session() if request_get is None else None
        self.request_get = request_get or self.session.get
        self.last_request_at: float | None = None
        self.batch_request_count = 0
        self.single_request_count = 0
        self.by_symbol: dict[str, tuple[date, date, tuple[str, ...]]] = {}
        grouped: dict[tuple[date, date], list[str]] = defaultdict(list)
        seen_symbols: set[str] = set()
        for task in tasks:
            try:
                symbol = str(task["symbol"]).strip().upper()
                start, end = date.fromisoformat(str(task["requested_start"])), date.fromisoformat(str(task["requested_end"]))
                if not symbol or start > end or symbol in seen_symbols:
                    raise ValueError
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("sip_batch_tasks_invalid") from exc
            seen_symbols.add(symbol)
            grouped[(start, end)].append(symbol)
        self.chunks: dict[tuple[str, ...], tuple[date, date]] = {}
        for (start, end), symbols in grouped.items():
            for offset in range(0, len(symbols), batch_size):
                chunk = tuple(symbols[offset:offset + batch_size])
                self.chunks[chunk] = (start, end)
                for symbol in chunk:
                    self.by_symbol[symbol] = (start, end, chunk)
        self.frames: dict[str, pd.DataFrame] = {}
        self.errors: dict[tuple[str, ...], Exception] = {}
        self.fallback_chunks: set[tuple[str, ...]] = set()
        self.single: dict[tuple[str, date, date], Callable[[str, date, date], pd.DataFrame]] = {}
        self.single_frames: dict[tuple[str, date, date], pd.DataFrame] = {}
        self.single_errors: dict[tuple[str, date, date], Exception] = {}

    def close(self) -> None:
        if self.session is not None:
            self.session.close()

    def _request(self, url: str, **kwargs: Any) -> Any:
        if self.last_request_at is not None:
            remaining = self.min_request_interval - (self.monotonic() - self.last_request_at)
            if remaining > 0:
                self.sleep(remaining)
        # The attempt, including a transport exception, consumes a rate slot.
        self.last_request_at = self.monotonic()
        if url == _URL: self.batch_request_count += 1
        else: self.single_request_count += 1
        return self.request_get(url, **kwargs)

    def _fetch_chunk(self, chunk: tuple[str, ...], start: date, end: date) -> None:
        start_at, end_at = _sip_window(start, end, self.now)
        token = ""
        seen_tokens: set[str] = set()
        rows: dict[str, list[dict[str, Any]]] = {symbol: [] for symbol in chunk}
        try:
            for _ in range(_MAX_PAGES):
                params: dict[str, Any] = {"symbols": ",".join(chunk), "timeframe": "1Day", "feed": "sip",
                                          "adjustment": "raw", "asof": "-", "start": start_at, "end": end_at,
                                          "limit": 10000, "sort": "asc"}
                if token:
                    params["page_token"] = token
                response = self._request(_URL, params=params, headers=self.headers, timeout=30)
                status = getattr(response, "status_code", None)
                if status in {401, 403}:
                    raise FatalProviderError(f"Alpaca authentication/feed authorization failed (HTTP {status})")
                if status in {400, 422}:
                    self.fallback_chunks.add(chunk)
                    return
                if status == 429 or (isinstance(status, int) and status >= 500):
                    raise requests.HTTPError(f"HTTP {status}")
                if status != 200:
                    raise requests.HTTPError(f"HTTP {status}")
                payload = response.json()
                bars = payload.get("bars") if isinstance(payload, dict) else None
                if not isinstance(bars, dict):
                    raise ValueError("alpaca_batch_response_invalid")
                for symbol in rows:
                    if symbol not in bars:
                        # A 200 response can omit a requested symbol.  Treat
                        # that as healthy-but-unknown only after all pages;
                        # never mistake malformed expected rows for absence.
                        continue
                    symbol_rows = bars[symbol]
                    if not isinstance(symbol_rows, list) or not all(isinstance(row, dict) for row in symbol_rows):
                        raise ValueError("alpaca_batch_expected_symbol_rows_invalid")
                    rows[symbol].extend(symbol_rows)
                next_token = payload.get("next_page_token") if isinstance(payload, dict) else None
                if next_token in (None, ""):
                    break
                if not isinstance(next_token, str) or next_token in seen_tokens:
                    raise ValueError("alpaca_batch_repeated_page_token")
                seen_tokens.add(next_token)
                token = next_token
            else:
                raise ValueError("alpaca_batch_page_limit_exceeded")
            # Publish only after all pages are valid and terminal.
            for symbol, symbol_rows in rows.items():
                if not symbol_rows:
                    self.frames[symbol] = pd.DataFrame()
                else:
                    self.frames[symbol] = pd.DataFrame(symbol_rows).rename(
                        columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
                    )
        except Exception as exc:
            self.errors[chunk] = exc
            raise

    def _single_fallback(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        key = (symbol, start, end)
        if key in self.single_errors:
            raise self.single_errors[key]
        if key in self.single_frames:
            return self.single_frames[key].copy()
        downloader = self.single.get(key)
        if downloader is None:
            from .recovery_sync import _alpaca_sip_request_wrapper
            def throttled_request(url: str, **kwargs: Any) -> Any:
                return self._request(url, **kwargs)
            downloader = make_alpaca_downloader(feed="sip", credential_file=self.credential_file, request_retries=0,
                                                request_get=_alpaca_sip_request_wrapper(self.now, request_get=throttled_request))
            self.single[key] = downloader
        try:
            frame = downloader(symbol, start, end)
        except Exception as exc:
            self.single_errors[key] = exc
            raise
        self.single_frames[key] = frame.copy()
        return frame

    def __call__(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        normalized = str(symbol).strip().upper()
        task = self.by_symbol.get(normalized)
        if task is None or task[:2] != (start, end):
            raise ValueError("sip_batch_symbol_or_window_not_planned")
        _, _, chunk = task
        if chunk in self.errors:
            raise self.errors[chunk]
        if chunk not in self.fallback_chunks and normalized not in self.frames:
            self._fetch_chunk(chunk, start, end)
        if chunk in self.errors:
            raise self.errors[chunk]
        if chunk in self.fallback_chunks:
            return self._single_fallback(normalized, start, end)
        frame = self.frames[normalized]
        if frame.empty:
            raise _unknown_history()
        return frame.copy()


def make_sip_batch_downloader(tasks: list[dict[str, Any]], credential_file: Path, now: datetime, batch_size: int = 50,
                              request_get: Callable[..., Any] | None = None, *, sleep: Callable[[float], None] = time.sleep,
                              monotonic: Callable[[], float] = time.monotonic, min_request_interval: float = 0.5) -> Callable[[str, date, date], pd.DataFrame]:
    """Return a throttled planned-task SIP batch downloader with optional ``close``."""
    return _SIPBatchDownloader(tasks, credential_file, now, batch_size, request_get, sleep, monotonic, min_request_interval)
