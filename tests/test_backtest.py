from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from quant_data.backtest import (
    AssetPool, BacktestBlocked, BacktestRequest, DailyLimitBar,
    SYNTHETIC_CALENDAR_VERSION, SYNTHETIC_DATASET_VERSION,
    SYNTHETIC_UNIVERSE_VERSION, run_synthetic_daily_limit,
)
from quant_data.strategies import DataRequirements, define_strategy
from quant_data.time_model import MarketEvent

UTC = timezone.utc


def _event(day: date) -> MarketEvent:
    stamp = datetime(day.year, day.month, day.day, 21, tzinfo=UTC)
    return MarketEvent(stamp, stamp, stamp, "America/New_York", day, "synthetic-p3-03", "v1")


def _request(**changes):
    requirements = DataRequirements(SYNTHETIC_DATASET_VERSION, SYNTHETIC_UNIVERSE_VERSION, SYNTHETIC_CALENDAR_VERSION, price_basis="raw")
    value = dict(
        strategy=define_strategy("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-02"}, requirements),
        asset_pool=AssetPool(SYNTHETIC_UNIVERSE_VERSION, "synthetic", ("ACME",), "not_applicable"),
        dataset_version=SYNTHETIC_DATASET_VERSION, calendar_version=SYNTHETIC_CALENDAR_VERSION,
        nautilus_version="1.221.0", initial_cash="1000", limit_price="100", requested_quantity="10",
        bars=(
            DailyLimitBar(date(2024, 1, 2), _event(date(2024, 1, 2)), Decimal("100"), Decimal("100"), Decimal("0")),
            DailyLimitBar(date(2024, 1, 3), _event(date(2024, 1, 3)), Decimal("101"), Decimal("100"), Decimal("4")),
            DailyLimitBar(date(2024, 1, 4), _event(date(2024, 1, 4)), Decimal("102"), Decimal("100"), Decimal("0")),
        ),
    )
    value.update(changes)
    return BacktestRequest(**value)


@pytest.mark.parametrize("template,parameters", [
    ("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-02"}),
    ("dual_moving_average", {"symbol": "ACME", "fast_window": 2, "slow_window": 3}),
])
def test_two_templates_rerun_same_version_and_match_p2_05_partial_fill_golden(template, parameters):
    request = _request(strategy=define_strategy(template, parameters, DataRequirements(SYNTHETIC_DATASET_VERSION, SYNTHETIC_UNIVERSE_VERSION, SYNTHETIC_CALENDAR_VERSION, price_basis="raw")))
    first, rerun = run_synthetic_daily_limit(request), run_synthetic_daily_limit(request)
    assert first == rerun
    assert first["ledger"] == {"initial_cash": "1000", "free_cash": "599", "position": "4", "mark_price": "102", "unrealized_pnl": "8", "fees": "1", "total_pnl": "7"}
    assert first["execution"]["cancelled_quantity"] == "6"
    assert first["engine"]["version"] == "1.221.0"


@pytest.mark.parametrize("changes, message", [
    ({"dataset_version": "curated-us-v1"}, "real or unqualified"),
    ({"nautilus_version": "1.222.0"}, "exactly"),
    ({"asset_pool": AssetPool(SYNTHETIC_UNIVERSE_VERSION, "synthetic", ("ACME",), "not_applicable"), "calendar_version": "nyse-v1"}, "calendar"),
])
def test_unqualified_versions_fail_closed(changes, message):
    with pytest.raises(BacktestBlocked, match=message):
        _request(**changes)


def test_real_pool_and_non_explicit_corporate_actions_are_blocked():
    with pytest.raises(BacktestBlocked, match="real asset pools"):
        AssetPool("real-pool-v1", "real", ("ACME",), "complete")
    with pytest.raises(BacktestBlocked, match="corporate_actions"):
        AssetPool(SYNTHETIC_UNIVERSE_VERSION, "synthetic", ("ACME",), "representative")
