"""Read-only views of the one accepted synthetic backtest artifact.

This module deliberately does not run backtests or read market data.  It turns
already-published ``backtest-report.json`` artifacts into a narrow, auditable
UI/API contract.  Paths are derived from the job id and never accepted from an
API caller.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

from .backtest import (
    BACKTEST_SCHEMA_VERSION,
    NAUTILUS_VERSION,
    REFERENCE_ACCOUNTING_IMPLEMENTATION,
    REFERENCE_RUNNER,
    SYNTHETIC_CALENDAR_VERSION,
    SYNTHETIC_DATASET_VERSION,
    SYNTHETIC_UNIVERSE_VERSION,
)
from .jobs import JobStore


REPORT_SCHEMA_VERSION = "p3-05-synthetic-report-v1"
_JOB_ID = re.compile(r"^[0-9a-f]{8}-(?:[0-9a-f]{4}-){3}[0-9a-f]{12}$", re.IGNORECASE)
_ARTIFACT_NAME = "backtest-report.json"
_ARTIFACT_KIND = "synthetic_backtest_report"
_MAX_ARTIFACT_BYTES = 512 * 1024


class BacktestReportError(ValueError):
    """Raised for unavailable, malformed, or unauthorised report artifacts."""


@dataclass(frozen=True)
class _Source:
    job: Mapping[str, Any]
    payload: Mapping[str, Any]
    artifact_sha256: str


def list_reports(store: JobStore, limit: int = 50) -> list[dict[str, Any]]:
    """Return summaries for published synthetic artifacts only."""
    items: list[dict[str, Any]] = []
    for job in store.list_jobs(limit=max(1, min(limit, 100))):
        if not _is_synthetic_report_job(job):
            continue
        try:
            report = _read(store, str(job["id"]))
            items.append(_summary(report))
        except BacktestReportError as exc:
            # A published index must not make an unverified file look like a
            # report.  Keep the job identifiable while exposing no path/data.
            items.append({"job_id": job["id"], "available": False, "reason": str(exc)})
    return items


def get_report(store: JobStore, job_id: str) -> dict[str, Any]:
    return _view(_read(store, job_id))


def compare_reports(store: JobStore, left_job_id: str, right_job_id: str) -> dict[str, Any]:
    """Compare only reports with the same economic/accounting contract.

    A 200 ``comparable: false`` is intentional: it lets the UI explain exactly
    why a selection is not comparable without manufacturing a difference.
    """
    left, right = _read(store, left_job_id), _read(store, right_job_id)
    left_view, right_view = _view(left), _view(right)
    fields = (
        ("数据快照", "contract.dataset_version"),
        ("资产池", "contract.asset_pool_version"),
        ("日历", "contract.calendar_version"),
        ("回测引擎合同", "contract.engine_contract"),
        ("价格口径", "contract.price_basis"),
        ("资金币种", "contract.currency"),
        ("日期区间", "contract.date_range"),
        ("合成日线输入", "contract.bars_sha256"),
        ("初始资金", "contract.initial_cash"),
        ("订单与成本模型", "contract.execution_contract"),
    )
    differences = [label for label, path in fields if _path(left_view, path) != _path(right_view, path)]
    response: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "left": _summary(left),
        "right": _summary(right),
        "comparable": not differences,
        "non_comparable_reasons": differences,
        "comparison_scope": "仅同一合成验收合同的终值账本对比；不构成真实回测或风险/基准分析。",
    }
    if differences:
        return response
    left_metrics, right_metrics = left_view["metrics"], right_view["metrics"]
    response["differences"] = {
        "terminal_equity": _sub(right_metrics["terminal_equity"], left_metrics["terminal_equity"]),
        "total_pnl": _sub(right_metrics["total_pnl"], left_metrics["total_pnl"]),
        "total_return_pct": _sub(right_metrics["total_return_pct"], left_metrics["total_return_pct"]),
        "fees": _sub(right_metrics["fees"], left_metrics["fees"]),
    }
    response["version_differences"] = _version_differences(left_view, right_view)
    return response


def _read(store: JobStore, job_id: str) -> _Source:
    if not isinstance(job_id, str) or not _JOB_ID.fullmatch(job_id):
        raise BacktestReportError("invalid report job id")
    job = store.get_job(job_id)
    if not job or not _is_synthetic_report_job(job):
        raise BacktestReportError("synthetic backtest report is not published for this job")
    expected = {"name": _ARTIFACT_NAME, "kind": _ARTIFACT_KIND, "job_id": job_id}
    if expected not in job.get("artifacts", []):
        raise BacktestReportError("published artifact index does not authorise this report")
    path = _artifact_path(store, job_id)
    try:
        raw = path.read_bytes()
        if len(raw) > _MAX_ARTIFACT_BYTES:
            raise BacktestReportError("report artifact is missing or exceeds the read-only size limit")
        payload = json.loads(raw)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BacktestReportError("published report artifact is unreadable") from exc
    if not isinstance(payload, Mapping):
        raise BacktestReportError("published report artifact must be a JSON object")
    _validate_payload(job, payload)
    return _Source(job=job, payload=payload, artifact_sha256=hashlib.sha256(raw).hexdigest())


def _artifact_path(store: JobStore, job_id: str) -> Path:
    # No caller-controlled filename/path. resolve() also rejects any future
    # symlink/traversal surprise before bytes are read.
    root = store.artifacts.resolve()
    directory = root / job_id
    candidate_source = directory / _ARTIFACT_NAME
    if directory.is_symlink() or candidate_source.is_symlink():
        raise BacktestReportError("report artifact symlinks are not allowed")
    candidate = candidate_source.resolve()
    if root not in candidate.parents or candidate.name != _ARTIFACT_NAME:
        raise BacktestReportError("report artifact path is outside the approved store")
    try:
        if not candidate.is_file() or candidate.stat().st_size > _MAX_ARTIFACT_BYTES:
            raise BacktestReportError("report artifact is missing or exceeds the read-only size limit")
    except OSError as exc:
        raise BacktestReportError("published report artifact is unreadable") from exc
    return candidate


def _is_synthetic_report_job(job: Mapping[str, Any]) -> bool:
    return job.get("kind") == "backtest" and job.get("operation") in {"synthetic_daily_limit", "synthetic_signal_daily"} and job.get("status") == "succeeded"


def _validate_payload(job: Mapping[str, Any], payload: Mapping[str, Any]) -> None:
    if job.get("operation") == "synthetic_signal_daily":
        from .signal_backtest import run_from_job
        try:
            expected = run_from_job(job["parameters"], job["strategy"], job["data_snapshot"])
        except (ValueError, TypeError, KeyError) as exc:
            raise BacktestReportError("signal artifact input contract is invalid") from exc
        expected.update({"job_id": job["id"], "code_version": job["code_version"], "created_at": job["created_at"]})
        if dict(payload) != expected:
            raise BacktestReportError("signal artifact does not match its fixed input and reconciled replay")
        return
    if payload.get("schema_version") != BACKTEST_SCHEMA_VERSION or payload.get("status") != "succeeded_synthetic_only":
        raise BacktestReportError("artifact is not the accepted synthetic backtest report")
    if payload.get("job_id") != job.get("id") or payload.get("code_version") != job.get("code_version") or payload.get("created_at") != job.get("created_at"):
        raise BacktestReportError("artifact job ownership or code version does not match its index")
    provenance, engine, execution, ledger = (_mapping(payload, name) for name in ("provenance", "engine", "execution", "ledger"))
    expected_provenance = {
        "dataset_version": SYNTHETIC_DATASET_VERSION,
        "asset_pool_version": SYNTHETIC_UNIVERSE_VERSION,
        "calendar_version": SYNTHETIC_CALENDAR_VERSION,
        "price_basis": "raw",
        "corporate_actions": "not_applicable",
    }
    if any(provenance.get(key) != value for key, value in expected_provenance.items()):
        raise BacktestReportError("artifact provenance is not the accepted synthetic contract")
    if engine.get("name") != REFERENCE_RUNNER or engine.get("version") != NAUTILUS_VERSION or execution.get("fee_model") != "USD 1 once on first fill":
        raise BacktestReportError("artifact engine or currency contract is not accepted")
    _validate_engine_provenance(engine)
    for key in ("initial_cash", "free_cash", "position", "mark_price", "unrealized_pnl", "fees", "total_pnl"):
        _decimal(ledger.get(key), f"ledger.{key}")
    fills = payload.get("fills")
    if not isinstance(fills, list) or not isinstance(payload.get("report_hash"), str):
        raise BacktestReportError("artifact lacks a verifiable ledger or report hash")
    # The P3-03 generator hashes its payload before durable job metadata is
    # appended. Verify that exact legacy contract as well as the full-file hash
    # exposed to callers.
    hash_input = dict(payload)
    for key in ("job_id", "code_version", "created_at", "report_hash"):
        hash_input.pop(key, None)
    expected_hash = hashlib.sha256(json.dumps(hash_input, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    if payload["report_hash"] != expected_hash:
        raise BacktestReportError("artifact report hash verification failed")
    initial, cash, position, mark, unrealized, fees, pnl = (_decimal(ledger[key], f"ledger.{key}") for key in ("initial_cash", "free_cash", "position", "mark_price", "unrealized_pnl", "fees", "total_pnl"))
    if cash + position * mark != initial + pnl:
        raise BacktestReportError("artifact ledger does not reconcile to terminal equity")
    if initial <= 0 or cash < 0 or position < 0 or fees < 0:
        raise BacktestReportError("artifact ledger has invalid synthetic account values")
    fill_quantity = Decimal("0")
    fill_cost = Decimal("0")
    fill_fees = Decimal("0")
    for index, fill in enumerate(fills):
        if not isinstance(fill, Mapping):
            raise BacktestReportError("artifact fill is not an object")
        quantity, price, fee = (_decimal(fill.get(key), f"fills[{index}].{key}") for key in ("quantity", "price", "fee_usd"))
        if quantity < 0 or price <= 0 or fee < 0:
            raise BacktestReportError("artifact fill has invalid quantity, price, or fee")
        fill_quantity += quantity; fill_cost += quantity * price; fill_fees += fee
    if fill_quantity != position or fill_fees != fees or initial - cash != fill_cost + fill_fees:
        raise BacktestReportError("artifact fills do not reconcile to cash, position, and fees")
    if unrealized != position * mark - fill_cost or pnl != unrealized - fees:
        raise BacktestReportError("artifact PnL does not reconcile to fills and mark")


def _view(source: _Source) -> dict[str, Any]:
    if source.job.get("operation") == "synthetic_signal_daily":
        return _signal_view(source)
    job, report = source.job, source.payload
    ledger, execution, provenance = (_mapping(report, name) for name in ("ledger", "execution", "provenance"))
    bars, bars_sha256 = _bars_and_hash(job)
    initial, cash, position, mark, fees, pnl = (_decimal(ledger[key], f"ledger.{key}") for key in ("initial_cash", "free_cash", "position", "mark_price", "fees", "total_pnl"))
    terminal = cash + position * mark
    return_pct = (pnl / initial * Decimal("100"))
    symbol = _symbol(job)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "job_id": job["id"],
        "available": True,
        "label": "合成验收报告（非真实回测）",
        "artifact": {"name": _ARTIFACT_NAME, "sha256": source.artifact_sha256, "sha256_status": "computed_read_time_only", "report_hash": report["report_hash"], "report_hash_verified": True},
        "contract": {
            "dataset_version": provenance["dataset_version"],
            "asset_pool_version": provenance["asset_pool_version"],
            "calendar_version": provenance["calendar_version"],
            "price_basis": provenance["price_basis"],
            "currency": "USD",
            "date_range": {"start": bars[0], "end": bars[-1]},
            "bars_sha256": bars_sha256,
            "initial_cash": _string(initial),
            "corporate_actions": provenance["corporate_actions"],
            "engine_version": _mapping(report, "engine")["version"],
            "engine_contract": _engine_contract(_mapping(report, "engine")),
            "execution_contract": _execution_contract(execution),
        },
        "versions": {"backtest_schema": report["schema_version"], "engine": _public_engine(_mapping(report, "engine")), "code_version": job["code_version"], "input_fingerprint": _input_fingerprint(job), "strategy": job["strategy"]},
        "metrics": {
            "initial_cash": _string(initial), "free_cash": _string(cash), "terminal_equity": _string(terminal),
            "total_pnl": _string(pnl), "total_return_pct": _string(return_pct), "fees": _string(fees),
            "unrealized_pnl": _string(_decimal(ledger["unrealized_pnl"], "ledger.unrealized_pnl")),
        },
        "holdings": [{"symbol": symbol, "quantity": _string(position), "mark_price": _string(mark), "market_value": _string(position * mark), "price_basis": "raw"}],
        "fills": report["fills"],
        "execution": execution,
        "warnings": list(report.get("limitations", [])) + [
            "无净值序列、最大回撤、波动率、Sharpe、基准或风险指标；这些字段在工件中不存在，未被推导。",
            "所有金额仅适用于固定合成 ACME 验收输入，不是历史收益、账户余额或可交易结果。",
        ],
        "created_at": job.get("created_at"),
    }


def _signal_view(source: _Source) -> dict[str, Any]:
    job, report = source.job, source.payload
    ledger, provenance, execution = report["ledger"], report["provenance"], report["execution"]
    curve = report["equity_curve"]
    engine = report["engine"]
    mark, quantity = Decimal(ledger["mark_price"]), Decimal(ledger["position"])
    return {
        "schema_version": REPORT_SCHEMA_VERSION, "job_id": job["id"], "available": True,
        "label": "合成策略闭环验收（非真实历史收益）",
        "artifact": {"name": _ARTIFACT_NAME, "sha256": source.artifact_sha256, "sha256_status": "computed_read_time_only", "report_hash": report["report_hash"], "report_hash_verified": True},
        "contract": {"dataset_version": provenance["dataset_version"], "asset_pool_version": provenance["asset_pool_version"],
                     "calendar_version": provenance["calendar_version"], "price_basis": "raw", "currency": "USD",
                     "date_range": {"start": curve[0]["day"], "end": curve[-1]["day"]}, "bars_sha256": provenance["bars_sha256"],
                     "initial_cash": ledger["initial_cash"], "corporate_actions": "not_applicable", "engine_version": engine["version"],
                     "engine_contract": engine, "execution_contract": execution},
        "versions": {"backtest_schema": report["schema_version"], "engine": engine, "code_version": job["code_version"], "input_fingerprint": _input_fingerprint(job), "strategy": job["strategy"]},
        "metrics": report["metrics"],
        "holdings": [{"symbol": "ACME", "quantity": ledger["position"], "mark_price": ledger["mark_price"], "market_value": _string(quantity * mark), "price_basis": "raw"}],
        "fills": report["fills"], "execution": execution, "warnings": report["limitations"],
        "equity_curve": curve, "signals": report["signals"], "orders": report["orders"], "created_at": job["created_at"],
    }


def _summary(source: _Source) -> dict[str, Any]:
    view = _view(source)
    return {key: view[key] for key in ("job_id", "available", "label", "contract", "metrics", "versions", "created_at")}


def _bars_and_hash(job: Mapping[str, Any]) -> tuple[list[str], str]:
    parameters = _mapping(job, "parameters")
    raw = parameters.get("bars")
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or not raw:
        raise BacktestReportError("job has no fixed synthetic date range")
    dates: list[str] = []
    for entry in raw:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("day"), str):
            raise BacktestReportError("job has malformed synthetic bars")
        try:
            dates.append(date.fromisoformat(entry["day"]).isoformat())
        except ValueError as exc:
            raise BacktestReportError("job has malformed synthetic bar date") from exc
    if dates != sorted(dates) or len(set(dates)) != len(dates):
        raise BacktestReportError("job synthetic bar dates are not a fixed chronological range")
    try:
        canonical = json.dumps(raw, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise BacktestReportError("job synthetic bars cannot be fingerprinted") from exc
    return dates, hashlib.sha256(canonical.encode()).hexdigest()


def _execution_contract(execution: Mapping[str, Any]) -> dict[str, Any]:
    """The order/cost assumptions that must match before PnL is comparable."""
    keys = ("order_type", "side", "limit_price", "requested_quantity", "fee_model", "slippage", "volume_constraint", "fill_time")
    values = {key: execution.get(key) for key in keys}
    if any(not isinstance(value, str) or not value for value in values.values()):
        raise BacktestReportError("artifact has incomplete execution contract")
    return values


def _validate_engine_provenance(engine: Mapping[str, Any]) -> None:
    """Accept immutable legacy reports, but fail closed for partial new claims."""
    provenance_keys = (
        "execution_implementation", "reference_runner",
        "reference_runner_version", "runner_invoked",
    )
    present = [key in engine for key in provenance_keys]
    if not any(present):
        return
    if not all(present):
        raise BacktestReportError("artifact engine provenance is incomplete")
    if (
        engine.get("execution_implementation") != REFERENCE_ACCOUNTING_IMPLEMENTATION
        or engine.get("reference_runner") != REFERENCE_RUNNER
        or engine.get("reference_runner_version") != NAUTILUS_VERSION
        or engine.get("runner_invoked") is not False
    ):
        raise BacktestReportError("artifact engine provenance is not accepted")


def _engine_contract(engine: Mapping[str, Any]) -> dict[str, Any]:
    """Return comparison-safe execution identity without rewriting old artifacts."""
    if "execution_implementation" not in engine:
        return {
            "execution_implementation": "legacy-unknown",
            "engine_version": engine["version"],
            "runner_invoked": False,
        }
    return {
        "execution_implementation": engine["execution_implementation"],
        "engine_version": engine["version"],
        "reference_runner": engine["reference_runner"],
        "reference_runner_version": engine["reference_runner_version"],
        "runner_invoked": engine["runner_invoked"],
    }


def _input_fingerprint(job: Mapping[str, Any]) -> str:
    """Fingerprint all persisted, execution-relevant job input, including strategy."""
    input_value = {"data_snapshot": job["data_snapshot"], "strategy": job["strategy"], "parameters": job["parameters"]}
    return hashlib.sha256(json.dumps(input_value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _symbol(job: Mapping[str, Any]) -> str:
    strategy = _mapping(job, "strategy")
    parameters = _mapping(strategy, "parameters")
    symbol = parameters.get("symbol")
    if not isinstance(symbol, str) or not symbol:
        raise BacktestReportError("job has no approved synthetic symbol")
    return symbol


def _mapping(value: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    item = value.get(key)
    if not isinstance(item, Mapping):
        raise BacktestReportError(f"artifact lacks {key} object")
    return item


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise BacktestReportError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise BacktestReportError(f"{field} must be a finite decimal")
    return result


def _string(value: Decimal) -> str:
    return format(value.normalize(), "f") if value else "0"


def _sub(right: Any, left: Any) -> str:
    return _string(_decimal(right, "comparison") - _decimal(left, "comparison"))


def _path(value: Mapping[str, Any], dotted: str) -> Any:
    current: Any = value
    for part in dotted.split("."):
        if not isinstance(current, Mapping):
            return None
        current = current.get(part)
    return current


def _version_differences(left: Mapping[str, Any], right: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    pairs = {"code_version": (left["versions"]["code_version"], right["versions"]["code_version"]), "input_fingerprint": (left["versions"]["input_fingerprint"], right["versions"]["input_fingerprint"]), "strategy": (left["versions"]["strategy"], right["versions"]["strategy"]), "engine": (left["versions"]["engine"], right["versions"]["engine"])}
    return {name: {"left": values[0], "right": values[1]} for name, values in pairs.items() if values[0] != values[1]}


def _public_engine(engine: Mapping[str, Any]) -> dict[str, Any]:
    """Expose truthfully-labelled provenance while preserving old artifacts."""
    contract = _engine_contract(engine)
    if contract["execution_implementation"] == "legacy-unknown":
        return {
            "name": "参考账务验收程序（旧报告）",
            "version": "unknown",
            "execution_implementation": "legacy-unknown",
            "reference_runner": engine.get("name"),
            "reference_runner_version": engine.get("version"),
            "runner_invoked": False,
            "verified_scope": "P2-05 synthetic daily buy-limit partial-fill/cancel",
        }
    return {
        "name": "参考账务验收程序",
        "version": contract["execution_implementation"],
        "execution_implementation": contract["execution_implementation"],
        "reference_runner": contract["reference_runner"],
        "reference_runner_version": contract["reference_runner_version"],
        "runner_invoked": False,
        "verified_scope": "P2-05 synthetic daily buy-limit partial-fill/cancel",
    }
