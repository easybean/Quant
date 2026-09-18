"""Durable, deliberately narrow background job and experiment metadata store.

This is the P3-01 foundation with one P3-03 synthetic acceptance operation.
No market files, accounts, network credentials, or arbitrary user code are
accepted here.  Formal real-data backtests remain blocked.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
from typing import Any, Mapping
from uuid import uuid4

UTC = timezone.utc
JOB_SCHEMA_VERSION = "p3-01-v1"
TERMINAL = {"succeeded", "failed", "cancelled", "blocked"}
ACTIVE = {"queued", "running"}


class JobInputError(ValueError):
    pass


@dataclass(frozen=True)
class Submission:
    kind: str
    operation: str
    strategy: Mapping[str, Any]
    parameters: Mapping[str, Any]
    data_snapshot: str
    code_version: str
    idempotency_key: str

    @classmethod
    def parse(cls, value: Mapping[str, Any], key: str | None) -> "Submission":
        if not isinstance(value, Mapping):
            raise JobInputError("request must be an object")
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise JobInputError("Idempotency-Key is required (1-128 characters)")
        kind, operation = value.get("kind"), value.get("operation")
        if kind not in {"research", "backtest"}:
            raise JobInputError("kind must be research or backtest")
        if kind == "research" and operation not in {"record_metadata", "factor_evaluation"}:
            raise JobInputError("only research operations record_metadata and factor_evaluation are available")
        if kind == "backtest" and operation not in {"synthetic_daily_limit", "synthetic_signal_daily"}:
            raise JobInputError("formal backtest is blocked; only P3-03 synthetic_daily_limit acceptance is available")
        strategy, parameters = value.get("strategy"), value.get("parameters")
        if not isinstance(strategy, Mapping) or not isinstance(parameters, Mapping):
            raise JobInputError("strategy and parameters must be objects")
        snapshot, version = value.get("data_snapshot"), value.get("code_version")
        for field, text in (("data_snapshot", snapshot), ("code_version", version)):
            if not isinstance(text, str) or not text.strip() or text.strip().lower() in {"latest", "current", "unknown"}:
                raise JobInputError(f"{field} must be a fixed, non-empty version")
        _json_object(strategy, "strategy")
        _json_object(parameters, "parameters")
        if kind == "research" and operation == "factor_evaluation":
            _validate_factor_evaluation(strategy, parameters, snapshot)
        if kind == "backtest" and operation == "synthetic_signal_daily":
            try:
                from .signal_backtest import validate_job
                validate_job(parameters, strategy, snapshot)
            except (ValueError, TypeError, KeyError) as exc:
                raise JobInputError(f"signal backtest blocked: {exc}") from exc
        elif kind == "backtest":
            _validate_synthetic_backtest(strategy, parameters, snapshot)
        return cls(kind, operation, dict(strategy), dict(parameters), snapshot.strip(), version.strip(), key.strip())

    def fingerprint(self) -> str:
        body = json.dumps({"kind": self.kind, "operation": self.operation, "strategy": self.strategy, "parameters": self.parameters, "data_snapshot": self.data_snapshot, "code_version": self.code_version}, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(body.encode()).hexdigest()


def state_dir(value: str | Path | None = None) -> Path:
    raw = value or os.getenv("QUANT_WORKBENCH_STATE_DIR")
    if raw:
        return Path(raw).expanduser()
    data_root = os.getenv("QUANT_DATA_ROOT")
    return Path(data_root).expanduser() / "workbench-state" if data_root else Path.cwd() / "data" / "workbench-state"


class JobStore:
    """SQLite store with a lease-based single-process worker claim protocol."""

    def __init__(self, root: str | Path | None = None) -> None:
        self.root = state_dir(root)
        self.db_path = self.root / "jobs.sqlite3"
        self.artifacts = self.root / "artifacts"
        self._lock = threading.Lock()

    def initialize(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(exist_ok=True)
        with self._connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
              kind TEXT NOT NULL, operation TEXT NOT NULL, status TEXT NOT NULL, strategy_json TEXT NOT NULL,
              parameters_json TEXT NOT NULL, data_snapshot TEXT NOT NULL, code_version TEXT NOT NULL,
              failure_code TEXT, failure_message TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
              attempts INTEGER NOT NULL DEFAULT 0, lease_until TEXT, created_at TEXT NOT NULL, started_at TEXT,
              finished_at TEXT, updated_at TEXT NOT NULL, artifact_index_json TEXT
            );
            CREATE TABLE IF NOT EXISTS experiments (
              id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, strategy_json TEXT NOT NULL,
              parameters_json TEXT NOT NULL, data_snapshot TEXT NOT NULL, code_version TEXT NOT NULL,
              status TEXT NOT NULL, artifact_index_json TEXT, created_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(id)
            );
            CREATE INDEX IF NOT EXISTS jobs_status_created ON jobs(status, created_at);
            """)
        self.recover()

    def submit(self, submission: Submission) -> tuple[dict[str, Any], bool]:
        self.initialize()
        now = _now()
        with self._connect() as db:
            existing = db.execute("SELECT * FROM jobs WHERE idempotency_key = ?", (submission.idempotency_key,)).fetchone()
            if existing:
                if existing["fingerprint"] != submission.fingerprint():
                    raise JobInputError("Idempotency-Key was already used for different input")
                return self._public(existing), False
            job_id = str(uuid4())
            fields = (job_id, submission.fingerprint(), submission.idempotency_key, submission.kind, submission.operation, "queued", _dump(submission.strategy), _dump(submission.parameters), submission.data_snapshot, submission.code_version, now, now)
            db.execute("INSERT INTO jobs (id,fingerprint,idempotency_key,kind,operation,status,strategy_json,parameters_json,data_snapshot,code_version,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", fields)
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(row), True

    def list_jobs(self, limit: int = 50) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [self._public(row) for row in rows]

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return self._public(row) if row else None

    def get_result(self, job_id: str) -> dict[str, Any] | None:
        """Return an already published factor report without exposing its path."""
        self.initialize()
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not row or row["operation"] != "factor_evaluation" or row["status"] != "succeeded":
            return None
        report = self.artifacts / job_id / "factor-report.json"
        return json.loads(report.read_text(encoding="utf-8")) if report.exists() else None

    def list_experiments(self, limit: int = 50) -> list[dict[str, Any]]:
        self.initialize()
        with self._connect() as db:
            rows = db.execute("SELECT * FROM experiments ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 100)),)).fetchall()
        return [{"id": r["id"], "job_id": r["job_id"], "strategy": json.loads(r["strategy_json"]), "parameters": json.loads(r["parameters_json"]), "data_snapshot": r["data_snapshot"], "code_version": r["code_version"], "status": r["status"], "artifacts": json.loads(r["artifact_index_json"] or "[]"), "created_at": r["created_at"]} for r in rows]

    def cancel(self, job_id: str) -> dict[str, Any] | None:
        self.initialize()
        now = _now()
        with self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return None
            if row["status"] in TERMINAL:
                return self._public(row)
            if row["status"] == "queued":
                db.execute("UPDATE jobs SET status='cancelled', failure_code='cancelled_by_user', failure_message='cancelled before worker start', finished_at=?, updated_at=? WHERE id=?", (now, now, job_id))
            else:
                db.execute("UPDATE jobs SET cancel_requested=1, updated_at=? WHERE id=?", (now, job_id))
            return self._public(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def recover(self) -> None:
        if not self.db_path.exists():
            return
        now = _now()
        with self._connect() as db:
            rows = db.execute("SELECT id, kind, operation, cancel_requested FROM jobs WHERE status='running'").fetchall()
            for row in rows:
                final = self.artifacts / row["id"] / ("backtest-report.json" if row["kind"] == "backtest" else "factor-report.json" if row["operation"] == "factor_evaluation" else "experiment.json")
                if final.exists():
                    self._mark_succeeded(db, row["id"], final, now)
                elif row["cancel_requested"]:
                    db.execute("UPDATE jobs SET status='cancelled', failure_code='cancelled_by_user', failure_message='cancelled during restart recovery', finished_at=?, updated_at=? WHERE id=?", (now, now, row["id"]))
                else:
                    db.execute("UPDATE jobs SET status='queued', lease_until=NULL, updated_at=? WHERE id=?", (now, row["id"]))

    def claim(self, lease_seconds: int = 30) -> sqlite3.Row | None:
        self.initialize()
        now, lease = _now(), (datetime.now(UTC) + timedelta(seconds=lease_seconds)).isoformat().replace("+00:00", "Z")
        with self._lock, self._connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE status='queued' ORDER BY created_at LIMIT 1").fetchone()
            if not row:
                return None
            db.execute("UPDATE jobs SET status='running', attempts=attempts+1, started_at=COALESCE(started_at, ?), lease_until=?, updated_at=? WHERE id=?", (now, lease, now, row["id"]))
            return db.execute("SELECT * FROM jobs WHERE id=?", (row["id"],)).fetchone()

    def run_once(self) -> bool:
        row = self.claim()
        if row is None:
            return False
        with self._connect() as db:
            if row["cancel_requested"]:
                self._cancel_running(db, row["id"], "cancelled before execution")
                return True
            try:
                final = self._publish_artifact(row)
                if self._cancel_requested(db, row["id"]):
                    self._cancel_running(db, row["id"], "cancelled during execution")
                else:
                    self._mark_succeeded(db, row["id"], final, _now())
            except JobInputError as exc:
                db.execute("UPDATE jobs SET status='failed', failure_code='invalid_research_input', failure_message=?, finished_at=?, updated_at=? WHERE id=?", (str(exc), _now(), _now(), row["id"]))
            except Exception as exc:  # persisted, redacted public error; no traceback/API leakage
                db.execute("UPDATE jobs SET status='failed', failure_code='worker_error', failure_message=?, finished_at=?, updated_at=? WHERE id=?", (f"metadata publication failed: {type(exc).__name__}", _now(), _now(), row["id"]))
        return True

    def _publish_artifact(self, row: sqlite3.Row) -> Path:
        final_dir = self.artifacts / row["id"]
        filename = "backtest-report.json" if row["kind"] == "backtest" else "factor-report.json" if row["operation"] == "factor_evaluation" else "experiment.json"
        final = final_dir / filename
        if final.exists():
            return final
        staging = self.artifacts / f".staging-{row['id']}-{uuid4().hex}"
        staging.mkdir(parents=True)
        if row["kind"] == "backtest":
            if row["operation"] == "synthetic_signal_daily":
                from .signal_backtest import run_from_job
                payload = run_from_job(json.loads(row["parameters_json"]), json.loads(row["strategy_json"]), row["data_snapshot"])
            else:
                from .backtest import request_from_job, run_synthetic_daily_limit
                payload = run_synthetic_daily_limit(request_from_job(json.loads(row["strategy_json"]), json.loads(row["parameters_json"]), row["data_snapshot"]))
            payload.update({"job_id": row["id"], "code_version": row["code_version"], "created_at": row["created_at"]})
        elif row["operation"] == "factor_evaluation":
            from .factor_jobs import validate_request
            from .factors import evaluate_factor
            parameters = json.loads(row["parameters_json"])
            entry = validate_request(row["data_snapshot"], parameters)
            request = {**parameters, "factor_id": parameters["factor_id"], "derived_root": entry["derived_root"], "read_only": True}
            try:
                result = evaluate_factor(request)
            except ValueError as exc:
                raise JobInputError(f"factor evaluation blocked: {exc}") from exc
            coverage = result["coverage"]
            if not any(item["指标"] == "可用于 IC 的日期数" and int(item["数值"]) > 0 for item in coverage):
                raise JobInputError("factor evaluation blocked: insufficient eligible cross-sections after missing values or required fields")
            payload = {"schema_version": "p4-02-v1", "experiment_id": row["id"], "job_id": row["id"], "operation": row["operation"], "factor_formula_version": entry.get("factor_formula_version", "p4-01-v1"), "strategy": json.loads(row["strategy_json"]), "parameters": parameters, "data_snapshot": row["data_snapshot"], "code_version": row["code_version"], "created_at": row["created_at"], "result": _json_safe(result)}
        else:
            payload = {"schema_version": JOB_SCHEMA_VERSION, "experiment_id": row["id"], "job_id": row["id"], "strategy": json.loads(row["strategy_json"]), "parameters": json.loads(row["parameters_json"]), "data_snapshot": row["data_snapshot"], "code_version": row["code_version"], "created_at": row["created_at"], "operation": row["operation"]}
        (staging / filename).write_text(_dump(payload), encoding="utf-8")
        os.replace(staging, final_dir)
        return final

    def _mark_succeeded(self, db: sqlite3.Connection, job_id: str, final: Path, now: str) -> None:
        row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row["kind"] == "backtest": artifacts = [{"name": "backtest-report.json", "kind": "synthetic_backtest_report", "job_id": job_id}]
        elif row["operation"] == "factor_evaluation": artifacts = [{"name": "factor-report.json", "kind": "factor_evaluation_report", "job_id": job_id}]
        else: artifacts = [{"name": "experiment.json", "kind": "reproducibility_record", "job_id": job_id}]
        db.execute("UPDATE jobs SET status='succeeded', lease_until=NULL, artifact_index_json=?, finished_at=?, updated_at=? WHERE id=?", (_dump(artifacts), now, now, job_id))
        db.execute("INSERT OR REPLACE INTO experiments (id,job_id,strategy_json,parameters_json,data_snapshot,code_version,status,artifact_index_json,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (job_id, job_id, row["strategy_json"], row["parameters_json"], row["data_snapshot"], row["code_version"], "published", _dump(artifacts), row["created_at"]))

    def _cancel_requested(self, db: sqlite3.Connection, job_id: str) -> bool:
        return bool(db.execute("SELECT cancel_requested FROM jobs WHERE id=?", (job_id,)).fetchone()[0])

    def _cancel_running(self, db: sqlite3.Connection, job_id: str, message: str) -> None:
        now = _now()
        db.execute("UPDATE jobs SET status='cancelled', lease_until=NULL, failure_code='cancelled_by_user', failure_message=?, finished_at=?, updated_at=? WHERE id=?", (message, now, now, job_id))

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.db_path, timeout=10)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        return db

    @staticmethod
    def _public(row: sqlite3.Row) -> dict[str, Any]:
        return {"id": row["id"], "kind": row["kind"], "operation": row["operation"], "status": row["status"], "strategy": json.loads(row["strategy_json"]), "parameters": json.loads(row["parameters_json"]), "data_snapshot": row["data_snapshot"], "code_version": row["code_version"], "failure": ({"code": row["failure_code"], "message": row["failure_message"]} if row["failure_code"] else None), "cancel_requested": bool(row["cancel_requested"]), "attempts": row["attempts"], "created_at": row["created_at"], "started_at": row["started_at"], "finished_at": row["finished_at"], "artifacts": json.loads(row["artifact_index_json"] or "[]")}


class JobWorker:
    """Bounded daemon worker.  One API process uses at most max_workers threads."""
    def __init__(self, store: JobStore, max_workers: int = 1, poll_seconds: float = 0.25) -> None:
        self.store, self.max_workers, self.poll_seconds = store, max(1, min(max_workers, 4)), poll_seconds
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self._threads:
            return
        self.store.initialize()
        for number in range(self.max_workers):
            thread = threading.Thread(target=self._loop, name=f"quant-job-{number}", daemon=True)
            thread.start(); self._threads.append(thread)

    def stop(self) -> None:
        self._stop.set()
        for thread in self._threads: thread.join(timeout=2)
        self._threads.clear()

    def _loop(self) -> None:
        while not self._stop.is_set():
            if not self.store.run_once(): self._stop.wait(self.poll_seconds)


def _json_object(value: Mapping[str, Any], field: str) -> None:
    try: json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc: raise JobInputError(f"{field} must be JSON-safe and finite") from exc

def _dump(value: Any) -> str: return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)
def _now() -> str: return datetime.now(UTC).isoformat().replace("+00:00", "Z")

def _validate_synthetic_backtest(strategy: Mapping[str, Any], parameters: Mapping[str, Any], snapshot: object) -> None:
    """Validate before persistence; execution repeats it, keeping bad jobs out."""
    try:
        from .backtest import request_from_job
        request_from_job(strategy, parameters, str(snapshot))
    except Exception as exc:
        raise JobInputError(f"synthetic backtest blocked: {exc}") from exc


def _validate_factor_evaluation(strategy: Mapping[str, Any], parameters: Mapping[str, Any], snapshot: object) -> None:
    if strategy.get("template") != "single_factor_evaluation-v1":
        raise JobInputError("factor evaluation requires fixed strategy template single_factor_evaluation-v1")
    try:
        from .factor_jobs import FactorResearchError, validate_request
        validate_request(str(snapshot), parameters)
    except FactorResearchError as exc:
        raise JobInputError(str(exc)) from exc


def _json_safe(value: Any) -> Any:
    """Turn bounded pandas results into JSON without leaking runtime objects."""
    import math
    import pandas as pd
    if isinstance(value, pd.DataFrame):
        copy = value.copy()
        for column in copy.select_dtypes(include=["datetime", "datetimetz"]).columns:
            copy[column] = copy[column].dt.strftime("%Y-%m-%d")
        return _json_safe(copy.where(pd.notna(copy), None).to_dict(orient="records"))
    if isinstance(value, dict): return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)): return [_json_safe(v) for v in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)): return None
    return value
