"""Offline inventory of actual existing histories, without changing prices."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from .daily_quote_browser import _atomic_json, _safe_raw_relative, _symbol, _QUERY


def build_sync_baselines(data_root: Path, output: Path) -> dict[str, object]:
    root = data_root / "bars" / "daily"
    if not root.is_dir():
        raise ValueError("existing_daily_data_root_missing")
    symbols: dict[str, dict[str, object]] = {}
    rejected: list[dict[str, str]] = []
    today = datetime.now(timezone.utc).date()
    for pattern in ("symbol=*/bars.parquet", "provider=*/symbol=*/bars.parquet", "provider=*/namespace=*/symbol=*/bars.parquet"):
        for path in root.glob(pattern):
            relative = path.relative_to(root).as_posix()
            if "namespace=yahoo-daily-v1/" in relative or "synthetic" in relative.lower():
                continue
            symbol = _symbol(path.parent.name.removeprefix("symbol="))
            if not _safe_raw_relative(relative) or not _QUERY.fullmatch(symbol) or root.resolve() not in path.resolve().parents:
                rejected.append({"relative_path": relative, "error_code": "invalid_path"})
                continue
            try:
                frame = pd.read_parquet(path, columns=["date", "source"])
                dates = pd.to_datetime(frame["date"], errors="raise")
                if dates.empty or dates.isna().any() or dates.max().date() > today:
                    raise ValueError("invalid_dates")
                if frame["source"].astype(str).str.contains("synthetic", case=False).any():
                    continue
                last = dates.max().date().isoformat()
                record = {"last_date": last, "first_date": dates.min().date().isoformat(),
                          "relative_path": relative, "source": sorted(frame["source"].dropna().astype(str).unique().tolist())}
                if last > str(symbols.get(symbol, {}).get("last_date", "")):
                    symbols[symbol] = record
            except (OSError, ValueError, KeyError, ImportError):
                rejected.append({"relative_path": relative, "error_code": "unreadable_or_invalid_history"})
    payload = {"schema_version": "yahoo-sync-baselines-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
               "purpose": "request boundaries only; no provider stitching or research qualification",
               "symbols": symbols, "rejected": rejected}
    _atomic_json(payload, output)
    return {"symbols": len(symbols), "rejected": len(rejected)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inventory actual pre-Yahoo history endpoints offline")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(build_sync_baselines(arguments.data_root, arguments.output)))
