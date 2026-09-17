"""Read-only, allow-listed projection of server-produced sync summaries."""
from __future__ import annotations

import json
import os
from pathlib import Path


def _read(root: Path, relative: str, schema: str | None = None) -> dict:
    path = root / relative
    if root.resolve() not in path.resolve().parents or path.stat().st_size > 32 * 1024 * 1024:
        raise ValueError("invalid_summary")
    value = json.loads(path.read_text())
    if not isinstance(value, dict) or (schema and value.get("schema_version") != schema):
        raise ValueError("invalid_summary")
    return value


def _count(value: dict, key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise ValueError("invalid_count")
    return result


def _text(value: object) -> str | None:
    return value[:120] if isinstance(value, str) else None


def sync_status_payload(data_root: str | Path | None = None) -> dict:
    root = Path(data_root or os.getenv("QUANT_DATA_ROOT", "/home/davidou/quant/data"))
    try:
        coverage = _read(root, "manifests/yahoo-daily-v1/coverage.json", "yahoo-sync-coverage-v1")
        active, current, pending = (_count(coverage, k) for k in ("active_symbols", "endpoint_current", "needs_update"))
        historical = _count(coverage, "historical_only_symbols")
        if current + pending != active:
            raise ValueError("inconsistent_coverage")
    except (OSError, ValueError):
        return {"available": False, "message": "同步覆盖清单缺失或无效；不能确认补齐进度。", "research_qualified": False}
    response = {"available": True, "target_date": _text(coverage.get("target_date")),
                "generated_at": _text(coverage.get("generated_at")), "active_symbols": active,
                "endpoint_current": current, "needs_update": pending,
                "historical_only_symbols": historical,
                "research_qualified": False, "providers": [], "run_available": False}
    try:
        latest = _read(root, "manifests/us-daily-sync/latest.json")
        providers = latest.get("providers")
        if not isinstance(providers, dict):
            raise ValueError("invalid_providers")
        response.update({"run_available": True, "run_status": _text(latest.get("status")),
                         "finished_at": _text(latest.get("finished_at")), "run_target_date": _text(latest.get("target_date"))})
        for name in ("alpaca", "yahoo", "nasdaq"):
            row = providers.get(name)
            if not isinstance(row, dict):
                continue
            response["providers"].append({"provider": name, "status": _text(row.get("status")),
                "success": _count(row, "success"), "failed": _count(row, "failed"),
                "attempted": _count(row, "attempted"), "last_error_code": _text(row.get("last_error_code")),
                "finished_at": _text(row.get("finished_at")), "resume_after": _text(row.get("resume_after"))})
    except (OSError, ValueError):
        response.update({"run_available": False, "providers": []})
    return response
