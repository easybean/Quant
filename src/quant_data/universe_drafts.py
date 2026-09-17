"""Versioned, non-executable instrument definitions and research asset pools.

Definitions contain one instrument and its contract rules. Asset pools contain
only immutable references to saved definition versions; neither type reads
market data, declares coverage, or starts execution.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .instruments import InstrumentDefinition, InstrumentRule, SymbolAssignment, validate_instrument_set
from .jobs import state_dir
from .security_catalog import SecurityCatalogueInputError, SecurityCatalogueStore

INSTRUMENT_DRAFT_SCHEMA_VERSION = "p2-06a-instrument-v1"
ASSET_POOL_DRAFT_SCHEMA_VERSION = "p2-06b-asset-pool-v1"


class UniverseDraftInputError(ValueError):
    """Common input error for reference records."""


class InstrumentDraftStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "instrument-drafts.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS instrument_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, instrument_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS instrument_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, instrument_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES instrument_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS instrument_drafts_updated ON instrument_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS instrument_draft_versions_draft ON instrument_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, instrument = _parse_instrument_draft(payload)
        now, encoded = _now(), json.dumps(instrument, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if draft_id:
                current = db.execute("SELECT version FROM instrument_drafts WHERE id=?", (draft_id,)).fetchone()
                if current is None: raise KeyError(draft_id)
                version = int(current["version"]) + 1
                db.execute("UPDATE instrument_drafts SET name=?,instrument_json=?,updated_at=?,version=? WHERE id=?", (name, encoded, now, version, draft_id))
            else:
                draft_id, version = str(uuid4()), 1
                db.execute("INSERT INTO instrument_drafts (id,name,instrument_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?)", (draft_id, name, encoded, now, now, version))
            db.execute("INSERT INTO instrument_draft_versions (id,draft_id,version,name,instrument_json,created_at) VALUES (?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, encoded, now))
            return _public_instrument(db.execute("SELECT * FROM instrument_drafts WHERE id=?", (draft_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM instrument_drafts ORDER BY updated_at DESC LIMIT ?", (_limit(limit),)).fetchall()
        return [_public_instrument(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM instrument_drafts WHERE id=?", (draft_id,)).fetchone() is None: return None
            rows = db.execute("SELECT * FROM instrument_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
        return [_public_instrument(row, history=True) for row in rows]

    def version_exists(self, draft_id: str, version: int) -> bool:
        with self._connect() as db:
            return db.execute("SELECT 1 FROM instrument_draft_versions WHERE draft_id=? AND version=?", (draft_id, version)).fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row; db.execute("PRAGMA journal_mode=WAL")
        return db


class AssetPoolDraftStore:
    def __init__(self, root: str | Path | None = None, instruments: InstrumentDraftStore | None = None, security_catalogue: SecurityCatalogueStore | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "asset-pool-drafts.sqlite3"
        self.instruments = instruments or InstrumentDraftStore(self.root)
        self.security_catalogue = security_catalogue or SecurityCatalogueStore(self.root)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True); self.instruments.initialize(); self.security_catalogue.initialize()
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS asset_pool_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, purpose TEXT NOT NULL, instruments_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS asset_pool_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, purpose TEXT NOT NULL, instruments_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES asset_pool_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS asset_pool_drafts_updated ON asset_pool_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS asset_pool_draft_versions_draft ON asset_pool_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, purpose, instruments = _parse_asset_pool(payload, self.instruments, self.security_catalogue)
        now, encoded = _now(), json.dumps(instruments, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if draft_id:
                current = db.execute("SELECT version FROM asset_pool_drafts WHERE id=?", (draft_id,)).fetchone()
                if current is None: raise KeyError(draft_id)
                version = int(current["version"]) + 1
                db.execute("UPDATE asset_pool_drafts SET name=?,purpose=?,instruments_json=?,updated_at=?,version=? WHERE id=?", (name, purpose, encoded, now, version, draft_id))
            else:
                draft_id, version = str(uuid4()), 1
                db.execute("INSERT INTO asset_pool_drafts (id,name,purpose,instruments_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?)", (draft_id, name, purpose, encoded, now, now, version))
            db.execute("INSERT INTO asset_pool_draft_versions (id,draft_id,version,name,purpose,instruments_json,created_at) VALUES (?,?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, purpose, encoded, now))
            return _public_pool(db.execute("SELECT * FROM asset_pool_drafts WHERE id=?", (draft_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM asset_pool_drafts ORDER BY updated_at DESC LIMIT ?", (_limit(limit),)).fetchall()
        return [_public_pool(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM asset_pool_drafts WHERE id=?", (draft_id,)).fetchone() is None: return None
            rows = db.execute("SELECT * FROM asset_pool_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
        return [_public_pool(row, history=True) for row in rows]

    def version_exists(self, draft_id: str, version: int) -> bool:
        """Whether a referenced, immutable asset-pool version exists."""
        with self._connect() as db:
            return db.execute(
                "SELECT 1 FROM asset_pool_draft_versions WHERE draft_id=? AND version=?",
                (draft_id, version),
            ).fetchone() is not None

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row; db.execute("PRAGMA journal_mode=WAL")
        return db


_FIELDS = frozenset({"instrument_id", "identity_key", "asset", "product", "venue", "venue_symbol", "base_currency", "quote_currency", "settlement_currency", "expiry", "timezone", "calendar_id", "listing_date", "delisting_date", "last_trade", "first_notice", "linear", "inverse", "symbol_history", "rules"})
_RULE_FIELDS = frozenset({"valid_from", "valid_to", "rule_version", "multiplier", "quantity_unit", "tick_size", "lot_size", "min_notional"})
_SYMBOL_FIELDS = frozenset({"venue_symbol", "valid_from", "valid_to"})

def _parse_instrument_draft(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping) or set(payload) != {"name", "instrument"}: raise UniverseDraftInputError("only name and instrument are accepted")
    return _name(payload.get("name")), _instrument(payload.get("instrument"))[0]

def _parse_asset_pool(payload: Mapping[str, Any], store: InstrumentDraftStore, catalogue: SecurityCatalogueStore) -> tuple[str, str, list[dict[str, Any]]]:
    if not isinstance(payload, Mapping) or set(payload) != {"name", "purpose", "instruments"}: raise UniverseDraftInputError("only name, purpose and instruments are accepted")
    name, purpose, raw = _name(payload.get("name")), _purpose(payload.get("purpose")), payload.get("instruments")
    if not isinstance(raw, list) or not raw or len(raw) > 200: raise UniverseDraftInputError("instruments must contain 1-200 saved instrument or catalogue references")
    refs: list[dict[str, Any]] = []; seen: set[tuple[str, str]] = set()
    for index, ref in enumerate(raw):
        if not isinstance(ref, Mapping): raise UniverseDraftInputError(f"instruments[{index}] must be a saved instrument or catalogue reference")
        if set(ref) == {"instrument_draft_id", "instrument_version"}:
            draft_id, version = ref.get("instrument_draft_id"), ref.get("instrument_version")
            if not isinstance(draft_id, str) or not draft_id.strip() or isinstance(version, bool) or not isinstance(version, int) or version < 1: raise UniverseDraftInputError(f"instruments[{index}] has an invalid saved instrument reference")
            draft_id = draft_id.strip(); key = ("instrument_draft", draft_id)
            if key in seen: raise UniverseDraftInputError("duplicate instrument or catalogue references are not allowed")
            if not store.version_exists(draft_id, version): raise UniverseDraftInputError(f"instruments[{index}] references an unknown instrument draft or version")
            seen.add(key); refs.append({"instrument_draft_id": draft_id, "instrument_version": version})
            continue
        if set(ref) != {"member_type", "catalog_id", "source_checksum"} or ref.get("member_type") != "security_catalogue_v1":
            raise UniverseDraftInputError(f"instruments[{index}] must contain a saved instrument reference or security_catalogue_v1 manifest reference")
        catalog_id, checksum = ref.get("catalog_id"), ref.get("source_checksum")
        if not isinstance(catalog_id, str) or not catalog_id.strip(): raise UniverseDraftInputError(f"instruments[{index}] has an invalid catalogue record id")
        key = ("security_catalogue", catalog_id.strip())
        if key in seen: raise UniverseDraftInputError("duplicate instrument or catalogue references are not allowed")
        try:
            resolved = catalogue.snapshot_reference(catalog_id.strip(), checksum)
        except SecurityCatalogueInputError as exc:
            raise UniverseDraftInputError(f"instruments[{index}] {exc}") from exc
        seen.add(key); refs.append(resolved)
    return name, purpose, refs

def _instrument(raw: Any) -> tuple[dict[str, Any], InstrumentDefinition]:
    if not isinstance(raw, Mapping) or set(raw) - _FIELDS: raise UniverseDraftInputError("instrument contains unsupported fields")
    value = dict(raw); symbols, rules = value.get("symbol_history"), value.get("rules")
    if not isinstance(symbols, list) or not isinstance(rules, list): raise UniverseDraftInputError("symbol_history and rules must be arrays")
    parsed_symbols = tuple(SymbolAssignment(venue_symbol=_string(item, "venue_symbol", _SYMBOL_FIELDS), valid_from=_date(item.get("valid_from")), valid_to=_date(item.get("valid_to"))) for item in symbols if _mapping_fields(item, _SYMBOL_FIELDS))
    parsed_rules = tuple(InstrumentRule(valid_from=_date(item.get("valid_from")), valid_to=_date(item.get("valid_to")), rule_version=_optional_string(item.get("rule_version")), multiplier=_decimal(item.get("multiplier")), quantity_unit=_optional_string(item.get("quantity_unit")), tick_size=_decimal(item.get("tick_size")), lot_size=_decimal(item.get("lot_size")), min_notional=_decimal(item.get("min_notional"))) for item in rules if _mapping_fields(item, _RULE_FIELDS))
    try:
        definition = InstrumentDefinition(instrument_id=_optional_string(value.get("instrument_id")) or "", identity_key=_optional_string(value.get("identity_key")), asset=value.get("asset", ""), product=value.get("product", ""), venue=_optional_string(value.get("venue")) or "", venue_symbol=_optional_string(value.get("venue_symbol")) or "", base_currency=_optional_string(value.get("base_currency")), quote_currency=_optional_string(value.get("quote_currency")), settlement_currency=_optional_string(value.get("settlement_currency")), expiry=_date(value.get("expiry")), timezone=_optional_string(value.get("timezone")), calendar_id=_optional_string(value.get("calendar_id")), listing_date=_date(value.get("listing_date")), delisting_date=_date(value.get("delisting_date")), last_trade=_date(value.get("last_trade")), first_notice=_date(value.get("first_notice")), linear=_bool(value.get("linear", False), "linear"), inverse=_bool(value.get("inverse", False), "inverse"), symbol_history=parsed_symbols, rules=parsed_rules)
    except (TypeError, ValueError, InvalidOperation) as exc: raise UniverseDraftInputError(str(exc)) from exc
    issues = [f"{result.instrument_id} {issue.field}: {issue.message}" for result in validate_instrument_set([definition]) for issue in result.issues]
    if issues: raise UniverseDraftInputError("; ".join(issues))
    return _jsonable(value), definition

def _name(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 120: raise UniverseDraftInputError("name is required (1-120 characters)")
    return value.strip()
def _purpose(value: Any) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 1000: raise UniverseDraftInputError("purpose is required (1-1000 characters)")
    return value.strip()
def _mapping_fields(value: Any, allowed: frozenset[str]) -> bool:
    if not isinstance(value, Mapping) or set(value) - allowed: raise UniverseDraftInputError("nested item contains unsupported fields")
    return True
def _string(value: Mapping[str, Any], field: str, allowed: frozenset[str]) -> str: _mapping_fields(value, allowed); return _optional_string(value.get(field)) or ""
def _optional_string(value: Any) -> str | None:
    if value is None: return None
    if not isinstance(value, str): raise UniverseDraftInputError("text fields must be strings")
    return value.strip() or None
def _date(value: Any) -> date | None:
    if value is None or value == "": return None
    if not isinstance(value, str): raise UniverseDraftInputError("dates must be YYYY-MM-DD strings")
    try: return date.fromisoformat(value)
    except ValueError as exc: raise UniverseDraftInputError("dates must be YYYY-MM-DD strings") from exc
def _decimal(value: Any) -> Decimal | None:
    if value is None or value == "": return None
    if isinstance(value, bool) or not isinstance(value, (str, int, float)): raise UniverseDraftInputError("numeric rule fields must be numbers or numeric strings")
    try:
        result = Decimal(str(value))
        if not result.is_finite(): raise InvalidOperation
        return result
    except InvalidOperation as exc: raise UniverseDraftInputError("numeric rule fields must be finite numbers") from exc
def _bool(value: Any, field: str) -> bool:
    if not isinstance(value, bool): raise UniverseDraftInputError(f"{field} must be boolean")
    return value
def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping): return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list): return [_jsonable(item) for item in value]
    return value
def _limit(value: int) -> int: return max(1, min(value, 100))
def _now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
def _public_instrument(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    result = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "instrument": json.loads(row["instrument_json"]), "created_at": row["created_at"], "version": row["version"], "schema_version": INSTRUMENT_DRAFT_SCHEMA_VERSION}
    if not history: result["updated_at"] = row["updated_at"]
    return result
def _public_pool(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    refs = json.loads(row["instruments_json"]); result = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "purpose": row["purpose"], "instruments": refs, "instrument_count": len(refs), "legacy_instrument_count": sum(1 for ref in refs if "instrument_draft_id" in ref), "catalogue_member_count": sum(1 for ref in refs if ref.get("member_type") == "security_catalogue_v1"), "research_qualified": False, "created_at": row["created_at"], "version": row["version"], "schema_version": ASSET_POOL_DRAFT_SCHEMA_VERSION}
    if not history: result["updated_at"] = row["updated_at"]
    return result
