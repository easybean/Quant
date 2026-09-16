"""Auditable import of point-in-time S&P 500 and Nasdaq-100 membership snapshots.

This module deliberately treats an external CSV as a *research source*, not as
an official index-license substitute.  It retains an immutable copy of the CSV
and converts each effective-date snapshot into compact half-open intervals.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import requests


SOURCE_REPOSITORY = "thuningxu/sp500nq100"
SOURCE_FILES = {
    "SP500": "sp500_components_history.csv",
    "NASDAQ100": "nasdaq100_components_history.csv",
}
SOURCE_URL = "https://github.com/thuningxu/sp500nq100"
QUALITY_TIER = "community_reconstructed_point_in_time"
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-^]{0,31}$")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        handle.write(contents)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_bytes(path, (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode())


def _parse_snapshots(path: Path, index_id: str) -> list[tuple[pd.Timestamp, tuple[str, ...]]]:
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    columns = {str(column).strip().lower(): column for column in frame.columns}
    if "date" not in columns or "tickers" not in columns:
        raise ValueError(f"{path} must contain CSV columns date,tickers")
    snapshots: list[tuple[pd.Timestamp, tuple[str, ...]]] = []
    previous: pd.Timestamp | None = None
    for number, row in frame.iterrows():
        effective = pd.to_datetime(row[columns["date"]], errors="raise").normalize()
        if previous is not None and effective <= previous:
            raise ValueError(f"{index_id}: dates must be strictly increasing (CSV row {number + 2})")
        symbols = tuple(sorted({item.strip().upper() for item in str(row[columns["tickers"]]).split(",") if item.strip()}))
        if not symbols:
            raise ValueError(f"{index_id}: empty membership snapshot on {effective.date()}")
        invalid = [symbol for symbol in symbols if not _SYMBOL.fullmatch(symbol)]
        if invalid:
            raise ValueError(f"{index_id}: invalid symbols on {effective.date()}: {invalid[:5]}")
        snapshots.append((effective, symbols))
        previous = effective
    if not snapshots:
        raise ValueError(f"{index_id}: no snapshots in {path}")
    return snapshots


def snapshots_to_intervals(
    snapshot_csv: Path, *, index_id: str, source_revision: str, source_file_sha256: str,
) -> pd.DataFrame:
    """Convert full effective-date snapshots to compact [start, end) membership intervals."""
    snapshots = _parse_snapshots(snapshot_csv, index_id)
    open_intervals: dict[str, pd.Timestamp] = {}
    records: list[dict[str, Any]] = []
    for effective, symbols_tuple in snapshots:
        members = set(symbols_tuple)
        for symbol in sorted(set(open_intervals).difference(members)):
            records.append({"index_id": index_id, "symbol": symbol, "effective_date": open_intervals.pop(symbol), "end_date_exclusive": effective})
        for symbol in sorted(members.difference(open_intervals)):
            open_intervals[symbol] = effective
    for symbol in sorted(open_intervals):
        records.append({"index_id": index_id, "symbol": symbol, "effective_date": open_intervals[symbol], "end_date_exclusive": pd.NaT})
    result = pd.DataFrame(records)
    result["source_repository"] = SOURCE_REPOSITORY
    result["source_url"] = SOURCE_URL
    result["source_revision"] = source_revision
    result["source_file_sha256"] = source_file_sha256
    result["quality_tier"] = QUALITY_TIER
    result["membership_semantics"] = "effective_date inclusive; end_date_exclusive exclusive; null end means current in source snapshot"
    return result.sort_values(["index_id", "symbol", "effective_date"], kind="stable", ignore_index=True)


def import_constituent_snapshots(
    *, data_root: Path, sp500_csv: Path, nasdaq100_csv: Path, source_revision: str,
    source_repository: str = SOURCE_REPOSITORY,
) -> dict[str, Any]:
    """Create a new immutable reference version from two downloaded/local CSVs.

    The version root must not already exist. This avoids silently replacing the
    exact input a prior backtest used.
    """
    if source_repository != SOURCE_REPOSITORY:
        raise ValueError(f"only the verified source repository is supported: {SOURCE_REPOSITORY}")
    revision = source_revision.strip()
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("source_revision must be a resolved 40-character Git commit SHA")
    output_root = data_root / "reference" / "constituents-v1"
    if output_root.exists():
        raise FileExistsError(f"refusing to overwrite immutable constituent reference: {output_root}")
    inputs = {"SP500": sp500_csv, "NASDAQ100": nasdaq100_csv}
    for input_path in inputs.values():
        if not input_path.is_file():
            raise FileNotFoundError(input_path)
    copied: dict[str, Path] = {}
    source_files: list[dict[str, Any]] = []
    try:
        for index_id, input_path in inputs.items():
            destination = output_root / "raw" / SOURCE_FILES[index_id]
            _atomic_bytes(destination, input_path.read_bytes())
            copied[index_id] = destination
            source_files.append({
                "index_id": index_id, "path": str(destination.relative_to(output_root)),
                "sha256": sha256_file(destination), "bytes": destination.stat().st_size,
            })
        intervals = pd.concat([
            snapshots_to_intervals(copied[index_id], index_id=index_id, source_revision=revision,
                                   source_file_sha256=sha256_file(copied[index_id]))
            for index_id in ("SP500", "NASDAQ100")
        ], ignore_index=True)
        intervals_path = output_root / "membership_intervals.parquet"
        temporary = intervals_path.with_suffix(".tmp.parquet")
        intervals.to_parquet(temporary, index=False)
        os.replace(temporary, intervals_path)
        metadata = {
            "schema_version": 1,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_repository": source_repository,
            "source_url": SOURCE_URL,
            "source_revision": revision,
            "quality_tier": QUALITY_TIER,
            "limitations": [
                "Community reconstruction; not an official S&P Dow Jones Indices or Nasdaq licensed constituent feed.",
                "Use only as a research universe after reviewing the upstream repository limitations.",
                "The final interval has null end_date_exclusive because the source only states membership through its last snapshot.",
            ],
            "files": source_files + [{
                "path": "membership_intervals.parquet", "sha256": sha256_file(intervals_path),
                "bytes": intervals_path.stat().st_size, "rows": int(len(intervals)),
            }],
        }
        _atomic_json(output_root / "source-manifest.json", metadata)
    except Exception:
        # Never clean up a partially created reference root automatically: it is
        # evidence of a failed import and must be inspected/replaced manually.
        raise
    return {
        "output": str(output_root), "source_revision": revision,
        "interval_rows": int(len(intervals)),
        "sp500_interval_rows": int((intervals["index_id"] == "SP500").sum()),
        "nasdaq100_interval_rows": int((intervals["index_id"] == "NASDAQ100").sum()),
    }


def fetch_and_import_constituents(
    *, data_root: Path, revision: str = "main", request_get: Callable[..., Any] = requests.get,
) -> dict[str, Any]:
    """Fetch a GitHub commit-pinned pair of CSVs, then import them as a version."""
    response = request_get(
        f"https://api.github.com/repos/{SOURCE_REPOSITORY}/commits/{revision}",
        headers={"Accept": "application/vnd.github+json", "User-Agent": "quant-data-pipeline"}, timeout=30,
    )
    response.raise_for_status()
    commit = str(response.json().get("sha", ""))
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("GitHub did not return a resolved 40-character commit SHA")
    with tempfile.TemporaryDirectory(prefix="quant-constituents-") as directory:
        temp = Path(directory)
        downloaded: dict[str, Path] = {}
        for index_id, filename in SOURCE_FILES.items():
            raw = request_get(
                f"https://raw.githubusercontent.com/{SOURCE_REPOSITORY}/{commit}/{filename}",
                headers={"User-Agent": "quant-data-pipeline"}, timeout=60,
            )
            raw.raise_for_status()
            destination = temp / filename
            destination.write_bytes(raw.content)
            downloaded[index_id] = destination
        result = import_constituent_snapshots(
            data_root=data_root, sp500_csv=downloaded["SP500"], nasdaq100_csv=downloaded["NASDAQ100"],
            source_revision=commit,
        )
    result["requested_revision"] = revision
    return result
