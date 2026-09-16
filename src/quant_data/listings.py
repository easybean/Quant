from __future__ import annotations

import io
import json
import os
from datetime import date
from pathlib import Path

import pandas as pd
import requests

ALPHA_VANTAGE_URL = "https://www.alphavantage.co/query"
CANONICAL_COLUMNS = [
    "symbol",
    "raw_symbol",
    "name",
    "exchange",
    "asset_type",
    "ipo_date",
    "delisting_date",
    "status",
    "source",
    "source_as_of",
    "sources",
    "provenance",
]


def _clean_date(series: pd.Series) -> pd.Series:
    values = pd.to_datetime(series, errors="coerce").dt.strftime("%Y-%m-%d")
    return values.fillna("")


def normalize_listing_frame(frame: pd.DataFrame, source_as_of: str = "") -> pd.DataFrame:
    """Normalize Alpha Vantage LISTING_STATUS CSV columns into one schema."""
    aliases = {str(column).strip().lower(): column for column in frame.columns}

    def column(name: str, default: str = "", *alternatives: str) -> pd.Series:
        original = next((aliases.get(candidate) for candidate in (name, *alternatives) if aliases.get(candidate)), None)
        if original is None:
            return pd.Series([default] * len(frame), index=frame.index, dtype="object")
        return frame[original].fillna("").astype(str).str.strip()

    result = pd.DataFrame(index=frame.index)
    result["symbol"] = column("symbol").str.upper()
    result["raw_symbol"] = column("raw_symbol")
    result.loc[result["raw_symbol"] == "", "raw_symbol"] = result["symbol"]
    result["name"] = column("name")
    result["exchange"] = column("exchange").str.upper()
    result["asset_type"] = column("assettype", "Unknown", "asset_type")
    result["ipo_date"] = _clean_date(column("ipodate", "", "ipo_date"))
    result["delisting_date"] = _clean_date(column("delistingdate", "", "delisting_date"))
    result["status"] = column("status", "Unknown").str.lower()
    result["source"] = column("source", "alpha_vantage_listing_status")
    result["source_as_of"] = source_as_of or column("source_as_of")
    result["sources"] = column("sources")
    result["provenance"] = column("provenance")
    result = result[result["symbol"] != ""].copy()
    return result[CANONICAL_COLUMNS].drop_duplicates(
        subset=["symbol", "ipo_date", "delisting_date", "status"], keep="last"
    )


def read_listing_csv(path: Path, source_as_of: str = "") -> pd.DataFrame:
    return normalize_listing_frame(pd.read_csv(path), source_as_of=source_as_of)


def merge_listing_files(paths: list[Path], source_as_of: str = "") -> pd.DataFrame:
    if not paths:
        raise ValueError("At least one listing CSV is required")
    frames = [read_listing_csv(path, source_as_of=source_as_of) for path in paths]
    return merge_security_master_frames(frames)


def _text(value: object) -> str:
    if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
        return ""
    return str(value).strip()


def _existing_provenance(value: object) -> list[dict[str, str]]:
    text = _text(value)
    if not text:
        return []
    try:
        decoded = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [item for item in decoded if isinstance(item, dict)]


