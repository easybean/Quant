"""Offline per-symbol acquisition endpoints; not a research quality verdict."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from .daily_sync import _target_end, _earliest_successful_request_starts
from .daily_quote_browser import _atomic_json


def build_sync_coverage(data_root: Path, security_master: Path) -> dict[str, object]:
    manifest = data_root / "manifests/yahoo-daily-v1"
    baseline = json.loads((manifest / "baselines.json").read_text())["symbols"]
    master = pd.read_parquet(security_master)
    eligible = master[master.asset_type.fillna("").str.casefold().isin(["stock", "etf"])]
    active = set(eligible.loc[eligible.status.fillna("").str.casefold().eq("active"), "symbol"].dropna().str.strip().str.upper())
    historical = set(eligible.symbol.dropna().str.strip().str.upper()) - active
    starts = _earliest_successful_request_starts(manifest / "records.jsonl")
    catalogue = json.loads((data_root / "catalogue/us-daily-browser-v1.json").read_text())
    if catalogue.get("schema_version") != "us-daily-browser-v1" or not isinstance(catalogue.get("series"), list):
        raise ValueError("browser_catalogue_invalid")
    acquired = {entry["symbol"]: entry["last_date"] for entry in catalogue["series"]
                if entry.get("provider") == "yfinance" and entry.get("namespace") == "yahoo-daily-v1"}
    target = _target_end(datetime.now(timezone.utc))
    rows = []
    for symbol in sorted(active):
        previous = baseline.get(symbol, {}).get("last_date")
        latest = previous
        error = None
        if symbol in acquired:
            try:
                current = pd.Timestamp(acquired[symbol]).date().isoformat()
                latest = max(latest or "", current)
            except (OSError, ValueError, KeyError, ImportError):
                error = "invalid_yahoo_catalogue_date"
        bridge_needed = bool(previous and previous < target.isoformat() and starts.get(symbol, target + timedelta(days=1)).isoformat() > (pd.Timestamp(previous).date() + timedelta(days=1)).isoformat())
        status = "needs_update" if not latest or latest < target.isoformat() or bridge_needed or error else "endpoint_current"
        rows.append({"symbol": symbol, "baseline_last_date": previous, "latest_date": latest,
                     "bridge_needed": bridge_needed, "status": status, "error_code": error})
    payload = {"schema_version": "yahoo-sync-coverage-v1", "generated_at": datetime.now(timezone.utc).isoformat(),
        "target_date": target.isoformat(), "active_symbols": len(active), "historical_only_symbols": len(historical),
        "endpoint_current": sum(row["status"] == "endpoint_current" for row in rows),
        "needs_update": sum(row["status"] != "endpoint_current" for row in rows),
        "warning": "Endpoint and request-window coverage only: missing sessions, corporate actions, identity and PIT still require validation; weekends/holidays may cause conservative stale flags.",
        "symbols": rows}
    _atomic_json(payload, manifest / "coverage.json")
    return {key: value for key, value in payload.items() if key != "symbols"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Publish actual acquisition endpoints, excluding historical-only delistings")
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--security-master", required=True, type=Path)
    arguments = parser.parse_args()
    print(json.dumps(build_sync_coverage(arguments.data_root, arguments.security_master)))
