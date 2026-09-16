"""Deliberately narrow P3-03 daily-bar backtest acceptance harness.

This is not a general execution simulator.  It is the one P2-05 proven path:
synthetic US-equity daily bars, buy limit orders, caller-declared partial fills,
and one fixed USD 1 fee per filled order.  Real-data use is fail-closed because
there is no qualified research pool with complete corporate-action applicability.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any, Mapping, Sequence

from .strategies import DataRequirements, ObservedClose, StrategyDefinition, define_strategy, target_weights
from .time_model import MarketEvent


BACKTEST_SCHEMA_VERSION = "p3-03-v1"
NAUTILUS_VERSION = "1.221.0"
SYNTHETIC_DATASET_VERSION = "synthetic-us-daily-v1"
SYNTHETIC_UNIVERSE_VERSION = "synthetic-us-equity-pool-v1"
SYNTHETIC_CALENDAR_VERSION = "synthetic-nyse-calendar-v1"


def public_availability() -> dict[str, Any]:
    """Describe the submit surface without implying that real data is usable.

    This is deliberately a small, static contract.  It exposes no paths,
    accounts, or market-data inventory; the qualified synthetic fixture is the
    only submit-capable option until P3-03A is completed.
    """
    return {
        "formal_backtest_available": False,
        "formal_backtest_reason": (
            "正式美股日线回测未开放：P3-03A 尚未发布具备稳定身份、原始价、可用时点、"
            "公司行为适用性和退市范围的合格资产池。"
        ),
        "synthetic_acceptance": {
            "available": True,
            "operation": "synthetic_daily_limit",
            "dataset_version": SYNTHETIC_DATASET_VERSION,
            "asset_pool_version": SYNTHETIC_UNIVERSE_VERSION,
            "calendar_version": SYNTHETIC_CALENDAR_VERSION,
            "engine_version": NAUTILUS_VERSION,
            "scope": "仅 P2-05 合成美股日线限价部分成交/撤余单验收；不代表真实回测。",
            "limitations": [
                "仅一只合成证券 ACME，固定三根日线与固定时间范围",
                "仅买入限价单；显式 4 股成交、余下 6 股撤销",
                "首次成交固定 USD 1 手续费；无滑点、公司行为或退市总回报",
            ],
        },
    }


class BacktestBlocked(ValueError):
    """Raised before execution when provenance or a claimed rule is not qualified."""


@dataclass(frozen=True)
class AssetPool:
    version: str
    kind: str
    symbols: tuple[str, ...]
    corporate_actions: str

    def __post_init__(self) -> None:
        if self.kind != "synthetic":
            raise BacktestBlocked("real asset pools are blocked: P1-04 is representative-event validation, not a qualified total-return pool")
        if self.version != SYNTHETIC_UNIVERSE_VERSION:
            raise BacktestBlocked("synthetic asset pool version is not qualified")
        if not self.symbols or any(not value or value != value.strip() for value in self.symbols):
            raise BacktestBlocked("asset pool requires non-empty stable synthetic symbols")
        if self.corporate_actions != "not_applicable":
            raise BacktestBlocked("only corporate_actions=not_applicable is qualified for synthetic acceptance bars")


@dataclass(frozen=True)
class DailyLimitBar:
    day: date
    available_time: MarketEvent
    close: Decimal
    limit_fill_price: Decimal
    fill_quantity: Decimal

    def __post_init__(self) -> None:
        for field in ("close", "limit_fill_price", "fill_quantity"):
            value = _decimal(getattr(self, field), field)
            if value < 0 or (field != "fill_quantity" and value <= 0):
                raise ValueError(f"{field} must be positive (fill_quantity may be zero)")
            object.__setattr__(self, field, value)
        if self.available_time.trading_day != self.day:
            raise ValueError("bar event trading_day must equal day")


@dataclass(frozen=True)
class BacktestRequest:
    strategy: StrategyDefinition
    asset_pool: AssetPool
    dataset_version: str
    calendar_version: str
    nautilus_version: str
    initial_cash: Decimal
    limit_price: Decimal
    requested_quantity: Decimal
    bars: tuple[DailyLimitBar, ...]

    def __post_init__(self) -> None:
        if self.nautilus_version != NAUTILUS_VERSION:
            raise BacktestBlocked(f"Nautilus version must be exactly {NAUTILUS_VERSION}")
        if self.dataset_version != SYNTHETIC_DATASET_VERSION:
            raise BacktestBlocked("real or unqualified data version blocked; only synthetic acceptance data is qualified")
        if self.calendar_version != SYNTHETIC_CALENDAR_VERSION:
            raise BacktestBlocked("calendar version is not qualified for synthetic acceptance")
        if self.strategy.data_requirements != DataRequirements(self.dataset_version, self.asset_pool.version, self.calendar_version, price_basis="raw"):
            raise BacktestBlocked("strategy data requirements must exactly match the qualified pool, dataset, calendar, raw price basis")
        for field in ("initial_cash", "limit_price", "requested_quantity"):
            value = _decimal(getattr(self, field), field)
            if value <= 0:
                raise ValueError(f"{field} must be positive")
            object.__setattr__(self, field, value)
        if not self.bars or tuple(sorted(bar.day for bar in self.bars)) != tuple(bar.day for bar in self.bars):
            raise ValueError("bars must be non-empty and strictly chronological")
        if len({bar.day for bar in self.bars}) != len(self.bars):
            raise ValueError("bars must not repeat a trading day")
        if self.strategy.parameters["symbol"] not in self.asset_pool.symbols:
            raise BacktestBlocked("strategy symbol is outside the qualified asset pool")


def run_synthetic_daily_limit(request: BacktestRequest) -> dict[str, Any]:
    """Run the exact accepted limit-fill contract and return an auditable report.

    A fill happens only on a bar whose caller-provided synthetic liquidity is
    positive and whose declared fill price is at or below the buy limit.  The
    first eligible fill charges USD 1 exactly once; remaining quantity is
    cancelled at end of input.  There is no market order, inferred volume,
    slippage, split/dividend handling, or delisting return.
    """
    remaining, cash, position, fees = request.requested_quantity, request.initial_cash, Decimal("0"), Decimal("0")
    fills: list[dict[str, str]] = []
    closes: list[ObservedClose] = []
    decisions: list[dict[str, str]] = []
    for bar in request.bars:
        closes.append(ObservedClose(request.strategy.parameters["symbol"], bar.available_time, bar.close))
        decision = target_weights(request.strategy, decision_time=bar.available_time.available_time, observations=tuple(closes))
        decisions.append({"day": bar.day.isoformat(), "cash_weight": str(decision.cash_weight), "reason": decision.reason})
        # This harness accepts one predeclared long limit order only.  Strategy
        # signals are recorded for reproducibility but cannot create unsupported
        # market/rebalance order semantics.
        if remaining and bar.fill_quantity and bar.limit_fill_price <= request.limit_price:
            quantity = min(remaining, bar.fill_quantity)
            charge = Decimal("1") if not fills else Decimal("0")
            cost = quantity * bar.limit_fill_price + charge
            if cost > cash:
                raise BacktestBlocked("synthetic fill would exceed available cash; margin is not supported")
            cash -= cost; position += quantity; remaining -= quantity; fees += charge
            fills.append({"day": bar.day.isoformat(), "quantity": str(quantity), "price": str(bar.limit_fill_price), "fee_usd": str(charge)})
    mark = request.bars[-1].close
    unrealized = position * mark - (request.initial_cash - cash - fees)
    report = {
        "schema_version": BACKTEST_SCHEMA_VERSION,
        "status": "succeeded_synthetic_only",
        "engine": {"name": "NautilusTrader", "version": NAUTILUS_VERSION, "verified_path": "P2-05 synthetic daily buy-limit partial-fill/cancel"},
        "provenance": {"dataset_version": request.dataset_version, "asset_pool_version": request.asset_pool.version, "calendar_version": request.calendar_version, "price_basis": "raw", "corporate_actions": "not_applicable"},
        "execution": {"order_type": "limit", "side": "buy", "limit_price": str(request.limit_price), "requested_quantity": str(request.requested_quantity), "filled_quantity": str(position), "cancelled_quantity": str(remaining), "fill_time": "per supplied daily synthetic bar", "volume_constraint": "explicit caller-declared fill_quantity only", "fee_model": "USD 1 once on first fill", "slippage": "unsupported"},
        "ledger": {"initial_cash": str(request.initial_cash), "free_cash": str(cash), "position": str(position), "mark_price": str(mark), "unrealized_pnl": str(unrealized), "fees": str(fees), "total_pnl": str(unrealized - fees)},
        "fills": fills, "strategy_decisions": decisions,
        "limitations": ["synthetic data only; formal real-data backtests are blocked", "no market orders or general partial-fill model", "no slippage", "no corporate actions", "no delisting or total-return claim", "no account, broker, network, raw, or derived-data access"],
    }
    report["report_hash"] = hashlib.sha256(json.dumps(report, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return report


def request_from_job(strategy_payload: Mapping[str, Any], parameters: Mapping[str, Any], data_snapshot: str) -> BacktestRequest:
    """Parse the only job payload accepted by P3-03; it accepts no file paths."""
    try:
        requirements = strategy_payload["data_requirements"]
        definition = define_strategy(strategy_payload["template"], strategy_payload["parameters"], DataRequirements(**requirements))
        pool = parameters["asset_pool"]
        asset_pool = AssetPool(pool["version"], pool["kind"], tuple(pool["symbols"]), pool["corporate_actions"])
        bars = tuple(_bar_from_job(item) for item in parameters["bars"])
        return BacktestRequest(definition, asset_pool, data_snapshot, parameters["calendar_version"], parameters["nautilus_version"], parameters["initial_cash"], parameters["limit_price"], parameters["requested_quantity"], bars)
    except (KeyError, TypeError, ValueError) as exc:
        if isinstance(exc, BacktestBlocked):
            raise
        raise BacktestBlocked(f"invalid synthetic backtest payload: {exc}") from exc


def _bar_from_job(item: Mapping[str, Any]) -> DailyLimitBar:
    if not isinstance(item, Mapping):
        raise ValueError("bar must be an object")
    day = date.fromisoformat(item["day"])
    available = datetime.fromisoformat(str(item["available_time"]).replace("Z", "+00:00"))
    if available.tzinfo is None or available.utcoffset().total_seconds() != 0:
        # The acceptance schema carries UTC; reject local/offset timestamps.
        raise ValueError("available_time must be UTC")
    available = available.astimezone(timezone.utc)
    event = MarketEvent(available, available, available, "America/New_York", day, "synthetic-p3-03-job", "v1")
    return DailyLimitBar(day, event, item["close"], item["limit_fill_price"], item["fill_quantity"])


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result
