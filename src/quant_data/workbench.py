"""A deliberately small, read-safe Streamlit workbench for the quant project.

This UI does not start downloads, orders, or backtests.  Until the raw daily
bars have been cleaned and adjusted, it only records strategy drafts.
"""

from __future__ import annotations

import json
import os
from html import escape
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


def configured_paths() -> dict[str, Path]:
    """Return server paths without opening credential files or reading secrets."""
    return {
        "data_root": Path(os.environ.get("QUANT_DATA_ROOT", "/home/davidou/quant/data")),
        "reports_root": Path(
            os.environ.get("QUANT_REPORTS_ROOT", "/home/davidou/quant/reports")
        ),
        "credential_file": Path(
            os.environ.get("ALPACA_CREDENTIAL_FILE", "/home/davidou/alpacakey")
        ),
    }


def credential_status(path: Path) -> str:
    """Return a non-sensitive local credential-file status."""
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        return "未配置"
    except OSError:
        return "无法检查"
    return "已配置（权限安全）" if mode & 0o077 == 0 else "已配置（请收紧文件权限）"


def list_reports(reports_root: Path) -> list[dict[str, Any]]:
    """List result artefacts only; report contents are never executed by the UI."""
    if not reports_root.is_dir():
        return []
    suffixes = {".html", ".json", ".csv", ".parquet", ".png"}
    records: list[dict[str, Any]] = []
    for path in reports_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        # Drafts are intentionally not reports and must never be presented as
        # verified backtest output.
        if path.relative_to(reports_root).parts[0] == "drafts":
            continue
        stat = path.stat()
        records.append(
            {
                "报告": str(path.relative_to(reports_root)),
                "类型": path.suffix.lower().lstrip(".").upper(),
                "更新时间": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "大小": _human_size(stat.st_size),
            }
        )
    return sorted(records, key=lambda item: item["更新时间"], reverse=True)


def _human_size(size: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}" if unit != "B" else f"{size} B"
        size /= 1024
    return f"{size:.1f} GB"


def save_draft(reports_root: Path, payload: dict[str, Any]) -> Path:
    """Save a draft separately from formal backtest reports."""
    draft_dir = reports_root / "drafts"
    draft_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    path = draft_dir / f"strategy-draft-{timestamp}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _render_backtest(st: Any, paths: dict[str, Path]) -> None:
    st.header("回测")
    st.warning("正式回测已锁定：当前行情仍为原始/未复权下载数据，尚未完成清洗、复权与退市事件处理。")
    st.caption("可以保存策略草稿，但这里不会读取 raw 数据、不会运行 Backtrader，也不会生成收益结果。")
    with st.form("strategy_draft"):
        name = st.text_input("策略名称", value="双均线草稿")
        universe = st.text_input("股票池", value="SPY, QQQ, AAPL, MSFT, NVDA")
        start = st.date_input("计划回测开始日")
        end = st.date_input("计划回测结束日")
        capital = st.number_input("初始资金（USD）", min_value=0.0, value=100000.0, step=1000.0)
        commission = st.number_input("手续费率", min_value=0.0, value=0.001, step=0.0001, format="%.4f")
        notes = st.text_area("策略说明", placeholder="例如：20/60 日均线交叉，下一交易日开盘成交。")
        saved = st.form_submit_button("保存策略草稿")
    if saved:
        payload = {
            "kind": "strategy_draft",
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "name": name.strip() or "未命名策略",
            "universe": [item.strip().upper() for item in universe.split(",") if item.strip()],
            "start": start.isoformat(),
            "end": end.isoformat(),
            "initial_capital_usd": capital,
            "commission_rate": commission,
            "notes": notes.strip(),
            "execution": "locked_pending_data_cleaning",
        }
        try:
            saved_path = save_draft(paths["reports_root"], payload)
        except OSError as exc:
            st.error(f"草稿未保存：{exc}")
        else:
            st.success(f"草稿已保存：{saved_path.name}")


