"""Bounded, read-only access to pre-indexed US daily raw bars.

The HTTP layer never walks ``bars/daily``.  An offline command first turns the
existing structural-audit coverage index into one fixed public catalogue.  A
request can then select exactly one catalogue entry and read at most one raw
Parquet file.  Provider/namespace duplicates intentionally remain separate
series instead of being silently stitched into a price history.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd


DAILY_BROWSER_SCHEMA_VERSION = "us-daily-browser-v1"
MAX_SEARCH_RESULTS = 20
MAX_CALENDAR_DAYS = 366
MAX_RETURNED_ROWS = 300
_QUERY = re.compile(r"[A-Z0-9._-]{1,32}")
_SERIES = re.compile(r"[A-Za-z0-9._:-]{3,180}")
_RAW_FILE = re.compile(r"(?:provider=[A-Za-z0-9._-]+/)?(?:namespace=[A-Za-z0-9._-]+/)?symbol=[A-Za-z0-9._-]+/bars\.parquet")
_PUBLIC_COLUMNS = ("date", "open", "high", "low", "close", "volume", "source", "adjustment_status")


class DailyQuoteInputError(ValueError):
    """Raised for a rejected bounded browser request."""


class DailyQuoteUnavailable(RuntimeError):
    """Raised when the pre-generated catalogue or selected raw file is absent."""


def _data_root(value: str | Path | None = None) -> Path:
    return Path(value or os.getenv("QUANT_DATA_ROOT", "/home/davidou/quant/data"))


def _catalogue_path(root: Path) -> Path:
    return root / "catalogue" / "us-daily-browser-v1.json"


def _atomic_json(value: object, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(f".{uuid.uuid4().hex}.tmp.json")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, destination)


def _symbol(symbol_key: object) -> str:
    return str(symbol_key).rsplit("-", 1)[0].upper()


def _safe_raw_relative(value: object) -> str | None:
    candidate = str(value).replace("\\", "/")
    return candidate if _RAW_FILE.fullmatch(candidate) else None


def build_us_daily_browser_catalogue(*, audit_root: str | Path, output: str | Path) -> dict[str, object]:
    """Create a catalogue from the existing audit index; do not open any bar.

    This offline operation reads ``coverage.parquet`` only and writes a new
    catalogue file.  It rejects malformed paths so the API can never turn a
    browser-supplied identifier into arbitrary filesystem access.
    """
    audit = Path(audit_root)
    coverage = pd.read_parquet(audit / "coverage.parquet")
    quality_by_identity: dict[tuple[str, str, str], list[str]] = {}
    quality_path = audit.parent / "quality-v1" / "findings.parquet"
    if quality_path.is_file():
        findings = pd.read_parquet(quality_path, columns=["provider", "namespace", "symbol_key", "kind"])
        for finding in findings.to_dict("records"):
            key = (str(finding.get("provider", "")), str(finding.get("namespace", "")), str(finding.get("symbol_key", "")))
            kind = str(finding.get("kind", ""))
            if key[2] and kind:
                quality_by_identity.setdefault(key, []).append(kind)
    entries: list[dict[str, object]] = []
    for record in coverage.to_dict("records"):
        relative = _safe_raw_relative(record.get("raw_relative_path"))
        if record.get("status") != "ok" or not relative:
            continue
        symbol_key = str(record.get("symbol_key", ""))
        provider, namespace = str(record.get("provider", "unknown")), str(record.get("namespace", "unknown"))
        series_id = f"{provider}:{namespace}:{symbol_key}"
        if not _SERIES.fullmatch(series_id):
            continue
        kinds = sorted(set(quality_by_identity.get((provider, namespace, symbol_key), [])))
        quality_warning = "结构审计已完成；公司行为、复权与退市最终回报未处理。"
        if kinds:
            quality_warning = f"质量发现：{'、'.join(kinds)}。{quality_warning}"
        entries.append({
            "series_id": series_id, "symbol": _symbol(symbol_key), "provider": provider,
            "namespace": namespace, "raw_relative_path": relative,
            "first_date": str(record.get("first_date") or ""), "last_date": str(record.get("last_date") or ""),
            "rows": int(record.get("input_rows") or 0),
            "quality_warning": quality_warning,
        })
    entries.sort(key=lambda item: (str(item["symbol"]), str(item["provider"]), str(item["namespace"]), str(item["series_id"])))
    payload: dict[str, object] = {
        "schema_version": DAILY_BROWSER_SCHEMA_VERSION,
        "scope": "美国股票 raw 日线；来源序列未合并。",
        "price_basis": "raw_or_unadjusted",
        "series": entries,
    }
    _atomic_json(payload, Path(output))
    return {"schema_version": DAILY_BROWSER_SCHEMA_VERSION, "series": len(entries), "output": str(output)}


def _load_catalogue(root: Path) -> list[dict[str, Any]]:
    try:
        payload = json.loads(_catalogue_path(root).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise DailyQuoteUnavailable("日线浏览索引尚未生成") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise DailyQuoteUnavailable("日线浏览索引不可读取") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != DAILY_BROWSER_SCHEMA_VERSION or not isinstance(payload.get("series"), list):
        raise DailyQuoteUnavailable("日线浏览索引格式无效")
    entries: list[dict[str, Any]] = []
    for item in payload["series"]:
        if not isinstance(item, dict):
            continue
        relative = _safe_raw_relative(item.get("raw_relative_path"))
        series_id, symbol = str(item.get("series_id", "")), str(item.get("symbol", "")).upper()
        if relative and _SERIES.fullmatch(series_id) and _QUERY.fullmatch(symbol):
            entries.append({**item, "raw_relative_path": relative, "symbol": symbol})
    return entries


def search_us_daily_series(query: str, limit: int = 10, *, data_root: str | Path | None = None) -> dict[str, object]:
    normalized = query.strip().upper()
    if not _QUERY.fullmatch(normalized):
        raise DailyQuoteInputError("query must be 1-32 uppercase letters, numbers, dot, dash, or underscore")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise DailyQuoteInputError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
    entries = _load_catalogue(_data_root(data_root))
    matches = [entry for entry in entries if normalized in entry["symbol"]][:limit]
    return {"schema_version": DAILY_BROWSER_SCHEMA_VERSION, "items": [_public_series(entry) for entry in matches], "limit": limit}


def _public_series(entry: dict[str, Any]) -> dict[str, object]:
    return {key: entry.get(key, "") for key in ("series_id", "symbol", "provider", "namespace", "first_date", "last_date", "rows", "quality_warning")}


def _bounded_dates(start: str, end: str) -> tuple[date, date]:
    try:
        start_date, end_date = date.fromisoformat(start), date.fromisoformat(end)
    except (TypeError, ValueError) as exc:
        raise DailyQuoteInputError("start and end must be ISO dates") from exc
    if end_date < start_date:
        raise DailyQuoteInputError("end must not be before start")
    if (end_date - start_date).days > MAX_CALENDAR_DAYS:
        raise DailyQuoteInputError(f"date range must not exceed {MAX_CALENDAR_DAYS} calendar days")
    return start_date, end_date


def read_us_daily_bars(series_id: str, start: str, end: str, *, data_root: str | Path | None = None) -> dict[str, object]:
    if not _SERIES.fullmatch(series_id):
        raise DailyQuoteInputError("series_id is invalid")
    start_date, end_date = _bounded_dates(start, end)
    root = _data_root(data_root)
    selected = next((entry for entry in _load_catalogue(root) if entry["series_id"] == series_id), None)
    if selected is None:
        raise DailyQuoteInputError("series_id is not in the fixed daily catalogue")
    raw_root = (root / "bars" / "daily").resolve()
    path = (raw_root / selected["raw_relative_path"]).resolve()
    if raw_root not in path.parents or not path.is_file():
        raise DailyQuoteUnavailable("所选原始日线文件不可读取")
    try:
        frame = pd.read_parquet(path, columns=list(_PUBLIC_COLUMNS))
    except (OSError, ValueError, KeyError, ImportError) as exc:
        raise DailyQuoteUnavailable("所选原始日线数据不可读取") from exc
    if "date" not in frame:
        raise DailyQuoteUnavailable("所选原始日线缺少日期字段")
    dates = pd.to_datetime(frame["date"], errors="coerce").dt.date
    frame = frame.loc[dates.notna() & (dates >= start_date) & (dates <= end_date)].copy()
    if len(frame) > MAX_RETURNED_ROWS:
        raise DailyQuoteUnavailable("所选日期区间超过日线返回上限")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        if column not in frame:
            raise DailyQuoteUnavailable("所选原始日线缺少 OHLCV 字段")
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values("date", kind="stable")
    bars = [{key: (None if pd.isna(value) else value.item() if hasattr(value, "item") else value) for key, value in row.items()} for row in frame[list(_PUBLIC_COLUMNS)].to_dict("records")]
    source_values = sorted({str(value) for value in frame.get("source", pd.Series(dtype=str)).dropna()})
    adjustment_values = sorted({str(value) for value in frame.get("adjustment_status", pd.Series(dtype=str)).dropna()})
    return {
        "schema_version": DAILY_BROWSER_SCHEMA_VERSION, "series": _public_series(selected),
        "requested_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "returned_rows": len(bars), "max_returned_rows": MAX_RETURNED_ROWS,
        "source": source_values, "adjustment_status": adjustment_values,
        "price_basis": "raw_or_unadjusted", "quality_warning": selected.get("quality_warning", ""),
        "limitations": ["原始/未复权价格，不代表总回报。", "来源序列未合并；缺失日期不等同于停牌。", "公司行为与退市最终回报未处理。"],
        "bars": bars,
    }
