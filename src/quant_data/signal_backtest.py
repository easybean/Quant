"""Controlled synthetic close-signal / next-session-limit reference runner.

This module is intentionally narrower than a general backtest.  Its public job
surface only runs a versioned, in-module synthetic fixture.  It demonstrates
the signal-to-order timing and accounting boundary without accepting market
files, network data, broker credentials, or corporate-action assumptions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
import hashlib
import json
from typing import Any, Mapping, Sequence

from .accounting import AccountSnapshot, apply_cash_fills
from .daily_execution import CostModel, DailyBar, DailyExecutionError, Order, execute_daily_order
from .strategies import DataRequirements, ObservedClose, StrategyDefinition, define_strategy, target_weights
from .time_model import MarketEvent


UTC = timezone.utc
SIGNAL_BACKTEST_SCHEMA_VERSION = "signal-backtest-v1"
REFERENCE_SIGNAL_VERSION = "reference-signal-daily-v1"
SYNTHETIC_SIGNAL_DATASET_VERSION = "synthetic-signal-daily-v1"
SYNTHETIC_SIGNAL_UNIVERSE_VERSION = "synthetic-signal-us-equity-pool-v1"
SYNTHETIC_SIGNAL_CALENDAR_VERSION = "synthetic-signal-nyse-calendar-v1"
SYNTHETIC_SYMBOL = "ACME"
_IMPLEMENTATION_VERSION = "signal-daily-coordinator-v1"
_DEFAULT_COST = {
    "per_share_commission": "0.01",
    "minimum_commission": "1",
    "spread_bps": "0",
    "slippage_bps": "0",
    "max_participation": "0.10",
}
_DEFAULT_PARAMETERS = {"initial_cash": "1000", "limit_buffer_bps": "100", "cost_model": _DEFAULT_COST}


class SignalBacktestBlocked(ValueError):
    """Raised when a non-reference input would expand this synthetic runner."""


@dataclass(frozen=True)
class SyntheticSignalBar:
    """A versioned raw-price daily bar and its close-observation availability."""

    session: date
    open_time: datetime
    close_time: datetime
    observation_available_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal


@dataclass(frozen=True)
class SignalBacktestConfig:
    strategy: StrategyDefinition
    initial_cash: Decimal
    limit_buffer_bps: Decimal
    cost: CostModel


def _utc(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


# This is a deliberately static reference fixture, not an importable data feed.
REFERENCE_SIGNAL_DAILY_BARS: tuple[SyntheticSignalBar, ...] = (
    SyntheticSignalBar(date(2024, 1, 2), _utc(2024, 1, 2, 14, 30), _utc(2024, 1, 2, 21), _utc(2024, 1, 2, 21, 5), Decimal("100"), Decimal("102"), Decimal("98"), Decimal("100"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 3), _utc(2024, 1, 3, 14, 30), _utc(2024, 1, 3, 21), _utc(2024, 1, 3, 21, 5), Decimal("90"), Decimal("94"), Decimal("85"), Decimal("90"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 4), _utc(2024, 1, 4, 14, 30), _utc(2024, 1, 4, 21), _utc(2024, 1, 4, 21, 5), Decimal("110"), Decimal("115"), Decimal("105"), Decimal("110"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 5), _utc(2024, 1, 5, 14, 30), _utc(2024, 1, 5, 21), _utc(2024, 1, 5, 21, 5), Decimal("120"), Decimal("125"), Decimal("115"), Decimal("120"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 8), _utc(2024, 1, 8, 14, 30), _utc(2024, 1, 8, 21), _utc(2024, 1, 8, 21, 5), Decimal("80"), Decimal("85"), Decimal("75"), Decimal("80"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 9), _utc(2024, 1, 9, 14, 30), _utc(2024, 1, 9, 21), _utc(2024, 1, 9, 21, 5), Decimal("70"), Decimal("75"), Decimal("65"), Decimal("70"), Decimal("1000")),
    SyntheticSignalBar(date(2024, 1, 10), _utc(2024, 1, 10, 14, 30), _utc(2024, 1, 10, 21), _utc(2024, 1, 10, 21, 5), Decimal("75"), Decimal("80"), Decimal("70"), Decimal("75"), Decimal("1000")),
)


def public_availability() -> dict[str, Any]:
    """Expose the fixed submit contract without claiming a real backtest."""
    return {
        "available": True,
        "operation": "synthetic_signal_daily",
        "dataset_version": SYNTHETIC_SIGNAL_DATASET_VERSION,
        "asset_pool_version": SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
        "calendar_version": SYNTHETIC_SIGNAL_CALENDAR_VERSION,
        "fixture_version": REFERENCE_SIGNAL_VERSION,
        "parameters": _json_safe(_DEFAULT_PARAMETERS),
        "parameters_default": _json_safe(_DEFAULT_PARAMETERS),
        "scope": "单一 ACME 合成日线：收盘信号、下一 session DAY 限价、long-only 整股 USD 参考账务。",
        "limitations": list(_limitations()),
    }


def validate_job(
    parameters: Mapping[str, Any], strategy_payload: Mapping[str, Any], data_snapshot: str
) -> SignalBacktestConfig:
    """Parse the intentionally small job payload, without running a backtest.

    The bars are not a job parameter by design: accepting caller data would
    make this reference runner look like a real-data backtest surface.
    """
    if data_snapshot != SYNTHETIC_SIGNAL_DATASET_VERSION:
        raise SignalBacktestBlocked("real or unqualified data snapshot blocked")
    if not isinstance(parameters, Mapping) or not isinstance(strategy_payload, Mapping):
        raise SignalBacktestBlocked("parameters and strategy_payload must be objects")
    allowed = {"initial_cash", "limit_buffer_bps", "cost_model"}
    unknown = set(parameters) - allowed
    if unknown:
        raise SignalBacktestBlocked(f"unsupported synthetic signal parameters: {', '.join(sorted(unknown))}")
    try:
        requirements = strategy_payload["data_requirements"]
        if not isinstance(requirements, Mapping):
            raise ValueError("data_requirements must be an object")
        definition = define_strategy(strategy_payload["template"], strategy_payload["parameters"], DataRequirements(**requirements))
    except (KeyError, TypeError, ValueError) as exc:
        raise SignalBacktestBlocked(f"invalid strategy payload: {exc}") from exc
    expected = DataRequirements(
        SYNTHETIC_SIGNAL_DATASET_VERSION,
        SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
        SYNTHETIC_SIGNAL_CALENDAR_VERSION,
        price_basis="raw",
    )
    if definition.data_requirements != expected:
        raise SignalBacktestBlocked("strategy data requirements must exactly match the fixed synthetic signal fixture")
    if definition.parameters["symbol"] != SYNTHETIC_SYMBOL:
        raise SignalBacktestBlocked("strategy symbol must be fixed synthetic symbol ACME")
    initial_cash = _positive(parameters.get("initial_cash", _DEFAULT_PARAMETERS["initial_cash"]), "initial_cash")
    if initial_cash > Decimal("1000000"):
        raise SignalBacktestBlocked("initial_cash exceeds the synthetic reference limit")
    buffer_bps = _nonnegative(parameters.get("limit_buffer_bps", _DEFAULT_PARAMETERS["limit_buffer_bps"]), "limit_buffer_bps")
    if buffer_bps > Decimal("1000"):
        raise SignalBacktestBlocked("limit_buffer_bps must not exceed 1000")
    raw_cost = parameters.get("cost_model", _DEFAULT_COST)
    if not isinstance(raw_cost, Mapping) or set(raw_cost) != set(_DEFAULT_COST):
        raise SignalBacktestBlocked("cost_model must contain exactly the fixed synthetic cost fields")
    try:
        cost = CostModel(**{name: _decimal(raw_cost[name], f"cost_model.{name}") for name in _DEFAULT_COST})
        _validate_cost_limits(cost)
    except (KeyError, TypeError, ValueError, DailyExecutionError) as exc:
        raise SignalBacktestBlocked(f"invalid cost_model: {exc}") from exc
    return SignalBacktestConfig(definition, initial_cash, buffer_bps, cost)


def run_from_job(
    parameters: Mapping[str, Any], strategy_payload: Mapping[str, Any], data_snapshot: str
) -> dict[str, Any]:
    """Run only the validated, in-module synthetic reference fixture."""
    return _run(validate_job(parameters, strategy_payload, data_snapshot), REFERENCE_SIGNAL_DAILY_BARS)


def _run(config: SignalBacktestConfig, bars: Sequence[SyntheticSignalBar]) -> dict[str, Any]:
    """Pure implementation used by the fixed runner and focused goldens."""
    _validate_config(config)
    checked = tuple(bars)
    _validate_bars(checked)
    account = AccountSnapshot("USD", config.initial_cash)
    observations: list[ObservedClose] = []
    signals: list[dict[str, Any]] = []
    orders: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    equity_curve: list[dict[str, Any]] = []
    pending: dict[str, Any] | None = None

    for index, bar in enumerate(checked):
        daily_bar = DailyBar(bar.session, bar.open_time, bar.close_time, bar.open, bar.high, bar.low, bar.close, bar.volume)
        if pending is not None:
            result = execute_daily_order(account, daily_bar, pending["order"], config.cost)
            order_record = pending["record"]
            order_record.update({
                "day": bar.session.isoformat(),
                "execution_session": bar.session.isoformat(),
                "execution_status": result.status,
                "status": result.status,
                "execution_reason": result.reason,
                "filled_quantity": str(result.filled_quantity),
                "cancelled_quantity": str(result.remaining_quantity),
            })
            if result.cash_fill is not None:
                signed = result.cash_fill.quantity
                fills.append({
                    "day": bar.session.isoformat(), "quantity": str(signed),
                    "side": "buy" if signed > 0 else "sell", "price": str(result.cash_fill.price),
                    "fee_usd": str(result.cash_fill.fee), "order_id": order_record["order_id"],
                })
            account = result.account
            pending = None
        else:
            account = apply_cash_fills(account, (), mark_price=bar.close)

        event = MarketEvent(
            bar.close_time, bar.observation_available_time, bar.observation_available_time,
            "America/New_York", bar.session, REFERENCE_SIGNAL_VERSION, "v1", sequence=index,
        )
        observation = ObservedClose(SYNTHETIC_SYMBOL, event, bar.close)
        observations.append(observation)
        decision = target_weights(config.strategy, decision_time=bar.observation_available_time, observations=tuple(observations))
        target_weight = decision.weights.get(SYNTHETIC_SYMBOL, Decimal("0"))
        signal = {
            "day": bar.session.isoformat(), "decision_time": bar.observation_available_time.isoformat(),
            "target_weight": str(target_weight), "cash_weight": str(decision.cash_weight),
            "reason": decision.reason, "observation_event_time": bar.close_time.isoformat(),
            "observation_available_time": bar.observation_available_time.isoformat(),
        }
        signals.append(signal)
        if index + 1 < len(checked):
            next_bar = checked[index + 1]
            if not bar.observation_available_time < next_bar.open_time:
                raise SignalBacktestBlocked("close observation is not available strictly before the next session open")
            equity_at_signal = account.free_cash + account.position_quantity * bar.close
            desired = _floor_shares(equity_at_signal * target_weight / bar.close)
            delta = desired - account.position_quantity
            if delta:
                side = "buy" if delta > 0 else "sell"
                quantity = abs(delta)
                buffer = config.limit_buffer_bps / Decimal("10000")
                limit = bar.close * (Decimal("1") + buffer if side == "buy" else Decimal("1") - buffer)
                order = Order(side, quantity, limit, bar.observation_available_time)
                order_record = {
                    "order_id": f"signal-{index + 1}", "signal_day": bar.session.isoformat(),
                    "day": bar.session.isoformat(),
                    "side": side, "quantity": str(quantity), "limit_price": str(limit),
                    "requested_quantity": str(quantity),
                    "submitted_at": bar.observation_available_time.isoformat(), "time_in_force": "DAY",
                    "sizing_equity": str(equity_at_signal), "sizing_close": str(bar.close),
                    "target_weight": str(target_weight), "execution_status": "scheduled",
                }
                orders.append(order_record)
                pending = {"order": order, "record": order_record}
                signal["next_session"] = next_bar.session.isoformat()
            else:
                signal["next_session"] = next_bar.session.isoformat()
                signal["order_status"] = "no_rebalance_required"
        else:
            signal["order_status"] = "not_submitted_no_next_session"
        terminal_equity = account.free_cash + account.position_quantity * bar.close
        if terminal_equity != config.initial_cash + account.realized_pnl + account.unrealized_pnl - account.fees:
            raise SignalBacktestBlocked("daily equity ledger reconciliation failed")
        equity_curve.append({
            "day": bar.session.isoformat(), "free_cash": str(account.free_cash),
            "position": str(account.position_quantity), "mark_price": str(bar.close),
            "equity": str(terminal_equity), "nav": str(terminal_equity / config.initial_cash), "realized_pnl": str(account.realized_pnl),
            "unrealized_pnl": str(account.unrealized_pnl), "fees": str(account.fees),
        })

    mark = checked[-1].close
    terminal = account.free_cash + account.position_quantity * mark
    total_pnl = account.realized_pnl + account.unrealized_pnl - account.fees
    if terminal != config.initial_cash + total_pnl:
        raise SignalBacktestBlocked("terminal equity ledger reconciliation failed")
    report = {
        "schema_version": SIGNAL_BACKTEST_SCHEMA_VERSION,
        "status": "succeeded_synthetic_only",
        "provenance": {
            "dataset_version": SYNTHETIC_SIGNAL_DATASET_VERSION,
            "asset_pool_version": SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
            "calendar_version": SYNTHETIC_SIGNAL_CALENDAR_VERSION,
            "fixture_version": REFERENCE_SIGNAL_VERSION, "bars_sha256": _bars_hash(checked),
            "price_basis": "raw", "corporate_actions": "not_applicable",
        },
        "engine": {
            "name": "策略驱动日线参考运行器", "version": REFERENCE_SIGNAL_VERSION,
            "execution_implementation": REFERENCE_SIGNAL_VERSION,
            "coordinator_implementation": _IMPLEMENTATION_VERSION,
            "daily_execution_implementation": "daily_execution.py", "runner_invoked": False,
        },
        "execution": {
            "order_type": "limit", "time_in_force": "DAY",
            "signal_timing": "close decision -> next session", "limit_rule": "prior close +/- fixed buffer",
            "currency": "USD", "long_only": True, "whole_shares": True,
            "limit_buffer_bps": str(config.limit_buffer_bps), "cost_model": _cost_as_dict(config.cost),
        },
        "ledger": {
            "initial_cash": str(config.initial_cash), "free_cash": str(account.free_cash),
            "position": str(account.position_quantity), "average_entry_price": str(account.average_entry_price),
            "mark_price": str(mark), "realized_pnl": str(account.realized_pnl),
            "unrealized_pnl": str(account.unrealized_pnl), "fees": str(account.fees),
            "total_pnl": str(total_pnl), "terminal_equity": str(terminal), "reconciled": True,
        },
        "signals": signals, "orders": orders, "fills": fills, "equity_curve": equity_curve,
        "metrics": {
            "initial_cash": str(config.initial_cash), "terminal_equity": str(terminal),
            "total_pnl": str(total_pnl), "total_return_pct": str(total_pnl / config.initial_cash * Decimal("100")),
            "free_cash": str(account.free_cash), "fees": str(account.fees),
            "unrealized_pnl": str(account.unrealized_pnl), "fill_count": len(fills), "order_count": len(orders),
        },
        "limitations": list(_limitations()),
    }
    report["report_hash"] = hashlib.sha256(_canonical_json(report).encode()).hexdigest()
    return report


def _validate_bars(bars: Sequence[SyntheticSignalBar]) -> None:
    if not bars:
        raise SignalBacktestBlocked("synthetic signal fixture must contain bars")
    last_day: date | None = None
    for index, bar in enumerate(bars):
        if not isinstance(bar.session, date) or isinstance(bar.session, datetime):
            raise SignalBacktestBlocked("bar.session must be a date")
        if last_day is not None and bar.session <= last_day:
            raise SignalBacktestBlocked("bars must be strictly chronological with no repeated sessions")
        last_day = bar.session
        for field in ("open_time", "close_time", "observation_available_time"):
            _require_utc(getattr(bar, field), f"bar.{field}")
        if not bar.open_time < bar.close_time <= bar.observation_available_time:
            raise SignalBacktestBlocked("bar timestamps must satisfy open < close <= close observation availability")
        if bar.open_time.date() != bar.session or bar.close_time.date() != bar.session:
            raise SignalBacktestBlocked("bar timestamps must be in their declared UTC synthetic session date")
        for field in ("open", "high", "low", "close"):
            _positive(getattr(bar, field), f"bar.{field}")
        _nonnegative(bar.volume, "bar.volume")
        if bar.low > min(bar.open, bar.close) or bar.high < max(bar.open, bar.close) or bar.low > bar.high:
            raise SignalBacktestBlocked("bar OHLC bounds are invalid")
        if index + 1 < len(bars) and not bar.observation_available_time < bars[index + 1].open_time:
            raise SignalBacktestBlocked("close observation must be available strictly before the next session open")


def _validate_cost_limits(cost: CostModel) -> None:
    if not isinstance(cost, CostModel):
        raise SignalBacktestBlocked("cost must be a CostModel")
    for field in ("per_share_commission", "minimum_commission", "spread_bps", "slippage_bps"):
        if not isinstance(getattr(cost, field), Decimal):
            raise SignalBacktestBlocked(f"cost.{field} must be a Decimal")
        _nonnegative(getattr(cost, field), f"cost.{field}")
    if not isinstance(cost.max_participation, Decimal):
        raise SignalBacktestBlocked("cost.max_participation must be a Decimal")
    _positive(cost.max_participation, "cost.max_participation")
    if cost.per_share_commission > Decimal("10") or cost.minimum_commission > Decimal("100"):
        raise SignalBacktestBlocked("synthetic commissions exceed reference limits")
    if cost.spread_bps > Decimal("100") or cost.slippage_bps > Decimal("100"):
        raise SignalBacktestBlocked("synthetic costs exceed reference limits")
    if not Decimal("0") < cost.max_participation <= Decimal("1"):
        raise SignalBacktestBlocked("max_participation must be within (0, 1]")


def _validate_config(config: SignalBacktestConfig) -> None:
    """Keep even the test-only pure runner inside the reference boundaries."""
    if not isinstance(config, SignalBacktestConfig) or not isinstance(config.strategy, StrategyDefinition):
        raise SignalBacktestBlocked("config must contain a StrategyDefinition")
    if not isinstance(config.initial_cash, Decimal) or not isinstance(config.limit_buffer_bps, Decimal):
        raise SignalBacktestBlocked("initial_cash and limit_buffer_bps must be Decimal")
    _positive(config.initial_cash, "initial_cash")
    if config.initial_cash > Decimal("1000000"):
        raise SignalBacktestBlocked("initial_cash exceeds the synthetic reference limit")
    _nonnegative(config.limit_buffer_bps, "limit_buffer_bps")
    if config.limit_buffer_bps > Decimal("1000"):
        raise SignalBacktestBlocked("limit_buffer_bps must not exceed 1000")
    _validate_cost_limits(config.cost)


def _bars_hash(bars: Sequence[SyntheticSignalBar]) -> str:
    items = [{
        "session": bar.session.isoformat(), "open_time": bar.open_time.isoformat(), "close_time": bar.close_time.isoformat(),
        "observation_available_time": bar.observation_available_time.isoformat(), "open": str(bar.open), "high": str(bar.high),
        "low": str(bar.low), "close": str(bar.close), "volume": str(bar.volume),
    } for bar in bars]
    return hashlib.sha256(_canonical_json(items).encode()).hexdigest()


def _cost_as_dict(cost: CostModel) -> dict[str, str]:
    return {name: str(getattr(cost, name)) for name in _DEFAULT_COST}


def _limitations() -> tuple[str, ...]:
    return (
        "synthetic fixture only; real-data backtests, network and broker connections are blocked",
        "single ACME USD long-only whole-share position; no shorting, leverage, FX or financing",
        "raw prices only; no splits, dividends, mergers, delistings or total-return claim",
        "daily OHLC cannot establish intraday path, queue priority or fill certainty",
        "DAY remainders are cancelled and never carried into another session",
    )


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def _decimal(value: Any, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


def _positive(value: Any, field: str) -> Decimal:
    result = _decimal(value, field)
    if result <= 0:
        raise SignalBacktestBlocked(f"{field} must be positive")
    return result


def _nonnegative(value: Any, field: str) -> Decimal:
    result = _decimal(value, field)
    if result < 0:
        raise SignalBacktestBlocked(f"{field} must be non-negative")
    return result


def _require_utc(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() != UTC.utcoffset(value):
        raise SignalBacktestBlocked(f"{field} must be an aware UTC datetime")


def _floor_shares(value: Decimal) -> Decimal:
    return value.to_integral_value(rounding=ROUND_FLOOR)
