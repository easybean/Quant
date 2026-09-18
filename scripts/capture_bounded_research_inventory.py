"""Freeze a narrow, truthful inventory; never qualify or mutate source bars."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from quant_data.pipeline import symbol_key

SYMBOLS = ("AAPL", "MSFT", "AMZN", "GOOGL", "TSLA")


def capture(root: Path, output: Path, asset_reference: Path | None = None, end: date = date(2026, 9, 17)) -> dict:
    if output.exists():
        raise ValueError("output must be a new immutable directory")
    sources = []
    for symbol in SYMBOLS:
        key = symbol_key(symbol)
        daily = root / "bars" / "daily"
        paths = list(daily.glob(f"provider=*/namespace=*/symbol={key}/bars.parquet"))
        legacy = daily / f"symbol={key}" / "bars.parquet"
        if legacy.exists():
            paths.append(legacy)
        for path in sorted(paths):
            if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError("source path escapes data root")
            before = hashlib.sha256(path.read_bytes()).hexdigest()
            frame = pd.read_parquet(path)
            dates = pd.to_datetime(frame["date"], errors="raise", utc=True)
            selected = frame.loc[(dates >= "2026-09-01") & (dates < (end + timedelta(days=1)).isoformat())].copy()
            after = hashlib.sha256(path.read_bytes()).hexdigest()
            if before != after:
                raise ValueError("source changed during capture; retry after writer completes")
            fields = [name for name in ("date", "open", "high", "low", "close", "volume", "available_at", "retrieved_at", "availability_policy", "actions_status") if name in selected]
            sources.append({"symbol": symbol, "relative_path": str(path.relative_to(root)), "sha256": before,
                            "rows_in_window": len(selected), "fields_present": fields,
                            "rows": json.loads(selected[fields].to_json(orient="records", date_format="iso")),
                            "event_time_evidence": "not_verified", "historical_identity": "not_verified"})
    if not sources or any(not any(s["symbol"] == symbol and s["rows_in_window"] for s in sources) for symbol in SYMBOLS):
        raise ValueError("every selected member must have diagnostic rows; missing coverage")
    members = []
    identity_source = None
    if asset_reference is not None:
        if asset_reference.is_symlink() or not asset_reference.resolve().is_relative_to(root.resolve()):
            raise ValueError("asset reference escapes data root")
        raw = asset_reference.read_bytes()
        manifest = json.loads(asset_reference.with_name("manifest.json").read_text())
        digest = hashlib.sha256(raw).hexdigest()
        if manifest.get("sha256") != digest or manifest.get("schema_version") != "provider-asset-reference-v1":
            raise ValueError("asset reference integrity mismatch")
        rows = json.loads(raw)
        for symbol in SYMBOLS:
            hits = [r for r in rows if r.get("symbol") == symbol and r.get("status") == "active"]
            if len(hits) != 1 or not hits[0].get("id"):
                raise ValueError("ambiguous or missing current identity")
            row = hits[0]
            members.append({"symbol": symbol, "provider_asset_id": row["id"], "name": row.get("name"),
                            "exchange": row.get("exchange"), "historical_identity_verified": False})
        identity_source = {"relative_path": str(asset_reference.relative_to(root)), "sha256": digest,
                           "observed_at": manifest.get("observed_at"), "source": manifest.get("source")}
    result = {"schema_version": "bounded-research-inventory-v1", "captured_at": datetime.now(timezone.utc).isoformat(),
              "symbols": list(SYMBOLS), "range": {"start": "2026-09-01", "end": end.isoformat()},
              "qualified": False, "sources": sources, "members": members, "identity_source": identity_source,
              "blockers": ["identity lifecycle, event time, calendar, corporate actions, delistings and license require independent evidence"],
              "warning": "Frozen inventory rows are diagnostic only; ingestion availability cannot establish historical PIT."}
    output.mkdir(parents=True, exist_ok=False)
    encoded = (json.dumps(result, indent=2, allow_nan=False) + "\n").encode()
    with (output / "inventory.json").open("xb") as handle:
        handle.write(encoded)
    with (output / "manifest.json").open("x") as handle:
        json.dump({"schema_version": result["schema_version"], "sha256": hashlib.sha256(encoded).hexdigest(),
                   "qualified": False, "captured_at": result["captured_at"]}, handle, indent=2)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--asset-reference", type=Path)
    parser.add_argument("--daily", action="store_true", help="Create a dated immutable inventory through the completed session")
    args = parser.parse_args()
    end = date(2026, 9, 17)
    output = args.output
    if args.daily:
        from quant_data.sync_scheduler import completed_session
        now = datetime.now(timezone.utc)
        end = completed_session(now)
        output = output / now.strftime("%Y%m%dT%H%M%S.%fZ")
    result = capture(args.data_root, output, args.asset_reference, end)
    print(json.dumps({"qualified": False, "sources": len(result["sources"]), "rows": sum(s["rows_in_window"] for s in result["sources"])}))
