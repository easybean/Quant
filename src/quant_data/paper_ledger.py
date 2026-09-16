"""Persistent, non-executable paper-account and immutable ledger primitives.

This module deliberately has no market-data, broker, matching, or PnL code.
Orders, fills, fees and audit records can only be appended by a future trusted
workflow (or a controlled accounting test); the HTTP surface exposes them read
only.  A ledger record is de-duplicated by its caller supplied ``dedupe_key``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import re
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .jobs import state_dir


PAPER_LEDGER_SCHEMA_VERSION = "p6-01-v1"
_CURRENCIES = re.compile(r"^[A-Z][A-Z0-9]{2,11}$")
_ACCOUNT_FIELDS = frozenset({"name", "base_currency", "initial_cash", "margin_mode"})
_EVENT_TYPES = frozenset({"order", "fill", "fee", "audit"})


class PaperLedgerInputError(ValueError):
    pass


class PaperLedgerConflictError(ValueError):
    pass


class PaperLedgerStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "paper-ledger.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS paper_accounts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, base_currency TEXT NOT NULL,
              initial_cash TEXT NOT NULL, margin_mode TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS paper_ledger_events (
              id TEXT PRIMARY KEY, account_id TEXT NOT NULL, event_type TEXT NOT NULL,
              dedupe_key TEXT NOT NULL UNIQUE, event_json TEXT NOT NULL,
              cash_delta TEXT NOT NULL, position_delta TEXT NOT NULL,
              created_at TEXT NOT NULL, FOREIGN KEY(account_id) REFERENCES paper_accounts(id)
            );
            CREATE INDEX IF NOT EXISTS paper_accounts_created ON paper_accounts(created_at DESC);
            CREATE INDEX IF NOT EXISTS paper_events_account ON paper_ledger_events(account_id, created_at DESC);
            CREATE TRIGGER IF NOT EXISTS paper_events_immutable_update BEFORE UPDATE ON paper_ledger_events BEGIN SELECT RAISE(ABORT, 'ledger events are immutable'); END;
            CREATE TRIGGER IF NOT EXISTS paper_events_immutable_delete BEFORE DELETE ON paper_ledger_events BEGIN SELECT RAISE(ABORT, 'ledger events are immutable'); END;
            """)

    def create_account(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        name, currency, cash, margin_mode = _parse_account(payload)
        account_id, now = str(uuid4()), _now()
        with self._lock, self._connect() as db:
            db.execute("INSERT INTO paper_accounts VALUES (?,?,?,?,?,?)", (account_id, name, currency, _decimal_text(cash), margin_mode, now))
            return _account_public(db.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone())

    def list_accounts(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM paper_accounts ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [_account_public(row) for row in rows]

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM paper_accounts WHERE id=?", (account_id,)).fetchone()
        return _account_public(row) if row else None

    def append_event(self, account_id: str, payload: Mapping[str, Any]) -> dict[str, Any]:
        event_type, key, event, cash_delta, position_delta = _parse_event(payload)
        encoded = json.dumps(event, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if db.execute("SELECT 1 FROM paper_accounts WHERE id=?", (account_id,)).fetchone() is None:
                raise KeyError(account_id)
            previous = db.execute("SELECT * FROM paper_ledger_events WHERE dedupe_key=?", (key,)).fetchone()
            if previous:
                if previous["account_id"] == account_id and previous["event_type"] == event_type and previous["event_json"] == encoded and previous["cash_delta"] == _decimal_text(cash_delta) and previous["position_delta"] == _decimal_text(position_delta):
                    return _event_public(previous)
                raise PaperLedgerConflictError("dedupe_key already belongs to a different immutable event")
            event_id, now = str(uuid4()), _now()
            db.execute("INSERT INTO paper_ledger_events VALUES (?,?,?,?,?,?,?,?)", (event_id, account_id, event_type, key, encoded, _decimal_text(cash_delta), _decimal_text(position_delta), now))
            return _event_public(db.execute("SELECT * FROM paper_ledger_events WHERE id=?", (event_id,)).fetchone())

    def events(self, account_id: str, limit: int = 100) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM paper_accounts WHERE id=?", (account_id,)).fetchone() is None:
                return None
            rows = db.execute("SELECT * FROM paper_ledger_events WHERE account_id=? ORDER BY created_at DESC, id DESC LIMIT ?", (account_id, max(1, min(limit, 250)))).fetchall()
        return [_event_public(row) for row in rows]

    def reconciliation(self, account_id: str) -> dict[str, Any] | None:
        account = self.get_account(account_id)
        if account is None:
            return None
        with self._connect() as db:
            row = db.execute("SELECT COUNT(*) AS count, COALESCE(SUM(CAST(cash_delta AS REAL)), 0) AS cash, COALESCE(SUM(CAST(position_delta AS REAL)), 0) AS position FROM paper_ledger_events WHERE account_id=?", (account_id,)).fetchone()
            # Decimal is recalculated from exact stored text below, never from SQLite REAL.
            deltas = db.execute("SELECT cash_delta, position_delta FROM paper_ledger_events WHERE account_id=?", (account_id,)).fetchall()
        cash_delta = sum((Decimal(item["cash_delta"]) for item in deltas), Decimal("0"))
        position_delta = sum((Decimal(item["position_delta"]) for item in deltas), Decimal("0"))
        return {"account_id": account_id, "base_currency": account["base_currency"], "initial_cash": account["initial_cash"], "recorded_cash_delta": _decimal_text(cash_delta), "reconciled_cash": _decimal_text(Decimal(account["initial_cash"]) + cash_delta), "net_recorded_position": _decimal_text(position_delta), "event_count": int(row["count"]), "scope": "recorded ledger movements only; not PnL, mark-to-market, margin, or broker balance"}

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db


def _parse_account(payload: Mapping[str, Any]) -> tuple[str, str, Decimal, str]:
    if not isinstance(payload, Mapping) or set(payload) != _ACCOUNT_FIELDS:
        raise PaperLedgerInputError("only name, base_currency, initial_cash and margin_mode are accepted")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise PaperLedgerInputError("name is required (1-120 characters)")
    currency = payload.get("base_currency")
    if not isinstance(currency, str) or not _CURRENCIES.fullmatch(currency):
        raise PaperLedgerInputError("base_currency must be an uppercase 3-12 character code")
    margin_mode = payload.get("margin_mode")
    if margin_mode not in {"cash", "cross", "isolated"}:
        raise PaperLedgerInputError("margin_mode must be cash, cross or isolated")
    cash = _decimal(payload.get("initial_cash"), "initial_cash", positive=True)
    return name.strip(), currency, cash, margin_mode


def _parse_event(payload: Mapping[str, Any]) -> tuple[str, str, dict[str, Any], Decimal, Decimal]:
    if not isinstance(payload, Mapping) or set(payload) != {"event_type", "dedupe_key", "event", "cash_delta", "position_delta"}:
        raise PaperLedgerInputError("ledger event fields are fixed")
    kind, key, event = payload.get("event_type"), payload.get("dedupe_key"), payload.get("event")
    if kind not in _EVENT_TYPES:
        raise PaperLedgerInputError("event_type must be order, fill, fee or audit")
    if not isinstance(key, str) or not key.strip() or len(key) > 160:
        raise PaperLedgerInputError("dedupe_key is required (1-160 characters)")
    if not isinstance(event, Mapping) or not event:
        raise PaperLedgerInputError("event must be a non-empty object")
    cash = _decimal(payload.get("cash_delta"), "cash_delta")
    position = _decimal(payload.get("position_delta"), "position_delta")
    if kind in {"order", "audit"} and (cash or position):
        raise PaperLedgerInputError(f"{kind} events cannot move cash or position")
    if kind == "fee" and (cash >= 0 or position):
        raise PaperLedgerInputError("fee event requires a negative cash_delta and zero position_delta")
    if kind == "fill" and not position:
        raise PaperLedgerInputError("fill event requires a non-zero position_delta")
    return kind, key.strip(), dict(event), cash, position


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise PaperLedgerInputError(f"{field} must be a finite number")
    try:
        result = Decimal(str(value))
    except InvalidOperation as exc:
        raise PaperLedgerInputError(f"{field} must be a finite number") from exc
    if not result.is_finite() or (positive and result <= 0):
        raise PaperLedgerInputError(f"{field} must be a finite{' positive' if positive else ''} number")
    return result


def _decimal_text(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _account_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "name": row["name"], "account_type": "paper", "base_currency": row["base_currency"], "initial_cash": row["initial_cash"], "margin_mode": row["margin_mode"], "created_at": row["created_at"], "schema_version": PAPER_LEDGER_SCHEMA_VERSION}


def _event_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"id": row["id"], "account_id": row["account_id"], "event_type": row["event_type"], "dedupe_key": row["dedupe_key"], "event": json.loads(row["event_json"]), "cash_delta": row["cash_delta"], "position_delta": row["position_delta"], "created_at": row["created_at"], "schema_version": PAPER_LEDGER_SCHEMA_VERSION}
