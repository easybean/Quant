"""Versioned visual strategy definitions; deliberately no execution surface."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import threading
from typing import Any, Mapping
from uuid import uuid4

from .factor_catalogue import availability
from .factors import list_factors
from .jobs import state_dir
from .universe_drafts import AssetPoolDraftStore

VISUAL_STRATEGY_SCHEMA_VERSION = "p3-02b-v1"
_FIELDS = frozenset({"name", "asset_pool", "signal_rules", "combination", "rebalance_frequency", "allocation", "constraints"})
_RULE_FIELDS = frozenset({"factor_id", "operator", "threshold", "direction"})
_POOL_FIELDS = frozenset({"asset_pool_draft_id", "asset_pool_version"})
_ALLOCATION_FIELDS = frozenset({"mode", "target_weight"})
_CONSTRAINT_FIELDS = frozenset({"max_position_weight", "max_holdings"})


class VisualStrategyDraftInputError(ValueError):
    pass


class VisualStrategyDraftStore:
    def __init__(self, root: str | Path | None = None, asset_pools: AssetPoolDraftStore | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "visual-strategy-drafts.sqlite3"
        self.asset_pools = asset_pools or AssetPoolDraftStore(self.root)
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True); self.asset_pools.initialize()
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS visual_strategy_drafts (
              id TEXT PRIMARY KEY, name TEXT NOT NULL, definition_json TEXT NOT NULL,
              created_at TEXT NOT NULL, updated_at TEXT NOT NULL, version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS visual_strategy_draft_versions (
              id TEXT PRIMARY KEY, draft_id TEXT NOT NULL, version INTEGER NOT NULL,
              name TEXT NOT NULL, definition_json TEXT NOT NULL, created_at TEXT NOT NULL,
              UNIQUE(draft_id, version), FOREIGN KEY(draft_id) REFERENCES visual_strategy_drafts(id)
            );
            CREATE INDEX IF NOT EXISTS visual_strategy_drafts_updated ON visual_strategy_drafts(updated_at DESC);
            CREATE INDEX IF NOT EXISTS visual_strategy_versions_draft ON visual_strategy_draft_versions(draft_id, version DESC);
            """)

    def save(self, payload: Mapping[str, Any], draft_id: str | None = None) -> dict[str, Any]:
        name, definition = _parse(payload, self.asset_pools)
        now, encoded = _now(), json.dumps(definition, sort_keys=True, separators=(",", ":"))
        with self._lock, self._connect() as db:
            if draft_id:
                row = db.execute("SELECT version FROM visual_strategy_drafts WHERE id=?", (draft_id,)).fetchone()
                if row is None: raise KeyError(draft_id)
                version = int(row["version"]) + 1
                db.execute("UPDATE visual_strategy_drafts SET name=?,definition_json=?,updated_at=?,version=? WHERE id=?", (name, encoded, now, version, draft_id))
            else:
                draft_id, version = str(uuid4()), 1
                db.execute("INSERT INTO visual_strategy_drafts (id,name,definition_json,created_at,updated_at,version) VALUES (?,?,?,?,?,?)", (draft_id, name, encoded, now, now, version))
            db.execute("INSERT INTO visual_strategy_draft_versions (id,draft_id,version,name,definition_json,created_at) VALUES (?,?,?,?,?,?)", (str(uuid4()), draft_id, version, name, encoded, now))
            return _public(db.execute("SELECT * FROM visual_strategy_drafts WHERE id=?", (draft_id,)).fetchone())

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as db:
            rows = db.execute("SELECT * FROM visual_strategy_drafts ORDER BY updated_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [_public(row) for row in rows]

    def history(self, draft_id: str) -> list[dict[str, Any]] | None:
        with self._connect() as db:
            if db.execute("SELECT 1 FROM visual_strategy_drafts WHERE id=?", (draft_id,)).fetchone() is None: return None
            rows = db.execute("SELECT * FROM visual_strategy_draft_versions WHERE draft_id=? ORDER BY version DESC", (draft_id,)).fetchall()
        return [_public(row, history=True) for row in rows]

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path); db.row_factory = sqlite3.Row; db.execute("PRAGMA journal_mode=WAL"); return db


