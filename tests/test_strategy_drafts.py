from quant_data.strategy_drafts import DraftInputError, StrategyDraftStore, template_catalogue


def test_fixed_templates_are_displayable_and_versioned():
    assert [(item["id"], item["version"]) for item in template_catalogue()] == [
        ("buy_and_hold", "buy-and-hold-v1"), ("dual_moving_average", "dual-moving-average-v1")]


def test_save_update_and_history_are_versioned(tmp_path):
    store = StrategyDraftStore(tmp_path); store.initialize()
    first = store.save({"name": "SPY 趋势", "template": "dual_moving_average", "parameters": {"symbol": "SPY", "fast_window": "20", "slow_window": "60", "target_weight": "1"}})
    second = store.save({"name": "SPY 趋势", "template": "dual_moving_average", "parameters": {"symbol": "SPY", "fast_window": 15, "slow_window": 60, "target_weight": "0.8"}}, first["id"])
    assert second["version"] == 2
    assert [item["version"] for item in store.history(first["id"])] == [2, 1]
    assert store.list()[0]["parameters"]["fast_window"] == 15


def test_invalid_parameters_never_become_a_draft(tmp_path):
    store = StrategyDraftStore(tmp_path); store.initialize()
    try:
        store.save({"name": "bad", "template": "dual_moving_average", "parameters": {"symbol": "SPY", "fast_window": 60, "slow_window": 20}})
    except DraftInputError as exc:
        assert "fast_window" in str(exc)
    else:
        raise AssertionError("expected validation failure")
    assert store.list() == []
