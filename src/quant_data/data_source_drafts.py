"""Versioned, non-executable data-source connection declarations.

This module stores only declared configuration.  It does not inspect a
credential reference, open a credential file, contact a supplier, or claim
that a declared data product is available.  A reference is a server-side name,
not a path and never a credential value.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .jobs import state_dir


DATA_SOURCE_DRAFT_SCHEMA_VERSION = "p1-07-v1"
_FIELDS = frozenset({"name", "provider", "market", "product", "frequency", "coverage_declaration", "license_declaration", "credential_reference", "verification_status"})
_PROVIDERS: dict[str, set[tuple[str, str]]] = {
    "alpaca": {("US", "US_EQUITY")},
    "nasdaq": {("US", "US_EQUITY")},
    "binance": {("CRYPTO", "CRYPTO_SPOT"), ("CRYPTO", "CRYPTO_PERPETUAL"), ("CRYPTO", "CRYPTO_DELIVERY")},
    "ctp": {("CN_FUTURES", "CN_COMMODITY_FUTURE"), ("CN_FUTURES", "CN_FINANCIAL_FUTURE")},
    "ibkr": {("US", "US_EQUITY"), ("OVERSEAS_FUTURES", "OVERSEAS_FUTURE")},
}
_FREQUENCIES = frozenset({"1m", "5m", "1h", "1d"})
_REFERENCE = re.compile(r"^[A-Z][A-Z0-9_]{2,79}$")


class DataSourceDraftInputError(ValueError):
    pass


class DataSourceDraftStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "data-source-drafts.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS data_source_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, source_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS data_source_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, source_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES data_source_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS data_source_drafts_updated ON data_source_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS data_source_draft_versions_draft ON data_source_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, source = _parse(payload)
        now, encoded = _now(), json.dumps(source, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if draft_id:
                current = db.execute("SELECT version FROM data_source_drafts WHERE id=?", (draft_id,)).fetchone()
                if current is None:
                    raise KeyError(draft_id)
                version = int(current["version"]) + 1
                db.execute("UPDATE data_source_drafts SET name=?,source_json=?,updated_at=?,version=? WHERE id=?", (name, encoded, now, version, draft_id))
            else:
                draft_id, version = str(uuid4()), 1
                db.execute("INSERT INTO data_source_drafts (id,name,source_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?)", (draft_id, name, encoded, now, now, version))
            db.execute("INSERT INTO data_source_draft_versions (id,draft_id,version,name,source_json,created_at) VALUES (?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, encoded, now))
            return _public(db.execute("SELECT * FROM data_source_drafts WHERE id=?", (draft_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM data_source_drafts ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [_public(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM data_source_drafts WHERE id=?", (draft_id,)).fetchone() is None:
                return None
            rows = db.execute("SELECT * FROM data_source_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
        return [_public(row, history=True) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db


def _parse(payload: Mapping[str, Any]) -> tuple[str, dict[str, str]]:
    if not isinstance(payload, Mapping):
        raise DataSourceDraftInputError("request must be an object")
    if set(payload) != _FIELDS:
        raise DataSourceDraftInputError("only declared data-source fields are accepted; credential values are never accepted")
    name = _text(payload.get("name"), "name", 120)
    provider = payload.get("provider")
    market, product, frequency = payload.get("market"), payload.get("product"), payload.get("frequency")
    if provider not in _PROVIDERS:
        raise DataSourceDraftInputError("provider is not in the approved declaration catalogue")
    if (market, product) not in _PROVIDERS[provider]:
        raise DataSourceDraftInputError("provider does not support this declared market/product combination")
    if frequency not in _FREQUENCIES:
        raise DataSourceDraftInputError("frequency is not in the approved declaration catalogue")
    coverage = _text(payload.get("coverage_declaration"), "coverage_declaration", 1000)
    license_ = _text(payload.get("license_declaration"), "license_declaration", 1000)
    reference = payload.get("credential_reference")
    if not isinstance(reference, str) or not _REFERENCE.fullmatch(reference):
        raise DataSourceDraftInputError("credential_reference must be an uppercase server reference name, not a path or credential value")
    if payload.get("verification_status") != "declared":
        raise DataSourceDraftInputError("verification_status is fixed to declared; this endpoint never verifies supplier access or coverage")
    return name, {"provider": provider, "market": market, "product": product, "frequency": frequency, "coverage_declaration": coverage, "license_declaration": license_, "credential_reference": reference, "verification_status": "declared"}


def _text(value: Any, field: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > limit:
        raise DataSourceDraftInputError(f"{field} is required (1-{limit} characters)")
    return value.strip()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _public(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    result = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "source": json.loads(row["source_json"]), "created_at": row["created_at"], "version": row["version"], "schema_version": DATA_SOURCE_DRAFT_SCHEMA_VERSION}
    if not history:
        result["updated_at"] = row["updated_at"]
    return result
