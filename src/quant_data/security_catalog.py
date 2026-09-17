"""Provisional, read-only catalogue derived from a consolidated listing master.

This is deliberately not an instrument master: source listing rows do not carry
an official stable identifier, contract rules, or a trading calendar.  Rows are
therefore kept as provisional lifecycle references and cannot qualify research
or be substituted for InstrumentDraftStore definitions.
"""
from __future__ import annotations

import argparse
from io import BytesIO
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid5, NAMESPACE_URL

import pandas as pd

from .jobs import state_dir
from .sync_eligibility import EXCHANGE_TEST_SYMBOLS

SECURITY_CATALOGUE_SCHEMA_VERSION = "security-catalogue-v1"
_REQUIRED_COLUMNS = frozenset({"symbol", "raw_symbol", "name", "exchange", "asset_type", "ipo_date", "delisting_date", "status", "source", "source_as_of", "sources", "provenance"})
_TEST_FLAG_COLUMNS = ("Test Issue", "test_issue", "is_test")


class SecurityCatalogueInputError(ValueError):
    """Raised when a purported consolidated master cannot safely be imported."""


class SecurityCatalogueStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "security-catalogue.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS catalogue_records (
              catalog_id TEXT PRIMARY KEY, lifecycle_fingerprint TEXT NOT NULL UNIQUE,
              symbol TEXT NOT NULL, raw_symbol TEXT, name TEXT, exchange TEXT,
              asset_type TEXT NOT NULL, status TEXT NOT NULL, ipo_date TEXT,
              delisting_date TEXT, source TEXT, source_as_of TEXT, sources_json TEXT,
              provenance_json TEXT, quarantined INTEGER NOT NULL, quarantine_reason TEXT,
              identity_status TEXT NOT NULL, research_qualified INTEGER NOT NULL,
              imported_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS catalogue_records_symbol ON catalogue_records(symbol);
            CREATE INDEX IF NOT EXISTS catalogue_records_name ON catalogue_records(name);
            CREATE TABLE IF NOT EXISTS catalogue_import_manifests (
              checksum TEXT PRIMARY KEY, source_path TEXT NOT NULL, source_as_of TEXT,
              source_rows INTEGER NOT NULL, imported_records INTEGER NOT NULL,
              imported_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS catalogue_record_snapshots (
              checksum TEXT NOT NULL, catalog_id TEXT NOT NULL, record_json TEXT NOT NULL,
              PRIMARY KEY(checksum, catalog_id),
              FOREIGN KEY(checksum) REFERENCES catalogue_import_manifests(checksum),
              FOREIGN KEY(catalog_id) REFERENCES catalogue_records(catalog_id)
            );
            """)

    def import_master(self, master_path: str | Path) -> dict[str, Any]:
        path = Path(master_path)
        if not path.is_file():
            raise SecurityCatalogueInputError("master parquet does not exist")
        try:
            contents = path.read_bytes()
            frame = pd.read_parquet(BytesIO(contents))
        except Exception as exc:  # pandas exposes engine-specific errors
            raise SecurityCatalogueInputError("master parquet could not be read") from exc
        missing = sorted(_REQUIRED_COLUMNS - set(frame.columns))
        if missing:
            raise SecurityCatalogueInputError("security_master_missing_required_columns: " + ",".join(missing))
        rows = [_record_from_source(row) for row in frame.to_dict(orient="records")]
        rows = [row for row in rows if row is not None]
        checksum = hashlib.sha256(contents).hexdigest()
        source_as_of = _max_as_of(frame["source_as_of"].tolist())
        now = _now()
        with self._lock, self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                # A byte-identical source has already been fully recorded.  Do not
                # churn timestamps or replace lineage on a retry.
                existing = db.execute("SELECT source_as_of,source_rows,imported_records FROM catalogue_import_manifests WHERE checksum=?", (checksum,)).fetchone()
                if existing is not None:
                    db.commit()
                    return {"checksum": checksum, "source_as_of": existing["source_as_of"], "source_rows": existing["source_rows"], "imported_records": existing["imported_records"]}
                for row in rows:
                    db.execute("""
                    INSERT INTO catalogue_records (
                      catalog_id,lifecycle_fingerprint,symbol,raw_symbol,name,exchange,asset_type,status,
                      ipo_date,delisting_date,source,source_as_of,sources_json,provenance_json,
                      quarantined,quarantine_reason,identity_status,research_qualified,imported_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(lifecycle_fingerprint) DO UPDATE SET
                      raw_symbol=excluded.raw_symbol,name=excluded.name,source=excluded.source,
                      source_as_of=excluded.source_as_of,sources_json=excluded.sources_json,
                      provenance_json=excluded.provenance_json,
                      quarantined=MAX(catalogue_records.quarantined, excluded.quarantined),
                      quarantine_reason=COALESCE(catalogue_records.quarantine_reason, excluded.quarantine_reason),
                      imported_at=excluded.imported_at
                    """, tuple(row[field] for field in _DB_FIELDS))
                db.execute("""
                    INSERT INTO catalogue_import_manifests(checksum,source_path,source_as_of,source_rows,imported_records,imported_at)
                    VALUES (?,?,?,?,?,?) ON CONFLICT(checksum) DO UPDATE SET
                    source_path=excluded.source_path,source_as_of=excluded.source_as_of,
                    source_rows=excluded.source_rows,imported_records=excluded.imported_records,imported_at=excluded.imported_at
                    """, (checksum, str(path), source_as_of, len(frame), len(rows), now))
                for row in rows:
                    db.execute("INSERT OR IGNORE INTO catalogue_record_snapshots(checksum,catalog_id,record_json) VALUES (?,?,?)", (checksum, row["catalog_id"], json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))))
                db.commit()
            except Exception:
                db.rollback()
                raise
        return {"checksum": checksum, "source_as_of": source_as_of, "source_rows": len(frame), "imported_records": len(rows)}

    def list(self, query: str = "", limit: int = 50, offset: int = 0, include_quarantined: bool = False) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
            raise SecurityCatalogueInputError("limit must be an integer from 1 to 100")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise SecurityCatalogueInputError("offset must be a non-negative integer")
        term = _text(query).casefold()
        where, params = [], []
        if not include_quarantined:
            where.append("quarantined=0")
        if len(term) > 120:
            raise SecurityCatalogueInputError("query must be at most 120 characters")
        if term:
            where.append("(lower(symbol) LIKE ? ESCAPE '\\' OR lower(coalesce(name,'')) LIKE ? ESCAPE '\\')")
            needle = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            params.extend((needle, needle))
        predicate = " WHERE " + " AND ".join(where) if where else ""
        with self._connect() as db:
            total = int(db.execute("SELECT COUNT(*) FROM catalogue_records" + predicate, params).fetchone()[0])
            total_records = int(db.execute("SELECT COUNT(*) FROM catalogue_records").fetchone()[0])
            quarantined = int(db.execute("SELECT COUNT(*) FROM catalogue_records WHERE quarantined=1").fetchone()[0])
            result = db.execute("SELECT * FROM catalogue_records" + predicate + " ORDER BY CASE WHEN symbol=? THEN 0 ELSE 1 END, symbol, ipo_date, catalog_id LIMIT ? OFFSET ?", [*params, term.upper(), limit, offset]).fetchall()
        return {"items": [_public_record(row) for row in result], "total": total, "total_records": total_records, "quarantined_records": quarantined, "research_qualified": False}

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db


_DB_FIELDS = ("catalog_id", "lifecycle_fingerprint", "symbol", "raw_symbol", "name", "exchange", "asset_type", "status", "ipo_date", "delisting_date", "source", "source_as_of", "sources_json", "provenance_json", "quarantined", "quarantine_reason", "identity_status", "research_qualified", "imported_at")


def _record_from_source(raw: Mapping[str, Any]) -> dict[str, Any] | None:
    symbol, asset_type, status = _text(raw.get("symbol")).upper(), _text(raw.get("asset_type")).casefold(), _text(raw.get("status")).casefold()
    if not symbol or asset_type not in {"stock", "etf"} or status not in {"active", "delisted"}:
        return None
    exchange = _text(raw.get("exchange")).upper() or None
    name, ipo_date, delisting_date = _text(raw.get("name")) or None, _date_text(raw.get("ipo_date")), _date_text(raw.get("delisting_date"))
    fingerprint_value = {"symbol": symbol, "exchange": exchange, "asset_type": asset_type, "ipo_date": ipo_date, "delisting_date": delisting_date, "status": status, "name": name}
    fingerprint = hashlib.sha256(json.dumps(fingerprint_value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    quarantine = symbol in EXCHANGE_TEST_SYMBOLS or any(_is_test(raw.get(column)) for column in _TEST_FLAG_COLUMNS if column in raw)
    return {"catalog_id": str(uuid5(NAMESPACE_URL, "quant-security-catalogue/" + fingerprint)), "lifecycle_fingerprint": fingerprint, "symbol": symbol, "raw_symbol": _text(raw.get("raw_symbol")) or None, "name": name, "exchange": exchange, "asset_type": asset_type, "status": status, "ipo_date": ipo_date, "delisting_date": delisting_date, "source": _text(raw.get("source")) or None, "source_as_of": _date_text(raw.get("source_as_of")), "sources_json": _json(raw.get("sources")), "provenance_json": _json(raw.get("provenance")), "quarantined": int(quarantine), "quarantine_reason": "exchange_test_symbol_or_test_flag" if quarantine else None, "identity_status": "provisional", "research_qualified": 0, "imported_at": _now()}


def _public_record(row: sqlite3.Row) -> dict[str, Any]:
    return {"catalog_id": row["catalog_id"], "symbol": row["symbol"], "raw_symbol": row["raw_symbol"], "name": row["name"], "exchange": row["exchange"], "asset_type": row["asset_type"], "status": row["status"], "ipo_date": row["ipo_date"], "delisting_date": row["delisting_date"], "source": row["source"], "source_as_of": row["source_as_of"], "sources": json.loads(row["sources_json"]), "provenance": json.loads(row["provenance_json"]), "quarantined": bool(row["quarantined"]), "quarantine_reason": row["quarantine_reason"], "identity_status": row["identity_status"], "research_qualified": False, "imported_at": row["imported_at"]}


def _text(value: Any) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()
def _date_text(value: Any) -> str | None:
    text = _text(value)
    return text or None
def _is_test(value: Any) -> bool:
    return _text(value).upper() in {"Y", "TRUE", "1"}
def _json(value: Any) -> str:
    return json.dumps(_json_value(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Mapping):
        return {str(key): normalized for key, item in value.items() if (normalized := _json_value(item)) is not None}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    # pyarrow commonly returns nested parquet fields as ndarray instances.
    if hasattr(value, "tolist"):
        return _json_value(value.tolist())
    return None if pd.isna(value) else value
def _max_as_of(values: list[Any]) -> str | None:
    dates = sorted(filter(None, (_date_text(value) for value in values)))
    return dates[-1] if dates else None
def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Import a consolidated security master into the provisional catalogue")
    parser.add_argument("--master", required=True, type=Path)
    parser.add_argument("--state-root", required=True, type=Path)
    args = parser.parse_args(argv)
    store = SecurityCatalogueStore(args.state_root); store.initialize()
    print(json.dumps(store.import_master(args.master), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
