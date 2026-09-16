from pathlib import Path

from quant_data.factor_catalogue import CATALOGUE_CATEGORIES, availability, detail
from quant_data.factor_lab import list_factors
from quant_data.factors import list_factors as listed_for_workbench
from quant_data.workbench import _catalogue_selection, _render_factor_lab


def test_catalogue_uses_measurement_categories_and_documents_every_visible_factor():
    core = {item["name"]: item for item in list_factors()}
    assert core["rsi_14"]["research_family"] == "震荡与强弱"
    assert core["ta_trend_macd"]["research_family"] == "收益与动量"
    assert core["qlib158_IMAX20"]["research_family"] == "趋势与均线"
    assert core["qlib158_RANK20"]["research_family"] == "价格位置与通道"
    assert core["qlib158_VMA20"]["research_family"] == "成交量"
    assert core["qlib158_BETA20"]["research_family"] == "统计与回归"
    assert set(item["research_family"] for item in core.values()).issubset(CATALOGUE_CATEGORIES)

    for item in listed_for_workbench():
        record = {"id": item["id"], "名称": item["display_name"], "说明": item["description"],
                  "数据要求": item["required_columns"], "来源": item["source"],
                  "预期用途": item["intended_use"], "近似重复组": item["redundancy_group"]}
        explanation = detail(record)
        assert all(explanation[key] for key in ("definition", "formula", "purpose", "data_requirements", "limitations", "source"))


def test_hidden_alpha360_and_honest_unavailable_ta_ids():
    assert not any(item["id"].startswith("qlib360_") for item in listed_for_workbench())
    assert availability("ta_trend_psar")[0] is False
    assert "暂不可运行" in availability("ta_trend_ichimoku_a_visual")[1]


def test_search_and_category_changes_keep_a_valid_or_empty_selection():
    factors = [
        {"id": "rsi_14", "名称": "RSI(14)", "说明": "Wilder RSI", "计算机制": "震荡器", "研究假设": "震荡与强弱"},
        {"id": "return_20", "名称": "20日收益", "说明": "收益率", "计算机制": "收益", "研究假设": "收益与动量"},
    ]
    state = {"factor_catalogue_selected": "rsi_14"}
    assert [item["id"] for item in _catalogue_selection(state, factors, "收益与动量", "")] == ["return_20"]
    assert state["factor_catalogue_selected"] == "return_20"
    assert _catalogue_selection(state, factors, "全部", "no such factor") == []
    assert state["factor_catalogue_selected"] is None
    assert [item["id"] for item in _catalogue_selection(state, factors, "全部", "rsi")] == ["rsi_14"]


def test_catalogue_renders_without_derived_data_and_has_no_legacy_navigation():
    source = Path(__file__).parents[1] / "src" / "quant_data" / "workbench.py"
    text = source.read_text(encoding="utf-8")
    lab = text[text.index("def _render_factor_lab"):text.index("def _render_results")]
    assert ".radio(" not in lab
    assert "selection_mode=" not in lab
    assert "factor_category_" in lab and "factor_item_" in lab


def test_documentation_covers_every_catalogue_id_with_a_chinese_name():
    for item in list_factors():
        info = detail({"id": item["name"], "名称": item["label"], "来源": item["source"]})
        assert info["name"]
        assert all(info[key] for key in ("definition", "formula", "purpose", "limitations",
                                         "data_requirements", "source", "related", "availability"))
    assert detail({"id": "ta_momentum_rsi", "名称": "Momentum · RSI"})["name"] == "相对强弱指数"


def test_custom_formulas_and_ta_variants_use_their_actual_inputs():
    assert "pct_change(20)" in detail({"id": "return_20"})["formula"]
    assert "std(ddof=1)" in detail({"id": "volatility_20"})["formula"]
    assert "SMA(close,60)" in detail({"id": "sma_crossover_20_60"})["formula"]

    macd = detail({"id": "ta_trend_macd"})
    signal = detail({"id": "ta_trend_macd_signal"})
    histogram = detail({"id": "ta_trend_macd_diff"})
    assert "12" in macd["formula"] and "26" in macd["formula"]
    assert "9期 EMA" in signal["formula"]
    assert "MACD-信号线" in histogram["formula"]
    assert "数学输入：close" in detail({"id": "ta_momentum_rsi"})["data_requirements"]
    assert "数学输入：volume" in detail({"id": "ta_momentum_pvo"})["data_requirements"]


def test_alpha158_documents_local_formula_differences_and_unavailable_inputs():
    roc = detail({"id": "qlib158_ROC20"})
    assert "pct_change(20)" in roc["formula"]
    assert "不同于 Qlib" in roc["purpose"]
    assert "ddof=0" in detail({"id": "qlib158_STD20"})["formula"]
    assert "average ties" in detail({"id": "qlib158_RANK20"})["formula"]
    assert "argmin(low" in detail({"id": "qlib158_IMXD20"})["formula"]
    assert availability("qlib158_IMXD20")[0] is True
    assert availability("qlib158_VWAP2")[0] is False
    assert availability("qlib158_CLOSE0")[0] is False
    assert "数学输入：close、volume" in detail({"id": "qlib158_CORD20"})["data_requirements"]
    assert "收益变化" in detail({"id": "qlib158_SUMD20"})["purpose"]
    beta = detail({"id": "qlib158_BETA20"})
    rsqr = detail({"id": "qlib158_RSQR20"})
    assert "不是对大盘" in beta["purpose"]
    assert "不是预测准确率" in rsqr["purpose"]


def test_ta_011_wrapper_defaults_match_server_verified_formulas():
    assert "平盘及首行都加volume" in detail({"id": "ta_volume_obv"})["formula"]
    assert "high.diff()" in detail({"id": "ta_volume_em"})["formula"]
    assert "原EM不平滑" in detail({"id": "ta_volume_em"})["formula"]
    assert "×100" in detail({"id": "ta_volatility_bbw"})["formula"]
    assert "SMA((high+low+close)/3,10)" in detail({"id": "ta_volatility_kcc"})["formula"]
    assert "4high-2low+close" in detail({"id": "ta_volatility_kch"})["formula"]
    assert "×100" in detail({"id": "ta_volatility_kcw"})["formula"]
    donchian = detail({"id": "ta_volatility_dcw"})
    assert "SMA(close,20)" in donchian["formula"]
    assert "数学输入：high、low、close" in donchian["data_requirements"]
    assert "10期" in detail({"id": "ta_volatility_atr"})["formula"]
    assert "argmax(high)/25" in detail({"id": "ta_trend_aroon_up"})["formula"]
    assert "argmin(low)/25" in detail({"id": "ta_trend_aroon_down"})["formula"]
    assert "shift(11,fill_value=close.mean())" in detail({"id": "ta_trend_dpo"})["formula"]
    assert "权重1/2/3/4" in detail({"id": "ta_trend_kst"})["formula"]
    assert "100×" in detail({"id": "ta_momentum_tsi"})["formula"]
    assert "4×BP/TR(7)" in detail({"id": "ta_momentum_uo"})["formula"]
    assert "100×" in detail({"id": "ta_others_cr"})["formula"]
    assert "填为0" in detail({"id": "ta_volume_adi"})["formula"]
    assert "填为0" in detail({"id": "ta_volume_cmf"})["formula"]
    assert "14期" in detail({"id": "ta_trend_adx"})["formula"]
    assert "20期" in detail({"id": "ta_trend_cci"})["formula"]
