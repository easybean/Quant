from __future__ import annotations

import hashlib
import json
import os
import random
import re
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pandas as pd
import requests


BarDownloader = Callable[[str, date, date], pd.DataFrame]


class PermanentDownloadError(RuntimeError):
    """A provider response that will not become valid after a retry."""


class FatalProviderError(RuntimeError):
    """A provider-wide configuration/authentication failure; abort the run."""


@dataclass(frozen=True)
class DownloadConfig:
    data_root: Path
    start: date
    end: date
    provider: str = "yfinance"
    feed: str = ""
    state_namespace: str = ""
    batch_size: int = 100
    retries: int = 3
    retry_base_seconds: float = 2.0
    request_delay_seconds: float = 0.5
    force: bool = False


@dataclass(frozen=True)
class BenchmarkDownloadConfig:
    """Configuration for separately stored, tradeable benchmark ETFs."""

    data_root: Path
    start: date
    end: date
    provider: str = "alpaca"
    feed: str = "iex"
    retries: int = 3
    retry_base_seconds: float = 2.0
    request_delay_seconds: float = 0.5
    force: bool = False


def symbol_key(symbol: str) -> str:
    readable = "".join(char if char.isalnum() or char in ".-_" else "_" for char in symbol)
    digest = hashlib.sha256(symbol.encode("utf-8")).hexdigest()[:8]
    return f"{readable[:64]}-{digest}"


def validate_state_namespace(namespace: str) -> str:
    value = namespace.strip()
    if value and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", value):
        raise ValueError(
            "state namespace must be 1-64 characters using letters, numbers, dot, underscore or dash"
        )
    return value


def state_directory(data_root: Path, namespace: str = "") -> Path:
    value = validate_state_namespace(namespace)
    return data_root / "manifests" / value if value else data_root / "manifests"


def bars_directory(data_root: Path, provider: str, namespace: str = "") -> Path:
    value = validate_state_namespace(namespace) or "default"
    return data_root / "bars" / "daily" / f"provider={provider}" / f"namespace={value}"


def benchmark_bars_directory(data_root: Path, provider: str) -> Path:
    """Benchmark data is deliberately isolated from the downloaded stock universe."""
    return bars_directory(data_root, provider, "benchmarks")


def benchmark_manifest_directory(data_root: Path, provider: str) -> Path:
    return data_root / "manifests" / "benchmarks" / f"provider={provider}"


def yahoo_symbol(symbol: str) -> str:
    """Translate the common US share-class separator to Yahoo's convention."""
    return symbol.replace(".", "-")


def default_yfinance_downloader(symbol: str, start: date, end: date) -> pd.DataFrame:
    import yfinance as yf

    # yfinance's end is exclusive; the public CLI treats end as inclusive.
    frame = yf.download(
        yahoo_symbol(symbol),
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
        actions=True,
        progress=False,
        threads=False,
        timeout=30,
    )
    if isinstance(frame.columns, pd.MultiIndex):
        frame.columns = frame.columns.get_level_values(0)
    return frame


def _nasdaq_number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    cleaned = str(value).replace("$", "").replace(",", "").strip()
    if cleaned.lower() in {"n/a", "na", "null", "--"}:
        return None
    return float(cleaned)


