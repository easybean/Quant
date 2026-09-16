from quant_data.universe_drafts import AssetPoolDraftStore, InstrumentDraftStore
from quant_data.visual_strategy_drafts import VisualStrategyDraftInputError, VisualStrategyDraftStore


def _pool(tmp_path):
    instruments = InstrumentDraftStore(tmp_path); instruments.initialize()
    instrument = {"instrument_id": "NYSE:VS-001", "identity_key": "VS-001", "asset": "equity", "product": "US_EQUITY", "venue": "NYSE", "venue_symbol": "VS", "timezone": "America/New_York", "calendar_id": "US-DRAFT", "linear": False, "inverse": False, "symbol_history": [{"venue_symbol": "VS", "valid_from": "2024-01-01"}], "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "share", "tick_size": "0.01", "lot_size": "1"}]}
    saved = instruments.save({"name": "Visual sample", "instrument": instrument})
    pools = AssetPoolDraftStore(tmp_path, instruments); pools.initialize()
    return pools, pools.save({"name": "Strategy pool", "purpose": "test", "instruments": [{"instrument_draft_id": saved["id"], "instrument_version": 1}]})


def _payload(pool):
    return {"name": "动量候选", "asset_pool": {"asset_pool_draft_id": pool["id"], "asset_pool_version": 1}, "signal_rules": [{"factor_id": "return_20", "operator": "gt", "threshold": 0, "direction": "long"}], "combination": "all", "rebalance_frequency": "monthly", "allocation": {"mode": "fixed_weight", "target_weight": 0.3}, "constraints": {"max_position_weight": 0.4, "max_holdings": 10}}


def test_visual_strategy_references_versions_and_keeps_history(tmp_path):
    pools, pool = _pool(tmp_path)
    store = VisualStrategyDraftStore(tmp_path, pools); store.initialize()
    first = store.save(_payload(pool))
    revised = _payload(pool); revised["rebalance_frequency"] = "weekly"
    second = store.save(revised, first["id"])
    assert second["version"] == 2
    assert second["definition"]["asset_pool"]["asset_pool_version"] == 1
    assert [row["version"] for row in store.history(first["id"])] == [2, 1]
    assert "formula" not in str(second)


def test_visual_strategy_rejects_unknown_or_unrunnable_factor(tmp_path):
    pools, pool = _pool(tmp_path)
    store = VisualStrategyDraftStore(tmp_path, pools); store.initialize()
    invalid = _payload(pool); invalid["signal_rules"][0]["factor_id"] = "qlib158_VWAP0"
    try: store.save(invalid)
    except VisualStrategyDraftInputError as exc: assert "runnable" in str(exc)
    else: raise AssertionError("expected unavailable factor to be rejected")
    invalid = _payload(pool); invalid["extra"] = True
    try: store.save(invalid)
    except VisualStrategyDraftInputError: pass
    else: raise AssertionError("expected unknown field to be rejected")
