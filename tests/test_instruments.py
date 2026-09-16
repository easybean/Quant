from datetime import date, datetime
from decimal import Decimal

import pytest

from quant_data.instruments import (
    AssetClass, InstrumentDefinition, InstrumentRule, SymbolAssignment,
    effective_rule_at, make_instrument_id, validate_instrument, validate_instrument_set,
)
from quant_data.market_capabilities import MarketProduct


def _rule(**changes):
    values = {"valid_from": date(2020, 1, 1), "rule_version": "v1", "multiplier": Decimal("1"),
              "quantity_unit": "share", "tick_size": Decimal("0.01"), "lot_size": Decimal("1")}
    values.update(changes)
    return InstrumentRule(**values)


def _definition(product, venue, symbol, identity_key="security-1", **changes):
    values = {
        "instrument_id": make_instrument_id(venue, identity_key), "identity_key": identity_key,
        "asset": AssetClass.EQUITY, "product": product, "venue": venue, "venue_symbol": symbol,
        "timezone": "America/New_York", "calendar_id": venue,
        "symbol_history": (SymbolAssignment(symbol, date(2020, 1, 1)),), "rules": (_rule(),),
    }
    values.update(changes)
    return InstrumentDefinition(**values)


def test_valid_asset_product_combinations_and_currency_requirements():
    equity = _definition(MarketProduct.US_EQUITY, "XNYS", "AAPL", identity_key="US0378331005")
    cn = _definition(MarketProduct.CN_FUTURE, "SHFE", "rb2501", identity_key="SHFE:rb2501", asset=AssetClass.FUTURE,
                     expiry=date(2025, 1, 15), last_trade=date(2025, 1, 15),
                     rules=(_rule(multiplier=10, quantity_unit="contract"),))
    spot = _definition(MarketProduct.CRYPTO_SPOT, "BINANCE", "BTCUSDT", identity_key="binance:btc-usdt", asset=AssetClass.CRYPTO,
                       base_currency="BTC", quote_currency="USDT", settlement_currency="USDT")
    perpetual = _definition(MarketProduct.CRYPTO_PERPETUAL, "BINANCE", "BTCUSDT-PERP", identity_key="binance:btc-usdt-perp", asset=AssetClass.CRYPTO,
                            base_currency="BTC", quote_currency="USDT", settlement_currency="USDT", linear=True,
                            rules=(_rule(multiplier=Decimal("0.001"), quantity_unit="BTC"),))
    assert all(validate_instrument(item).valid for item in (equity, cn, spot, perpetual))


def test_symbol_migration_and_nonoverlapping_ticker_reuse_are_valid():
    migrated = _definition(MarketProduct.US_EQUITY, "XNYS", "NEW", identity_key="security-old",
                           symbol_history=(SymbolAssignment("OLD", date(2010, 1, 1), date(2018, 12, 31)),
                                           SymbolAssignment("NEW", date(2019, 1, 1))),)
    reused = _definition(MarketProduct.US_EQUITY, "XNYS", "OLD", identity_key="security-new",
                          symbol_history=(SymbolAssignment("OLD", date(2020, 1, 1)),),)
    results = validate_instrument_set((migrated, reused))
    assert all(result.valid for result in results)
    assert migrated.instrument_id != reused.instrument_id


def test_overlapping_ticker_lifecycles_and_bad_stable_identity_are_explained():
    first = _definition(MarketProduct.US_EQUITY, "XNYS", "ABC", identity_key="one")
    second = _definition(MarketProduct.US_EQUITY, "XNYS", "ABC", identity_key="two")
    result = validate_instrument_set((first, second))
    assert all("ticker_lifecycle_overlap" in {issue.code for issue in item.issues} for item in result)
    bad = _definition(MarketProduct.US_EQUITY, "XNYS", "AAPL", identity_key="US0378331005", instrument_id="XNYS:AAPL")
    assert "stable_id_required" in {issue.code for issue in validate_instrument(bad).issues}


def test_invalid_dates_products_and_all_finite_numeric_boundaries_are_reported():
    invalid = _definition(MarketProduct.CRYPTO_SPOT, "BINANCE", "BTCUSDT", identity_key="btc",
                          asset=AssetClass.FUTURE, expiry=date(2025, 1, 1), base_currency=None,
                          quote_currency=None, settlement_currency=None,
                          symbol_history=(SymbolAssignment("BTCUSDT"),),
                          rules=(_rule(valid_from=date(2025, 2, 1), valid_to=date(2025, 1, 1),
                                       tick_size=float("inf"), lot_size=Decimal("NaN"), min_notional=float("nan")),))
    codes = {issue.code for issue in validate_instrument(invalid).issues}
    assert {"product_asset_mismatch", "spot_has_expiry", "required", "effective_from_required", "before_valid_from", "positive_finite_required"} <= codes


def test_derivative_contract_model_and_rule_effectivity_are_required():
    future = _definition(MarketProduct.CN_FUTURE, "SHFE", "rb2501", identity_key="rb2501", asset=AssetClass.FUTURE,
                         expiry=None, rules=(_rule(multiplier=0), _rule(rule_version="v2", valid_from=date(2020, 6, 1))))
    perpetual = _definition(MarketProduct.CRYPTO_PERPETUAL, "BINANCE", "ETHUSDT-PERP", identity_key="eth-perp", asset=AssetClass.CRYPTO,
                            base_currency="ETH", quote_currency="USDT", settlement_currency="USDT",
                            rules=(_rule(multiplier=None),))
    assert {"required", "positive_finite_required", "rule_effectivity_overlap"} <= {issue.code for issue in validate_instrument(future).issues}
    assert {"contract_model_required", "positive_finite_required"} <= {issue.code for issue in validate_instrument(perpetual).issues}


def test_effective_rule_lookup_uses_requested_historical_date_not_latest_rule():
    instrument = _definition(MarketProduct.US_EQUITY, "XNYS", "AAPL", rules=(
        _rule(rule_version="v1", valid_from=date(2020, 1, 1), valid_to=date(2021, 12, 31)),
        _rule(rule_version="v2", valid_from=date(2022, 1, 1), tick_size=Decimal("0.05")),
    ))
    assert effective_rule_at(instrument, date(2021, 12, 31)).rule_version == "v1"  # type: ignore[union-attr]
    assert effective_rule_at(instrument, date(2022, 1, 1)).rule_version == "v2"  # type: ignore[union-attr]
    assert effective_rule_at(instrument, date(2019, 12, 31)) is None
    with pytest.raises(TypeError):
        effective_rule_at(instrument, datetime(2022, 1, 1))  # type: ignore[arg-type]


def test_constructor_type_errors_remain_normal_python_errors():
    with pytest.raises(TypeError):
        InstrumentDefinition()  # type: ignore[call-arg]
