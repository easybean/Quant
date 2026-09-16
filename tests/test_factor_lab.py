import pandas as pd
import pytest

from quant_data.factor_lab import FACTOR_FORMULA_VERSION, compute_factor, evaluate_factor, list_factors, load_derived_panel
from quant_data.factors import evaluate_factor as evaluate_request
from quant_data.factors import list_factors as list_workbench_factors


def _panel(symbols=3, periods=30):
    rows = []
    dates = pd.bdate_range("2024-01-02", periods=periods)
    for number in range(symbols):
        for index, day in enumerate(dates):
            close = 100 + number * 10 + index * (number + 1)
            rows.append({"date": day, "symbol": f"S{number}", "open": close - 1, "high": close + 1, "low": close - 2, "close": close, "volume": 1000 + index})
    return pd.DataFrame(rows)


def test_factor_registry_and_missing_volume_are_explicit():
    registry = {item["name"]: item for item in list_factors()}
    assert set(registry) >= {
        "return_5", "rsi_14", "volume_ratio_20",
        "ta_volume_obv", "ta_volatility_bbm", "ta_trend_macd", "ta_momentum_rsi", "ta_others_dr",
    }
    # The fixed ta 0.11 inventory covers all five aggregate API categories;
    # descriptions and category are deliberately suitable for the Chinese UI.
    assert len([name for name in registry if name.startswith("ta_")]) >= 80
    assert registry["ta_trend_macd"]["category"] == "趋势"
    assert "ta 0.11 Trend" in registry["ta_trend_macd"]["description"]
    panel = _panel(symbols=1)
    panel.loc[panel.index[-1], "volume"] = None
    result = compute_factor(panel, "volume_ratio_20")
    assert result.loc[result.index[-1], "volume_ratio_20"] != result.loc[result.index[-1], "volume_ratio_20"]
    assert compute_factor(panel, "return_5").loc[5, "return_5"] == pytest.approx(5 / 100)


def test_qlib_feature_sets_have_stable_complete_counts_and_no_vwap_proxy():
    registry = list_factors()
    ids = {item["name"] for item in registry}
    assert len([name for name in ids if name.startswith("qlib158_")]) == 158
    assert len([name for name in ids if name.startswith("qlib360_")]) == 360
    panel = _panel(symbols=1, periods=65)
    close0 = compute_factor(panel, "qlib360_CLOSE0")
    assert close0.loc[0, "qlib360_CLOSE0"] == pytest.approx(1.0)
    # Structural OHLCV has no VWAP, and must not silently substitute typical price.
    assert compute_factor(panel, "qlib360_VWAP0")["qlib360_VWAP0"].isna().all()
    assert compute_factor(panel, "qlib158_MA5")["qlib158_MA5"].notna().any()


def test_alpha158_extrema_goldens_match_the_qlib_index_formula():
    # The last five rows deliberately make high's minimum differ from low's
    # minimum.  This catches the prior high-vs-high IMXD implementation and
    # the prior (window - 1) rescaling.
    panel = pd.DataFrame({
        "date": pd.bdate_range("2024-01-02", periods=5), "symbol": "GOLD",
        "open": [3, 2, 2, 3, 4], "high": [4, 1, 2, 3, 5],
        "low": [1, 4, 3, 2, 2], "close": [3, 2, 2, 3, 4], "volume": 100,
    })
    assert compute_factor(panel, "qlib158_IMAX5").iloc[-1, -1] == pytest.approx(4 / 5)
    assert compute_factor(panel, "qlib158_IMIN5").iloc[-1, -1] == pytest.approx(0.0)
    assert compute_factor(panel, "qlib158_IMXD5").iloc[-1, -1] == pytest.approx(4 / 5)
    assert {item["formula_version"] for item in list_factors()} == {FACTOR_FORMULA_VERSION}


