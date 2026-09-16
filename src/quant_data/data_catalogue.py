"""Read-only, bounded data-catalogue snapshots for the workbench API.

This module intentionally opens only four explicitly named JSON summaries.  It
never walks a data directory, opens Parquet, or triggers collection work from
an HTTP request.  Data generation belongs to the offline audit commands.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CATALOGUE_SCHEMA_VERSION = "v1"


def _data_root(value: str | None = None) -> Path:
    return Path(value or os.getenv("QUANT_DATA_ROOT", "/home/davidou/quant/data"))


def _read_json(path: Path) -> tuple[dict[str, Any] | None, str | None]:
    """Read one pre-generated JSON object without leaking filesystem details."""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "盘点快照缺失"
    except (OSError, json.JSONDecodeError):
        return None, "盘点快照不可读取"
    return (payload, None) if isinstance(payload, dict) else (None, "盘点快照格式无效")


def _text(value: object) -> str:
    return value if isinstance(value, str) else ""


def _count(value: object) -> int:
    return value if isinstance(value, int) and value >= 0 else 0


def _date_range(value: object) -> dict[str, str]:
    value = value if isinstance(value, dict) else {}
    return {"start": _text(value.get("start")), "end": _text(value.get("end"))}


def _inventory_summary(inventory: dict[str, Any]) -> dict[str, object]:
    """Whitelist the public inventory contract; never forward snapshot internals."""
    sources = inventory.get("sources", [])
    versions = inventory.get("versions", [])
    return {
        "market": _text(inventory.get("market")),
        "frequency": _text(inventory.get("frequency")),
        "raw_files": _count(inventory.get("raw_files")),
        "derived_files": _count(inventory.get("derived_files")),
        "unique_symbols": _count(inventory.get("unique_symbols")),
        "lifecycle_records": _count(inventory.get("lifecycle_records")),
        "rows": _count(inventory.get("rows")),
        "date_range": _date_range(inventory.get("date_range")),
        "benchmarks": [item for item in inventory.get("benchmarks", []) if isinstance(item, str)],
        "sources": [
            {
                "name": _text(source.get("name")), "files": _count(source.get("files")),
                "symbols": _count(source.get("symbols")), "date_range": _date_range(source.get("date_range")),
                "adjustment_status": _text(source.get("adjustment_status")),
            }
            for source in sources if isinstance(source, dict)
        ],
        "versions": [
            {"name": _text(version.get("name")), "version": _text(version.get("version")), "scope": _text(version.get("scope"))}
            for version in versions if isinstance(version, dict)
        ],
    }


def data_catalogue_payload(data_root: str | Path | None = None) -> tuple[dict[str, Any] | None, list[str]]:
    """Return fixed, sanitized summaries or a concise list of unavailable parts.

    ``data_root`` exists for tests and offline callers.  The runtime uses only
    ``QUANT_DATA_ROOT``.  The four paths are deliberately fixed so callers
    cannot ask the API to browse arbitrary server files.
    """
    root = _data_root(str(data_root) if data_root is not None else None)
    inventory, inventory_problem = _read_json(root / "catalogue" / "inventory-v1.json")
    quality, quality_problem = _read_json(root / "audit" / "quality-v1" / "summary.json")
    structural, structural_problem = _read_json(root / "audit" / "structural-v1" / "run-metadata.json")
    actions, actions_problem = _read_json(
        root / "curated" / "reference" / "corporate-actions" / "alpaca" / "20260908-narrow-v1" / "verification-report.json"
    )
    missing = [
        label for label, problem in (
            ("数据盘点", inventory_problem),
            ("质量报告", quality_problem),
            ("结构清洗摘要", structural_problem),
            ("公司行动验证快照", actions_problem),
        ) if problem
    ]
    if missing:
        return None, missing

    assert inventory and quality and structural and actions
    findings = quality.get("findings", {})
    golden_checks = actions.get("golden_checks", [])
    passed_checks = sum(1 for check in golden_checks if isinstance(check, dict) and check.get("passed") is True)
    return {
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "generated_at": str(inventory.get("generated_at", "")),
        "inventory": _inventory_summary(inventory),
        "quality": {
            "rule_version": str(quality.get("rule_version", "")),
            "findings_rows": int(quality.get("findings_rows", 0)),
            "findings": {str(key): int(value) for key, value in findings.items()} if isinstance(findings, dict) else {},
            "limitations": [str(item) for item in quality.get("limitations", []) if isinstance(item, str)],
            "structural": {
                "run_at": str(structural.get("run_at", "")),
                "cleaning_status": str(structural.get("cleaning_status", "")),
                "raw_files": int(structural.get("raw_files", 0)),
                "derived_files_written": int(structural.get("derived_files_written", 0)),
            },
        },
        "corporate_actions": {
            "source": "Alpaca",
            "snapshot_id": str(actions.get("snapshot_id", "")),
            "event_count": int(actions.get("event_count", 0)),
            "golden_checks_passed": passed_checks,
            "golden_checks_total": len(golden_checks) if isinstance(golden_checks, list) else 0,
            "scope": "代表性事件验证，非全市场公司行动覆盖。",
            "delisting_resolution": str(actions.get("delisting_resolution", "unknown")),
        },
    }, []