def _parse(payload: Mapping[str, Any], pools: AssetPoolDraftStore) -> tuple[str, dict[str, Any]]:
    if not isinstance(payload, Mapping) or set(payload) != _FIELDS: raise VisualStrategyDraftInputError("only visual strategy definition fields are accepted")
    name = payload.get("name")
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 120: raise VisualStrategyDraftInputError("name is required (1-120 characters)")
    pool = payload.get("asset_pool")
    if not isinstance(pool, Mapping) or set(pool) != _POOL_FIELDS: raise VisualStrategyDraftInputError("asset_pool must contain only asset_pool_draft_id and asset_pool_version")
    pool_id, pool_version = pool.get("asset_pool_draft_id"), pool.get("asset_pool_version")
    if not isinstance(pool_id, str) or not pool_id.strip() or isinstance(pool_version, bool) or not isinstance(pool_version, int) or pool_version < 1 or not pools.version_exists(pool_id.strip(), pool_version): raise VisualStrategyDraftInputError("asset_pool must reference an existing saved asset-pool version")
    rules = payload.get("signal_rules")
    if not isinstance(rules, list) or not 1 <= len(rules) <= 10: raise VisualStrategyDraftInputError("signal_rules must contain 1-10 rules")
    known = {str(item["id"]) for item in list_factors()}
    normalized_rules: list[dict[str, Any]] = []
    for index, rule in enumerate(rules):
        if not isinstance(rule, Mapping) or set(rule) != _RULE_FIELDS: raise VisualStrategyDraftInputError(f"signal_rules[{index}] must contain factor_id, operator, threshold and direction")
        factor, operator, direction = rule.get("factor_id"), rule.get("operator"), rule.get("direction")
        if not isinstance(factor, str) or factor not in known or not availability(factor)[0]: raise VisualStrategyDraftInputError(f"signal_rules[{index}].factor_id must be a current runnable factor")
        if operator not in {"gt", "gte", "lt", "lte"}: raise VisualStrategyDraftInputError(f"signal_rules[{index}].operator must be gt, gte, lt or lte")
        if direction not in {"long", "exclude"}: raise VisualStrategyDraftInputError(f"signal_rules[{index}].direction must be long or exclude")
        threshold = _number(rule.get("threshold"), f"signal_rules[{index}].threshold")
        normalized_rules.append({"factor_id": factor, "operator": operator, "threshold": threshold, "direction": direction})
    if len({(rule["factor_id"], rule["operator"], rule["threshold"], rule["direction"]) for rule in normalized_rules}) != len(normalized_rules): raise VisualStrategyDraftInputError("duplicate signal rules are not allowed")
    combination, frequency = payload.get("combination"), payload.get("rebalance_frequency")
    if combination not in {"all", "any"}: raise VisualStrategyDraftInputError("combination must be all or any")
    if frequency not in {"daily", "weekly", "monthly"}: raise VisualStrategyDraftInputError("rebalance_frequency must be daily, weekly or monthly")
    allocation = payload.get("allocation")
    if not isinstance(allocation, Mapping) or set(allocation) - _ALLOCATION_FIELDS or allocation.get("mode") not in {"equal_weight", "fixed_weight"}: raise VisualStrategyDraftInputError("allocation mode must be equal_weight or fixed_weight")
    mode = allocation["mode"]
    if mode == "equal_weight" and set(allocation) != {"mode"}: raise VisualStrategyDraftInputError("equal_weight allocation accepts no target_weight")
    if mode == "fixed_weight" and set(allocation) != _ALLOCATION_FIELDS: raise VisualStrategyDraftInputError("fixed_weight allocation requires target_weight")
    normalized_allocation: dict[str, Any] = {"mode": mode}
    if mode == "fixed_weight": normalized_allocation["target_weight"] = _bounded(allocation.get("target_weight"), "allocation.target_weight", 0, 1, lower_open=True)
    constraints = payload.get("constraints")
    if not isinstance(constraints, Mapping) or set(constraints) != _CONSTRAINT_FIELDS: raise VisualStrategyDraftInputError("constraints must contain only max_position_weight and max_holdings")
    max_weight = _bounded(constraints.get("max_position_weight"), "constraints.max_position_weight", 0, 1, lower_open=True)
    max_holdings = constraints.get("max_holdings")
    if isinstance(max_holdings, bool) or not isinstance(max_holdings, int) or not 1 <= max_holdings <= 200: raise VisualStrategyDraftInputError("constraints.max_holdings must be an integer from 1 to 200")
    if mode == "fixed_weight" and normalized_allocation["target_weight"] > max_weight: raise VisualStrategyDraftInputError("allocation.target_weight cannot exceed constraints.max_position_weight")
    return name.strip(), {"asset_pool": {"asset_pool_draft_id": pool_id.strip(), "asset_pool_version": pool_version}, "signal_rules": normalized_rules, "combination": combination, "rebalance_frequency": frequency, "allocation": normalized_allocation, "constraints": {"max_position_weight": max_weight, "max_holdings": max_holdings}}


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool): raise VisualStrategyDraftInputError(f"{field} must be a finite number")
    try: result = float(value)
    except (TypeError, ValueError): raise VisualStrategyDraftInputError(f"{field} must be a finite number") from None
    if not math.isfinite(result): raise VisualStrategyDraftInputError(f"{field} must be a finite number")
    return result

def _bounded(value: Any, field: str, lower: float, upper: float, *, lower_open: bool = False) -> float:
    result = _number(value, field)
    if result > upper or result < lower or (lower_open and result == lower): raise VisualStrategyDraftInputError(f"{field} must be within ({lower}, {upper}]")
    return result

def _now() -> str: return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def _public(row: sqlite3.Row, history: bool = False) -> dict[str, Any]:
    definition = json.loads(row["definition_json"])
    result = {"id": row["id"], "draft_id": row["draft_id"] if history else row["id"], "name": row["name"], "definition": definition, "created_at": row["created_at"], "version": row["version"], "schema_version": VISUAL_STRATEGY_SCHEMA_VERSION}
    if not history: result["updated_at"] = row["updated_at"]
    return result
