"""Browser-level Streamlit checks for catalogue button callbacks.

They intentionally point at an absent data root: browsing documentation must
not depend on derived Parquet files.
"""

from pathlib import Path

import pytest

streamlit = pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest


WORKBENCH = Path(__file__).parents[1] / "src" / "quant_data" / "workbench.py"


def _app(monkeypatch, tmp_path):
    monkeypatch.setenv("QUANT_DATA_ROOT", str(tmp_path / "absent-data"))
    app = AppTest.from_file(str(WORKBENCH))
    app.run()
    assert not app.exception
    return app


def test_catalogue_browses_without_data_and_category_callback_updates_selection(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    assert app.session_state["factor_catalogue_category"] == "全部"
    assert app.session_state["factor_catalogue_selected"]
    assert app.button(key="factor_category_全部").proto.type == "primary"
    assert app.button(key="factor_category_震荡与强弱").proto.type == "secondary"
    app.button(key="factor_category_震荡与强弱").click().run()
    assert not app.exception
    assert app.session_state["factor_catalogue_category"] == "震荡与强弱"
    assert app.session_state["factor_catalogue_selected"]
    assert app.button(key="factor_category_震荡与强弱").proto.type == "primary"
    assert app.button(key="factor_category_全部").proto.type == "secondary"


def test_factor_button_callback_is_immediate_and_sidebar_navigation_works(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.button(key="factor_item_rsi_14").click().run()
    assert not app.exception
    assert app.session_state["factor_catalogue_selected"] == "rsi_14"
    app.button(key="workbench_page_绩效分析").click().run()
    assert not app.exception
    assert app.session_state["workbench_page"] == "绩效分析"
    assert app.session_state["workbench_subpage"] == "收益与风险"


def test_sidebar_catalogue_has_all_second_level_pages_and_expands_current_section(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    expected = {
        "总览": ["我的工作台", "运行概览"],
        "市场与数据": ["行情浏览", "资产与合约", "数据目录", "股票事件", "指数成分", "衍生品资料"],
        "研究实验室": ["因子百科", "因子研究", "数据集与资产池", "实验记录"],
        "策略中心": ["策略模板", "我的策略", "策略详情"],
        "回测中心": ["新建回测", "回测任务", "回测报告", "结果对比", "稳健性验证"],
        "组合与账户": ["组合配置", "模拟账户", "持仓与资金", "资金流水"],
        "模拟交易": ["运行实例", "交易监控", "订单与成交", "同步与对账"],
        "风控中心": ["风控规则", "风险看板", "风险事件"],
        "绩效分析": ["收益与风险", "成本分析", "归因分析"],
        "系统设置": ["数据源与交易连接", "任务与资源", "用户与安全", "备份与维护"],
    }
    pages = list(expected)
    assert [button.label for button in app.button if button.key.startswith("workbench_page_")] == pages
    assert app.session_state["workbench_page"] == "研究实验室"
    assert app.session_state["workbench_subpage"] == "因子百科"
    assert app.button(key="workbench_page_研究实验室").proto.type == "primary"
    assert app.button(key="workbench_page_总览").proto.type == "secondary"
    assert [button.label.removeprefix("↳ ") for button in app.button if button.key.startswith("workbench_subpage_")] == expected["研究实验室"]
    assert app.button(key="workbench_subpage_研究实验室_因子百科").proto.type == "primary"

    app.button(key="workbench_page_总览").click().run()
    assert not app.exception
    assert app.session_state["workbench_page"] == "总览"
    assert app.session_state["workbench_subpage"] == "我的工作台"
    assert app.button(key="workbench_page_总览").proto.type == "primary"
    assert app.button(key="workbench_page_研究实验室").proto.type == "secondary"
    assert [button.label.removeprefix("↳ ") for button in app.button if button.key.startswith("workbench_subpage_")] == expected["总览"]
    assert "敬请期待" in [element.value for element in app.caption]
    assert "规划中：此模块当前不可用" in " ".join(element.value for element in app.info)

    for page, subpages in expected.items():
        app.button(key=f"workbench_page_{page}").click().run()
        assert [button.label.removeprefix("↳ ") for button in app.button if button.key.startswith("workbench_subpage_")] == subpages


def test_second_level_selection_updates_state_and_placeholder(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.button(key="workbench_page_市场与数据").click().run()
    app.button(key="workbench_subpage_市场与数据_资产与合约").click().run()
    assert not app.exception
    assert app.session_state["workbench_page"] == "市场与数据"
    assert app.session_state["workbench_subpage"] == "资产与合约"
    assert app.button(key="workbench_subpage_市场与数据_资产与合约").proto.type == "primary"
    assert "市场与数据 / 资产与合约" in [element.value for element in app.header]


@pytest.mark.parametrize(
    ("page", "subpage", "expected_title"),
    [
        ("研究实验室", "因子百科", "因子实验室"),
        ("研究实验室", "因子研究", "因子实验室"),
        ("回测中心", "新建回测", "回测"),
        ("回测中心", "回测报告", "结果"),
        ("模拟交易", "运行实例", "模拟盘"),
        ("绩效分析", "收益与风险", "结果"),
    ],
)
def test_existing_routes_remain_available(monkeypatch, tmp_path, page, subpage, expected_title):
    app = _app(monkeypatch, tmp_path)
    app.button(key=f"workbench_page_{page}").click().run()
    app.button(key=f"workbench_subpage_{page}_{subpage}").click().run()
    assert not app.exception
    assert app.session_state["workbench_page"] == page
    assert app.session_state["workbench_subpage"] == subpage
    assert expected_title in [element.value for element in app.header]


def test_stale_session_page_falls_back_to_factor_lab(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.session_state["workbench_page"] = "结果"
    app.session_state["workbench_subpage"] = "旧页面"
    app.run()
    assert not app.exception
    assert app.session_state["workbench_page"] == "研究实验室"
    assert app.session_state["workbench_subpage"] == "因子百科"


def test_stale_second_level_page_falls_back_to_current_section_default(monkeypatch, tmp_path):
    app = _app(monkeypatch, tmp_path)
    app.session_state["workbench_page"] = "回测中心"
    app.session_state["workbench_subpage"] = "不存在"
    app.run()
    assert not app.exception
    assert app.session_state["workbench_page"] == "回测中心"
    assert app.session_state["workbench_subpage"] == "新建回测"
