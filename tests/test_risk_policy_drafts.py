import pytest

from quant_data.risk_policy_drafts import RiskPolicyDraftInputError, RiskPolicyDraftStore


def _policy(**changes):
    policy = {
        "name": "全局保护线", "scope": "global", "max_instrument_exposure_pct": "10",
        "max_market_exposure_pct": "35", "max_gross_leverage": "1.5",
        "max_daily_loss_pct": "2", "max_drawdown_pct": "12",
        "max_orders_per_minute": 20, "trading_halted": False,
    }
    policy.update(changes)
    return policy


def test_saves_immutable_global_policy_versions(tmp_path):
    store = RiskPolicyDraftStore(tmp_path)
    store.initialize()
    first = store.save(_policy())
    second = store.save(_policy(name="全局保护线 v2", trading_halted=True), first["id"])
    assert second["version"] == 2
    assert second["policy"]["trading_halted"] is True
    assert [item["version"] for item in store.history(first["id"])] == [2, 1]
    assert store.list()[0]["policy"]["scope"] == "global"


@pytest.mark.parametrize("changes, message", [
    ({"scope": "strategy"}, "strategy constraints"),
    ({"max_instrument_exposure_pct": "36"}, "cannot exceed"),
    ({"max_daily_loss_pct": "13"}, "cannot exceed"),
    ({"max_gross_leverage": "NaN"}, "finite number"),
    ({"max_orders_per_minute": 0}, "integer"),
    ({"trading_halted": "yes"}, "boolean"),
])
def test_rejects_invalid_or_contradictory_policies(tmp_path, changes, message):
    store = RiskPolicyDraftStore(tmp_path)
    store.initialize()
    with pytest.raises(RiskPolicyDraftInputError, match=message):
        store.save(_policy(**changes))
    assert store.list() == []
