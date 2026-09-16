from quant_data.universe_drafts import AssetPoolDraftStore, InstrumentDraftStore, UniverseDraftInputError


def _instrument(product="US_EQUITY"):
    item = {"instrument_id": "NYSE:US-EXAMPLE-001", "identity_key": "US-EXAMPLE-001", "asset": "equity", "product": product, "venue": "NYSE", "venue_symbol": "EXM", "timezone": "America/New_York", "calendar_id": "US-DRAFT", "linear": False, "inverse": False, "symbol_history": [{"venue_symbol": "EXM", "valid_from": "2024-01-01"}], "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "share", "tick_size": "0.01", "lot_size": "1"}]}
    if product == "CRYPTO_SPOT":
        item.update({"instrument_id": "BINANCE:BTC-USDT", "identity_key": "BTC-USDT", "asset": "crypto", "venue": "BINANCE", "venue_symbol": "BTCUSDT", "timezone": "UTC", "calendar_id": "CRYPTO-24X7", "base_currency": "BTC", "quote_currency": "USDT", "settlement_currency": "USDT", "symbol_history": [{"venue_symbol": "BTCUSDT", "valid_from": "2024-01-01"}]})
    if product in {"CRYPTO_PERPETUAL", "CRYPTO_DELIVERY", "CN_FUTURE", "GLOBAL_FUTURE"}:
        item.update({"asset": "crypto" if product.startswith("CRYPTO") else "future", "linear": True, "rules": [{"valid_from": "2024-01-01", "rule_version": "draft-v1", "quantity_unit": "contract", "multiplier": "1", "tick_size": "0.01", "lot_size": "1"}]})
    if product in {"CRYPTO_PERPETUAL", "CRYPTO_DELIVERY"}: item.update({"base_currency": "BTC", "quote_currency": "USDT", "settlement_currency": "USDT"})
    if product == "CRYPTO_DELIVERY": item["expiry"] = "2025-12-31"
    return item


def test_saves_versions_with_stable_identity(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    first = store.save({"name": "美股示例", "instrument": _instrument()})
    second = store.save({"name": "美股示例 v2", "instrument": _instrument()}, first["id"])
    assert second["version"] == 2
    assert [row["version"] for row in store.history(first["id"])] == [2, 1]
    assert store.list()[0]["instrument"]["instrument_id"] == "NYSE:US-EXAMPLE-001"


def test_rejects_invalid_product_contract_combinations(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    invalid = _instrument("CRYPTO_DELIVERY"); invalid.pop("expiry")
    try: store.save({"name": "bad", "instrument": invalid})
    except UniverseDraftInputError as exc: assert "expiry" in str(exc)
    else: raise AssertionError("delivery without expiry must fail")
    assert store.list() == []


def test_accepts_spot_and_derivative_draft_shapes(tmp_path):
    store = InstrumentDraftStore(tmp_path); store.initialize()
    spot = store.save({"name": "现货", "instrument": _instrument("CRYPTO_SPOT")})
    delivery = store.save({"name": "交割", "instrument": _instrument("CRYPTO_DELIVERY")})
    assert spot["instrument"]["product"] == "CRYPTO_SPOT"
    assert delivery["instrument"]["product"] == "CRYPTO_DELIVERY"


def test_asset_pool_only_persists_saved_immutable_references(tmp_path):
    instruments = InstrumentDraftStore(tmp_path); instruments.initialize()
    definition = instruments.save({"name": "美股", "instrument": _instrument()})
    pools = AssetPoolDraftStore(tmp_path, instruments); pools.initialize()
    reference = {"instrument_draft_id": definition["id"], "instrument_version": 1}
    created = pools.save({"name": "研究池", "purpose": "研究用途", "instruments": [reference]})
    assert created["instruments"] == [reference]
    assert pools.history(created["id"])[0]["purpose"] == "研究用途"
