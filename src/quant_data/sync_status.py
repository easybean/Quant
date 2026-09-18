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
        for name in ("alpaca-mapped", "alpaca", "yahoo", "nasdaq"):
            row = providers.get(name)
            if not isinstance(row, dict):
                continue
            if all(row.get(key) is None for key in ("success", "failed", "attempted")) and row.get("status") == "failed" and row.get("error_type"):
                response["providers"].append({"provider": name, "status": "failed", "counts_available": False,
                    "success": None, "failed": None, "attempted": None, "last_error_code": "provider_phase_failed",
                    "finished_at": _text(row.get("finished_at")), "resume_after": _text(row.get("resume_after"))})
                continue
            response["providers"].append({"provider": name, "status": _text(row.get("status")),
                "counts_available": True,
                "success": _count(row, "success"), "failed": _count(row, "failed"),
                "attempted": _count(row, "attempted"), "last_error_code": _text(row.get("last_error_code")),
                "finished_at": _text(row.get("finished_at")), "resume_after": _text(row.get("resume_after"))})
    except (OSError, ValueError):
        response.update({"run_available": False, "providers": []})
    try:
        row = _read(root, "manifests/massive-daily-v1/latest.json", "massive-daily-publication-v1")
        response["providers"].append({"provider": "massive", "status": _text(row.get("status")), "counts_available": True,
            "success": _count(row, "success"), "failed": _count(row, "failed"), "attempted": _count(row, "attempted"),
            "skipped": _count(row, "skipped"), "unmatched": _count(row, "unmatched"), "missing": _count(row, "missing"),
            "last_error_code": _text(row.get("last_error_code")), "finished_at": _text(row.get("finished_at")), "resume_after": None})
    except (OSError, ValueError):
        pass
    try:
        audit = _read(root, "manifests/massive-code-audit-v1/latest.json", "massive-code-audit-v1")
        total, tested, remaining = (_count(audit, k) for k in ("total", "tested", "remaining"))
        if tested + remaining != total or _count(audit, "target_returned") > tested:
            raise ValueError("invalid_audit_counts")
        counts = audit.get("counts", {})
        if not isinstance(counts, dict): raise ValueError("invalid_audit_counts")
        response["massive_audit"] = {"target_date": _text(audit.get("target_date")), "total": total,
            "tested": tested, "remaining": remaining, "target_returned": _count(audit, "target_returned"),
            "updated_at": _text(audit.get("updated_at")), "status": _text(audit.get("status")),
            "empty_unknown": _count({"empty_unknown": counts.get("empty_unknown", 0)}, "empty_unknown")}
    except (OSError, ValueError):
        pass
    try:
        evidence = _read(root, "catalogue/current-listing-gap-evidence-v1.json", "current-listing-gap-evidence-v1")
        items = evidence.get("items")
        if not isinstance(items, list) or not all(isinstance(item, dict) for item in items):
            raise ValueError("invalid_listing_evidence")
        endpoint = [item for item in items if item.get("reason") == "endpoint_or_bridge_gap"]
        present = sum(item.get("listing_evidence") == "present_in_current_directory" for item in endpoint)
        absent = sum(item.get("listing_evidence") == "absent_from_current_directory_unknown" for item in endpoint)
        if present + absent != len(endpoint):
            raise ValueError("invalid_listing_classification")
        response["listing_evidence"] = {"observed_at": _text(evidence.get("observed_at")),
            "target_date": _text(evidence.get("target_date")), "present_endpoint_gaps": present,
            "absent_endpoint_gaps_unknown": absent, "research_qualified": False}
    except (OSError, ValueError):
        pass
    return response