def _factor_engine() -> tuple[Any | None, str | None]:
    """Load the optional factor engine without making the UI depend on it.

    The workbench deliberately does not calculate factors from bars itself.
    That keeps all reads in the reviewed factor engine and, in particular,
    prevents an accidental fallback to the immutable raw-data partition.
    """
    try:
        from quant_data import factors
    except ImportError:
        return None, "因子引擎尚未安装。"
    except Exception as exc:  # pragma: no cover - defensive UI boundary
        return None, f"因子引擎无法加载：{exc}"
    if not callable(getattr(factors, "list_factors", None)) or not callable(
        getattr(factors, "evaluate_factor", None)
    ):
        return None, "因子引擎接口尚未完成（需要 list_factors 与 evaluate_factor）。"
    return factors, None


def _as_record(value: Any) -> dict[str, Any]:
    """Convert a small factor-engine result object to a display record."""
    if isinstance(value, dict):
        return value
    as_dict = getattr(value, "to_dict", None)
    if callable(as_dict):
        candidate = as_dict()
        if isinstance(candidate, dict):
            return candidate
    fields = getattr(value, "__dict__", None)
    return dict(fields) if isinstance(fields, dict) else {"value": value}


def _available_factors(engine: Any) -> list[dict[str, Any]]:
    """Normalise factor metadata for the research-oriented workbench UI.

    The calculation engine owns the taxonomy.  These fallbacks only keep an
    older registry usable while that taxonomy is rolled out; they must never
    infer factor values or change the calculation selected by the user.
    """
    entries = engine.list_factors()
    result: list[dict[str, Any]] = []
    for entry in entries:
        record = _as_record(entry)
        factor_id = str(record.get("id") or record.get("name") or "")
        if not factor_id:
            continue
        legacy_category = str(record.get("category") or "未标注")
        source = str(record.get("source") or _factor_source_fallback(factor_id, legacy_category))
        research_family = str(record.get("research_family") or legacy_category)
        measurement_type = str(record.get("measurement_type") or "未标注")
        tags = _string_list(record.get("tags") or record.get("factor_tags"))
        required_columns = _string_list(
            record.get("required_columns") or record.get("data_requirements")
        )
        result.append(
            {
                "id": factor_id,
                "名称": str(record.get("display_name") or record.get("label") or factor_id),
                "研究假设": research_family,
                "来源": source,
                "计算机制": measurement_type,
                "标签": tags,
                "数据要求": required_columns,
                "预期用途": str(record.get("intended_use") or record.get("use_case") or "未标注"),
                "近似重复组": str(
                    record.get("redundancy_group") or record.get("duplicate_group") or "未标注"
                ),
                "说明": str(record.get("description") or ""),
                "默认参数": record.get("default_parameters") or record.get("parameters") or {},
            }
        )
    return result


def _string_list(value: Any) -> list[str]:
    """Return display-safe metadata labels without treating a string as chars."""
    if value is None:
        return []
    if isinstance(value, str):
        return [value] if value else []
    if isinstance(value, (list, tuple, set, frozenset)):
        return [str(item) for item in value if str(item)]
    return [str(value)]


def _factor_source_fallback(factor_id: str, legacy_category: str) -> str:
    """Give pre-taxonomy registries a conservative source label."""
    if factor_id.startswith("qlib158_"):
        return "Alpha158"
    if factor_id.startswith("qlib360_") or legacy_category == "Alpha360":
        return "Alpha360"
    if factor_id.startswith("ta_"):
        return "ta"
    return "自定义"


def _metadata_filter_options(items: list[dict[str, Any]], field: str) -> list[str]:
    return ["全部"] + sorted({str(item[field]) for item in items if str(item[field])})


