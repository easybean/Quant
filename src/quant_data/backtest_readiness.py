"""Offline evidence review and a small read-only public projection, not approval."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from .bounded_backtest_data import SYMBOLS, safe, sha
from .massive_daily import _atomic_json
from .massive_publish import _load_snapshot
from .massive_reference_audit import _load_reference
from .bounded_actions_capture import validate as validate_actions
from datetime import date

SCHEMA = "backtest-data-readiness-v1"
CODES = ("coverage", "calendar", "availability", "identity", "actions", "license", "execution")


def read(root: Path, path: Path, limit: int = 1024 * 1024) -> dict:
    safe(root, path)
    if not path.is_file() or path.stat().st_size > limit: raise ValueError("evidence_size_or_type_invalid")
    value = json.loads(path.read_text())
    if not isinstance(value, dict): raise ValueError("evidence_object_required")
    return value


def review(root: Path, candidate: Path, actions: Path) -> dict:
    manifest = read(root, candidate / "manifest.json")
    expected_files = {"bars.json", "calendar.json", "members.json", "report.json"}
    if manifest.get("schema_version") != "bounded-backtest-candidate-v1" or set(manifest.get("files", {})) != expected_files:
        raise ValueError("candidate_manifest_invalid")
    for name, digest in manifest["files"].items():
        if sha(safe(root, candidate / name)) != digest: raise ValueError("candidate_hash_invalid")
    report = read(root, candidate / "report.json")
    if report.get("symbols") != list(SYMBOLS) or report.get("qualified") is not False or report.get("formal_backtest_enabled") is not False:
        raise ValueError("candidate_scope_or_gate_invalid")
    bars = json.loads((candidate / "bars.json").read_text())
    calendar = json.loads((candidate / "calendar.json").read_text())
    actual = {(b["symbol"], b["session_date"]) for b in bars}
    sessions = {s["session_date"] for s in calendar}
    expected = {(s, d) for s in SYMBOLS for d in sessions}
    if not actual or len(actual) != len(bars) or not actual <= expected or report["rows"] != len(bars) or report["expected_rows"] != len(expected):
        raise ValueError("candidate_row_contract_invalid")
    missing = len(expected - actual)
    inputs = report.get("inputs")
    if not isinstance(inputs, list) or not inputs: raise ValueError("source_inputs_missing")
    source_rows = {}
    for item in inputs:
        snapshot = safe(root, root / item["relative_path"])
        if sha(snapshot / "manifest.json") != item["manifest_sha256"]: raise ValueError("source_manifest_hash_invalid")
        captured, frame = _load_snapshot(root, snapshot, date.fromisoformat(item["session_date"]))
        if captured["response_sha256"] != item["response_sha256"] or captured["normalized_sha256"] != item["normalized_sha256"]:
            raise ValueError("source_hash_invalid")
        for row in frame.loc[frame.symbol.isin(SYMBOLS)].to_dict("records"):
            source_rows[(item["relative_path"], row["symbol"], item["session_date"])] = (row, captured["observed_at"])
    for bar in bars:
        original, observed = source_rows[(bar["source_snapshot"], bar["symbol"], bar["session_date"])]
        if any(original[k] != bar[k] for k in ("open", "high", "low", "close", "volume")) or datetime.fromisoformat(observed) != datetime.fromisoformat(bar["available_at"]):
            raise ValueError("frozen_row_source_mismatch")
    ref = report["reference"]
    ref_path = safe(root, root / ref["relative_path"])
    if sha(ref_path / "manifest.json") != ref["manifest_sha256"]: raise ValueError("reference_hash_invalid")
    _, ref_manifest = _load_reference(ref_path)
    if ref_manifest["all_tickers_sha256"] != ref["all_tickers_sha256"]: raise ValueError("reference_hash_invalid")
    action_manifest = read(root, actions / "manifest.json")
    if action_manifest.get("schema_version") != "bounded-actions-evidence-v1" or sha(safe(root, actions / "report.json")) != action_manifest.get("report_sha256"):
        raise ValueError("actions_report_hash_invalid")
    action_report = read(root, actions / "report.json")
    records = action_report.get("records")
    if not isinstance(records, list) or action_report.get("range") != report.get("range"):
        raise ValueError("actions_scope_invalid")
    observed_queries = set()
    for row in records:
        key = (row.get("symbol"), row.get("kind"))
        if key[0] not in SYMBOLS or key[1] not in {"splits", "dividends"} or key in observed_queries:
            raise ValueError("action_query_scope_invalid")
        observed_queries.add(key)
        raw = safe(root, actions / row["raw_file"])
        if sha(raw) != row["sha256"]: raise ValueError("actions_raw_hash_invalid")
        if row.get("status") == "captured":
            events = validate_actions(read(root, raw), key[0], key[1], date.fromisoformat(report["range"]["start"]), date.fromisoformat(report["range"]["end"]))
            if events != row.get("events") or len(events) != row.get("returned"): raise ValueError("actions_row_contract_invalid")
    queries = sum(r.get("status") == "captured" for r in records)
    events = sum(r.get("returned", 0) for r in records)
    checks = [
        {"code": "coverage", "label": "固定副本与交易日覆盖", "status": "passed" if not missing else "blocked", "detail": f"五股应有{len(expected)}条、实际{len(bars)}条，缺{missing}个股票交易日；不代表全市场。"},
        {"code": "calendar", "label": "交易日历版本", "status": "partial", "detail": "已冻结XNYS日历表和版本；日历边界不等于独立验证的行情事件时间。"},
        {"code": "availability", "label": "历史可用时点（PIT）", "status": "blocked", "detail": "旧行情实际下载在历史决策之后，不能回填available_at；当前副本不得用于历史PIT收益。"},
        {"code": "identity", "label": "证券历史身份与有效期", "status": "blocked", "detail": "当前证券reference已留存，不是整个历史区间的生命周期证明。"},
        {"code": "actions", "label": "公司行为与退市适用性", "status": "partial", "detail": f"拆股/分红查询成功{queries}/10，返回{events}个事件；空响应、单个发行人核对不证明全范围完整。"},
        {"code": "license", "label": "数据使用范围", "status": "blocked", "detail": "账户授权查询已有证据，数据使用许可仍需独立登记审查。"},
        {"code": "execution", "label": "真实运行器、成本与账务", "status": "blocked", "detail": "费用、限价和分红应收已通过工程黄金测试；未验收真实行情运行器适配。"},
    ]
    result = {"schema_version": SCHEMA, "available": True, "qualified": False, "formal_backtest_available": False,
              "snapshot_id": report["snapshot_id"], "assessed_at": datetime.now(timezone.utc).isoformat(),
              "range": report["range"], "symbols": list(SYMBOLS), "checks": checks,
              "evidence": {"candidate_manifest_sha256": sha(candidate / "manifest.json"), "actions_manifest_sha256": sha(actions / "manifest.json")}}
    output = root / "reference/backtest-readiness" / uuid4().hex
    output.mkdir(parents=True, exist_ok=False)
    _atomic_json(output / "report.json", result)
    _atomic_json(root / "catalogue/backtest-data-readiness-v1.json", {"schema_version": SCHEMA,
                 "report_relative_path": (output / "report.json").relative_to(root).as_posix(), "sha256": sha(output / "report.json")})
    return result


def public_readiness(root: Path | None = None) -> dict:
    root = root or Path(os.getenv("QUANT_DATA_ROOT", "/home/davidou/quant/data"))
    try:
        pointer = read(root, root / "catalogue/backtest-data-readiness-v1.json", 4096)
        if pointer.get("schema_version") != SCHEMA: raise ValueError("invalid_pointer")
        relative = Path(pointer["report_relative_path"])
        if relative.is_absolute() or ".." in relative.parts: raise ValueError("invalid_pointer")
        path = safe(root, root / relative)
        if sha(path) != pointer["sha256"]: raise ValueError("invalid_report_hash")
        result = read(root, path, 32768)
        if result.get("schema_version") != SCHEMA or result.get("qualified") is not False or result.get("formal_backtest_available") is not False or result.get("symbols") != list(SYMBOLS):
            raise ValueError("invalid_report_scope")
        checks = result["checks"]
        if not isinstance(checks, list) or [c["code"] for c in checks] != list(CODES): raise ValueError("invalid_checks")
        if any(not isinstance(c, dict) or c.get("status") not in {"passed", "partial", "blocked"} or not isinstance(c.get("label"), str) or not isinstance(c.get("detail"), str) for c in checks): raise ValueError("invalid_checks")
        return {k: result[k] for k in ("schema_version", "available", "qualified", "formal_backtest_available", "snapshot_id", "assessed_at", "range", "symbols", "checks")}
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return {"available": False, "qualified": False, "formal_backtest_available": False, "message": "真实数据验收记录缺失或校验失败；保持回测阻断。"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--actions", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(review(args.data_root, args.candidate, args.actions)))
