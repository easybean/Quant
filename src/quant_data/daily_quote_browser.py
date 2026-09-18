"""Bounded, read-only access to pre-indexed US daily raw bars.

The HTTP layer never walks ``bars/daily``.  An offline command first turns the
existing structural-audit coverage index into one fixed public catalogue.  A
request can then select exactly one catalogue entry and read at most one raw
Parquet file.  Provider/namespace duplicates intentionally remain separate
series instead of being silently stitched into a price history.
"""
from __future__ import annotations

import json
import argparse
import fcntl
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
# A display composite is deliberately small and bounded.  It is not a general
# purpose cross-provider reconciliation mechanism.
MAX_COMPOSITE_MEMBERS = 8
_QUERY = re.compile(r"[A-Z0-9.$_-]{1,32}")
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


def refresh_yahoo_browser_catalogue(data_root: str | Path) -> dict[str, object]:
    """Offline publication of acquired Yahoo views; never grants research eligibility.

    Preserve every other provider/namespace. The HTTP layer still reads a fixed
    catalogue and never scans bar directories or stitches sources together.
    """
    root = _data_root(data_root)
    destination = _catalogue_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with (destination.parent / "us-daily-browser-refresh.lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        if destination.exists():
            payload = json.loads(destination.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or payload.get("schema_version") != DAILY_BROWSER_SCHEMA_VERSION or not isinstance(payload.get("series"), list):
                raise DailyQuoteUnavailable("Existing browser catalogue is invalid; refusing replacement")
        else:
            payload = {"schema_version": DAILY_BROWSER_SCHEMA_VERSION,
                       "scope": "美国股票 raw 日线；来源序列未合并。", "price_basis": "raw_or_unadjusted", "series": []}
        entries = [item for item in payload["series"] if not (
            isinstance(item, dict) and item.get("provider") == "yfinance" and item.get("namespace") == "yahoo-daily-v1")]
        raw_root = root / "bars" / "daily"
        namespace = raw_root / "provider=yfinance" / "namespace=yahoo-daily-v1"
        rejected = 0
        for path in sorted(namespace.glob("symbol=*/bars.parquet")):
            relative = path.relative_to(raw_root).as_posix()
            key = path.parent.name.removeprefix("symbol=")
            series_id = f"yfinance:yahoo-daily-v1:{key}"
            if (not _safe_raw_relative(relative) or not _SERIES.fullmatch(series_id)
                    or not _QUERY.fullmatch(_symbol(key)) or raw_root.resolve() not in path.resolve().parents):
                rejected += 1
                continue
            try:
                frame = pd.read_parquet(path, columns=list(_PUBLIC_COLUMNS))
                dates = pd.to_datetime(frame["date"], errors="raise")
                if frame.empty or dates.isna().any() or dates.dt.normalize().duplicated().any() or not frame["source"].eq("yfinance").all():
                    raise ValueError("invalid_yahoo_view")
            except (OSError, ValueError, KeyError, ImportError):
                rejected += 1
                continue
            entries.append({"series_id": series_id, "symbol": _symbol(key), "provider": "yfinance",
                "namespace": "yahoo-daily-v1", "raw_relative_path": relative,
                "first_date": dates.min().date().isoformat(), "last_date": dates.max().date().isoformat(),
                "rows": len(frame), "quality_warning": "Yahoo 同步视图，未获研究资格；历史价格可能经过拆股调整，不能视为总回报或历史 PIT 数据。"})
        # Newest series first within each symbol, keeping sources visibly separate.
        entries.sort(key=lambda item: str(item.get("last_date", "")), reverse=True)
        entries.sort(key=lambda item: str(item.get("symbol", "")))
        payload["series"] = entries
        _atomic_json(payload, destination)
        return {"series": len(entries), "rejected_yahoo_files": rejected,
                "yahoo_series": sum(item.get("provider") == "yfinance" and item.get("namespace") == "yahoo-daily-v1" for item in entries)}


def search_us_daily_series(query: str, limit: int = 10, *, data_root: str | Path | None = None) -> dict[str, object]:
    normalized = query.strip().upper()
    if not _QUERY.fullmatch(normalized):
        raise DailyQuoteInputError("query must be 1-32 uppercase letters, numbers, dot, dollar sign, dash, or underscore")
    if not isinstance(limit, int) or not 1 <= limit <= MAX_SEARCH_RESULTS:
        raise DailyQuoteInputError(f"limit must be between 1 and {MAX_SEARCH_RESULTS}")
    entries = _load_catalogue(_data_root(data_root))
    composites = _canonical_catalogue(entries)
    # A ticker lookup should not be pushed behind a fuzzy match (for example
    # ``AA`` behind ``AAPL``).  The limit is deliberately applied to stocks,
    # rather than to underlying provider files.
    exact = [item for item in composites if item["symbol"] == normalized]
    partial = [item for item in composites if item["symbol"] != normalized and normalized in item["symbol"]]
    matches = exact + partial
    items = [_public_series(entry) for entry in matches[:limit]]
    _attach_sync_status(items, _data_root(data_root))
    return {"schema_version": DAILY_BROWSER_SCHEMA_VERSION, "items": items, "limit": limit}


def _attach_sync_status(items: list[dict[str, Any]], root: Path) -> None:
    # Only the server-published active acquisition pool receives a stale warning;
    # historical/delisted records are not expected to have today's bar.
    status_path = root / "catalogue/us-daily-sync-status-v1.json"
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(status, dict) and status.get("schema_version") == "us-daily-sync-status-v1" and isinstance(status.get("symbols"), dict):
            for item in items:
                symbol_status = status.get("symbols", {}).get(item["symbol"])
                if symbol_status:
                    item["sync_status"] = {**symbol_status, "target_date": status["target_date"], "checked_at": status["generated_at"]}
                    if symbol_status.get("state") == "needs_update":
                        item["quality_warning"] = f"同步尚未完成：目标交易日 {status['target_date']}，实际行情末日 {item['last_date']}；可能还存在中间缺口。" + str(item["quality_warning"])
                    elif symbol_status.get("state") == "missing_sessions_candidate":
                        item["quality_warning"] = "行情末日已更新，但历史中间交易日存在待核实缺口，不能视为完整数据。" + str(item["quality_warning"])
    except (OSError, ValueError, TypeError, KeyError):
        pass  # Absent/unreadable status is unknown, never advertised as current.


def _public_series(entry: dict[str, Any]) -> dict[str, object]:
    return {key: entry.get(key, "") for key in ("series_id", "symbol", "provider", "namespace", "first_date", "last_date", "rows", "rows_estimated", "quality_warning")}


def _entry_symbol_key(entry: dict[str, Any]) -> str:
    """Return the catalogue identity, never the user-visible ticker alone."""
    return str(entry["series_id"]).rsplit(":", 1)[-1]


def _is_yahoo(entry: dict[str, Any]) -> bool:
    return entry.get("provider") == "yfinance" and entry.get("namespace") == "yahoo-daily-v1"


def _is_append_source(entry: dict[str, Any]) -> bool:
    return _is_yahoo(entry) or (entry.get("provider"), entry.get("namespace")) in {("nasdaq", "nasdaq-daily-recovery-v1"), ("yfinance", "yahoo-symbol-recovery-v1"), ("alpaca", "alpaca-sip-recovery-v1"), ("alpaca", "alpaca-sip-symbol-mapping-recovery-v1"), ("massive", "massive-daily-v1"), ("massive", "massive-current-alias-daily-v1")}


def _append_priority(entry: dict[str, Any]) -> tuple[int, str]:
    priorities = {"massive-daily-v1": 0, "massive-current-alias-daily-v1": 0, "yahoo-daily-v1": 1, "yahoo-symbol-recovery-v1": 2, "alpaca-sip-recovery-v1": 3, "alpaca-sip-symbol-mapping-recovery-v1": 4, "nasdaq-daily-recovery-v1": 5}
    return priorities.get(str(entry.get("namespace")), 4), str(entry["series_id"])


def _as_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _canonical_catalogue(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build public stock views from the fixed catalogue, without opening bars."""
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entry in entries:
        grouped.setdefault((_entry_symbol_key(entry), entry["symbol"]), []).append(entry)
    composites: list[dict[str, Any]] = []
    warning = ("仅用于行情浏览：逐行保留原始来源；跨来源复权口径未经验证，"
               "不得用于回测、正式研究快照或交易决策。")
    for (symbol_key, symbol), members in grouped.items():
        base = min(members, key=_base_priority)
        base_first, base_last = _as_date(base.get("first_date")), _as_date(base.get("last_date"))
        # These fields must describe the files the reader can actually use:
        # an unrelated second non-Yahoo feed is intentionally not stitched.
        yahoo_tail = [member for member in members if _is_append_source(member) and member is not base
                      and base_last is not None and (_as_date(member.get("last_date")) or date.min) > base_last]
        usable_lasts = [base_last] + [_as_date(member.get("last_date")) for member in yahoo_tail]
        composites.append({
            "series_id": f"us-daily:{symbol_key}", "symbol": symbol,
            "provider": "display_composite", "namespace": "us-daily-view-v1",
            "first_date": base_first.isoformat() if base_first else "",
            "last_date": max(item for item in usable_lasts if item).isoformat() if any(usable_lasts) else "",
            # Catalogue counts cannot account for Yahoo/base date overlap.
            "rows": int(base.get("rows") or 0) + sum(int(member.get("rows") or 0) for member in yahoo_tail),
            "rows_estimated": True,
            "quality_warning": warning, "_members": members,
        })
    return sorted(composites, key=lambda item: (str(item["symbol"]), str(item["series_id"])))


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
    entries = _load_catalogue(root)
    selected = next((entry for entry in _canonical_catalogue(entries) if entry["series_id"] == series_id), None)
    # Keep direct catalogue ids readable for old clients.  New search results
    # always use the canonical stock id above.
    legacy_selected = next((entry for entry in entries if entry["series_id"] == series_id), None)
    if selected is None and legacy_selected is None:
        raise DailyQuoteInputError("series_id is not in the fixed daily catalogue")
    if legacy_selected is not None and selected is None:
        _attach_sync_status([legacy_selected], root)
        return _read_one_series(legacy_selected, start_date, end_date, root)
    assert selected is not None
    _attach_sync_status([selected], root)
    members = list(selected["_members"])
    if not 1 <= len(members) <= MAX_COMPOSITE_MEMBERS:
        raise DailyQuoteUnavailable("该行情展示视图包含过多来源文件，拒绝读取")
    return _read_composite(selected, members, start_date, end_date, root)


def _read_frame(entry: dict[str, Any], root: Path) -> pd.DataFrame:
    raw_root = (root / "bars" / "daily").resolve()
    path = (raw_root / entry["raw_relative_path"]).resolve()
    if raw_root not in path.parents or not path.is_file():
        raise DailyQuoteUnavailable("所选原始日线文件不可读取")
    try:
        frame = pd.read_parquet(path, columns=list(_PUBLIC_COLUMNS))
    except (OSError, ValueError, KeyError, ImportError) as exc:
        raise DailyQuoteUnavailable("所选原始日线数据不可读取") from exc
    if "date" not in frame or any(column not in frame for column in _PUBLIC_COLUMNS):
        raise DailyQuoteUnavailable("所选原始日线缺少日期字段")
    dates = pd.to_datetime(frame["date"], errors="coerce")
    if dates.isna().any():
        raise DailyQuoteUnavailable("所选原始日线日期无效")
    frame = frame.copy()
    frame["date"] = dates.dt.normalize()
    return frame


def _bars_from_frame(frame: pd.DataFrame, start_date: date, end_date: date) -> list[dict[str, object]]:
    dates = frame["date"].dt.date
    frame = frame.loc[(dates >= start_date) & (dates <= end_date)].copy()
    if len(frame) > MAX_RETURNED_ROWS:
        raise DailyQuoteUnavailable("所选日期区间超过日线返回上限")
    frame["date"] = frame["date"].dt.strftime("%Y-%m-%d")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.sort_values("date", kind="stable")
    return [{key: (None if pd.isna(value) else value.item() if hasattr(value, "item") else value) for key, value in row.items()} for row in frame[list(_PUBLIC_COLUMNS)].to_dict("records")]


def _response(selected: dict[str, Any], bars: list[dict[str, object]], start_date: date, end_date: date, *, composite: bool) -> dict[str, object]:
    frame = pd.DataFrame(bars)
    source_values = sorted({str(value) for value in frame.get("source", pd.Series(dtype=str)).dropna()})
    adjustment_values = sorted({str(value) for value in frame.get("adjustment_status", pd.Series(dtype=str)).dropna()})
    response: dict[str, object] = {
        "schema_version": DAILY_BROWSER_SCHEMA_VERSION, "series": _public_series(selected),
        "requested_range": {"start": start_date.isoformat(), "end": end_date.isoformat()},
        "returned_rows": len(bars), "max_returned_rows": MAX_RETURNED_ROWS,
        "source": source_values, "adjustment_status": adjustment_values,
        "price_basis": "display_composite_unverified" if composite else "raw_or_unadjusted", "quality_warning": selected.get("quality_warning", ""),
        "limitations": ["原始/未复权价格，不代表总回报。", "来源序列未合并；缺失日期不等同于停牌。", "公司行为与退市最终回报未处理。"],
        "bars": bars,
    }
    if composite:
        response["limitations"] = ["仅用于行情浏览；跨来源复权口径与总回报未经验证，不得用于回测或正式研究快照。", "基准历史优先保留；补数仅追加基准最后日期之后，补数重叠时优先保留 Yahoo。", "公司行为、退市与 PIT 适用性均未验证。"]
        response["source_segments"] = _source_segments(bars)
    return response


def _read_one_series(selected: dict[str, Any], start_date: date, end_date: date, root: Path) -> dict[str, object]:
    return _response(selected, _bars_from_frame(_read_frame(selected, root), start_date, end_date), start_date, end_date, composite=False)


def _base_priority(entry: dict[str, Any]) -> tuple[int, int, str, str]:
    # The migrated legacy catalogue has provider/namespace ``unknown`` and is
    # the historic Nasdaq file.  Otherwise prefer a non-Yahoo source with the
    # broadest advertised span; all ties are deterministic.
    legacy = entry.get("provider") == "unknown" and str(entry.get("raw_relative_path", "")).startswith("symbol=")
    start, end = _as_date(entry.get("first_date")), _as_date(entry.get("last_date"))
    span = (end - start).days if start and end else -1
    return (0 if legacy else 1 if not _is_append_source(entry) else 2, -span, str(entry["series_id"]), str(entry["raw_relative_path"]))


def _read_composite(selected: dict[str, Any], members: list[dict[str, Any]], start_date: date, end_date: date, root: Path) -> dict[str, object]:
    base = min(members, key=_base_priority)
    base_frame = _read_frame(base, root)
    # This is deliberately the full base history, not the requested interval:
    # chunked UI requests must choose the same source at the boundary.
    base_last = base_frame["date"].max()
    merged = base_frame.copy()
    for member in sorted((item for item in members if _is_append_source(item) and item is not base), key=_append_priority):
        yahoo = _read_frame(member, root)
        merged = pd.concat([merged, yahoo.loc[yahoo["date"] > base_last]], ignore_index=True)
    # A base always wins an overlap.  Yahoo rows were appended only after its
    # final date, but retain stable de-duplication as a defensive invariant.
    merged = merged.sort_values("date", kind="stable").drop_duplicates("date", keep="first")
    bars = _bars_from_frame(merged, start_date, end_date)
    if len(bars) > MAX_RETURNED_ROWS:
        raise DailyQuoteUnavailable("所选日期区间超过日线返回上限")
    return _response(selected, bars, start_date, end_date, composite=True)


def _source_segments(bars: list[dict[str, object]]) -> list[dict[str, object]]:
    segments: list[dict[str, object]] = []
    for bar in bars:
        signature = (bar.get("source"), bar.get("adjustment_status"))
        if segments and (segments[-1]["source"], segments[-1]["adjustment_status"]) == signature:
            segments[-1]["end"] = bar["date"]
            segments[-1]["rows"] = int(segments[-1]["rows"]) + 1
        else:
            segments.append({"start": bar["date"], "end": bar["date"], "rows": 1,
                             "source": bar.get("source"), "adjustment_status": bar.get("adjustment_status")})
    return segments


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Offline refresh of unqualified Yahoo browser series")
    parser.add_argument("--data-root", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(refresh_yahoo_browser_catalogue(arguments.data_root)))
