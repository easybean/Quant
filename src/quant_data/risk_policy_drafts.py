"""Versioned, non-executable global risk-policy drafts.

This is deliberately a configuration-only store.  It neither reads market or
account state nor evaluates limits, submits orders, cancels orders, or closes
positions.  Strategy-specific constraints belong to strategy drafts, not here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .jobs import state_dir


RISK_POLICY_DRAFT_SCHEMA_VERSION = "p6-00-v1"
_FIELDS = frozenset({
    "name", "scope", "max_instrument_exposure_pct", "max_market_exposure_pct",
    "max_gross_leverage", "max_daily_loss_pct", "max_drawdown_pct",
    "max_orders_per_minute", "trading_halted",
})


class RiskPolicyDraftInputError(ValueError):
    pass


class RiskPolicyDraftStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "risk-policy-drafts.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS risk_policy_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, policy_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS risk_policy_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, policy_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES risk_policy_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS risk_policy_drafts_updated ON risk_policy_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS risk_policy_draft_versions_draft ON risk_policy_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, policy = _parse(payload)
        now, encoded = _now(), json.dumps(policy, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if draft_id:
                current = db.execute("SELECT version FROM risk_policy_drafts WHERE id=?", (draft_id,)).fetchone()
                if current is None:
                    raise KeyError(draft_id)
                version = int(current["version"]) + 1
                db.execute("UPDATE risk_policy_drafts SET name=?,policy_json=?,updated_at=?,version=? WHERE id=?", (name, encoded, now, version, draft_id))
            else:
                draft_id, version = str(uuid4()), 1
                db.execute("INSERT INTO risk_policy_drafts (id,name,policy_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?)", (draft_id, name, encoded, now, now, version))
            db.execute("INSERT INTO risk_policy_draft_versions (id,draft_id,version,name,policy_json,created_at) VALUES (?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, encoded, now))
            return _public(db.execute("SELECT * FROM risk_policy_drafts WHERE id=?", (draft_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM risk_policy_drafts ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [_public(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM risk_policy_drafts WHERE id=?", (draft_id,)).fetchone() is None:
                return None
            rows = db.execute("SELECT * FROM risk_policy_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
        return [_public(row, history=True) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db


def _parse(payload: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise RiskPolicyDraftInputError("request must be an object")
    if set(payload) != _FIELDS:
        raise RiskPolicyDraftInputError("only global risk-policy fields are accepted")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise RiskPolicyDraftInputError("name is required (1-120 characters)")
    if payload.get("scope") != "global":
        raise RiskPolicyDraftInputError("scope must be global; strategy constraints belong to strategy drafts")
    instrument = _percent(payload.get("max_instrument_exposure_pct"), "max_instrument_exposure_pct")
    market = _percent(payload.get("max_market_exposure_pct"), "max_market_exposure_pct")
    daily_loss = _percent(payload.get("max_daily_loss_pct"), "max_daily_loss_pct")
    drawdown = _percent(payload.get("max_drawdown_pct"), "max_drawdown_pct")
    leverage = _number(payload.get("max_gross_leverage"), "max_gross_leverage", Decimal("0"), Decimal("100"), inclusive_low=False)
    frequency = payload.get("max_orders_per_minute")
    if isinstance(frequency, bool) or not isinstance(frequency, int) or not 1 <= frequency <= 100000:
        raise RiskPolicyDraftInputError("max_orders_per_minute must be an integer from 1 to 100000")
    if not isinstance(payload.get("trading_halted"), bool):
        raise RiskPolicyDraftInputError("trading_halted must be boolean")
    if instrument > market:
        raise RiskPolicyDraftInputError("max_instrument_exposure_pct cannot exceed max_market_exposure_pct")
    if daily_loss > drawdown:
        raise RiskPolicyDraftInputError("max_daily_loss_pct cannot exceed max_drawdown_pct")
    return name.strip(), {
        "scope": "global", "max_instrument_exposure_pct": _stringify(instrument),
        "max_market_exposure_pct": _stringify(market), "max_gross_leverage": _stringify(leverage),
        "max_daily_loss_pct": _stringify(daily_loss), "max_drawdown_pct": _stringify(drawdown),
        "max_orders_per_minute": frequency, "trading_halted": payload["trading_halted"],
    }


def _percent(value: Any, field: str) -> Decimal:
    return _number(value, field, Decimal("0"), Decimal("100"), inclusive_low=False)


def _number(value: Any, field: str, low: Decimal, high: Decimal, *, inclusive_low: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise RiskPolicyDraftInputError(f"{field} must be a finite number")
    try:
        number = Decimal(str(value))
    except InvalidOperation as exc:
        raise RiskPolicyDraftInputError(f"{field} must be a finite number") from exc
    if not number.is_finite():
        raise RiskPolicyDraftInputError(f"{field} must be a finite number")
    if number > high or (number < low if inclusive_low else number <= low):
        boundary = f"[{low}, {high}]" if inclusive_low else f"({low}, {high}]"
        raise RiskPolicyDraftInputError(f"{field} must be within {boundary}")
    return number


def _stringify(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _public(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    result = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "policy": json.loads(row["policy_json"]), "created_at": row["created_at"], "version": row["version"], "schema_version": RISK_POLICY_DRAFT_SCHEMA_VERSION}
    if not history:
        result["updated_at"] = row["updated_at"]
    return result