def _render_factor_metadata(st: Any, factor: dict[str, Any]) -> None:
    """Render escaped, documentation-first detail without touching market data."""
    from quant_data.factor_catalogue import availability, detail

    info = detail(factor)
    runnable, _ = availability(str(factor["id"]))
    st.subheader("因子说明")
    st.markdown(f"### {escape(str(info.get('name', factor['名称'])))}\n`{escape(str(factor['id']))}`")
    if runnable:
        st.caption("定义已登记 · 尚未验证预测能力")
    else:
        st.warning(info["availability"])
    st.markdown("**中文定义**")
    st.write(info["definition"])
    st.markdown("**当前计算公式 / 默认窗口**")
    st.write(info["formula"])
    st.markdown("**用途与解读**")
    st.write(info["purpose"])
    source = str(info["source"])
    if source == "ta":
        source_label = "来源：[ta 官方定义](https://technical-analysis-library-in-python.readthedocs.io/en/latest/ta.html)"
    elif source.startswith("Alpha"):
        source_label = "来源：[Qlib 参考定义](https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py)"
        if source == "Alpha158":
            source_label += " · 本地兼容口径"
    else:
        source_label = f"来源：{escape(source)}"
    st.caption(f"{source_label} · 相关指标：{escape(str(info['related']))}")
    st.caption(escape(str(info["data_requirements"])))
    with st.expander("限制说明", expanded=False):
        st.write(info["limitations"])


def _catalogue_selection(
    state: dict[str, Any], factors: list[dict[str, Any]], category: str, query: str,
) -> list[dict[str, Any]]:
    """Keep selection valid across category changes and empty searches.

    The helper is deliberately data-free so the catalogue still renders before
    structural-v1 exists and is straightforward to unit test.
    """
    matches = [item for item in factors if category == "全部" or item["研究假设"] == category]
    needle = query.casefold().strip()
    if needle:
        matches = [item for item in matches if needle in " ".join(
            [str(item.get("目录名称", item["名称"])), str(item.get("目录说明", item["说明"])), str(item.get("目录用途", item.get("预期用途", ""))),
             str(item["名称"]), str(item["id"]), str(item["说明"]), str(item["计算机制"])]
        ).casefold()]
    matches.sort(key=lambda item: (str(item["名称"]), str(item["id"])))
    current = state.get("factor_catalogue_selected")
    match_ids = {item["id"] for item in matches}
    if current not in match_ids:
        state["factor_catalogue_selected"] = matches[0]["id"] if matches else None
    return matches


def _select_catalogue_category(state: dict[str, Any], category: str) -> None:
    state["factor_catalogue_category"] = category
    state["factor_catalogue_selected"] = None


def _select_catalogue_factor(state: dict[str, Any], factor_id: str) -> None:
    state["factor_catalogue_selected"] = factor_id


def _select_page(state: dict[str, Any], page: str) -> None:
    """Select a top-level section and its deterministic default subpage."""
    state["workbench_page"] = page
    state["workbench_subpage"] = WORKBENCH_PAGE_BY_NAME[page]["subpages"][0]["name"]


def _select_subpage(state: dict[str, Any], page: str, subpage: str) -> None:
    """Select a validated second-level page without changing its section."""
    state["workbench_page"] = page
    state["workbench_subpage"] = subpage


