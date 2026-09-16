"""Durable, non-executable strategy drafts for the workbench.

This store only persists a template selected from the fixed P3-02 registry and
validated parameters.  It deliberately has no data snapshot, runner, account,
or execution entry point.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .jobs import state_dir
from .strategies import BUY_AND_HOLD_VERSION, DUAL_MOVING_AVERAGE_VERSION, StrategyTemplate, parameter_schema, validate_parameters


DRAFT_SCHEMA_VERSION = "p3-02a-v1"


class DraftInputError(ValueError):
    pass


def template_catalogue() -> list[dict[str, Any]]:
    """Public, fixed template metadata; no user-provided versions are accepted."""
    return [
        {"id": StrategyTemplate.BUY_AND_HOLD.value, "version": BUY_AND_HOLD_VERSION,
         "name": "买入持有", "description": "从指定首个合格交易日起保持单一研究代码的目标权重。",
         "parameters": parameter_schema(StrategyTemplate.BUY_AND_HOLD)},
        {"id": StrategyTemplate.DUAL_MOVING_AVERAGE.value, "version": DUAL_MOVING_AVERAGE_VERSION,
         "name": "双均线", "description": "仅基于以后由合格数据输入提供的可用日线收盘价比较快慢均线。",
         "parameters": parameter_schema(StrategyTemplate.DUAL_MOVING_AVERAGE)},
    ]


class StrategyDraftStore:
    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "strategy-drafts.sqlite3"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS strategy_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, template TEXT NOT NULL,
              template_version TEXT NOT NULL, parameters_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS strategy_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, template TEXT NOT NULL, template_version TEXT NOT NULL,
              parameters_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES strategy_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS strategy_drafts_updated ON strategy_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS strategy_draft_versions_draft ON strategy_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, template, parameters = _parse(payload)
        now = _now()
        with self._lock, self._connect() as db:
            if draft_id:
                current = db.execute("SELECT * FROM strategy_drafts WHERE id=?", (draft_id,)).fetchone()
                if current is None:
                    raise KeyError(draft_id)
                version = int(current["version"]) + 1
            else:
                draft_id, version = str(uuid4()), 1
            template_version = _template_version(template)
            encoded = json.dumps(parameters, sort_keys=True, separators=(",", ":"))
            if version == 1:
                db.execute("INSERT INTO strategy_drafts (id,name,template,template_version,parameters_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?,?,?)", (draft_id, name, template.value, template_version, encoded, now, now, version))
            else:
                db.execute("UPDATE strategy_drafts SET name=?,template=?,template_version=?,parameters_json=?,updated_at=?,version=? WHERE id=?", (name, template.value, template_version, encoded, now, version, draft_id))
            db.execute("INSERT INTO strategy_draft_versions (id,draft_id,version,name,template,template_version,parameters_json,created_at) VALUES (?,?,?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, template.value, template_version, encoded, now))
            row = db.execute("SELECT * FROM strategy_drafts WHERE id=?", (draft_id,)).fetchone()
            return _public(row)

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM strategy_drafts ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
            return [_public(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            exists = db.execute("SELECT 1 FROM strategy_drafts WHERE id=?", (draft_id,)).fetchone()
            if not exists:
                return None
            rows = db.execute("SELECT * FROM strategy_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
            return [_public(row, history=True) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db


def _parse(payload: Mapping[str, Any]) -> tuple[str, StrategyTemplate, dict[str, Any]]:
    if not isinstance(payload, Mapping):
        raise DraftInputError("request must be an object")
    name, template, parameters = payload.get("name"), payload.get("template"), payload.get("parameters")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120:
        raise DraftInputError("name is required (1-120 characters)")
    try:
        choice = StrategyTemplate(template)
    except ValueError as exc:
        raise DraftInputError("template must be a fixed registered template") from exc
    if not isinstance(parameters, Mapping):
        raise DraftInputError("parameters must be an object")
    normalized = dict(parameters)
    for field in ("fast_window", "slow_window"):
        if isinstance(normalized.get(field), str) and normalized[field].strip().isdigit():
            normalized[field] = int(normalized[field])
    issues = validate_parameters(choice, normalized)
    if issues:
        raise DraftInputError("; ".join(f"{item.field}: {item.message}" for item in issues))
    return name.strip(), choice, normalized


def _template_version(template: StrategyTemplate) -> str:
    return BUY_AND_HOLD_VERSION if template is StrategyTemplate.BUY_AND_HOLD else DUAL_MOVING_AVERAGE_VERSION


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _public(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    payload = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "template": row["template"], "template_version": row["template_version"], "parameters": json.loads(row["parameters_json"]), "created_at": row["created_at"], "version": row["version"]}
    if not history:
        payload["updated_at"] = row["updated_at"]
    return payload
