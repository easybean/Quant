from pathlib import Path


def test_p2_05_harness_is_synthetic_and_tests_market_and_limit_paths():
    script = Path("scripts/run_p2_05_nautilus.py").read_text()
    assert 'choices=("market", "limit")' in script
    assert "FourShareBook" in script and "Quantity.from_int(4)" in script
    assert "FixedFeeModel(Money(1, USD))" in script
    assert '"free_cash": "599"' in script
    assert "engine.add_data([_bar(" in script
    assert '"OrderFilled", "OrderCanceled"' in script