# The navigation catalogue is deliberately independent from the renderers: it
# keeps page labels, stable button keys, and the availability boundary in one
# reviewed place. Planned pages must never gain an implicit data or trading
# action simply by appearing in the sidebar.
# Navigation and availability are intentionally centralised.  Merely adding an
# item here cannot invoke data access, a background job, or a trading action.
WORKBENCH_PAGES: tuple[dict[str, Any], ...] = (
    {"name": "总览", "subpages": ({"name": "我的工作台"}, {"name": "运行概览"})},
    {"name": "市场与数据", "subpages": (
        {"name": "行情浏览"}, {"name": "资产与合约"}, {"name": "数据目录"},
        {"name": "股票事件"}, {"name": "指数成分"}, {"name": "衍生品资料"},
    )},
    {"name": "研究实验室", "subpages": (
        {"name": "因子百科", "renderer": "factor_lab"},
        {"name": "因子研究", "renderer": "factor_lab"},
        {"name": "数据集与资产池"}, {"name": "实验记录"},
    )},
    {"name": "策略中心", "subpages": ({"name": "策略模板"}, {"name": "我的策略"}, {"name": "策略详情"})},
    {"name": "回测中心", "subpages": (
        {"name": "新建回测", "renderer": "backtest"}, {"name": "回测任务"},
        {"name": "回测报告", "renderer": "results"}, {"name": "结果对比"}, {"name": "稳健性验证"},
    )},
    {"name": "组合与账户", "subpages": (
        {"name": "组合配置"}, {"name": "模拟账户"}, {"name": "持仓与资金"}, {"name": "资金流水"},
    )},
    {"name": "模拟交易", "subpages": (
        {"name": "运行实例", "renderer": "paper"}, {"name": "交易监控"},
        {"name": "订单与成交"}, {"name": "同步与对账"},
    )},
    {"name": "风控中心", "subpages": ({"name": "风控规则"}, {"name": "风险看板"}, {"name": "风险事件"})},
    {"name": "绩效分析", "subpages": (
        {"name": "收益与风险", "renderer": "results"}, {"name": "成本分析"}, {"name": "归因分析"},
    )},
    {"name": "系统设置", "subpages": (
        {"name": "数据源与交易连接"}, {"name": "任务与资源"},
        {"name": "用户与安全"}, {"name": "备份与维护"},
    )},
)
DEFAULT_WORKBENCH_PAGE = "研究实验室"
WORKBENCH_PAGE_BY_NAME = {item["name"]: item for item in WORKBENCH_PAGES}


def _current_page(state: dict[str, Any]) -> tuple[str, str]:
    """Return valid navigation state and repair old or stale session values."""
    page = state.get("workbench_page", DEFAULT_WORKBENCH_PAGE)
    if page not in WORKBENCH_PAGE_BY_NAME:
        page = DEFAULT_WORKBENCH_PAGE
    subpages = WORKBENCH_PAGE_BY_NAME[page]["subpages"]
    allowed_subpages = {item["name"] for item in subpages}
    subpage = state.get("workbench_subpage")
    if subpage not in allowed_subpages:
        subpage = subpages[0]["name"]
    state["workbench_page"] = page
    state["workbench_subpage"] = subpage
    return page, subpage


def _render_coming_soon(st: Any, page: dict[str, Any], subpage: dict[str, Any]) -> None:
    """Render an inert placeholder for a planned second-level workbench page."""
    st.header(f"{page['name']} / {subpage['name']}")
    st.caption("敬请期待")
    st.info("规划中：此模块当前不可用，不会启动后台任务、读取账户或发起交易。")
    st.caption("开放前将先完成规则、数据、权限和验收验证。")


def _render_factor_result(st: Any, result: Any) -> None:
    """Render only engine-produced results; this function never opens bars."""
    payload = _as_record(result)
    metrics = payload.get("metrics") or {}
    if not isinstance(metrics, dict):
        metrics = _as_record(metrics)
    metric_items = [(str(key), value) for key, value in metrics.items() if value is not None]
    if metric_items:
        columns = st.columns(min(4, len(metric_items)))
        for column, (label, value) in zip(columns, metric_items):
            if isinstance(value, float):
                column.metric(label, f"{value:.4f}")
            else:
                column.metric(label, str(value))

    # Expected optional result fields are tables produced by the engine, such
    # as ic_series, quantile_returns and coverage.  Streamlit accepts pandas
    # data frames and ordinary record lists alike.
    ic_series = payload.get("ic_series")
    if ic_series is not None:
        st.subheader("IC 时序")
        try:
            st.line_chart(ic_series)
        except Exception:
            st.dataframe(ic_series, use_container_width=True)

    quantile_returns = payload.get("quantile_returns")
    if quantile_returns is not None:
        st.subheader("分层收益")
        try:
            st.line_chart(quantile_returns)
        except Exception:
            st.dataframe(quantile_returns, use_container_width=True)

    coverage = payload.get("coverage")
    if coverage is not None:
        st.subheader("覆盖率与换手")
        st.dataframe(coverage, use_container_width=True, hide_index=True)

    diagnostics = payload.get("diagnostics")
    if diagnostics:
        with st.expander("数据质量与计算说明"):
            st.json(diagnostics)


