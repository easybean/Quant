import pytest

from quant_data.market_capabilities import (
    CAPABILITY_FIELDS, CapabilityRequirement, CapabilityStatus, MarketProduct,
    Runner, DEFAULT_REGISTRY, validate_requirements,
)


def test_every_runner_product_is_explicitly_unverified_not_package_supported():
    for runner in Runner:
        for product in MarketProduct:
            profile = DEFAULT_REGISTRY.get_profile(runner, product)
            if (runner, product) == (Runner.NAUTILUS, MarketProduct.US_EQUITY):
                assert profile.status is CapabilityStatus.SUPPORTED
                assert profile.capability_status("bar_frequency:1d") is CapabilityStatus.SUPPORTED
                assert profile.capability_status("order_type:limit") is CapabilityStatus.SUPPORTED
                assert profile.capability_status("order_type:market") is CapabilityStatus.UNVERIFIED
                continue
            assert profile.status is CapabilityStatus.UNVERIFIED
            assert set(profile.capabilities) == set(CAPABILITY_FIELDS)
            assert set(profile.capabilities.values()) == {CapabilityStatus.UNVERIFIED}
    assert "package presence" in DEFAULT_REGISTRY.get_profile(Runner.BACKTRADER, MarketProduct.US_EQUITY).evidence


def test_exact_product_frequency_and_order_requirements_block_until_verified():
    result = validate_requirements(
        Runner.BACKTRADER, MarketProduct.US_EQUITY,
        [CapabilityRequirement("bar_frequency", "1d"), "order_type:limit"],
    )
    assert not result.supported
    assert {item.capability for item in result.unverified} == {"profile", "bar_frequency:1d", "order_type:limit"}


def test_mapping_expands_exact_values_but_rejects_boolean_or_generic_requirements():
    expanded = validate_requirements(Runner.NAUTILUS, MarketProduct.CRYPTO_PERPETUAL,
                                    {"bar_frequency": ["1m"], "order_type": "market", "funding": ["perpetual"]})
    assert {item.capability for item in expanded.unverified} == {"profile", "bar_frequency:1m", "order_type:market", "funding:perpetual"}
    vague = validate_requirements(Runner.NAUTILUS, MarketProduct.CRYPTO_PERPETUAL, {"order_type": True})
    assert not vague.ok
    assert vague.unknown[0].actual is CapabilityStatus.UNKNOWN
    assert "exact value" in vague.unknown[0].reason


def test_unknown_runner_product_and_capability_return_readable_problems():
    result = validate_requirements("not-an-engine", MarketProduct.US_EQUITY, ["order_type:market"])
    assert not result.supported and result.profile_status is CapabilityStatus.UNKNOWN
    assert result.issues[0].capability == "registry"
    with pytest.raises(ValueError, match="unknown market product"):
        DEFAULT_REGISTRY.get_profile(Runner.NAUTILUS, "NOT_A_PRODUCT")
    unknown = validate_requirements(Runner.VEIGHNA, MarketProduct.CN_FUTURE, ["made_up:thing"])
    assert unknown.unknown[0].capability == "made_up:thing"
    assert "not declared" in unknown.unknown[0].reason


def test_p2_04_candidate_matrix_remains_fail_closed_without_product_accounting_evidence():
    """A runner import or a synthetic bar must not unlock a tradable product."""
    cases = (
        (Runner.NAUTILUS, MarketProduct.US_EQUITY, ("bar_frequency:1m", "order_type:limit")),
        (Runner.NAUTILUS, MarketProduct.GLOBAL_FUTURE, ("bar_frequency:1m", "margin:cross")),
        (Runner.VEIGHNA, MarketProduct.CN_FUTURE, ("bar_frequency:1m", "open_close:close_today")),
        (Runner.NAUTILUS, MarketProduct.CRYPTO_SPOT, ("bar_frequency:1m", "order_type:limit")),
        (Runner.NAUTILUS, MarketProduct.CRYPTO_PERPETUAL, ("bar_frequency:1m", "funding:perpetual")),
        (Runner.NAUTILUS, MarketProduct.CRYPTO_DELIVERY, ("bar_frequency:1m", "expiry_settlement:delivery")),
    )
    for runner, product, requirements in cases:
        result = validate_requirements(runner, product, requirements)
        assert not result.supported
        expected_profile = CapabilityStatus.SUPPORTED if (runner, product) == (Runner.NAUTILUS, MarketProduct.US_EQUITY) else CapabilityStatus.UNVERIFIED
        assert result.profile_status is expected_profile
        expected_unverified = {"bar_frequency:1m"} if (runner, product) == (Runner.NAUTILUS, MarketProduct.US_EQUITY) else {"profile", *requirements}
        assert {issue.capability for issue in result.unverified} == expected_unverified


def test_p2_05_linux_limit_path_is_verified_but_market_remains_closed():
    limit = validate_requirements(
        Runner.NAUTILUS, MarketProduct.US_EQUITY, ("bar_frequency:1d", "order_type:limit"),
    )
    assert limit.supported
    market = validate_requirements(
        Runner.NAUTILUS,
        MarketProduct.US_EQUITY,
        ("bar_frequency:1d", "order_type:market", "order_type:limit"),
    )
    assert not market.supported
    assert market.profile_status is CapabilityStatus.SUPPORTED
    assert {issue.capability for issue in market.unverified} == {"order_type:market"}
