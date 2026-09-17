"""Archive official current listing files and classify acquisition gaps.

Current membership is observation evidence, not historical permanent identity
or proof that a missing symbol is delisted. Original masters remain untouched.
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import uuid

import pandas as pd
import requests

from .daily_sync import _atomic_json
from .listings import normalize_nasdaq_trader_files

DIRECTORY_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/"
FILES = ("nasdaqlisted.txt", "otherlisted.txt")


def validate_directory(contents: bytes, name: str) -> str:
    if name not in FILES or not contents or len(contents) > 5_000_000:
        raise ValueError("invalid_directory_size_or_name")
    text = contents.decode("utf-8-sig")
    lines = text.splitlines()
    footer = [line for line in lines if line.startswith("File Creation Time:")]
    if len(footer) != 1 or lines[-1] != footer[0]:
        raise ValueError("directory_creation_footer_missing")
    raw = pd.read_csv(io.StringIO(text), sep="|", dtype=str, keep_default_na=False)
    symbol = "Symbol" if name == FILES[0] else "ACT Symbol"
    if not {symbol, "Security Name", "Test Issue", "ETF"}.issubset(raw.columns):
        raise ValueError("directory_required_columns_missing")
    rows = raw[~raw[symbol].str.startswith("File Creation Time:")]
    if rows.empty or not rows["Test Issue"].isin(["N", "Y"]).all() or not rows["ETF"].isin(["N", "Y"]).all():
        raise ValueError("directory_flags_invalid")
    if rows[symbol].eq("").any() or rows[symbol].duplicated().any():
        raise ValueError("directory_symbol_invalid")
    return footer[0].split("|")[0]


def classify_tasks(tasks: list[dict], current_symbols: set[str]) -> dict:
    items = []
    for task in tasks:
        symbol = task["symbol"]
        items.append({"symbol": symbol, "latest_date": task.get("latest_date"),
                      "reason": task.get("reason"),
                      "listing_evidence": "present_in_current_directory" if symbol in current_symbols else "absent_from_current_directory_unknown",
                      "research_qualified": False})
    return {"counts": dict(Counter(row["listing_evidence"] for row in items)), "items": items}


def publish_acquisition_master(root: Path, snapshot: Path, current: pd.DataFrame, observed_at: str) -> dict:
    """Append absent current symbols; never reinterpret an existing lifecycle."""
    base = root / "metadata/security_master.consolidated.parquet"
    if not base.exists():
        base = root / "metadata/security_master.parquet"
    original = pd.read_parquet(base)
    base_rows = len(original)
    inherited_lineage = None
    previous_pointer = root / "catalogue/current-acquisition-master-v1.json"
    if previous_pointer.exists():
        previous = json.loads(previous_pointer.read_text())
        relative = Path(previous.get("relative_path", ""))
        candidate = root / relative
        if (previous.get("schema_version") != "current-acquisition-master-v1" or relative.is_absolute()
                or ".." in relative.parts or not relative.parts or candidate.is_symlink()
                or root.resolve() not in candidate.resolve().parents
                or hashlib.sha256(candidate.read_bytes()).hexdigest() != previous.get("sha256")):
            raise ValueError("previous_acquisition_master_invalid")
        inherited = pd.read_parquet(candidate)
        inherited_lineage = {"relative_path": previous["relative_path"], "sha256": previous["sha256"]}
        # Retain previously discovered symbols even if absent today, as unknown
        # lifecycle evidence rather than interpreting absence as delisting.
        original = pd.concat([original, inherited[~inherited.symbol.isin(original.symbol)]], ignore_index=True, sort=False)
    if "symbol" not in original or current.symbol.duplicated().any():
        raise ValueError("acquisition_master_symbols_invalid")
    added = current[~current.symbol.isin(original.symbol)].copy()
    result = pd.concat([original, added], ignore_index=True, sort=False)
    output = snapshot / "acquisition-master.parquet"
    result.to_parquet(output, index=False)
    pointer = {"schema_version": "current-acquisition-master-v1",
               "relative_path": output.relative_to(root).as_posix(),
               "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
               "base_relative_path": base.relative_to(root).as_posix(),
               "base_sha256": hashlib.sha256(base.read_bytes()).hexdigest(),
               "observed_at": observed_at, "base_rows": base_rows,
               "inherited_acquisition_rows": len(original) - base_rows,
               "inherited_input": inherited_lineage,
               "rows": len(result), "added_symbols": sorted(added.symbol.tolist()),
               "research_qualified": False,
               "warning": "Acquisition scope only; original rows preserved. Current symbols do not establish historical identity or availability."}
    _atomic_json(snapshot / "acquisition-master-manifest.json", pointer)
    _atomic_json(root / "catalogue/current-acquisition-master-v1.json", pointer)
    return pointer


def capture_and_reconcile(root: Path, *, request_get=requests.get) -> dict:
    contents = {}; footers = {}
    for name in FILES:
        response = request_get(DIRECTORY_URL + name, timeout=30)
        response.raise_for_status()
        contents[name] = response.content
        footers[name] = validate_directory(contents[name], name)
    stamp = datetime.now(timezone.utc)
    try:
        dates = {datetime.strptime(value.removeprefix("File Creation Time: "), "%m%d%Y%H:%M").date() for value in footers.values()}
    except ValueError as exc:
        raise ValueError("directory_source_date_invalid") from exc
    if len(dates) != 1 or not 0 <= (stamp.date() - next(iter(dates))).days <= 7:
        raise ValueError("directory_source_date_stale_or_inconsistent")
    snapshot = root / "reference/current-listing-directory" / (stamp.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    snapshot.mkdir(parents=True, exist_ok=False)
    hashes = {}
    for name, raw in contents.items():
        with (snapshot / name).open("xb") as handle:
            handle.write(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    current = normalize_nasdaq_trader_files(snapshot / FILES[0], snapshot / FILES[1], next(iter(dates)).isoformat())
    # Observation time is not a source publication date or a backtest as-of.
    current.to_parquet(snapshot / "observed-current-listings.parquet", index=False)
    queue = json.loads((root / "manifests/us-daily-sync/queue.json").read_text())
    if queue.get("schema_version") != "us-daily-gap-queue-v1" or not isinstance(queue.get("tasks"), list):
        raise ValueError("gap_queue_invalid")
    report = {"schema_version": "current-listing-gap-evidence-v1", "observed_at": stamp.isoformat(),
              "snapshot_relative_path": snapshot.relative_to(root).as_posix(),
              "source": DIRECTORY_URL, "source_footers": footers, "source_hashes": hashes,
              "current_directory_symbols": len(current), "target_date": queue.get("target_date"),
              "queue_generated_at": queue.get("generated_at"),
              **classify_tasks(queue["tasks"], set(current.symbol)), "research_qualified": False,
              "warning": "Current directory evidence only. Absence does not establish delisting, cessation of trading, historical identity, or permission to remove a gap. No symbol aliases inferred."}
    _atomic_json(snapshot / "gap-evidence.json", report)
    master = publish_acquisition_master(root, snapshot, current, stamp.isoformat())
    _atomic_json(root / "catalogue/current-listing-gap-evidence-v1.json", report)
    return {**{key: value for key, value in report.items() if key != "items"},
            "acquisition_master_added": len(master["added_symbols"])}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture_and_reconcile(args.data_root), ensure_ascii=False))


if __name__ == "__main__":
    main()
