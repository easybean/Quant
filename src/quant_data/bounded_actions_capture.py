"""Bounded original corporate-action evidence; no qualification or backtest writes."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import requests
from .bounded_backtest_data import SYMBOLS
from .massive_daily import _load_api_key
from .massive_rate import wait_for_slot
from .sync_scheduler import completed_session


def validate(payload: object, symbol: str, kind: str, start: date, end: date) -> list:
    if kind not in {"splits", "dividends"} or start > end: raise ValueError("invalid_actions_scope")
    if not isinstance(payload, dict) or payload.get("status") != "OK" or payload.get("next_url"):
        raise ValueError("invalid_or_paginated_actions_response")
    rows = payload.get("results", [])
    if not isinstance(rows, list): raise ValueError("invalid_actions_rows")
    field = "ex_dividend_date" if kind == "dividends" else "execution_date"
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or row.get("ticker") != symbol or not row.get("id") or row["id"] in seen:
            raise ValueError("invalid_action_identity")
        seen.add(row["id"])
        if not start <= date.fromisoformat(row[field]) <= end: raise ValueError("action_outside_range")
        for name in (("cash_amount",) if kind == "dividends" else ("split_from", "split_to")):
            if isinstance(row[name], bool): raise ValueError("invalid_action_amount")
            try: value = Decimal(str(row[name]))
            except ArithmeticError as exc: raise ValueError("invalid_action_amount") from exc
            if not value.is_finite() or value <= 0: raise ValueError("invalid_action_amount")
        if kind == "dividends" and row.get("currency") != "USD": raise ValueError("unexpected_dividend_currency")
    return rows


def capture(root: Path, credential: Path, output: Path, start: date, end: date) -> dict:
    if output.exists() or start > end or (end - start).days > 31 or end > completed_session(datetime.now(timezone.utc)):
        raise ValueError("new_output_and_completed_bounded_range_required")
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    key = _load_api_key(credential)
    records = []
    with (root / "manifests/massive-network.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        for symbol in SYMBOLS:
            for kind in ("splits", "dividends"):
                wait_for_slot(root)
                field = "ex_dividend_date" if kind == "dividends" else "execution_date"
                endpoint = f"https://api.massive.com/stocks/v1/{kind}"
                params = {"ticker": symbol, f"{field}.gte": start.isoformat(), f"{field}.lte": end.isoformat(), "limit": 1000}
                record = {"symbol": symbol, "kind": kind, "request": {"url": endpoint, "params": params}, "qualified": False}
                try:
                    response = requests.get(endpoint, params=params, headers={"Authorization": f"Bearer {key}"}, timeout=30, allow_redirects=False)
                    record.update(observed_at=datetime.now(timezone.utc).isoformat(), http_status=response.status_code)
                    if len(response.content) > 8 * 1024 * 1024: raise ValueError("actions_response_too_large")
                    name = f"{symbol}-{kind}.json"
                    with (output / name).open("xb") as handle: handle.write(response.content)
                    record.update(raw_file=name, sha256=hashlib.sha256(response.content).hexdigest())
                    if response.status_code != 200: raise ValueError(f"http_{response.status_code}")
                    rows = validate(response.json(), symbol, kind, start, end)
                    record.update(status="captured", returned=len(rows), events=rows)
                except (requests.RequestException, ValueError, KeyError, TypeError, ArithmeticError):
                    record["status"] = "failed_or_unverified"
                records.append(record)
                if record.get("http_status") in (401, 403, 429): break
            if records[-1].get("http_status") in (401, 403, 429): break
    report = {"schema_version": "bounded-actions-evidence-v1", "range": {"start": start.isoformat(), "end": end.isoformat()},
              "qualified": False, "completed_queries": len(records), "planned_queries": 10, "records": records,
              "warning": "Empty vendor response is not independent proof of no actions; no historical availability backdating, merger or delisting completeness inferred."}
    encoded = json.dumps(report, indent=2, allow_nan=False).encode()
    with (output / "report.json").open("xb") as handle: handle.write(encoded)
    with (output / "manifest.json").open("x") as handle:
        json.dump({"schema_version": report["schema_version"], "report_sha256": hashlib.sha256(encoded).hexdigest(), "qualified": False}, handle)
    return {"completed_queries": len(records), "captured": sum(r["status"] == "captured" for r in records),
            "events": sum(r.get("returned", 0) for r in records), "qualified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--start", type=date.fromisoformat, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    args = parser.parse_args()
    result = capture(args.data_root, args.credential_file, args.output, args.start, args.end)
    print(json.dumps(result))
    raise SystemExit(0 if result["captured"] == 10 else 2)
