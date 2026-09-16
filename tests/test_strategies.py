from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quant_data.market_capabilities import Runner
from quant_data.strategies import (
    BUY_AND_HOLD_VERSION,
    DUAL_MOVING_AVERAGE_VERSION,
    DataRequirements,
    ObservedClose,
    StrategyDefinition,
    StrategyTemplate,
    define_strategy,
    parameter_schema,
    target_weights,
    validate_parameters,
)
from quant_data.time_model import MarketEvent


UTC = timezone.utc


def _requirements() -> DataRequirements:
    return DataRequirements("curated-us-equity-v1", "research-pool-v1", "nyse-calendar-v1", price_basis="raw")


def _event(day: date, available_hour: int = 21) -> MarketEvent:
    stamp = datetime(day.year, day.month, day.day, available_hour, tzinfo=UTC)
    return MarketEvent(stamp, stamp, stamp, "America/New_York", day, "synthetic-test", "v1")


def _close(day: date, value: str, available_hour: int = 21) -> ObservedClose:
    return ObservedClose("ACME", _event(day, available_hour), Decimal(value))


def test_fixed_template_versions_schema_and_readable_parameter_issues():
    assert "start_date" in parameter_schema(StrategyTemplate.BUY_AND_HOLD)
    assert parameter_schema("dual_moving_average")["fast_window"]["required"]
    issues = validate_parameters("dual_moving_average", {"symbol": " ACME ", "fast_window": 8, "slow_window": 4, "extra": True})
    assert [(item.field, item.message) for item in issues] == [
        ("extra", "is not supported by this fixed template version"),
        ("symbol", "must be a non-empty string without surrounding whitespace"),
        ("fast_window", "must be smaller than slow_window"),
    ]


def test_buy_and_hold_targets_cash_before_start_and_weight_after_start():
    strategy = define_strategy("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-05", "target_weight": "0.7"}, _requirements())
    assert strategy.template_version == BUY_AND_HOLD_VERSION
    early = target_weights(strategy, decision_time=datetime(2024, 1, 4, tzinfo=UTC), observations=())
    assert early.weights == {} and early.cash_weight == Decimal("1")
    active = target_weights(strategy, decision_time=datetime(2024, 1, 5, tzinfo=UTC), observations=())
    assert active.weights == {"ACME": Decimal("0.7")} and active.cash_weight == Decimal("0.3")


def test_dual_average_uses_only_available_daily_history_and_outputs_weights():
    strategy = define_strategy("dual_moving_average", {"symbol": "ACME", "fast_window": 2, "slow_window": 3}, _requirements())
    assert strategy.template_version == DUAL_MOVING_AVERAGE_VERSION
    observations = (_close(date(2024, 1, 2), "10"), _close(date(2024, 1, 3), "11"), _close(date(2024, 1, 4), "12"))
    result = target_weights(strategy, decision_time=datetime(2024, 1, 4, 21, tzinfo=UTC), observations=observations)
    assert result.weights == {"ACME": Decimal("1")} and result.cash_weight == Decimal("0")
    insufficient = target_weights(strategy, decision_time=datetime(2024, 1, 3, 21, tzinfo=UTC), observations=observations[:2])
    assert insufficient.weights == {} and insufficient.cash_weight == Decimal("1")


def test_future_information_dates_and_duplicate_daily_closes_fail_closed():
    strategy = define_strategy("dual_moving_average", {"symbol": "ACME", "fast_window": 2, "slow_window": 3}, _requirements())
    with pytest.raises(PermissionError, match="future information"):
        target_weights(strategy, decision_time=datetime(2024, 1, 4, 20, tzinfo=UTC), observations=(_close(date(2024, 1, 4), "12"),))
    with pytest.raises(PermissionError, match="future trading_day"):
        target_weights(strategy, decision_time=datetime(2024, 1, 4, 22, tzinfo=UTC), observations=(_close(date(2024, 1, 5), "12"),))
    with pytest.raises(ValueError, match="one close"):
        target_weights(strategy, decision_time=datetime(2024, 1, 4, 22, tzinfo=UTC), observations=(_close(date(2024, 1, 4), "11"), _close(date(2024, 1, 4), "12")))


def test_versions_record_json_and_only_limit_adapter_path_is_allowed():
    strategy = define_strategy("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-05"}, _requirements())
    record = strategy.reproducibility_record(code_version="git-abc123", created_at=datetime(2024, 1, 5, tzinfo=UTC))
    assert '"template_version":"buy-and-hold-v1"' in record.to_json()
    assert '"dataset_version":"curated-us-equity-v1"' in record.to_json()
    gate = strategy.run_gate(Runner.NAUTILUS)
    assert gate.allowed
    assert gate.capability_result.issues == ()
    # P3-03's data-pool/corporate-action gate still blocks real data.


@pytest.mark.parametrize("parameters", [
    {"symbol": "ACME", "start_date": "2024-02-30"},
    {"symbol": "ACME", "fast_window": 4, "slow_window": 4},
    {"symbol": "ACME", "fast_window": True, "slow_window": 5},
    {"symbol": "ACME", "start_date": "2024-01-01", "target_weight": "NaN"},
])
def test_invalid_parameters_are_rejected(parameters):
    template = "buy_and_hold" if "start_date" in parameters else "dual_moving_average"
    with pytest.raises(ValueError, match="invalid strategy parameters"):
        define_strategy(template, parameters, _requirements())


def test_data_versions_and_target_weight_boundaries_are_not_implicit():
    with pytest.raises(ValueError, match="fixed"):
        DataRequirements("latest", "pool-v1", "calendar-v1", price_basis="raw")
    with pytest.raises(ValueError, match="price_basis"):
        DataRequirements("data-v1", "pool-v1", "calendar-v1", price_basis="unknown")
    with pytest.raises(ValueError, match="invalid strategy parameters"):
        define_strategy("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-01", "target_weight": "0"}, _requirements())