def _render_factor_lab(st: Any, paths: dict[str, Path]) -> None:
    """Render a read-only factor research surface against structural-v1 only."""
    st.header("因子实验室")
    st.markdown("""
    <style>
    [data-testid="stMainBlockContainer"] {padding-top: 2rem;}
    .st-key-factor-lab-shell [data-testid="stButton"] button, .st-key-workbench-sidebar-shell [data-testid="stButton"] button {justify-content:flex-start !important;text-align:left !important;width:100%;border-radius:.5rem;background:transparent !important;border:1px solid transparent !important;box-shadow:none !important;}
    .st-key-factor-lab-shell [data-testid="stButton"] button > div, .st-key-factor-lab-shell [data-testid="stButton"] button > div div, .st-key-workbench-sidebar-shell [data-testid="stButton"] button > div, .st-key-workbench-sidebar-shell [data-testid="stButton"] button > div div {width:100% !important;flex:1 1 auto !important;justify-content:flex-start !important;align-items:flex-start !important;margin-left:0 !important;margin-right:0 !important;}
    .st-key-factor-lab-shell [data-testid="stButton"] button p, .st-key-factor-lab-shell [data-testid="stButton"] button [data-testid="stMarkdownContainer"], .st-key-workbench-sidebar-shell [data-testid="stButton"] button p, .st-key-workbench-sidebar-shell [data-testid="stButton"] button [data-testid="stMarkdownContainer"] {text-align:left !important;width:100%;}
    .st-key-factor-lab-shell [data-testid="stButton"] button [data-testid="stMarkdownContainer"], .st-key-workbench-sidebar-shell [data-testid="stButton"] button [data-testid="stMarkdownContainer"] {display:block !important;}
    .st-key-factor-lab-shell [data-testid="stButton"] button:hover, .st-key-workbench-sidebar-shell [data-testid="stButton"] button:hover {border-color:rgba(94,234,212,.45) !important;}
    .st-key-factor-lab-shell [data-testid="stButton"] button[kind="primary"], .st-key-workbench-sidebar-shell [data-testid="stButton"] button[kind="primary"] {background:#16423c !important;border-color:rgba(94,234,212,.72) !important;color:rgb(204,251,241) !important;}
    .st-key-factor-lab-shell [data-testid="stTextInput"] input {border-radius: .55rem;}
    </style>
    """, unsafe_allow_html=True)
    derived_root = paths["data_root"] / "derived" / "structural-v1"

    engine, problem = _factor_engine()
    if engine is None:
        st.info(problem or "因子引擎尚不可用。")
        return
    try:
        factors = _available_factors(engine)
    except Exception as exc:
        st.error(f"读取因子注册表失败：{exc}")
        return
    if not factors:
        st.info("因子注册表为空。等待内置因子发布后可在此运行实验。")
        return

    # Buttons keep all choices keyboard-accessible; this is intentionally not
    # radio, checkbox, or dataframe-selection navigation.
    from quant_data.factor_catalogue import CATALOGUE_CATEGORIES, availability
    from quant_data.factor_catalogue import detail as catalogue_detail
    for factor in factors:
        factor_detail = catalogue_detail(factor)
        factor["目录名称"] = str(factor_detail.get("name", factor["名称"]))
        factor["目录说明"] = str(factor_detail.get("definition", factor["说明"]))
        factor["目录用途"] = str(factor_detail.get("purpose", factor["预期用途"]))
    st.caption(f"浏览 {len(factors)} 个定义 · 按测量对象分类")
    families = [family for family in CATALOGUE_CATEGORIES if any(item["研究假设"] == family for item in factors)]
    state = st.session_state
    active_category = state.get("factor_catalogue_category", "全部")
    if active_category not in {"全部", *families}:
        active_category = "全部"
    # Keep these keys present even on a first no-data-directory render, so
    # callbacks and AppTest can inspect a deterministic initial UI state.
    state["factor_catalogue_category"] = active_category
    with st.container(key="factor-lab-shell"):
        navigation, catalogue = st.columns([1, 3], gap="large")
        with navigation:
            st.caption("分类")
            for category in ["全部", *families]:
                count = len(factors) if category == "全部" else sum(item["研究假设"] == category for item in factors)
                st.button(f"{category} · {count}", key=f"factor_category_{category}", use_container_width=True,
                          type="primary" if category == active_category else "secondary",
                          on_click=_select_catalogue_category, args=(state, category))
        with catalogue:
            st.caption("因子目录")
            query = st.text_input("搜索名称、ID、说明", key="factor_catalogue_search", placeholder="例如 RSI、qlib158_ROC、波动")
            visible = _catalogue_selection(state, factors, active_category, query)
            if not visible:
                st.info("没有匹配项。清空搜索或切换分类后继续浏览。")
            else:
                with st.container(height=290, key="factor-catalogue-list"):
                    for item in visible:
                        runnable, note = availability(str(item["id"]))
                        brief = str(item["目录用途"]).replace("\n", " ")[:58]
                        label = f"**{'⚠ ' if not runnable else ''}{item['目录名称']}**  \n`{item['id']}` · {brief}"
                        st.button(label, key=f"factor_item_{item['id']}", use_container_width=True,
                                  type="primary" if state.get("factor_catalogue_selected") == item["id"] else "secondary", help=note,
                                  on_click=_select_catalogue_factor, args=(state, item["id"]))
                selected = next((item for item in visible if item["id"] == state.get("factor_catalogue_selected")), visible[0])
                _render_factor_metadata(st, selected)

    if not visible:
        return
    selected = next(item for item in visible if item["id"] == state.get("factor_catalogue_selected"))
    runnable, note = availability(str(selected["id"]))
    with st.expander("因子检验（固定参数、只读）", expanded=False):
        st.caption("内置因子使用详情中列出的固定默认窗口；当前版本不支持 JSON 参数输入。")
        if not derived_root.is_dir():
            st.warning("尚未发现 structural-v1 数据层：目录可浏览，检验暂不可运行。")
            return
        if not runnable:
            st.warning(note)
            return
        with st.form("factor_experiment"):
            universe = st.text_input(
                "股票范围（逗号分隔）",
                value=(
                    "AAPL,MSFT,NVDA,AMZN,META,GOOGL,GOOG,AVGO,TSLA,BRK.B,LLY,JPM,V,MA,"
                    "XOM,UNH,COST,HD,PG,JNJ,ABBV,ORCL,KO,PEP,MRK,CRM,ADBE,NFLX,AMD,QCOM"
                ),
                help="因子 IC 是横截面指标；首版至少需要 20 只有效股票。",
            )
            left, middle, right = st.columns(3)
            with left:
                start = st.date_input("开始日期", value=date(2020, 1, 2))
            with middle:
                end = st.date_input("结束日期", value=date.today())
            with right:
                holding_period = st.number_input("持有期（交易日）", min_value=1, max_value=252, value=20)
            quantiles = st.slider("分组数", min_value=2, max_value=10, value=5)
            submitted = st.form_submit_button("运行因子检验")
        if not submitted:
            return

    request = {
        "factor_id": selected["id"],
        "symbols": [item.strip().upper() for item in universe.split(",") if item.strip()],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "holding_period": int(holding_period),
        "quantiles": int(quantiles),
        "parameters": {},
        # This explicit path is part of the API contract: the engine must not
        # discover, infer, or fall back to a raw input directory.
        "derived_root": derived_root,
        "read_only": True,
    }
    try:
        with st.spinner("正在从 structural-v1 计算因子与检验指标…"):
            result = engine.evaluate_factor(request)
    except Exception as exc:
        st.error(f"因子实验未完成：{exc}")
        return
    st.success("因子实验完成。")
    _render_factor_result(st, result)


