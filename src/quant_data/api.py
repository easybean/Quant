"""Small, read-only HTTP surface for the Quant workbench.

The catalogue is assembled from the in-process registry only.  It never
opens market-data files, accepts calculation requests, or exposes runtime
configuration.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import os
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from .factor_catalogue import CATALOGUE_CATEGORIES, availability, detail
from .factors import list_factors
from .data_catalogue import data_catalogue_payload
from .daily_quote_browser import DailyQuoteInputError, DailyQuoteUnavailable, read_us_daily_bars, search_us_daily_series
from .jobs import JobInputError, JobStore, JobWorker, Submission
from .factor_jobs import FactorResearchError, public_availability
from .backtest import public_availability as backtest_public_availability
from .backtest_reports import BacktestReportError, compare_reports, get_report, list_reports
from .strategy_drafts import DraftInputError, StrategyDraftStore, template_catalogue
from .visual_strategy_drafts import VisualStrategyDraftInputError, VisualStrategyDraftStore
from .universe_drafts import AssetPoolDraftStore, InstrumentDraftStore, UniverseDraftInputError
from .risk_policy_drafts import RiskPolicyDraftInputError, RiskPolicyDraftStore
from .paper_ledger import PaperLedgerInputError, PaperLedgerStore
from .data_source_drafts import DataSourceDraftInputError, DataSourceDraftStore
from .security_catalog import SecurityCatalogueStore
from .sync_status import sync_status_payload
from .backtest_readiness import public_readiness

_DEFAULT_ORIGINS = ("http://127.0.0.1:5173", "http://192.168.1.132:8510")
_SCHEMA_VERSION = "v1"


def allowed_origins(value: str | None = None) -> list[str]:
    """Return explicit browser origins from QUANT_UI_ORIGINS or safe defaults."""
    raw = os.getenv("QUANT_UI_ORIGINS") if value is None else value
    candidates = _DEFAULT_ORIGINS if not raw else tuple(part.strip() for part in raw.split(","))
    origins: list[str] = []
    for candidate in candidates:
        parsed = urlsplit(candidate)
        if (
            parsed.scheme in {"http", "https"}
            and parsed.netloc
            and not parsed.username
            and not parsed.password
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
        ):
            origin = f"{parsed.scheme}://{parsed.netloc}"
            if origin not in origins:
                origins.append(origin)
    return origins


def _status(runnable: bool, note: str, limitations: str) -> str:
    if runnable:
        return "可计算"
    if "待校准" in note or "待校准" in limitations:
        return "待校准"
    return "不可运行"


def factor_catalogue_payload() -> dict[str, object]:
    """Normalize the visible registry into a stable JSON-only catalogue."""
    items: list[dict[str, object]] = []
    for record in list_factors():
        factor_id = str(record["id"])
        runnable, note = availability(factor_id)
        documentation = detail(
            {
                "id": factor_id,
                "名称": record.get("display_name", ""),
                "说明": record.get("description", ""),
                "数据要求": record.get("required_columns", []),
                "来源": record.get("source", ""),
                "预期用途": record.get("intended_use", ""),
                "近似重复组": record.get("redundancy_group", ""),
            }
        )
        limitations = str(documentation["limitations"])
        category = str(record["research_family"])
        items.append(
            {
                "id": factor_id,
                "name": str(documentation["name"]),
                "category": category,
                "source": str(record["source"]),
                "measurement_type": str(record["measurement_type"]),
                "tags": [str(tag) for tag in record.get("tags", [])],
                "required_columns": [str(column) for column in record.get("required_columns", [])],
                "intended_use": str(record["intended_use"]),
                "redundancy_group": str(record["redundancy_group"]),
                "description": str(record["description"]),
                "formula": str(documentation["formula"]),
                "purpose": str(documentation["purpose"]),
                "data_requirements": str(documentation["data_requirements"]),
                "limitations": limitations,
                "runnable": runnable,
                "availability_note": note,
                "status": _status(runnable, note, limitations),
            }
        )
    counts = Counter(str(item["category"]) for item in items)
    categories = [{"name": name, "count": counts[name]} for name in CATALOGUE_CATEGORIES if counts[name]]
    return {
        "schema_version": _SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "categories": categories,
        "count": len(items),
        "items": items,
    }


def create_app() -> FastAPI:
    app = FastAPI(title="Quant Workbench API", version=_SCHEMA_VERSION, openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Idempotency-Key", "Content-Type"],
    )
    state_root = os.getenv("QUANT_WORKBENCH_STATE_DIR")
    store = JobStore(state_root)
    worker = JobWorker(store, max_workers=_worker_count())
    app.state.job_store = store
    app.state.job_worker = worker
    draft_store = StrategyDraftStore(state_root)
    draft_store.initialize()
    app.state.strategy_draft_store = draft_store
    instrument_store = InstrumentDraftStore(state_root)
    instrument_store.initialize()
    app.state.instrument_draft_store = instrument_store
    security_catalogue_store = SecurityCatalogueStore(state_root)
    security_catalogue_store.initialize()
    app.state.security_catalogue_store = security_catalogue_store
    asset_pool_store = AssetPoolDraftStore(state_root, instrument_store, security_catalogue_store)
    asset_pool_store.initialize()
    app.state.asset_pool_draft_store = asset_pool_store
    visual_strategy_store = VisualStrategyDraftStore(state_root, asset_pool_store)
    visual_strategy_store.initialize()
    app.state.visual_strategy_draft_store = visual_strategy_store
    risk_policy_store = RiskPolicyDraftStore(state_root)
    risk_policy_store.initialize()
    app.state.risk_policy_draft_store = risk_policy_store
    paper_ledger_store = PaperLedgerStore(state_root)
    paper_ledger_store.initialize()
    app.state.paper_ledger_store = paper_ledger_store
    data_source_store = DataSourceDraftStore(state_root)
    data_source_store.initialize()
    app.state.data_source_draft_store = data_source_store
    @app.on_event("startup")
    def start_jobs() -> None:
        worker.start()

    @app.on_event("shutdown")
    def stop_jobs() -> None:
        worker.stop()

    @app.get("/api/v1/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "schema_version": _SCHEMA_VERSION}

    @app.get("/api/v1/us-daily-sync-status")
    def us_daily_sync_status() -> dict[str, object]:
        return sync_status_payload()

    @app.get("/api/v1/factors")
    def factors() -> dict[str, object]:
        return factor_catalogue_payload()

    @app.get("/api/v1/factor-research/availability")
    def factor_research_availability() -> dict[str, object]:
        try:
            return public_availability()
        except FactorResearchError as exc:
            return {"available": False, "snapshots": [], "message": str(exc)}

    @app.get("/api/v1/backtests/availability")
    def backtest_availability() -> dict[str, object]:
        return backtest_public_availability()

    @app.get("/api/v1/backtests/data-readiness")
    def backtest_data_readiness() -> dict[str, object]:
        return public_readiness()

    @app.get("/api/v1/backtests/reports")
    def backtest_reports(limit: int = Query(default=50, ge=1, le=100)) -> dict[str, object]:
        return {"schema_version": "p3-05-synthetic-report-v1", "items": list_reports(store, limit)}

    @app.get("/api/v1/backtests/reports/compare")
    def compare_backtest_reports(left_job_id: str = Query(min_length=1, max_length=64), right_job_id: str = Query(min_length=1, max_length=64)) -> dict[str, object]:
        try:
            return compare_reports(store, left_job_id, right_job_id)
        except BacktestReportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/backtests/reports/{job_id}")
    def backtest_report(job_id: str) -> dict[str, object]:
        try:
            return get_report(store, job_id)
        except BacktestReportError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/v1/strategy-templates")
    def strategy_templates() -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": template_catalogue()}

    @app.get("/api/v1/security-catalogue")
    def security_catalogue(query: str = Query(default="", max_length=120), limit: int = Query(default=50, ge=1, le=100), offset: int = Query(default=0, ge=0)) -> dict[str, object]:
        return security_catalogue_store.list(query=query, limit=limit, offset=offset)

    @app.get("/api/v1/strategy-drafts")
    def strategy_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": draft_store.list(limit)}

    @app.get("/api/v1/strategy-drafts/{draft_id}/history")
    def strategy_draft_history(draft_id: str) -> dict[str, object]:
        items = draft_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="strategy draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/strategy-drafts", status_code=201)
    async def create_strategy_draft(request: Request) -> dict[str, object]:
        try:
            return draft_store.save(await request.json())
        except DraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/strategy-drafts/{draft_id}")
    async def update_strategy_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return draft_store.save(await request.json(), draft_id)
        except DraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="strategy draft not found") from exc

    @app.get("/api/v1/visual-strategy-drafts")
    def visual_strategy_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": visual_strategy_store.list(limit)}

    @app.get("/api/v1/visual-strategy-drafts/{draft_id}/history")
    def visual_strategy_draft_history(draft_id: str) -> dict[str, object]:
        items = visual_strategy_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="visual strategy draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/visual-strategy-drafts", status_code=201)
    async def create_visual_strategy_draft(request: Request) -> dict[str, object]:
        try:
            return visual_strategy_store.save(await request.json())
        except VisualStrategyDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/visual-strategy-drafts/{draft_id}")
    async def update_visual_strategy_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return visual_strategy_store.save(await request.json(), draft_id)
        except VisualStrategyDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="visual strategy draft not found") from exc

    @app.get("/api/v1/instrument-drafts")
    def instrument_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": instrument_store.list(limit)}

    @app.get("/api/v1/instrument-drafts/{draft_id}/history")
    def instrument_draft_history(draft_id: str) -> dict[str, object]:
        items = instrument_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="instrument draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/instrument-drafts", status_code=201)
    async def create_instrument_draft(request: Request) -> dict[str, object]:
        try:
            return instrument_store.save(await request.json())
        except UniverseDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/instrument-drafts/{draft_id}")
    async def update_instrument_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return instrument_store.save(await request.json(), draft_id)
        except UniverseDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="instrument draft not found") from exc

    @app.get("/api/v1/asset-pool-drafts")
    def asset_pool_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": asset_pool_store.list(limit)}

    @app.get("/api/v1/asset-pool-drafts/{draft_id}/history")
    def asset_pool_draft_history(draft_id: str) -> dict[str, object]:
        items = asset_pool_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="asset pool draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/asset-pool-drafts", status_code=201)
    async def create_asset_pool_draft(request: Request) -> dict[str, object]:
        try:
            return asset_pool_store.save(await request.json())
        except UniverseDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/asset-pool-drafts/{draft_id}")
    async def update_asset_pool_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return asset_pool_store.save(await request.json(), draft_id)
        except UniverseDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="asset pool draft not found") from exc

    @app.get("/api/v1/risk-policy-drafts")
    def risk_policy_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": risk_policy_store.list(limit)}

    @app.get("/api/v1/risk-policy-drafts/{draft_id}/history")
    def risk_policy_draft_history(draft_id: str) -> dict[str, object]:
        items = risk_policy_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="risk policy draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/risk-policy-drafts", status_code=201)
    async def create_risk_policy_draft(request: Request) -> dict[str, object]:
        try:
            return risk_policy_store.save(await request.json())
        except RiskPolicyDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/risk-policy-drafts/{draft_id}")
    async def update_risk_policy_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return risk_policy_store.save(await request.json(), draft_id)
        except RiskPolicyDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="risk policy draft not found") from exc

    @app.get("/api/v1/paper-accounts")
    def paper_accounts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": paper_ledger_store.list_accounts(limit)}

    @app.get("/api/v1/data-source-drafts")
    def data_source_drafts(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": data_source_store.list(limit)}

    @app.get("/api/v1/data-source-drafts/{draft_id}/history")
    def data_source_draft_history(draft_id: str) -> dict[str, object]:
        items = data_source_store.history(draft_id)
        if items is None:
            raise HTTPException(status_code=404, detail="data-source draft not found")
        return {"schema_version": _SCHEMA_VERSION, "items": items}

    @app.post("/api/v1/data-source-drafts", status_code=201)
    async def create_data_source_draft(request: Request) -> dict[str, object]:
        try:
            return data_source_store.save(await request.json())
        except DataSourceDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.put("/api/v1/data-source-drafts/{draft_id}")
    async def update_data_source_draft(draft_id: str, request: Request) -> dict[str, object]:
        try:
            return data_source_store.save(await request.json(), draft_id)
        except DataSourceDraftInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="data-source draft not found") from exc

    @app.post("/api/v1/paper-accounts", status_code=201)
    async def create_paper_account(request: Request) -> dict[str, object]:
        try:
            return paper_ledger_store.create_account(await request.json())
        except PaperLedgerInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/api/v1/paper-accounts/{account_id}/ledger")
    def paper_account_ledger(account_id: str, limit: int = 100) -> dict[str, object]:
        items = paper_ledger_store.events(account_id, limit)
        if items is None:
            raise HTTPException(status_code=404, detail="paper account not found")
        reconciliation = paper_ledger_store.reconciliation(account_id)
        return {"schema_version": _SCHEMA_VERSION, "items": items, "reconciliation": reconciliation}

    @app.get("/api/v1/data-catalogue", response_model=None)
    def data_catalogue() -> dict[str, object] | JSONResponse:
        payload, missing = data_catalogue_payload()
        if payload is None:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "message": "数据目录快照尚不可用", "missing": missing},
            )
        return payload

    @app.get("/api/v1/market-data/us-daily/search", response_model=None)
    def us_daily_search(query: str, limit: int = 10) -> dict[str, object] | JSONResponse:
        try:
            return search_us_daily_series(query, limit)
        except DailyQuoteInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DailyQuoteUnavailable as exc:
            return JSONResponse(status_code=503, content={"status": "unavailable", "message": str(exc)})

    @app.get("/api/v1/market-data/us-daily/bars", response_model=None)
    def us_daily_bars(series_id: str, start: str, end: str) -> dict[str, object] | JSONResponse:
        try:
            return read_us_daily_bars(series_id, start, end)
        except DailyQuoteInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except DailyQuoteUnavailable as exc:
            return JSONResponse(status_code=503, content={"status": "unavailable", "message": str(exc)})

    @app.get("/api/v1/jobs")
    def jobs(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": store.list_jobs(limit)}

    @app.get("/api/v1/jobs/{job_id}")
    def job(job_id: str) -> dict[str, object]:
        result = store.get_job(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return result

    @app.get("/api/v1/jobs/{job_id}/result")
    def job_result(job_id: str) -> dict[str, object]:
        result = store.get_result(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="factor result not published")
        return result

    @app.get("/api/v1/experiments")
    def experiments(limit: int = 50) -> dict[str, object]:
        return {"schema_version": _SCHEMA_VERSION, "items": store.list_experiments(limit)}

    @app.post("/api/v1/jobs", status_code=202)
    async def submit_job(request: Request) -> dict[str, object]:
        try:
            body = await request.json()
            submission = Submission.parse(body, request.headers.get("Idempotency-Key"))
            job, created = store.submit(submission)
        except JobInputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"created": created, "job": job}

    @app.post("/api/v1/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, object]:
        result = store.cancel(job_id)
        if result is None:
            raise HTTPException(status_code=404, detail="job not found")
        return result

    return app


def _worker_count() -> int:
    try:
        return max(1, min(int(os.getenv("QUANT_JOB_WORKERS", "1")), 4))
    except ValueError:
        return 1


app = create_app()
