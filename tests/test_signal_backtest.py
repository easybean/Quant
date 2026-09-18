from dataclasses import replace
from decimal import Decimal

import pytest

from quant_data.daily_execution import CostModel
from quant_data.signal_backtest import (
    REFERENCE_SIGNAL_DAILY_BARS,
    SIGNAL_BACKTEST_SCHEMA_VERSION,
    SYNTHETIC_SIGNAL_CALENDAR_VERSION,
    SYNTHETIC_SIGNAL_DATASET_VERSION,
    SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
    SignalBacktestBlocked,
    SignalBacktestConfig,
    _run,
    run_from_job,
    validate_job,
)
from quant_data.strategies import DataRequirements, define_strategy


def _payload(template="buy_and_hold", parameters=None):
    if parameters is None:
        parameters = {"symbol": "ACME", "start_date": "2024-01-02"}
    return {
        "template": template,
        "parameters": parameters,
        "data_requirements": {
            "dataset_version": SYNTHETIC_SIGNAL_DATASET_VERSION,
            "universe_version": SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
            "calendar_version": SYNTHETIC_SIGNAL_CALENDAR_VERSION,
            "price_basis": "raw",
        },
    }


def _config(*, initial_cash="1000", cost=None):
    definition = define_strategy(
        "buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-02"},
        DataRequirements(
            SYNTHETIC_SIGNAL_DATASET_VERSION,
            SYNTHETIC_SIGNAL_UNIVERSE_VERSION,
            SYNTHETIC_SIGNAL_CALENDAR_VERSION,
            price_basis="raw",
        ),
    )
    return SignalBacktestConfig(
        definition, Decimal(initial_cash), Decimal("100"),
        cost or CostModel(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("1")),
    )


def test_buy_and_hold_reference_uses_prior_close_limit_and_next_session_fill():
    # Two bars isolate the hand-checkable initial allocation: 2 shares at the
    # next opening price, plus USD 1 minimum commission, then mark at close.
    report = _run(_config(initial_cash="200", cost=CostModel(Decimal("0.1"), Decimal("1"), Decimal("0"), Decimal("0"), Decimal("1"))), REFERENCE_SIGNAL_DAILY_BARS[:2])
    assert report["schema_version"] == SIGNAL_BACKTEST_SCHEMA_VERSION
    assert report["orders"] == [{
        "order_id": "signal-1", "signal_day": "2024-01-02", "day": "2024-01-03", "side": "buy", "quantity": "2",
        "limit_price": "101.00", "requested_quantity": "2", "submitted_at": "2024-01-02T21:05:00+00:00", "time_in_force": "DAY",
        "sizing_equity": "200", "sizing_close": "100", "target_weight": "1", "execution_status": "filled",
        "execution_session": "2024-01-03", "status": "filled", "execution_reason": "filled_opening_marketable",
        "filled_quantity": "2", "cancelled_quantity": "0",
    }]
    assert report["fills"] == [{"day": "2024-01-03", "quantity": "2", "side": "buy", "price": "90", "fee_usd": "1", "order_id": "signal-1"}]
    assert report["ledger"] == {
        "initial_cash": "200", "free_cash": "19", "position": "2", "average_entry_price": "90",
        "mark_price": "90", "realized_pnl": "0", "unrealized_pnl": "0", "fees": "1",
        "total_pnl": "-1", "terminal_equity": "199", "reconciled": True,
    }
    assert report["equity_curve"][-1]["equity"] == "199"
    assert report["equity_curve"][-1]["nav"] == "0.995"


def test_two_three_moving_average_has_different_timed_fills_and_reconciles():
    report = run_from_job(
        {}, _payload("dual_moving_average", {"symbol": "ACME", "fast_window": 2, "slow_window": 3}),
        SYNTHETIC_SIGNAL_DATASET_VERSION,
    )
    assert [(item["day"], item["side"], item["quantity"], item["price"]) for item in report["fills"]] == [
        ("2024-01-08", "buy", "8", "80"), ("2024-01-10", "sell", "-8", "75"),
    ]
    assert report["orders"][1]["execution_status"] == "unfilled"
    assert report["orders"][1]["cancelled_quantity"] == "8"
    assert report["ledger"]["terminal_equity"] == "958"
    assert report["ledger"]["total_pnl"] == "-42"
    assert report["ledger"]["terminal_equity"] == str(
        Decimal(report["ledger"]["initial_cash"]) + Decimal(report["ledger"]["total_pnl"])
    )
    assert report["signals"][-1]["order_status"] == "not_submitted_no_next_session"