def _render_results(st: Any, paths: dict[str, Path]) -> None:
    st.header("结果")
    st.caption("这里只列出未来正式回测写入 reports 目录的文件；不会把未清洗行情视为回测结果。")
    reports = list_reports(paths["reports_root"])
    if not reports:
        st.info("暂无正式回测报告。完成数据清洗、复权和验证后，报告会出现在这里。")
        return
    st.dataframe(reports, use_container_width=True, hide_index=True)


def _render_paper(st: Any, paths: dict[str, Path]) -> None:
    st.header("模拟盘")
    st.info("仅显示配置状态。当前页面不读取密钥、不发起 Alpaca 请求、不创建或提交订单。")
    st.metric("Alpaca Paper 凭证", credential_status(paths["credential_file"]))
    st.caption("模拟盘接入将在回测数据完成清洗和策略验证后单独启用；仍只使用 Paper Trading，不连接真实账户。")


def main() -> None:
    import streamlit as st

    st.set_page_config(page_title="量化工作台", page_icon="📈", layout="wide")
    st.markdown(
        """
        <style>
        .st-key-workbench-sidebar-shell [data-testid="stButton"] button {
            justify-content:flex-start !important; text-align:left !important; width:100%;
            border-radius:.5rem; background:transparent !important;
            border:1px solid transparent !important; box-shadow:none !important;
        }
        .st-key-workbench-sidebar-shell [data-testid="stButton"] button:hover {
            border-color:rgba(94,234,212,.45) !important;
        }
        .st-key-workbench-sidebar-shell [data-testid="stButton"] button[kind="primary"] {
            background:#16423c !important; border-color:rgba(94,234,212,.72) !important;
            color:rgb(204,251,241) !important;
        }
        .st-key-workbench-sidebar-shell .st-key-workbench-submenu [data-testid="stButton"] button {
            min-height: 2rem !important; margin-left: .55rem !important; width: calc(100% - .55rem) !important;
            font-size: .88rem !important; border-radius: .4rem !important;
        }
        .st-key-workbench-sidebar-shell .st-key-workbench-submenu [data-testid="stButton"] button[kind="primary"] {
            background:#123832 !important; border-left: 3px solid rgb(94,234,212) !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    paths = configured_paths()
    page, subpage = _current_page(st.session_state)
    with st.sidebar:
        with st.container(key="workbench-sidebar-shell"):
            st.caption("量化工作台 · 个人研究")
            st.subheader("功能")
            for item in WORKBENCH_PAGES:
                candidate = str(item["name"])
                st.button(candidate, key=f"workbench_page_{candidate}", use_container_width=True,
                          type="primary" if candidate == page else "secondary",
                          on_click=_select_page, args=(st.session_state, candidate))
                if candidate == page:
                    with st.container(key="workbench-submenu"):
                        for child in item["subpages"]:
                            child_name = str(child["name"])
                            st.button(
                                f"↳ {child_name}",
                                key=f"workbench_subpage_{candidate}_{child_name}",
                                use_container_width=True,
                                type="primary" if child_name == subpage else "secondary",
                                on_click=_select_subpage,
                                args=(st.session_state, candidate, child_name),
                            )
    selected = WORKBENCH_PAGE_BY_NAME[page]
    selected_subpage = next(item for item in selected["subpages"] if item["name"] == subpage)
    renderer = selected_subpage.get("renderer")
    if renderer == "factor_lab":
        _render_factor_lab(st, paths)
    elif renderer == "backtest":
        _render_backtest(st, paths)
    elif renderer == "paper":
        _render_paper(st, paths)
    elif renderer == "results":
        _render_results(st, paths)
    else:
        _render_coming_soon(st, selected, selected_subpage)


if __name__ == "__main__":
    main()
