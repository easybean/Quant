"""Server-controlled input contract for P4 factor research jobs.

Snapshot locations are configuration, never browser input.  A snapshot must be
explicitly marked as P3-03A qualified before any forward-return statistics can
be calculated; the representative P1-04 corporate-action sample is not such a
snapshot.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Mapping

from .factor_catalogue import availability


class FactorResearchError(ValueError):
    pass


def snapshots() -> dict[str, dict[str, Any]]:
    """Load the administrator-owned fixed snapshot registry.

    ``QUANT_FACTOR_RESEARCH_SNAPSHOTS`` is a JSON object keyed by immutable
    version.  It deliberately cannot be supplied in an HTTP request.
    """
    raw = os.getenv("QUANT_FACTOR_RESEARCH_SNAPSHOTS", "{}")
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise FactorResearchError("factor snapshot registry is invalid") from exc
    if not isinstance(value, dict):
        raise FactorResearchError("factor snapshot registry is invalid")
    return {str(key): dict(item) for key, item in value.items() if isinstance(item, dict)}


def validate_request(snapshot_id: str, parameters: Mapping[str, Any]) -> dict[str, Any]:
    entry = snapshots().get(snapshot_id)
    if entry is None:
        raise FactorResearchError("fixed factor snapshot is unavailable; P3-03A qualified asset pool is not configured")
    if entry.get("p3_03a_qualified") is not True:
        raise FactorResearchError("P3-03A qualified asset pool is required; this snapshot cannot produce research returns")
    root = entry.get("derived_root")
    if not isinstance(root, str) or not Path(root).is_dir():
        raise FactorResearchError("fixed factor snapshot is unavailable")
    factor_id = parameters.get("factor_id")
    if not isinstance(factor_id, str) or not factor_id.strip():
        raise FactorResearchError("factor_id is required")
    runnable, reason = availability(factor_id)
    if not runnable:
        raise FactorResearchError(f"factor is disabled: {reason}")
    normalized: dict[str, int] = {}
    for field, low, high, default in (("holding_period", 1, 60, 5), ("quantiles", 2, 10, 5), ("min_cross_section", 2, 300, 5)):
        value = parameters.get(field, default)
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= high:
            raise FactorResearchError(f"{field} must be an integer from {low} to {high}")
        normalized[field] = value
    symbols = parameters.get("symbols", [])
    if not isinstance(symbols, list) or len(symbols) > 300 or not all(isinstance(x, str) and x.strip() for x in symbols):
        raise FactorResearchError("symbols must be a list of at most 300 non-empty ticker strings")
    if normalized["min_cross_section"] > (len(symbols) or 300):
        raise FactorResearchError("min_cross_section exceeds selected symbols")
    return entry


def public_availability() -> dict[str, object]:
    """Safe page gate: expose versions and eligibility, never filesystem paths."""
    entries = snapshots()
    qualified = [key for key, item in entries.items() if item.get("p3_03a_qualified") is True]
    return {"available": bool(qualified), "snapshots": sorted(qualified),
            "message": "可选择固定研究快照" if qualified else "P3-03A 真实研究资产池尚未验收；不能计算或展示收益统计。"}