def nasdaq_web_downloader(
    symbol: str, start: date, end: date, *, assetclass: str = "stocks"
) -> pd.DataFrame:
    """Download unadjusted bars from Nasdaq's public website JSON endpoint."""
    provider_symbol = yahoo_symbol(symbol)
    url = f"https://api.nasdaq.com/api/quote/{provider_symbol}/historical"
    response = requests.get(
        url,
        params={
            "assetclass": assetclass,
            "fromdate": start.isoformat(),
            "todate": end.isoformat(),
            "limit": 5000,
        },
        headers={
            "Accept": "application/json, text/plain, */*",
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
            ),
        },
        timeout=45,
    )
    if response.status_code == 400 and "symbol not exists" in response.text.lower():
        raise PermanentDownloadError(f"Nasdaq symbol does not exist: {provider_symbol}")
    response.raise_for_status()
    payload = response.json()
    status = payload.get("status") or {}
    messages = status.get("bCodeMessage") or []
    message_text = " ".join(
        str(item.get("errorMessage", "")) if isinstance(item, dict) else str(item)
        for item in messages
    ).strip()
    if str(status.get("rCode")) == "400" and "symbol not exists" in message_text.lower():
        raise PermanentDownloadError(f"Nasdaq symbol does not exist: {provider_symbol}")
    data = payload.get("data") or {}
    table = data.get("tradesTable") or {}
    rows = table.get("rows") or []
    if not rows:
        message = payload.get("message") or payload.get("status") or "no rows returned"
        raise ValueError(f"Nasdaq response has no historical rows: {message}")
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns={column: str(column).strip().lower() for column in frame.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f"Nasdaq response missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], format="%m/%d/%Y", errors="raise")
    for column in required[1:]:
        frame[column] = frame[column].map(_nasdaq_number)
    # Nasdaq returns newest first and does not expose adjustments/actions here.
    frame["adj_close"] = frame["close"]
    frame["dividends"] = 0.0
    frame["stock_splits"] = 0.0
    return frame.sort_values("date", ignore_index=True)


def _normalize_nasdaq_historical_response(payload: dict[str, Any], provider_symbol: str) -> pd.DataFrame:
    """Parse the public Nasdaq historical response without converting missing values."""
    status = payload.get("status") or {}
    messages = status.get("bCodeMessage") or []
    message_text = " ".join(
        str(item.get("errorMessage", "")) if isinstance(item, dict) else str(item)
        for item in messages
    ).strip()
    if str(status.get("rCode")) == "400" and "symbol not exists" in message_text.lower():
        raise PermanentDownloadError(f"Nasdaq symbol does not exist: {provider_symbol}")
    data = payload.get("data") or {}
    table = data.get("tradesTable") or {}
    rows = table.get("rows") or []
    if not rows:
        message = payload.get("message") or payload.get("status") or "no rows returned"
        raise ValueError(f"Nasdaq response has no historical rows: {message}")
    frame = pd.DataFrame(rows)
    frame = frame.rename(columns={column: str(column).strip().lower() for column in frame.columns})
    required = ["date", "open", "high", "low", "close", "volume"]
    missing = set(required).difference(frame.columns)
    if missing:
        raise ValueError(f"Nasdaq response missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], format="%m/%d/%Y", errors="raise")
    for column in required[1:]:
        frame[column] = frame[column].map(_nasdaq_number)
    # The endpoint is newest-first and does not supply adjustment/actions data.
    frame["adj_close"] = frame["close"]
    frame["dividends"] = 0.0
    frame["stock_splits"] = 0.0
    return frame.sort_values("date", ignore_index=True)


BENCHMARK_ASSET_CLASSES: dict[str, dict[str, str]] = {
    "alpaca": {"SPY": "etf", "QQQ": "etf"},
    "nasdaq": {"SPY": "etf", "QQQ": "etf", "NDX": "index"},
}


def make_nasdaq_benchmark_downloader(
    assetclass_by_symbol: dict[str, str],
    request_get: Callable[..., Any] = requests.get,
) -> BarDownloader:
    """Build an allow-listed Nasdaq benchmark downloader.

    `assetclass` is never inferred from user input.  The caller passes the
    provider's small, verified symbol map so an ETF is not accidentally fetched
    as a stock (or an index treated as a tradeable security).
    """
    invalid = set(assetclass_by_symbol.values()).difference({"etf", "index"})
    if invalid:
        raise ValueError(f"unsupported Nasdaq benchmark asset classes: {sorted(invalid)}")

    def download(symbol: str, start: date, end: date) -> pd.DataFrame:
        assetclass = assetclass_by_symbol.get(symbol.upper())
        if assetclass is None:
            raise PermanentDownloadError(f"Nasdaq benchmark symbol is not mapped: {symbol}")
        provider_symbol = yahoo_symbol(symbol)
        url = f"https://api.nasdaq.com/api/quote/{provider_symbol}/historical"
        response = request_get(
            url,
            params={
                "assetclass": assetclass,
                "fromdate": start.isoformat(),
                "todate": end.isoformat(),
                "limit": 5000,
            },
            headers={
                "Accept": "application/json, text/plain, */*",
                "User-Agent": (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            },
            timeout=45,
        )
        if response.status_code == 400 and "symbol not exists" in response.text.lower():
            raise PermanentDownloadError(f"Nasdaq symbol does not exist: {provider_symbol}")
        response.raise_for_status()
        return _normalize_nasdaq_historical_response(response.json(), provider_symbol)

    return download


def load_alpaca_credentials(credential_file: Path | None = None) -> tuple[str, str]:
    """Load Alpaca credentials without returning/logging their source or values."""
    key_names = ("APCA_API_KEY_ID", "ALPACA_API_KEY", "ALPACA_KEY_ID")
    secret_names = ("APCA_API_SECRET_KEY", "ALPACA_API_SECRET_KEY", "ALPACA_SECRET_KEY")
    key = next((os.environ[name].strip() for name in key_names if os.environ.get(name, "").strip()), "")
    secret = next(
        (os.environ[name].strip() for name in secret_names if os.environ.get(name, "").strip()), ""
    )
    if key and secret:
        return key, secret
    if credential_file is not None:
        lines = [
            line.strip() for line in credential_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        values: dict[str, str] = {}
        bare: list[str] = []
        for line in lines:
            if "=" in line:
                name, value = line.split("=", 1)
                values[name.strip().upper()] = value.strip().strip("'\"")
            else:
                bare.append(line.strip().strip("'\""))
        key = next((values[name] for name in key_names if values.get(name)), "")
        secret = next((values[name] for name in secret_names if values.get(name)), "")
        if not (key and secret) and len(bare) >= 2:
            key, secret = bare[0], bare[1]
    if not key or not secret:
        raise FatalProviderError("Alpaca credentials are missing or incomplete")
    return key, secret


def make_alpaca_downloader(
    *,
    feed: str = "iex",
    credential_file: Path | None = None,
    page_delay_seconds: float = 0.25,
    request_retries: int = 3,
    request_get: Callable[..., Any] = requests.get,
    sleep: Callable[[float], None] = time.sleep,
) -> BarDownloader:
    if feed not in {"iex", "sip"}:
        raise ValueError("Alpaca stock feed must be iex or sip")
    api_key, api_secret = load_alpaca_credentials(credential_file)
    headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}

    def download(symbol: str, start: date, end: date) -> pd.DataFrame:
        url = f"https://data.alpaca.markets/v2/stocks/{quote(symbol, safe='')}/bars"
        page_token = ""
        rows: list[dict[str, Any]] = []
        while True:
            params = {
                "timeframe": "1Day",
                "start": f"{start.isoformat()}T00:00:00Z",
                "end": f"{(end + timedelta(days=1)).isoformat()}T00:00:00Z",
                "limit": 10000,
                "adjustment": "raw",
                "feed": feed,
                "sort": "asc",
            }
            if page_token:
                params["page_token"] = page_token
            response = None
            for attempt in range(request_retries + 1):
                try:
                    response = request_get(url, params=params, headers=headers, timeout=45)
                except requests.RequestException:
                    if attempt >= request_retries:
                        raise
                    sleep(2 ** attempt)
                    continue
                if response.status_code in {401, 403}:
                    raise FatalProviderError(
                        f"Alpaca authentication/feed authorization failed (HTTP {response.status_code})"
                    )
                if response.status_code == 404:
                    raise PermanentDownloadError(f"Alpaca symbol does not exist: {symbol}")
                if response.status_code == 429 or response.status_code >= 500:
                    if attempt >= request_retries:
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = float(retry_after)
                    except ValueError:
                        delay = float(2 ** attempt)
                    sleep(delay)
                    continue
                response.raise_for_status()
                break
            if response is None:
                raise RuntimeError("Alpaca request produced no response")
            payload = response.json()
            rows.extend(payload.get("bars") or [])
            page_token = payload.get("next_page_token") or ""
            if not page_token:
                break
            if page_delay_seconds > 0:
                sleep(page_delay_seconds)
        if not rows:
            raise ValueError("Alpaca returned no daily bars")
        frame = pd.DataFrame(rows).rename(
            columns={"t": "date", "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume"}
        )
        frame["adj_close"] = frame["close"]
        frame["dividends"] = 0.0
        frame["stock_splits"] = 0.0
        return frame

    return download


def normalize_bars(
    frame: pd.DataFrame,
    symbol: str,
    source: str = "unknown",
    adjustment_status: str = "unknown",
    feed: str = "",
) -> pd.DataFrame:
    if frame.empty:
        raise ValueError("no rows returned")
    bars = frame.copy().reset_index()
    bars.columns = [str(column).strip().lower().replace(" ", "_") for column in bars.columns]
    if "datetime" in bars.columns and "date" not in bars.columns:
        bars = bars.rename(columns={"datetime": "date"})
    if "date" not in bars.columns:
        raise ValueError("download has no date index/column")
    required = {"open", "high", "low", "close", "volume"}
    missing = required.difference(bars.columns)
    if missing:
        raise ValueError(f"download missing columns: {sorted(missing)}")
    bars["date"] = pd.to_datetime(bars["date"], errors="raise", utc=True).dt.tz_localize(None)
    bars["symbol"] = symbol
    bars["source"] = source
    bars["adjustment_status"] = adjustment_status
    bars["feed"] = feed
    for optional in ("adj_close", "dividends", "stock_splits"):
        if optional not in bars.columns:
            bars[optional] = 0.0 if optional != "adj_close" else bars["close"]
    columns = [
        "date", "symbol", "open", "high", "low", "close", "adj_close",
        "volume", "dividends", "stock_splits", "source", "adjustment_status", "feed",
    ]
    return bars[columns].drop_duplicates("date", keep="last").sort_values("date", ignore_index=True)


def _atomic_parquet(frame: pd.DataFrame, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.parquet")
    frame.to_parquet(temporary, index=False)
    temporary.replace(destination)


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _existing_terminal_records(manifest_path: Path) -> dict[tuple[str, str, str, str], str]:
    terminal: dict[tuple[str, str, str, str], str] = {}
    if not manifest_path.exists():
        return terminal
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue
            key = (
                record.get("symbol", ""), record.get("requested_start", ""),
                record.get("requested_end", ""), record.get("provider", "yfinance"),
            )
            if record.get("status") == "success":
                terminal[key] = "success"
            elif record.get("status") == "failed" and (
                record.get("retryable") is False
                or "symbol not exists" in str(record.get("error", "")).lower()
            ):
                terminal[key] = "permanent_failure"
    return terminal


def _log_symbol(event: str, symbol: str, **details: Any) -> None:
    print(
        json.dumps({"event": event, "symbol": symbol, **details}, ensure_ascii=False),
        flush=True,
    )


def failed_symbols_for_provider(manifest_path: Path, provider: str) -> list[str]:
    """Return symbols whose latest record for a provider is failed."""
    latest: dict[str, str] = {}
    if not manifest_path.exists():
        return []
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if record.get("provider", "yfinance") == provider and record.get("symbol"):
                latest[str(record["symbol"])] = str(record.get("status", ""))
    return sorted(symbol for symbol, status in latest.items() if status == "failed")


def _latest_tables(manifest_path: Path, output_dir: Path) -> None:
    if not manifest_path.exists():
        return
    records = []
    with manifest_path.open(encoding="utf-8") as handle:
        for line in handle:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not records:
        return
    table = pd.DataFrame(records).sort_values("finished_at").drop_duplicates("symbol", keep="last")
    output_dir.mkdir(parents=True, exist_ok=True)
    table.to_csv(output_dir / "latest.csv", index=False)
    table[table["status"] == "failed"].to_csv(output_dir / "failures.csv", index=False)


def eligible_symbols(security_master: pd.DataFrame, start: date, end: date) -> list[str]:
    records = eligible_records(security_master, start, end)
    return sorted(records["symbol"].dropna().astype(str).str.strip().str.upper().unique())


def eligible_records(security_master: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
    if "symbol" not in security_master.columns:
        raise ValueError("security master must contain a symbol column")
    frame = security_master.copy()
    blank_dates = pd.Series([""] * len(frame), index=frame.index)
    ipo = pd.to_datetime(frame["ipo_date"] if "ipo_date" in frame else blank_dates, errors="coerce")
    delisted = pd.to_datetime(
        frame["delisting_date"] if "delisting_date" in frame else blank_dates, errors="coerce"
    )
    start_timestamp = pd.Timestamp(start).normalize()
    end_timestamp = pd.Timestamp(end).normalize()
    ipo = ipo.dt.normalize()
    delisted = delisted.dt.normalize()
    mask = (ipo.isna() | (ipo <= end_timestamp)) & (
        delisted.isna() | (delisted >= start_timestamp)
    )
    return frame.loc[mask].copy().reset_index(drop=True)


def select_universe(
    security_master: pd.DataFrame,
    start: date,
    end: date,
    *,
    statuses: list[str] | None = None,
    asset_types: list[str] | None = None,
    restrict_symbols: set[str] | None = None,
    limit: int | None = None,
) -> tuple[pd.DataFrame, list[str], dict[str, Any]]:
    """Filter lifecycle records, then collapse them to unique download symbols."""
    frame = security_master.copy()
    requested_statuses = {value.strip().lower() for value in statuses or []}
    requested_assets = {value.strip().casefold() for value in asset_types or []}
    if requested_statuses:
        if "status" not in frame:
            raise ValueError("security master has no status column")
        frame = frame[frame["status"].fillna("").astype(str).str.lower().isin(requested_statuses)]
    if requested_assets:
        if "asset_type" not in frame:
            raise ValueError("security master has no asset_type column")
        frame = frame[
            frame["asset_type"].fillna("").astype(str).str.casefold().isin(requested_assets)
        ]
    active_duplicate_rows = 0
    if "status" in frame:
        active = frame[frame["status"].fillna("").astype(str).str.lower().eq("active")]
        active_duplicate_rows = int(active["symbol"].astype(str).str.upper().duplicated().sum())
    if requested_statuses and "active" in requested_statuses and active_duplicate_rows:
        raise ValueError(
            f"active universe contains {active_duplicate_rows} duplicate symbol rows; "
            "run `quant-data listing consolidate` first"
        )
    records = eligible_records(frame, start, end)
    if restrict_symbols is not None:
        allowed = {symbol.strip().upper() for symbol in restrict_symbols}
        records = records[
            records["symbol"].fillna("").astype(str).str.strip().str.upper().isin(allowed)
        ].copy()
    symbols = sorted(records["symbol"].dropna().astype(str).str.strip().str.upper().unique())
    if limit is not None:
        symbols = symbols[:limit]
        records = records[
            records["symbol"].astype(str).str.strip().str.upper().isin(set(symbols))
        ].copy()
    audit = {
        "security_master_rows": int(len(security_master)),
        "status_filter": sorted(requested_statuses),
        "asset_type_filter": sorted(requested_assets),
        "active_duplicate_rows": active_duplicate_rows,
        "selected_lifecycle_rows": int(len(records)),
        "selected_unique_symbols": len(symbols),
        "collapsed_lifecycle_rows": int(len(records) - len(symbols)),
        "restricted_symbol_count": len(restrict_symbols) if restrict_symbols is not None else None,
        "limit": limit,
    }
    return records.reset_index(drop=True), symbols, audit


def run_download(
    symbols: Iterable[str],
    config: DownloadConfig,
    downloader: BarDownloader | None = None,
    sleep: Callable[[float], None] = time.sleep,
    universe_records: pd.DataFrame | None = None,
    universe_audit: dict[str, Any] | None = None,
) -> dict[str, int]:
    if config.start > config.end:
        raise ValueError("start must not be after end")
    if config.batch_size < 1 or config.retries < 0:
        raise ValueError("batch_size must be positive and retries non-negative")

    adjustment_statuses = {
        "yfinance": "raw_ohlc_with_adjusted_close_and_actions",
        "nasdaq": "unadjusted",
        "alpaca": "raw",
    }
    provider_sources = {
        "yfinance": "yfinance",
        "nasdaq": "nasdaq_web_unadjusted",
        "alpaca": "alpaca_stock_historical_v2",
    }
    provider_symbol = lambda symbol: symbol if config.provider == "alpaca" else yahoo_symbol(symbol)
    if config.provider not in adjustment_statuses:
        raise ValueError(
            f"unknown provider {config.provider!r}; choose one of {sorted(adjustment_statuses)}"
        )
    adjustment_status = adjustment_statuses[config.provider]
    if downloader is None:
        if config.provider == "yfinance":
            downloader = default_yfinance_downloader
        elif config.provider == "nasdaq":
            downloader = nasdaq_web_downloader
        else:
            downloader = make_alpaca_downloader(feed=config.feed or "iex")

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    namespace = validate_state_namespace(config.state_namespace)
    manifests = state_directory(config.data_root, namespace)
    bars_root = bars_directory(config.data_root, config.provider, namespace)
    manifest_path = manifests / "download.jsonl"
    requested = (config.start.isoformat(), config.end.isoformat())
    completed = _existing_terminal_records(manifest_path)
    unique_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    totals = {"requested": len(unique_symbols), "success": 0, "failed": 0, "skipped": 0}

    run_metadata = {
        "run_id": run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "config": {**asdict(config), "data_root": str(config.data_root)},
        "symbol_count": len(unique_symbols),
        "provider": config.provider,
        "state_namespace": namespace or "default",
        "adjustment_status": adjustment_status,
        "actions_status": "included" if config.provider == "yfinance" else "not_available",
        "feed": config.feed,
        "universe_audit": universe_audit or {},
    }
    manifests.mkdir(parents=True, exist_ok=True)
    if universe_records is not None:
        universe_path = manifests / f"universe-{run_id}.parquet"
        _atomic_parquet(universe_records, universe_path)
        run_metadata["universe_snapshot"] = str(universe_path)
    (manifests / f"run-{run_id}.json").write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )

    for offset in range(0, len(unique_symbols), config.batch_size):
        batch = unique_symbols[offset : offset + config.batch_size]
        for symbol in batch:
            destination = bars_root / f"symbol={symbol_key(symbol)}" / "bars.parquet"
            key = (symbol, *requested, config.provider)
            if not config.force and key in completed:
                terminal_status = completed[key]
                if terminal_status == "permanent_failure" or destination.exists():
                    totals["skipped"] += 1
                    _log_symbol("symbol_skipped", symbol, reason=terminal_status)
                    continue
            error = ""
            started_at = datetime.now(timezone.utc).isoformat()
            succeeded = False
            attempts_used = 0
            retryable = True
            for attempt in range(1, config.retries + 2):
                attempts_used = attempt
                try:
                    source = provider_sources[config.provider]
                    incoming = normalize_bars(
                        downloader(symbol, config.start, config.end), symbol, source,
                        adjustment_status, config.feed,
                    )
                    if destination.exists():
                        existing = pd.read_parquet(destination)
                        incoming = pd.concat([existing, incoming], ignore_index=True)
                        incoming = incoming.drop_duplicates("date", keep="last").sort_values("date", ignore_index=True)
                    _atomic_parquet(incoming, destination)
                    record = {
                        "run_id": run_id,
                        "symbol": symbol,
                        "provider_symbol": provider_symbol(symbol),
                        "provider": config.provider,
                        "source": provider_sources[config.provider],
                        "adjustment_status": adjustment_status,
                        "actions_status": "included" if config.provider == "yfinance" else "not_available",
                        "feed": config.feed,
                        "status": "success",
                        "attempts": attempt,
                        "requested_start": requested[0],
                        "requested_end": requested[1],
                        "first_date": incoming["date"].min().date().isoformat(),
                        "last_date": incoming["date"].max().date().isoformat(),
                        "rows": len(incoming),
                        "path": str(destination),
                        "started_at": started_at,
                        "finished_at": datetime.now(timezone.utc).isoformat(),
                        "error": "",
                    }
                    _append_jsonl(manifest_path, record)
                    totals["success"] += 1
                    succeeded = True
                    _log_symbol("symbol_success", symbol, rows=len(incoming), attempts=attempt)
                    break
                except Exception as exc:  # errors are persisted per symbol; the run continues
                    if isinstance(exc, FatalProviderError):
                        raise
                    error = f"{type(exc).__name__}: {exc}"
                    if isinstance(exc, PermanentDownloadError):
                        retryable = False
                        break
                    if attempt <= config.retries:
                        sleep(config.retry_base_seconds * (2 ** (attempt - 1)) + random.random())
            if not succeeded:
                record = {
                    "run_id": run_id,
                    "symbol": symbol,
                    "provider_symbol": provider_symbol(symbol),
                    "provider": config.provider,
                    "source": provider_sources[config.provider],
                    "adjustment_status": adjustment_status,
                    "actions_status": "included" if config.provider == "yfinance" else "not_available",
                    "feed": config.feed,
                    "status": "failed",
                    "attempts": attempts_used,
                    "retryable": retryable,
                    "requested_start": requested[0],
                    "requested_end": requested[1],
                    "first_date": "",
                    "last_date": "",
                    "rows": 0,
                    "path": str(destination),
                    "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(),
                    "error": error,
                }
                _append_jsonl(manifest_path, record)
                totals["failed"] += 1
                _log_symbol(
                    "symbol_failed", symbol, error=error, attempts=attempts_used,
                    retryable=retryable,
                )
            if config.request_delay_seconds > 0:
                sleep(config.request_delay_seconds)
        _latest_tables(manifest_path, manifests)
    return totals


def run_benchmark_download(
    symbols: Iterable[str],
    config: BenchmarkDownloadConfig,
    downloader: BarDownloader | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, int]:
    """Download SPY/QQQ as explicit, independently auditable ETF benchmarks.

    This function intentionally does not call :func:`run_download`: benchmark
    bars have a separate namespace and manifest so they cannot be mistaken for
    the broad raw-stock universe.  It records only credential-free provenance.
    """
    if config.start > config.end:
        raise ValueError("start must not be after end")
    if config.provider not in BENCHMARK_ASSET_CLASSES:
        raise ValueError(f"unsupported benchmark provider: {config.provider}")
    if config.provider == "alpaca" and config.feed not in {"iex", "sip"}:
        raise ValueError("Alpaca stock feed must be iex or sip")
    if config.retries < 0:
        raise ValueError("retries must be non-negative")

    unique_symbols = list(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    supported = set(BENCHMARK_ASSET_CLASSES[config.provider])
    unknown = sorted(set(unique_symbols).difference(supported))
    if unknown:
        raise ValueError(f"unsupported benchmark symbols: {unknown}; supported: {sorted(supported)}")
    if not unique_symbols:
        raise ValueError("at least one explicit benchmark symbol is required")
    if downloader is None:
        if config.provider == "alpaca":
            downloader = make_alpaca_downloader(feed=config.feed)
        else:
            downloader = make_nasdaq_benchmark_downloader(BENCHMARK_ASSET_CLASSES["nasdaq"])

    source = (
        "alpaca_stock_historical_v2"
        if config.provider == "alpaca"
        else "nasdaq_web_benchmark_unadjusted"
    )
    adjustment_status = "raw" if config.provider == "alpaca" else "unadjusted"
    actions_status = "not_available"
    feed = config.feed if config.provider == "alpaca" else ""

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    manifest_dir = benchmark_manifest_directory(config.data_root, config.provider)
    bars_root = benchmark_bars_directory(config.data_root, config.provider)
    manifest_path = manifest_dir / "download.jsonl"
    requested_start, requested_end = config.start.isoformat(), config.end.isoformat()
    completed = _existing_terminal_records(manifest_path)
    collected_at = datetime.now(timezone.utc).isoformat()
    run_metadata = {
        "run_id": run_id,
        "started_at": collected_at,
        "kind": "tradeable_etf_benchmark_bars",
        "symbols": unique_symbols,
        "requested_start": requested_start,
        "requested_end": requested_end,
        "provider": config.provider,
        "source": source,
        "feed": feed,
        "adjustment_status": adjustment_status,
        "actions_status": actions_status,
        "assetclass_by_symbol": {symbol: BENCHMARK_ASSET_CLASSES[config.provider][symbol] for symbol in unique_symbols},
        "output_layout": "bars/daily/provider=<provider>/namespace=benchmarks/symbol=<symbol>/bars.parquet",
        "config": {
            "retries": config.retries,
            "retry_base_seconds": config.retry_base_seconds,
            "request_delay_seconds": config.request_delay_seconds,
            "force": config.force,
        },
    }
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / f"run-{run_id}.json").write_text(
        json.dumps(run_metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    totals = {"requested": len(unique_symbols), "success": 0, "failed": 0, "skipped": 0}
    for symbol in unique_symbols:
        destination = bars_root / f"symbol={symbol_key(symbol)}" / "bars.parquet"
        key = (symbol, requested_start, requested_end, config.provider)
        if not config.force and key in completed and destination.exists():
            totals["skipped"] += 1
            _log_symbol("benchmark_skipped", symbol, reason=completed[key])
            continue
        started_at = datetime.now(timezone.utc).isoformat()
        attempts_used = 0
        error = ""
        retryable = True
        succeeded = False
        for attempt in range(1, config.retries + 2):
            attempts_used = attempt
            try:
                incoming = normalize_bars(
                    downloader(symbol, config.start, config.end),
                    symbol,
                    source=source,
                    adjustment_status=adjustment_status,
                    feed=feed,
                )
                incoming["actions_status"] = actions_status
                incoming["assetclass"] = BENCHMARK_ASSET_CLASSES[config.provider][symbol]
                incoming["collected_at"] = datetime.now(timezone.utc).isoformat()
                if destination.exists():
                    existing = pd.read_parquet(destination)
                    incoming = pd.concat([existing, incoming], ignore_index=True)
                    incoming = incoming.drop_duplicates("date", keep="last").sort_values(
                        "date", ignore_index=True
                    )
                _atomic_parquet(incoming, destination)
                record = {
                    "run_id": run_id, "kind": "tradeable_etf_benchmark_bars", "symbol": symbol,
                    "provider": config.provider, "source": source,
                    "feed": feed, "adjustment_status": adjustment_status,
                    "actions_status": actions_status,
                    "assetclass": BENCHMARK_ASSET_CLASSES[config.provider][symbol],
                    "status": "success", "attempts": attempt,
                    "requested_start": requested_start, "requested_end": requested_end,
                    "first_date": incoming["date"].min().date().isoformat(),
                    "last_date": incoming["date"].max().date().isoformat(), "rows": len(incoming),
                    "path": str(destination), "started_at": started_at,
                    "finished_at": datetime.now(timezone.utc).isoformat(), "error": "",
                }
                _append_jsonl(manifest_path, record)
                totals["success"] += 1
                succeeded = True
                _log_symbol("benchmark_success", symbol, rows=len(incoming), attempts=attempt)
                break
            except Exception as exc:
                if isinstance(exc, FatalProviderError):
                    raise
                error = f"{type(exc).__name__}: {exc}"
                if isinstance(exc, PermanentDownloadError):
                    retryable = False
                    break
                if attempt <= config.retries:
                    sleep(config.retry_base_seconds * (2 ** (attempt - 1)) + random.random())
        if not succeeded:
            record = {
                "run_id": run_id, "kind": "tradeable_etf_benchmark_bars", "symbol": symbol,
                "provider": config.provider, "source": source, "feed": feed,
                "adjustment_status": adjustment_status, "actions_status": actions_status,
                "assetclass": BENCHMARK_ASSET_CLASSES[config.provider][symbol], "status": "failed",
                "attempts": attempts_used, "retryable": retryable, "requested_start": requested_start,
                "requested_end": requested_end, "first_date": "", "last_date": "", "rows": 0,
                "path": str(destination), "started_at": started_at,
                "finished_at": datetime.now(timezone.utc).isoformat(), "error": error,
            }
            _append_jsonl(manifest_path, record)
            totals["failed"] += 1
            _log_symbol("benchmark_failed", symbol, error=error, attempts=attempts_used, retryable=retryable)
        if config.request_delay_seconds > 0:
            sleep(config.request_delay_seconds)
    _latest_tables(manifest_path, manifest_dir)
    return totals