def merge_security_master_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge cross-source records without collapsing separate ticker lifecycles."""
    if not frames:
        return pd.DataFrame(columns=CANONICAL_COLUMNS)
    combined = pd.concat(frames, ignore_index=True, sort=False)
    for column in CANONICAL_COLUMNS:
        if column not in combined:
            combined[column] = ""
    combined["symbol"] = combined["symbol"].map(_text).str.upper()
    combined["status"] = combined["status"].map(_text).str.lower()
    combined = combined[combined["symbol"] != ""].copy()

    groups: dict[tuple[str, ...], list[dict[str, object]]] = {}
    for record in combined.to_dict("records"):
        symbol = _text(record["symbol"]).upper()
        status = _text(record["status"]).lower()
        if status == "active":
            identity = ("active", symbol)
        else:
            # Missing lifecycle dates remain distinct from dated lifecycles. This is
            # intentionally conservative because ticker symbols can be reused.
            identity = (
                status or "unknown", symbol, _text(record["ipo_date"]),
                _text(record["delisting_date"]),
            )
        groups.setdefault(identity, []).append(record)

    def source_rank(record: dict[str, object]) -> tuple[int, str]:
        source = _text(record.get("source"))
        rank = 0 if source == "nasdaq_trader_symbol_directory" else 1
        return rank, source

    output: list[dict[str, str]] = []
    for identity, records in groups.items():
        ordered = sorted(records, key=source_rank)

        def choose(field: str, *, prefer_alpha: bool = False) -> str:
            candidates = records if prefer_alpha else ordered
            if prefer_alpha:
                candidates = sorted(
                    records,
                    key=lambda record: (
                        0 if _text(record.get("source")) == "alpha_vantage_listing_status" else 1,
                        _text(record.get("source")),
                    ),
                )
            return next((_text(record.get(field)) for record in candidates if _text(record.get(field))), "")

        provenance: list[dict[str, str]] = []
        sources: set[str] = set()
        for record in records:
            source = _text(record.get("source"))
            if source and source != "merged":
                sources.add(source)
            inherited = _existing_provenance(record.get("provenance"))
            if inherited:
                provenance.extend(inherited)
                continue
            provenance.append(
                {
                    "source": source,
                    "source_as_of": _text(record.get("source_as_of")),
                    "raw_symbol": _text(record.get("raw_symbol")) or _text(record.get("symbol")),
                    "name": _text(record.get("name")),
                    "exchange": _text(record.get("exchange")),
                    "asset_type": _text(record.get("asset_type")),
                    "ipo_date": _text(record.get("ipo_date")),
                    "delisting_date": _text(record.get("delisting_date")),
                    "status": _text(record.get("status")),
                }
            )
        # Deduplicate provenance deterministically across repeated incremental imports.
        unique_provenance = {
            json.dumps(item, ensure_ascii=False, sort_keys=True): item for item in provenance
        }
        provenance = [unique_provenance[key] for key in sorted(unique_provenance)]
        sources.update(item.get("source", "") for item in provenance if item.get("source"))
        source_list = sorted(sources)
        status = "active" if identity[0] == "active" else identity[0]
        output.append(
            {
                "symbol": identity[1],
                "raw_symbol": choose("raw_symbol") or identity[1],
                "name": choose("name"),
                "exchange": choose("exchange"),
                "asset_type": choose("asset_type"),
                "ipo_date": choose("ipo_date", prefer_alpha=True),
                "delisting_date": choose("delisting_date", prefer_alpha=True),
                "status": status,
                "source": source_list[0] if len(source_list) == 1 else "merged",
                "source_as_of": max(
                    (_text(record.get("source_as_of")) for record in records), default=""
                ),
                "sources": json.dumps(source_list, ensure_ascii=False, separators=(",", ":")),
                "provenance": json.dumps(provenance, ensure_ascii=False, separators=(",", ":")),
            }
        )
    return pd.DataFrame(output, columns=CANONICAL_COLUMNS).sort_values(
        ["symbol", "status", "ipo_date", "delisting_date"], kind="stable", ignore_index=True
    )


NASDAQ_TRADER_EXCHANGES = {
    "Q": "NASDAQ",
    "G": "NASDAQ",
    "S": "NASDAQ",
    "N": "NYSE",
    "A": "NYSE AMERICAN",
    "P": "NYSE ARCA",
    "Z": "CBOE BZX",
    "V": "IEX",
}


def _read_pipe_file(path: Path) -> pd.DataFrame:
    return pd.read_csv(path, sep="|", dtype=str, keep_default_na=False)


def normalize_nasdaq_trader_files(
    nasdaq_listed: Path,
    other_listed: Path,
    source_as_of: str,
) -> pd.DataFrame:
    """Convert Nasdaq Trader Symbol Directory pipe files to the canonical master."""
    frames: list[pd.DataFrame] = []
    specifications = [
        (_read_pipe_file(nasdaq_listed), "Symbol", None),
        (_read_pipe_file(other_listed), "ACT Symbol", "Exchange"),
    ]
    for raw, symbol_column, exchange_column in specifications:
        required = {symbol_column, "Security Name", "ETF", "Test Issue"}
        missing = required.difference(raw.columns)
        if missing:
            raise ValueError(f"Nasdaq Trader file missing columns: {sorted(missing)}")
        symbols = raw[symbol_column].astype(str).str.strip()
        valid = (
            symbols.ne("")
            & ~symbols.str.startswith("File Creation Time", na=False)
            & raw["Test Issue"].astype(str).str.strip().str.upper().ne("Y")
        )
        raw = raw.loc[valid].copy()
        symbols = raw[symbol_column].astype(str).str.strip()
        result = pd.DataFrame(index=raw.index)
        result["symbol"] = symbols.str.upper()
        result["raw_symbol"] = symbols
        result["name"] = raw["Security Name"].astype(str).str.strip()
        if exchange_column is None:
            result["exchange"] = "NASDAQ"
        else:
            codes = raw[exchange_column].astype(str).str.strip().str.upper()
            result["exchange"] = codes.map(NASDAQ_TRADER_EXCHANGES).fillna(codes)
        is_etf = raw["ETF"].astype(str).str.strip().str.upper().eq("Y")
        result["asset_type"] = is_etf.map({True: "ETF", False: "Stock"})
        result["ipo_date"] = ""
        result["delisting_date"] = ""
        result["status"] = "active"
        result["source"] = "nasdaq_trader_symbol_directory"
        result["source_as_of"] = source_as_of
        result["sources"] = ""
        result["provenance"] = ""
        frames.append(result[CANONICAL_COLUMNS])
    combined = pd.concat(frames, ignore_index=True)
    return combined.drop_duplicates("symbol", keep="first").sort_values(
        "symbol", kind="stable", ignore_index=True
    )


def fetch_alpha_vantage_listing(
    *,
    state: str,
    api_key_env: str = "ALPHAVANTAGE_API_KEY",
    as_of: str = "",
    timeout: float = 60,
) -> pd.DataFrame:
    if state not in {"active", "delisted"}:
        raise ValueError("state must be active or delisted")
    api_key = os.environ.get(api_key_env)
    if not api_key:
        raise RuntimeError(f"Environment variable {api_key_env} is not set")
    params = {"function": "LISTING_STATUS", "state": state, "apikey": api_key}
    if as_of:
        params["date"] = as_of
    response = requests.get(ALPHA_VANTAGE_URL, params=params, timeout=timeout)
    response.raise_for_status()
    if response.text.lstrip().startswith("{"):
        raise RuntimeError(f"Alpha Vantage returned an error: {response.text[:300]}")
    return normalize_listing_frame(
        pd.read_csv(io.StringIO(response.text)), source_as_of=as_of or date.today().isoformat()
    )