def test_every_factor_has_complete_stable_taxonomy_and_adapter_forwards_it():
    registry = list_factors()
    families = {
        "收益与动量", "趋势与均线", "价格位置与通道", "震荡与强弱", "波动与风险",
        "成交量", "量价关系", "K线结构", "统计与回归", "机器学习输入",
    }
    for item in registry:
        assert item["research_family"] in families
        assert item["measurement_type"]
        assert item["source"] in {"自定义", "ta", "Alpha158", "Alpha360"}
        assert item["data_requirements"] == item["required_columns"]
        assert item["redundancy_group"]
        assert item["intended_use"]
        assert item["tags"]

    by_name = {item["name"]: item for item in registry}
    assert by_name["return_20"]["redundancy_group"] == "收益动量"
    assert by_name["ta_momentum_rsi"]["research_family"] == "震荡与强弱"
    assert by_name["qlib158_KMID"]["research_family"] == "K线结构"
    assert by_name["qlib360_CLOSE20"]["research_family"] == "机器学习输入"

    ui = {item["id"]: item for item in list_workbench_factors()}
    assert "qlib360_CLOSE20" not in ui
    assert ui["qlib158_MA20"]["source"] == "Alpha158"
    assert ui["ta_trend_macd"]["research_family"] == "收益与动量"


def test_ta_representatives_are_calculated_per_symbol_without_volume_imputation():
    pytest.importorskip("ta")
    panel = _panel(symbols=2, periods=90)
    # The second symbol has a very different level: a concatenated calculation
    # would create boundary artefacts instead of a fresh per-symbol warm-up.
    panel.loc[panel["symbol"] == "S1", "close"] += 10_000
    panel.loc[panel.index[50], "volume"] = None
    for factor in ("ta_volume_obv", "ta_volatility_bbm", "ta_trend_macd", "ta_momentum_rsi", "ta_others_dr"):
        values = compute_factor(panel, factor)
        assert values[factor].notna().any(), factor
        assert list(values.columns) == ["date", "symbol", factor]
    volume = compute_factor(panel, "ta_volume_obv")
    assert pd.isna(volume.loc[50, "ta_volume_obv"])


def test_ta_011_inventory_covers_all_non_visual_aggregate_outputs():
    pytest.importorskip("ta")
    from ta import add_all_ta_features

    panel = _panel(symbols=1, periods=90).loc[:, ["open", "high", "low", "close", "volume"]]
    actual = set(add_all_ta_features(panel.copy(), open="open", high="high", low="low", close="close", volume="volume", fillna=False).columns) - set(panel.columns)
    registered = {item["name"].removeprefix("ta_") for item in list_factors() if item["name"].startswith("ta_")}
    assert actual - registered == {"trend_visual_ichimoku_a", "trend_visual_ichimoku_b"}
    # Legacy public IDs are retained only so their unavailable state remains
    # explicit; ta 0.11 has no corresponding aggregate columns.
    assert registered - actual == {"trend_psar", "trend_ichimoku_a_visual", "trend_ichimoku_b_visual"}


def test_evaluate_factor_has_no_future_factor_and_reports_small_cross_sections():
    panel = _panel(symbols=4, periods=35)
    result = evaluate_factor(panel, "return_5", horizon=2, quantiles=2, min_cross_section=3)
    assert result["summary"]["eligible_rows"] > 0
    assert set(result["quantile_returns"]["quantile"]) == {1, 2}
    # The final two rows per symbol have no forward price and cannot enter the study.
    final_dates = set(panel.groupby("symbol")["date"].max())
    assert not set(result["eligible"]["date"]).intersection(final_dates)
    assert result["observations"].loc[:, "forward_end_date"].notna().any()


def test_evaluator_supports_default_research_cross_section_of_twenty_symbols():
    result = evaluate_factor(_panel(symbols=24, periods=35), "return_5", horizon=2, quantiles=5, min_cross_section=20)
    assert result["summary"]["eligible_dates"] > 0
    assert result["summary"]["quantile_dates"] > 0
    assert set(result["quantile_returns"]["quantile"]) == {1, 2, 3, 4, 5}


def test_read_only_derived_loader_rejects_duplicate_symbol_provider_files(tmp_path):
    for provider in ("a", "b"):
        path = tmp_path / f"provider={provider}" / "namespace=default" / "symbol=ABC" / "bars.parquet"
        path.parent.mkdir(parents=True)
        _panel(symbols=1, periods=3).assign(symbol="ABC").to_parquet(path, index=False)
    loaded = load_derived_panel(tmp_path)
    assert loaded.empty
    assert loaded.attrs["excluded_duplicate_symbols"] == ["ABC"]
    assert loaded.attrs["loaded_symbols"] == []


def test_ui_request_adapter_requires_explicit_derived_root_and_never_raw_fallback(tmp_path):
    with pytest.raises(ValueError, match="derived_root"):
        evaluate_request({"factor_id": "return_5"})
    with pytest.raises(ValueError, match="read-only"):
        evaluate_request({"derived_root": tmp_path, "factor_id": "return_5", "read_only": False})
