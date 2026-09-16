"""Versioned, pure strategy definitions for the research/backtest boundary.

This module defines strategy intent only.  It does not read market files, run
an engine, place an order, or know about accounts.  A caller supplies already
point-in-time-safe observations and receives desired portfolio weights.  Run
submission stays blocked until an adapter satisfies the capability registry.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
import json
from typing import Any, Mapping, Sequence

from .market_capabilities import (
    CapabilityRegistry,
    CapabilityStatus,
    DEFAULT_REGISTRY,
    MarketProduct,
    Runner,
    ValidationResult,
)
from .time_model import MarketEvent


UTC = timezone.utc
STRATEGY_SCHEMA_VERSION = "p3-02-v1"
BUY_AND_HOLD_VERSION = "buy-and-hold-v1"
DUAL_MOVING_AVERAGE_VERSION = "dual-moving-average-v1"


class StrategyTemplate(str, Enum):
    BUY_AND_HOLD = "buy_and_hold"
    DUAL_MOVING_AVERAGE = "dual_moving_average"


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    message: str


@dataclass(frozen=True)
class DataRequirements:
    """Versioned data contract consumed by a strategy definition.

    The template intentionally asks for observations carrying ``available_time``;
    it never treats an event timestamp as proof that data was readable then.
    """

    dataset_version: str
    universe_version: str
    calendar_version: str
    bar_frequency: str = "1d"
    price_field: str = "close"
    price_basis: str = "declared"
    availability_field: str = "available_time"

    def __post_init__(self) -> None:
        for field in ("dataset_version", "universe_version", "calendar_version"):
            _require_fixed_version(field, getattr(self, field))
        if self.bar_frequency != "1d":
            raise ValueError("P3-02 templates require daily (1d) bars")
        if self.price_field != "close":
            raise ValueError("P3-02 templates require a declared close price field")
        if not self.price_basis.strip() or self.price_basis.lower() in {"unknown", "unspecified"}:
            raise ValueError("price_basis must be declared, not unknown")
        if self.availability_field != "available_time":
            raise ValueError("availability_field must be available_time to prevent future reads")


@dataclass(frozen=True)
class StrategyDefinition:
    """A validated, immutable template instance; it has no execution authority."""

    template: StrategyTemplate
    template_version: str
    parameters: Mapping[str, Any]
    data_requirements: DataRequirements
    schema_version: str = STRATEGY_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.schema_version != STRATEGY_SCHEMA_VERSION:
            raise ValueError(f"unsupported strategy schema version: {self.schema_version}")
        expected = _template_version(self.template)
        if self.template_version != expected:
            raise ValueError(f"{self.template.value} requires fixed template version {expected}")
        issues = validate_parameters(self.template, self.parameters)
        if issues:
            raise ValueError("invalid strategy parameters: " + "; ".join(f"{item.field}: {item.message}" for item in issues))
        object.__setattr__(self, "parameters", _canonical_parameters(self.template, self.parameters))

    @property
    def required_capabilities(self) -> tuple[str, ...]:
        """Minimum adapter behavior needed for a future daily-bar submission."""
        # P3-03 deliberately chooses only the P2-05-proven limit path. A
        # caller wanting market/rebalance semantics needs new verification.
        return ("bar_frequency:1d", "order_type:limit")

    def run_gate(
        self,
        runner: Runner | str,
        registry: CapabilityRegistry = DEFAULT_REGISTRY,
    ) -> "RunGate":
        result = registry.validate_requirements(runner, MarketProduct.US_EQUITY, self.required_capabilities)
        if result.supported:
            return RunGate(True, "adapter verified", result)
        if result.profile_status is CapabilityStatus.UNVERIFIED or result.unverified:
            return RunGate(False, "adapter unverified: P2-05 daily US-equity adapter acceptance has not passed", result)
        return RunGate(False, "adapter blocked by capability requirements", result)

    def reproducibility_record(self, *, code_version: str, created_at: datetime) -> "ReproducibilityRecord":
        _require_fixed_version("code_version", code_version)
        _require_utc(created_at, "created_at")
        return ReproducibilityRecord(
            schema_version=self.schema_version,
            template=self.template.value,
            template_version=self.template_version,
            parameters=dict(self.parameters),
            data_requirements=self.data_requirements,
            code_version=code_version,
            created_at=created_at,
        )


@dataclass(frozen=True)
class ObservedClose:
    """A caller-provided close observation, bound to its publication metadata."""

    symbol: str
    event: MarketEvent
    close: Decimal

    def __post_init__(self) -> None:
        if not self.symbol or self.symbol != self.symbol.strip():
            raise ValueError("symbol is required and cannot contain surrounding whitespace")
        close = _decimal(self.close, "close")
        if close <= 0:
            raise ValueError("close must be finite and positive")
        object.__setattr__(self, "close", close)


@dataclass(frozen=True)
class TargetWeights:
    """Target portfolio weights, not orders or executable fills."""

    weights: Mapping[str, Decimal]
    cash_weight: Decimal
    as_of: datetime
    reason: str

    def __post_init__(self) -> None:
        _require_utc(self.as_of, "as_of")
        cash = _decimal(self.cash_weight, "cash_weight")
        if cash < 0 or cash > 1:
            raise ValueError("cash_weight must be within [0, 1]")
        normalized: dict[str, Decimal] = {}
        for symbol, weight in self.weights.items():
            if not symbol or symbol != symbol.strip():
                raise ValueError("target symbols are required and cannot contain surrounding whitespace")
            value = _decimal(weight, f"weight for {symbol}")
            if value < 0 or value > 1:
                raise ValueError(f"weight for {symbol} must be within [0, 1]")
            normalized[symbol] = value
        if sum(normalized.values(), cash) != Decimal("1"):
            raise ValueError("target weights plus cash_weight must equal exactly 1")
        if not self.reason:
            raise ValueError("reason is required")
        object.__setattr__(self, "weights", normalized)
        object.__setattr__(self, "cash_weight", cash)


@dataclass(frozen=True)
class RunGate:
    allowed: bool
    reason: str
    capability_result: ValidationResult


@dataclass(frozen=True)
class ReproducibilityRecord:
    """JSON-safe definition metadata; it deliberately contains no result claim."""

    schema_version: str
    template: str
    template_version: str
    parameters: Mapping[str, Any]
    data_requirements: DataRequirements
    code_version: str
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["created_at"] = self.created_at.isoformat()
        return _json_safe(payload)

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"))


def parameter_schema(template: StrategyTemplate | str) -> dict[str, dict[str, Any]]:
    """A readable schema intended for forms/config validation, not JSON Schema magic."""
    template = _coerce_template(template)
    if template is StrategyTemplate.BUY_AND_HOLD:
        return {
            "symbol": {"type": "string", "required": True, "description": "One stable research symbol to hold."},
            "start_date": {"type": "YYYY-MM-DD", "required": True, "description": "First eligible trading date."},
            "target_weight": {"type": "decimal", "required": False, "default": "1", "range": "(0, 1]", "description": "Portfolio share allocated after start_date."},
        }
    return {
        "symbol": {"type": "string", "required": True, "description": "One stable research symbol."},
        "fast_window": {"type": "integer", "required": True, "range": "[2, slow_window-1]", "description": "Daily closes in fast arithmetic mean."},
        "slow_window": {"type": "integer", "required": True, "range": "[3, +inf)", "description": "Daily closes in slow arithmetic mean."},
        "target_weight": {"type": "decimal", "required": False, "default": "1", "range": "(0, 1]", "description": "Long weight when fast mean exceeds slow mean."},
    }


def validate_parameters(template: StrategyTemplate | str, parameters: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    """Return deterministic, display-ready validation issues without side effects."""
    try:
        template = _coerce_template(template)
    except ValueError as exc:
        return (ValidationIssue("template", str(exc)),)
    if not isinstance(parameters, Mapping):
        return (ValidationIssue("parameters", "must be an object"),)
    allowed = set(parameter_schema(template))
    issues: list[ValidationIssue] = []
    for name in sorted(set(parameters) - allowed):
        issues.append(ValidationIssue(name, "is not supported by this fixed template version"))
    for name in sorted(allowed):
        schema = parameter_schema(template)[name]
        if schema["required"] and name not in parameters:
            issues.append(ValidationIssue(name, "is required"))
    symbol = parameters.get("symbol")
    if symbol is not None and (not isinstance(symbol, str) or not symbol.strip() or symbol != symbol.strip()):
        issues.append(ValidationIssue("symbol", "must be a non-empty string without surrounding whitespace"))
    if template is StrategyTemplate.BUY_AND_HOLD:
        value = parameters.get("start_date")
        if value is not None:
            try:
                date.fromisoformat(value) if isinstance(value, str) else (_ for _ in ()).throw(ValueError())
            except ValueError:
                issues.append(ValidationIssue("start_date", "must be an ISO calendar date (YYYY-MM-DD)"))
    else:
        fast, slow = parameters.get("fast_window"), parameters.get("slow_window")
        if not _positive_int(fast) or fast < 2:
            issues.append(ValidationIssue("fast_window", "must be an integer of at least 2"))
        if not _positive_int(slow) or slow < 3:
            issues.append(ValidationIssue("slow_window", "must be an integer of at least 3"))
        if _positive_int(fast) and _positive_int(slow) and fast >= slow:
            issues.append(ValidationIssue("fast_window", "must be smaller than slow_window"))
    if "target_weight" in parameters:
        try:
            weight = _decimal(parameters["target_weight"], "target_weight")
            if not Decimal("0") < weight <= Decimal("1"):
                issues.append(ValidationIssue("target_weight", "must be within (0, 1]"))
        except ValueError as exc:
            issues.append(ValidationIssue("target_weight", str(exc)))
    return tuple(issues)


def define_strategy(template: StrategyTemplate | str, parameters: Mapping[str, Any], data_requirements: DataRequirements) -> StrategyDefinition:
    template = _coerce_template(template)
    return StrategyDefinition(template, _template_version(template), parameters, data_requirements)


def target_weights(definition: StrategyDefinition, *, decision_time: datetime, observations: Sequence[ObservedClose]) -> TargetWeights:
    """Compute a target weight from supplied observations after point-in-time checks."""
    _require_utc(decision_time, "decision_time")
    _validate_observations(definition, decision_time, observations)
    if definition.template is StrategyTemplate.BUY_AND_HOLD:
        start = date.fromisoformat(definition.parameters["start_date"])
        if decision_time.date() < start:
            return TargetWeights({}, Decimal("1"), decision_time, "before configured start_date")
        return _long_target(definition, decision_time, "buy-and-hold allocation")
    return _dual_moving_average_target(definition, decision_time, observations)


def _dual_moving_average_target(definition: StrategyDefinition, decision_time: datetime, observations: Sequence[ObservedClose]) -> TargetWeights:
    slow = definition.parameters["slow_window"]
    fast = definition.parameters["fast_window"]
    ordered = sorted(observations, key=lambda item: (item.event.trading_day, item.event.available_time, item.event.sequence or -1))
    if len(ordered) < slow:
        return TargetWeights({}, Decimal("1"), decision_time, f"insufficient available history: need {slow} daily closes")
    closes = [item.close for item in ordered[-slow:]]
    slow_mean = sum(closes) / Decimal(slow)
    fast_mean = sum(closes[-fast:]) / Decimal(fast)
    if fast_mean > slow_mean:
        return _long_target(definition, decision_time, "fast moving average is above slow moving average")
    return TargetWeights({}, Decimal("1"), decision_time, "fast moving average is not above slow moving average")


def _long_target(definition: StrategyDefinition, decision_time: datetime, reason: str) -> TargetWeights:
    weight = definition.parameters["target_weight"]
    return TargetWeights({definition.parameters["symbol"]: weight}, Decimal("1") - weight, decision_time, reason)


def _validate_observations(definition: StrategyDefinition, decision_time: datetime, observations: Sequence[ObservedClose]) -> None:
    symbol = definition.parameters["symbol"]
    seen_days: set[date] = set()
    for observation in observations:
        if observation.symbol != symbol:
            raise ValueError("observations must match the strategy symbol")
        if observation.event.trading_day > decision_time.date():
            raise PermissionError("future trading_day blocked")
        observation.event.require_available_at(decision_time)
        if observation.event.trading_day in seen_days:
            raise ValueError("at most one close observation is allowed per trading_day")
        seen_days.add(observation.event.trading_day)


def _canonical_parameters(template: StrategyTemplate, parameters: Mapping[str, Any]) -> dict[str, Any]:
    output = dict(parameters)
    output.setdefault("target_weight", Decimal("1"))
    output["target_weight"] = _decimal(output["target_weight"], "target_weight")
    if template is StrategyTemplate.DUAL_MOVING_AVERAGE:
        output["fast_window"] = int(output["fast_window"])
        output["slow_window"] = int(output["slow_window"])
    return output


def _template_version(template: StrategyTemplate) -> str:
    return BUY_AND_HOLD_VERSION if template is StrategyTemplate.BUY_AND_HOLD else DUAL_MOVING_AVERAGE_VERSION


def _coerce_template(value: StrategyTemplate | str) -> StrategyTemplate:
    if isinstance(value, StrategyTemplate):
        return value
    try:
        return StrategyTemplate(value)
    except ValueError as exc:
        raise ValueError(f"unknown strategy template: {value}") from exc


def _require_fixed_version(field: str, value: object) -> None:
    if not isinstance(value, str) or not value.strip() or value.strip().lower() in {"latest", "current", "unknown", "unspecified"}:
        raise ValueError(f"{field} must be a fixed, non-empty version identifier")


def _decimal(value: object, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} must be a finite decimal") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be a finite decimal")
    return result


def _positive_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _require_utc(value: datetime, field: str) -> None:
    if value.tzinfo is None or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field} must be timezone-aware UTC")


def _json_safe(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value
