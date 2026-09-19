"""Freeze one existing TSLA raw-price source for an offline exploratory backtest.

No network or credentials. The original parquet is read-only; this is not a
qualified point-in-time dataset or a formal research snapshot.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd

SOURCE = Path("bars/daily/symbol=TSLA-2ff6985a/bars.parquet")
START, END = date(2026, 1, 2), date(2026, 8, 31)
SCHEMA = "exploratory-tsla-daily-snapshot-v1"


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def freeze(root: Path, output: Path) -> dict:
    root = root.resolve()
    source = root / SOURCE
    if not source.is_file() or source.is_symlink() or output.exists():
        raise ValueError("source_missing_or_output_exists")
    before = sha(source)
    frame = pd.read_parquet(source)
    needed = {"date", "symbol", "open", "high", "low", "close", "volume", "dividends", "stock_splits", "source", "adjustment_status"}
    if not needed.issubset(frame.columns):
        raise ValueError("source_schema_invalid")
    selection = frame.loc[(frame.date >= pd.Timestamp(START)) & (frame.date <= pd.Timestamp(END))].sort_values("date")
    expected = [x.date() for x in calendars.get_calendar("XNYS").sessions_in_range(START.isoformat(), END.isoformat())]
    actual = [x.date() for x in selection.date]
    if actual != expected or len(set(actual)) != len(actual):
        raise ValueError("missing_or_duplicate_calendar_session")
    if set(selection.symbol) != {"TSLA"} or set(selection.source) != {"nasdaq_web_unadjusted"} or set(selection.adjustment_status) != {"unadjusted"}:
        raise ValueError("source_identity_or_basis_invalid")
    if selection[["dividends", "stock_splits"]].isna().any().any() or (selection[["dividends", "stock_splits"]] != 0).any().any():
        raise ValueError("corporate_action_requires_accounting")
    rows = []
    for item in selection.itertuples(index=False):
        values = {key: Decimal(str(getattr(item, key))) for key in ("open", "high", "low", "close", "volume")}
        if any(not value.is_finite() or value <= 0 for value in values.values()):
            raise ValueError("invalid_ohlcv")
        if values["low"] > min(values["open"], values["close"]) or values["high"] < max(values["open"], values["close"]):
            raise ValueError("invalid_ohlc_range")
        rows.append({"symbol": "TSLA", "session_date": item.date.date().isoformat(), **{k: str(v) for k, v in values.items()},
                     "source": "nasdaq_web_unadjusted", "adjustment_status": "unadjusted", "event_time": None,
                     "historical_available_at": None})
    if sha(source) != before:
        raise ValueError("source_changed_during_freeze")
    report = {"schema_version": SCHEMA, "symbol": "TSLA", "range": {"start": START.isoformat(), "end": END.isoformat()},
              "rows": len(rows), "expected_rows": len(expected), "calendar_version": f"XNYS-exchange_calendars-{calendars.__version__}",
              "source_relative_path": SOURCE.as_posix(), "source_sha256": before,
              "captured_at": datetime.now(timezone.utc).isoformat(), "qualified": False, "formal_backtest_enabled": False,
              "limitations": ["single existing source; publisher capture/vintage not independently verified",
                              "event_time and historical_available_at unknown; next-session timing is an explicit exploratory assumption",
                              "no historical security-identity, delisting, or corporate-action completeness proof",
                              "action columns contain zero in selected window but zero alone is not independent no-event evidence"]}
    output.mkdir(parents=True, exist_ok=False)
    write(output / "bars.json", rows)
    write(output / "report.json", report)
    manifest = {"schema_version": SCHEMA, "files": {name: sha(output / name) for name in ("bars.json", "report.json")},
                "source_relative_path": SOURCE.as_posix(), "source_sha256": before}
    write(output / "manifest.json", manifest)
    return {"snapshot": str(output), "manifest_sha256": sha(output / "manifest.json"), "rows": len(rows), "qualified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(freeze(args.data_root, args.output)))