def test_day_partial_remainder_is_cancelled_then_next_signal_is_new_rebalance():
    partial_cost = CostModel(Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0.005"))
    report = _run(_config(cost=partial_cost), REFERENCE_SIGNAL_DAILY_BARS[:3])
    first, second = report["orders"]
    assert (first["quantity"], first["filled_quantity"], first["cancelled_quantity"], first["execution_status"]) == ("10", "5", "5", "partially_filled")
    assert second["signal_day"] == "2024-01-03"
    assert second["quantity"] == "6"  # recalculated from the new signal, not a carried order remainder
    assert first["time_in_force"] == second["time_in_force"] == "DAY"


def test_later_bar_change_cannot_alter_prior_signal_order_fill_or_equity_rows():
    config = _config()
    original = _run(config, REFERENCE_SIGNAL_DAILY_BARS[:4])
    changed = _run(config, REFERENCE_SIGNAL_DAILY_BARS[:3] + (replace(REFERENCE_SIGNAL_DAILY_BARS[3], close=Decimal("123"), high=Decimal("126")),))
    assert changed["signals"][:3] == original["signals"][:3]
    assert changed["orders"] == original["orders"]
    assert changed["fills"] == original["fills"]
    assert changed["equity_curve"][:3] == original["equity_curve"][:3]


def test_job_surface_rejects_bars_real_snapshots_and_unknown_parameters():
    with pytest.raises(SignalBacktestBlocked, match="unqualified"):
        validate_job({}, _payload(), "curated-us-daily-v1")
    with pytest.raises(SignalBacktestBlocked, match="unsupported"):
        validate_job({"bars": []}, _payload(), SYNTHETIC_SIGNAL_DATASET_VERSION)
    with pytest.raises(SignalBacktestBlocked, match="fixed synthetic symbol"):
        validate_job({}, _payload(parameters={"symbol": "REAL", "start_date": "2024-01-02"}), SYNTHETIC_SIGNAL_DATASET_VERSION)


@pytest.mark.parametrize("cost_model", [
    {"per_share_commission": "-0.01", "minimum_commission": "0", "spread_bps": "0", "slippage_bps": "0", "max_participation": "1"},
    {"per_share_commission": "NaN", "minimum_commission": "0", "spread_bps": "0", "slippage_bps": "0", "max_participation": "1"},
    {"per_share_commission": "0", "minimum_commission": "0", "spread_bps": "-1", "slippage_bps": "0", "max_participation": "1"},
    {"per_share_commission": "0", "minimum_commission": "0", "spread_bps": "0", "slippage_bps": "Infinity", "max_participation": "1"},
    {"per_share_commission": "0", "minimum_commission": "0", "spread_bps": "0", "slippage_bps": "0", "max_participation": "0"},
])
def test_job_cost_model_fails_closed_before_execution(cost_model):
    with pytest.raises(SignalBacktestBlocked, match="cost_model"):
        validate_job({"cost_model": cost_model}, _payload(), SYNTHETIC_SIGNAL_DATASET_VERSION)


def test_private_pure_runner_also_rejects_invalid_config_before_a_fill():
    invalid = replace(_config(), initial_cash=Decimal("-1"))
    with pytest.raises(SignalBacktestBlocked, match="initial_cash"):
        _run(invalid, REFERENCE_SIGNAL_DAILY_BARS[:1])


def test_late_close_observation_fails_closed_before_next_open():
    late = replace(REFERENCE_SIGNAL_DAILY_BARS[0], observation_available_time=REFERENCE_SIGNAL_DAILY_BARS[1].open_time)
    with pytest.raises(SignalBacktestBlocked, match="strictly before"):
        _run(_config(), (late,) + REFERENCE_SIGNAL_DAILY_BARS[1:3])
