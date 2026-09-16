"""Read-only technical-factor calculation and point-in-time evaluation.

The factor lab deliberately consumes a *derived* Parquet layer, never the raw
download directory.  It creates in-memory panels only; callers choose where
to persist reports, if at all.  Indicators at date ``t`` use data available by
that close.  Forward returns are then calculated from ``close[t]`` to a later
available close, so they are suitable for research but not a claim of an
executable intraday fill price.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

try:  # The project dependency supplies standard technical-indicator semantics.
    from ta import add_all_ta_features
    from ta.momentum import RSIIndicator
    from ta.volatility import AverageTrueRange
except ImportError:  # pragma: no cover - permits core-only inspection environments
    add_all_ta_features = None  # type: ignore[assignment,misc]
    RSIIndicator = None  # type: ignore[assignment,misc]
    AverageTrueRange = None  # type: ignore[assignment,misc]


REQUIRED_PANEL_COLUMNS = {"date", "symbol", "close"}

# This identifies the formulas implemented below, rather than a data snapshot
# or a claim that the factors have validated investment performance.  It must
# travel with future experiment records once P4-02 persists calculations.
FACTOR_FORMULA_VERSION = "p4-01-v1"
TA_DEPENDENCY_CONTRACT = "ta 0.11.x"
QLIB_REFERENCE_CONTRACT = "qlib Alpha158DL source, reviewed 2026-09-09"


@dataclass(frozen=True)
class FactorDefinition:
    """A stable, UI-facing factor specification.

    ``ta_column`` is deliberately the upstream ``ta`` output name rather than
    a Python callable.  That lets us use ``add_all_ta_features`` consistently
    per symbol, and preserves a stable public factor id if ta reorganises its
    implementation classes in a later release.
    """

    name: str
    label: str
    required_columns: tuple[str, ...]
    description: str
    category: str = "自定义"
    ta_column: str | None = None
    # These fields deliberately describe a factor on different axes.  ``category``
    # is kept for backwards compatibility with the first workbench release;
    # callers should use the taxonomy fields below for new work.
    research_family: str = "统计与回归"
    measurement_type: str = "未标注"
    source: str = "自定义"
    redundancy_group: str = "未分组"
    intended_use: str = "单因子研究"
    tags: tuple[str, ...] = ()


_RESEARCH_FAMILIES = frozenset({
    "收益与动量", "趋势与均线", "价格位置与通道", "震荡与强弱", "波动与风险",
    "成交量", "量价关系", "K线结构", "统计与回归", "机器学习输入",
})


def _taxonomy(
    name: str, category: str, required_columns: tuple[str, ...],
) -> dict[str, object]:
    """Return deterministic, explainable metadata for every registry entry.

    This is a *research catalogue*, not a statement that an indicator has one
    immutable trading interpretation.  In particular, RSI and bands can be
    used either as reversal signals or as trend filters.  The selected family
    says what the value primarily measures; ``intended_use`` makes that
    distinction explicit.  Prefix and vocabulary rules are intentionally kept
    here rather than inferred from a dataframe, making the catalogue stable.
    """
    source = "自定义"
    family, mechanism, group, use = "统计与回归", "派生统计量", "未分组", "单因子研究"
    tags: list[str] = []

    if name.startswith("qlib360_"):
        field = name.removeprefix("qlib360_").rstrip("0123456789")
        requirements = tuple(required_columns)
        return {
            "research_family": "机器学习输入", "measurement_type": "归一化原始时序窗口",
            "source": "Alpha360", "redundancy_group": f"Alpha360 {field} 滞后窗口",
            "intended_use": "仅作为联合机器学习特征输入，不建议单独选股",
            "tags": ("Qlib", "时序特征", field, "隐藏于单因子列表"),
            "data_requirements": requirements,
        }

    if name.startswith("qlib158_"):
        source = "Alpha158"
        feature = name.removeprefix("qlib158_")
        kind = feature.rstrip("0123456789")
        if feature in _A158_KBAR:
            family, mechanism, group = "K线结构", "K线实体与影线", "K线形态"
        elif kind in {"VOLUME", "VMA"}:
            family, mechanism, group = "成交量", "成交量归一化与均值", "相对成交量"
        elif kind in {"CORR", "CORD"}:
            family, mechanism, group = "量价关系", "量价相关性", "量价相关"
        elif kind in {"STD"}:
            family, mechanism, group = "波动与风险", "滚动离散度", "滚动波动率"
        elif kind in {"BETA", "RSQR", "RESI"}:
            family, mechanism, group = "统计与回归", "滚动线性回归", "滚动回归"
        elif kind in {"IMAX", "IMIN", "IMXD"}:
            family, mechanism, group = "趋势与均线", "极值出现时点", "趋势时序"
        elif kind in {"MAX", "MIN", "QTLU", "QTLD", "RANK", "RSV"}:
            family, mechanism, group = "价格位置与通道", "区间位置与极值", "价格位置与突破"
        elif kind in {"ROC", "MA", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN", "SUMD"}:
            family, mechanism, group = ("趋势与均线", "均线平滑", "均线趋势") if kind == "MA" else ("收益与动量", "收益与趋势聚合", "收益动量")
        else:  # OPEN/HIGH/LOW/CLOSE/VWAP lagged relative values
            family, mechanism, group = "价格位置与通道", "归一化价格滞后", "价格滞后窗口"
        return {
            "research_family": family, "measurement_type": mechanism, "source": source,
            "redundancy_group": group,
            "intended_use": "单因子研究或与其他 Alpha158 特征联合建模",
            "tags": ("Qlib", "Alpha158", kind or feature),
            "data_requirements": tuple(required_columns),
        }

    if name.startswith("return_"):
        family, mechanism, group = "收益与动量", "区间收益率", "收益动量"
    elif name in {"sma_gap_20", "sma_crossover_20_60"}:
        family, mechanism, group = "趋势与均线", "均线位置与差值", "均线趋势"
    elif name == "rsi_14":
        family, mechanism, group, use = "震荡与强弱", "震荡器", "RSI/震荡", "超买超卖研究或趋势过滤"
    elif name in {"atr_14_pct", "volatility_20"}:
        family, mechanism, group = "波动与风险", "波动率", "波动率"
    elif name == "volume_ratio_20":
        family, mechanism, group = "成交量", "相对成交量", "相对成交量"
    elif name.startswith("ta_"):
        source = "ta"
        column = name.removeprefix("ta_")
        # ta's implementation categories are not research hypotheses.  These
        # explicit groups intentionally cross that boundary where appropriate.
        if column.startswith("volume_"):
            if column in {"volume_adi", "volume_obv", "volume_cmf", "volume_fi", "volume_em", "volume_sma_em", "volume_vpt", "volume_mfi", "volume_nvi", "volume_vwap"}:
                family, mechanism, group = "量价关系", "量价流量或累积指标", "量价关系"
            else:
                family, mechanism, group = "成交量", "成交量指标", "成交量"
        elif column.startswith("volatility_"):
            family, mechanism, group = "价格位置与通道", "价格通道", "价格通道"
            if column in {"volatility_bbw", "volatility_kcw", "volatility_dcw", "volatility_atr", "volatility_ui"}:
                family = "波动与风险"
                mechanism, group = "波动率", "波动率"
        elif column.startswith("trend_"):
            if column in {"trend_dpo", "trend_stc"}:
                family, mechanism, group, use = "震荡与强弱", "去趋势/循环震荡器", "震荡器", "超买超卖研究或趋势过滤"
            elif column in {"trend_mass_index"}:
                family, mechanism, group = "波动与风险", "价格区间扩张", "区间扩张"
            elif column in {"trend_macd", "trend_macd_signal", "trend_macd_diff", "trend_trix", "trend_kst", "trend_kst_sig", "trend_kst_diff"}:
                family, mechanism, group = "收益与动量", "动量差分与变化率", "收益动量"
            else:
                family, mechanism, group = "趋势与均线", "趋势滤波与交叉", "趋势指标"
        elif column.startswith("momentum_"):
            if column in {"momentum_rsi", "momentum_stoch_rsi", "momentum_stoch_rsi_k", "momentum_stoch_rsi_d", "momentum_uo", "momentum_stoch", "momentum_stoch_signal", "momentum_wr"}:
                family, mechanism, group, use = "震荡与强弱", "震荡器", "RSI/震荡", "超买超卖研究或趋势过滤"
            elif column in {"momentum_pvo", "momentum_pvo_signal", "momentum_pvo_hist"}:
                family, mechanism, group = "成交量", "成交量动量", "成交量动量"
            elif column == "momentum_kama":
                family, mechanism, group = "趋势与均线", "自适应均线", "均线趋势"
            else:
                family, mechanism, group = "收益与动量", "动量振荡与变化率", "收益动量"
        elif column.startswith("others_"):
            family, mechanism, group = "收益与动量", "收益率变换", "收益动量"

    assert family in _RESEARCH_FAMILIES, family
    base_tags = [source, *required_columns]
    return {
        "research_family": family, "measurement_type": mechanism, "source": source,
        "redundancy_group": group, "intended_use": use,
        "tags": tuple(dict.fromkeys(base_tags + tags)),
        "data_requirements": tuple(required_columns),
    }


_CUSTOM_DEFINITIONS = (
    FactorDefinition("return_5", "5日收益", ("close",), "收盘价过去 5 个交易观测值的收益率。"),
    FactorDefinition("return_20", "20日收益", ("close",), "收盘价过去 20 个交易观测值的收益率。"),
    FactorDefinition("return_60", "60日收益", ("close",), "收盘价过去 60 个交易观测值的收益率。"),
    FactorDefinition("sma_gap_20", "20日均线乖离", ("close",), "close / SMA(20) - 1。"),
    FactorDefinition("sma_crossover_20_60", "20/60日均线差", ("close",), "SMA(20) / SMA(60) - 1，不是交易信号。"),
    FactorDefinition("rsi_14", "RSI(14)", ("close",), "ta 库的 Wilder RSI(14)。"),
    FactorDefinition("atr_14_pct", "ATR(14)/价格", ("high", "low", "close"), "ta 库的 Wilder ATR(14) 除以收盘价。"),
    FactorDefinition("volatility_20", "20日波动率", ("close",), "20期日收益率标准差，未年化。"),
    FactorDefinition("volume_ratio_20", "20日相对成交量", ("volume",), "当日成交量 / 过去20期平均成交量；缺量保持为空。"),
)

# ``ta.add_all_ta_features`` is the maintainer-supported aggregate API in
# ta 0.11.  Keep this explicit inventory (rather than discovering columns from
# a user dataframe) so the UI catalogue is deterministic, reviewable, and does
# not vary with sparse input data.  Each listed item is one output of a ta
# indicator; e.g. Bollinger Bands intentionally exposes its middle/high/low
# bands and derived width/percent values separately.
_TA_OUTPUTS: dict[str, tuple[str, ...]] = {
    "成交量": (
        "volume_adi", "volume_obv", "volume_cmf", "volume_fi", "volume_em", "volume_sma_em",
        "volume_vpt", "volume_vwap", "volume_mfi", "volume_nvi",
    ),
    "波动率": (
        "volatility_bbm", "volatility_bbh", "volatility_bbl", "volatility_bbw", "volatility_bbp",
        "volatility_bbhi", "volatility_bbli", "volatility_kcc", "volatility_kch", "volatility_kcl",
        "volatility_kcw", "volatility_kcp", "volatility_kchi", "volatility_kcli", "volatility_dcl",
        "volatility_dch", "volatility_dcm", "volatility_dcw", "volatility_dcp", "volatility_atr",
        "volatility_ui",
    ),
    "趋势": (
        "trend_macd", "trend_macd_signal", "trend_macd_diff", "trend_sma_fast", "trend_sma_slow",
        "trend_ema_fast", "trend_ema_slow", "trend_vortex_ind_pos", "trend_vortex_ind_neg",
        "trend_vortex_ind_diff", "trend_trix", "trend_mass_index", "trend_dpo", "trend_kst",
        "trend_kst_sig", "trend_kst_diff", "trend_ichimoku_conv", "trend_ichimoku_base",
        "trend_ichimoku_a", "trend_ichimoku_b", "trend_ichimoku_a_visual", "trend_ichimoku_b_visual",
        "trend_aroon_up", "trend_aroon_down", "trend_aroon_ind", "trend_psar", "trend_psar_up",
        "trend_psar_down", "trend_psar_up_indicator", "trend_psar_down_indicator", "trend_stc",
        "trend_adx", "trend_adx_pos", "trend_adx_neg", "trend_cci",
    ),
    "动量": (
        "momentum_rsi", "momentum_stoch_rsi", "momentum_stoch_rsi_k", "momentum_stoch_rsi_d",
        "momentum_tsi", "momentum_uo", "momentum_stoch", "momentum_stoch_signal", "momentum_wr",
        "momentum_ao", "momentum_roc", "momentum_ppo", "momentum_ppo_signal", "momentum_ppo_hist",
        "momentum_pvo", "momentum_pvo_signal", "momentum_pvo_hist", "momentum_kama",
    ),
    "其他": ("others_dr", "others_dlr", "others_cr"),
}

_CATEGORY_EN = {"成交量": "Volume", "波动率": "Volatility", "趋势": "Trend", "动量": "Momentum", "其他": "Other"}


def _ta_label(column: str, category: str) -> str:
    """Compact Chinese labels plus upstream English term, stable for the UI."""
    prefix = {"成交量": "volume_", "波动率": "volatility_", "趋势": "trend_", "动量": "momentum_", "其他": "others_"}[category]
    return f"{category} · {column.removeprefix(prefix).upper()}"


def _ta_description(column: str, category: str) -> str:
    return (
        f"ta 0.11 {_CATEGORY_EN[category]} output `{column}` computed with ta's documented default parameters "
        "from this symbol's current and prior bars; missing inputs are not imputed."
    )


_TA_DEFINITIONS = tuple(
    FactorDefinition(
        name=f"ta_{column}", label=_ta_label(column, category),
        required_columns=("open", "high", "low", "close", "volume"),
        description=_ta_description(column, category), category=category, ta_column=column,
    )
    for category, columns in _TA_OUTPUTS.items() for column in columns
)

# Qlib's Alpha360 is deliberately kept as a *feature set*, rather than making
# this workbench depend on qlib's binary data store.  Its definition is six
# OHLCV fields times Ref(field, d), d=0..59, scaled by the current close
# (prices) or current volume (volume).  ``vwap`` is not present in our daily
# bars and is therefore unavailable (NaN), not approximated from OHLC.
_QLIB360_FIELDS = ("CLOSE", "OPEN", "HIGH", "LOW", "VWAP", "VOLUME")
_QLIB360_DEFINITIONS = tuple(
    FactorDefinition(
        f"qlib360_{field}{lag}", f"Alpha360 · {field}{lag}",
        ("volume",) if field == "VOLUME" else (("vwap", "close") if field == "VWAP" else (("close",) if field == "CLOSE" else (field.lower(), "close"))),
        f"Qlib Alpha360: Ref(${field.lower()}, {lag}) / current {'volume' if field == 'VOLUME' else 'close'}. "
        "VWAP is intentionally unavailable when the source has no VWAP column.",
        "Alpha360",
    )
    for field in _QLIB360_FIELDS for lag in range(60)
)

# Alpha158's public name denotes the standard compact Qlib tabular feature
# family.  These 158 stable ids preserve its common K-bar, price/volume-lag and
# rolling-feature vocabulary.  They are calculated from a single symbol's
# past bars only; unavailable VWAP-derived values stay NaN.
_A158_KBAR = ("KMID", "KLEN", "KMID2", "KUP", "KUP2", "KLOW", "KLOW2", "KSFT", "KSFT2")
_A158_PRICE = tuple(f"{field}{lag}" for field in ("OPEN", "HIGH", "LOW", "CLOSE", "VWAP") for lag in range(5))
_A158_VOLUME = tuple(f"VOLUME{lag}" for lag in range(5))
_A158_ROLLING = tuple(f"{kind}{window}" for kind in (
    "ROC", "MA", "STD", "BETA", "RSQR", "RESI", "MAX", "MIN", "QTLU", "QTLD", "RANK", "RSV", "IMAX", "IMIN", "IMXD",
    "CORR", "CORD", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN", "SUMD",
) for window in (5, 10, 20, 30, 60)) + tuple(f"VMA{window}" for window in (5, 10, 20, 30))
_A158_NAMES = _A158_KBAR + _A158_PRICE + _A158_VOLUME + _A158_ROLLING
assert len(_A158_NAMES) == 158
_QLIB158_DEFINITIONS = tuple(
    FactorDefinition(f"qlib158_{name}", f"Alpha158 · {name}", ("open", "high", "low", "close", "volume"),
                     f"Qlib Alpha158-compatible feature {name}; computed per symbol using only current and prior OHLCV bars; no missing-value imputation.", "Alpha158")
    for name in _A158_NAMES
)

_UNCLASSIFIED_DEFINITIONS = _CUSTOM_DEFINITIONS + _TA_DEFINITIONS + _QLIB158_DEFINITIONS + _QLIB360_DEFINITIONS


def _with_taxonomy(item: FactorDefinition) -> FactorDefinition:
    """Attach immutable taxonomy metadata to one stable factor definition."""
    taxonomy = _taxonomy(item.name, item.category, item.required_columns)
    return replace(
        item,
        research_family=str(taxonomy["research_family"]),
        measurement_type=str(taxonomy["measurement_type"]),
        source=str(taxonomy["source"]),
        redundancy_group=str(taxonomy["redundancy_group"]),
        intended_use=str(taxonomy["intended_use"]),
        tags=tuple(str(value) for value in taxonomy["tags"]),
    )


_DEFINITIONS = tuple(_with_taxonomy(item) for item in _UNCLASSIFIED_DEFINITIONS)
_BY_NAME = {item.name: item for item in _DEFINITIONS}


def list_factors() -> list[dict[str, object]]:
    """Return UI-safe metadata for the built-in factors (no data is loaded)."""
    return [
        {
            "name": item.name,
            "label": item.label,
            "required_columns": list(item.required_columns),
            # data_requirements is intentionally duplicated for API consumers
            # that use taxonomy terminology; it is not a different data set.
            "data_requirements": list(item.required_columns),
            "description": item.description,
            "category": item.category,
            "research_family": item.research_family,
            "measurement_type": item.measurement_type,
            "source": item.source,
            "redundancy_group": item.redundancy_group,
            "intended_use": item.intended_use,
            "tags": list(item.tags),
            "default_parameters": {},
            "formula_version": FACTOR_FORMULA_VERSION,
        }
        for item in _DEFINITIONS
    ]


def _validate_panel(panel: pd.DataFrame, required: Iterable[str] = ()) -> pd.DataFrame:
    missing = REQUIRED_PANEL_COLUMNS.union(required).difference(panel.columns)
    if missing:
        raise ValueError(f"panel missing required columns: {sorted(missing)}")
    result = panel.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce").dt.tz_localize(None).dt.normalize()
    result["symbol"] = result["symbol"].astype(str)
    for column in {"close", *required}:
        result[column] = pd.to_numeric(result[column], errors="coerce")
    result = result.dropna(subset=["date", "symbol"]).sort_values(["symbol", "date"], kind="stable")
    # Duplicated provider files should have been isolated before this stage.
    # Keep the last input row deterministically rather than silently averaging.
    return result.drop_duplicates(["symbol", "date"], keep="last").reset_index(drop=True)


def load_derived_panel(
    derived_root: Path,
    *,
    symbols: Iterable[str] | None = None,
    start: str | pd.Timestamp | None = None,
    end: str | pd.Timestamp | None = None,
    max_symbols: int = 300,
) -> pd.DataFrame:
    """Read a bounded panel from ``structural-v1`` style ``bars.parquet`` files.

    No files are written. ``max_symbols`` is a guard for interactive use; set
    it explicitly higher for an offline study.  If several provider files have
    the same symbol, all are rejected rather than splicing price histories.
    """
    root = Path(derived_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"derived root does not exist or is not a directory: {root}")
    wanted = {str(value).upper() for value in symbols} if symbols is not None else None
    files = sorted(root.rglob("bars.parquet"))
    selected: list[tuple[str, Path]] = []
    seen: set[str] = set()
    duplicate: set[str] = set()
    for path in files:
        # Structural-v1 preserves the ``symbol=<ticker>-<stable-id>`` partition
        # name.  Use it to avoid opening every one of ~10k Parquet files for a
        # five-ticker interactive request.  Keep the column check as a safe
        # fallback for a differently laid-out derived version.
        parent = path.parent.name
        symbol = ""
        if parent.startswith("symbol=") and "-" in parent[len("symbol="):]:
            symbol = parent[len("symbol="):].rsplit("-", 1)[0].upper()
        if not symbol:
            try:
                sample = pd.read_parquet(path, columns=["symbol"])
            except Exception:
                continue
            if sample.empty:
                continue
            values = set(sample["symbol"].dropna().astype(str).str.upper())
            if len(values) != 1:
                continue
            symbol = next(iter(values))
        if wanted is not None and symbol not in wanted:
            continue
        if symbol in seen:
            duplicate.add(symbol)
            continue
        seen.add(symbol)
        selected.append((symbol, path))
    selected = [(symbol, path) for symbol, path in selected if symbol not in duplicate]
    if wanted is None:
        selected = selected[:max_symbols]
    elif len(selected) > max_symbols:
        raise ValueError(f"requested {len(selected)} symbols exceeds max_symbols={max_symbols}")
    frames: list[pd.DataFrame] = []
    columns = ["date", "symbol", "open", "high", "low", "close", "volume", "cleaning_status", "adjustment_status", "raw_relative_path"]
    for _, path in selected:
        available = pd.read_parquet(path)
        keep = [column for column in columns if column in available.columns]
        frames.append(available.loc[:, keep])
    if not frames:
        panel = pd.DataFrame(columns=sorted(REQUIRED_PANEL_COLUMNS))
        panel.attrs["excluded_duplicate_symbols"] = sorted(duplicate)
        panel.attrs["loaded_symbols"] = []
        return panel
    panel = pd.concat(frames, ignore_index=True)
    panel = _validate_panel(panel)
    if start is not None:
        panel = panel.loc[panel["date"] >= pd.Timestamp(start)]
    if end is not None:
        panel = panel.loc[panel["date"] <= pd.Timestamp(end)]
    panel.attrs["excluded_duplicate_symbols"] = sorted(duplicate)
    panel.attrs["loaded_symbols"] = sorted(panel["symbol"].unique())
    return panel.reset_index(drop=True)


def _compute_ta_column(frame: pd.DataFrame, definition: FactorDefinition) -> pd.Series:
    """Compute one aggregate-ta output independently for every symbol.

    Calling ta once on a concatenated panel would incorrectly carry rolling
    state from one ticker into the next.  Conversely, calling individual ta
    classes would duplicate the library's parameter/default logic and leave
    some of its indicators out of the registry.  The aggregate function is
    therefore called on each already time-sorted ticker frame.

    We intentionally pass ``fillna=False`` and mask a missing current volume
    value again after ta returns.  Some cumulative ta indicators can otherwise
    emit a numeric value at a row whose volume is missing; treating that as an
    observed factor would violate the lab's no-imputation contract.
    """
    if add_all_ta_features is None:
        raise RuntimeError(f"ta package is required for {definition.name}; install the project dependencies")
    assert definition.ta_column is not None
    output = pd.Series(np.nan, index=frame.index, dtype=float)
    errors: list[str] = []
    for _, group in frame.groupby("symbol", sort=False):
        inputs = group.loc[:, ["open", "high", "low", "close", "volume"]].copy()
        for price in ("open", "high", "low", "close"):
            inputs[price] = pd.to_numeric(inputs[price], errors="coerce").where(lambda value: value > 0)
        inputs["volume"] = pd.to_numeric(inputs["volume"], errors="coerce").where(lambda value: value >= 0)
        try:
            enriched = add_all_ta_features(
                inputs, open="open", high="high", low="low", close="close", volume="volume", fillna=False,
            )
        except (ArithmeticError, ValueError, TypeError) as exc:
            # A malformed symbol must not borrow another symbol's data or turn
            # into synthetic zero values.  Keep it unavailable and include the
            # causal error in the raised message only if *every* symbol fails.
            # Individual unavailable rows remain ordinary NaN observations.
            errors.append(f"{group['symbol'].iloc[0]}: {exc}")
            continue
        if definition.ta_column not in enriched.columns:
            raise RuntimeError(
                f"installed ta does not expose expected output {definition.ta_column!r}; "
                "use ta 0.11.x or update the factor registry"
            )
        value = pd.to_numeric(enriched[definition.ta_column], errors="coerce")
        if definition.category == "成交量":
            value = value.where(inputs["volume"].notna())
        output.loc[group.index] = value.to_numpy()
    if output.notna().sum() == 0 and errors:
        raise RuntimeError(f"ta could not calculate {definition.name}: {errors[0]}")
    return output


def _rolling_slope(values: pd.Series, window: int) -> pd.Series:
    """Least-squares slope, matching Qlib's time-series Slope intent."""
    x = np.arange(window, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    return values.rolling(window, min_periods=window).apply(
        lambda y: np.dot(xc, y - y.mean()) / denom if np.isfinite(y).all() else np.nan, raw=True
    )


def _rolling_rsquare(values: pd.Series, window: int) -> pd.Series:
    x = np.arange(window, dtype=float)
    xc = x - x.mean()
    denom = float(np.dot(xc, xc))
    def calc(y: np.ndarray) -> float:
        if not np.isfinite(y).all(): return np.nan
        slope = np.dot(xc, y - y.mean()) / denom
        fitted = y.mean() + slope * xc
        sst = np.dot(y - y.mean(), y - y.mean())
        return np.nan if sst == 0 else float(1 - np.dot(y - fitted, y - fitted) / sst)
    return values.rolling(window, min_periods=window).apply(calc, raw=True)


def _qlib_series(group: pd.DataFrame, name: str) -> pd.Series:
    """Calculate one Alpha158/360 feature on a single, sorted ticker."""
    close = group["close"].where(group["close"] > 0)
    open_ = group.get("open", pd.Series(np.nan, index=group.index)).where(lambda x: x > 0)
    high = group.get("high", pd.Series(np.nan, index=group.index)).where(lambda x: x > 0)
    low = group.get("low", pd.Series(np.nan, index=group.index)).where(lambda x: x > 0)
    volume = group.get("volume", pd.Series(np.nan, index=group.index)).where(lambda x: x >= 0)
    vwap = group.get("vwap", pd.Series(np.nan, index=group.index))  # never manufacture a VWAP proxy
    fields = {"OPEN": open_, "HIGH": high, "LOW": low, "CLOSE": close, "VWAP": vwap, "VOLUME": volume}
    if name.startswith("qlib360_"):
        token = name.removeprefix("qlib360_")
        field = next(key for key in _QLIB360_FIELDS if token.startswith(key))
        lag = int(token[len(field):])
        denom = volume if field == "VOLUME" else close
        return fields[field].shift(lag) / denom.where(denom != 0)
    token = name.removeprefix("qlib158_")
    spread = (high - low).replace(0, np.nan)
    if token == "KMID": return (close - open_) / open_
    if token == "KLEN": return (high - low) / open_
    if token == "KMID2": return (close - open_) / spread
    if token == "KUP": return (high - np.maximum(open_, close)) / open_
    if token == "KUP2": return (high - np.maximum(open_, close)) / spread
    if token == "KLOW": return (np.minimum(open_, close) - low) / open_
    if token == "KLOW2": return (np.minimum(open_, close) - low) / spread
    if token == "KSFT": return (2 * close - high - low) / open_
    if token == "KSFT2": return (2 * close - high - low) / spread
    for field in _QLIB360_FIELDS:
        if token.startswith(field) and token[len(field):].isdigit():
            lag = int(token[len(field):]); denom = volume if field == "VOLUME" else close
            return fields[field].shift(lag) / denom.where(denom != 0)
    kinds = ("ROC", "MA", "STD", "BETA", "RSQR", "RESI", "MAX", "MIN", "QTLU", "QTLD", "RANK", "RSV", "IMAX", "IMIN", "IMXD", "CORR", "CORD", "CNTP", "CNTN", "CNTD", "SUMP", "SUMN", "SUMD", "VMA")
    kind = next(k for k in kinds if token.startswith(k))
    window = int(token[len(kind):])
    ret = close.pct_change(fill_method=None)
    if kind == "ROC": return close.pct_change(window, fill_method=None)
    if kind == "MA": return close.rolling(window, min_periods=window).mean() / close
    if kind == "STD": return close.rolling(window, min_periods=window).std(ddof=0) / close
    if kind == "BETA": return _rolling_slope(close, window) / close
    if kind == "RSQR": return _rolling_rsquare(close, window)
    if kind == "RESI": return (close - (close.rolling(window, min_periods=window).mean() + _rolling_slope(close, window) * ((window - 1) / 2))) / close
    if kind == "MAX": return high.rolling(window, min_periods=window).max() / close
    if kind == "MIN": return low.rolling(window, min_periods=window).min() / close
    if kind == "QTLU": return close.rolling(window, min_periods=window).quantile(.8) / close
    if kind == "QTLD": return close.rolling(window, min_periods=window).quantile(.2) / close
    if kind == "RANK": return close.rolling(window, min_periods=window).apply(lambda x: pd.Series(x).rank(pct=True).iloc[-1], raw=True)
    if kind == "RSV": return (close - low.rolling(window, min_periods=window).min()) / (high.rolling(window, min_periods=window).max() - low.rolling(window, min_periods=window).min()).replace(0, np.nan)
    # Qlib Alpha158DL defines these as IdxMax(high, d)/d,
    # IdxMin(low, d)/d and their difference.  In particular IMXD's minimum
    # comes from low, not from high.  Keep the denominator as ``window`` to
    # match the referenced expression rather than rescaling to [0, 1].
    if kind == "IMAX": return high.rolling(window, min_periods=window).apply(lambda x: np.argmax(x) / len(x), raw=True)
    if kind == "IMIN": return low.rolling(window, min_periods=window).apply(lambda x: np.argmin(x) / len(x), raw=True)
    if kind == "IMXD": return (
        high.rolling(window, min_periods=window).apply(np.argmax, raw=True)
        - low.rolling(window, min_periods=window).apply(np.argmin, raw=True)
    ) / window
    if kind == "CORR": return close.rolling(window, min_periods=window).corr(np.log1p(volume))
    if kind == "CORD": return ret.rolling(window, min_periods=window).corr(volume.pct_change(fill_method=None))
    if kind == "CNTP": return (ret > 0).rolling(window, min_periods=window).mean()
    if kind == "CNTN": return (ret < 0).rolling(window, min_periods=window).mean()
    if kind == "CNTD": return (ret > 0).rolling(window, min_periods=window).mean() - (ret < 0).rolling(window, min_periods=window).mean()
    if kind == "SUMP": return ret.clip(lower=0).rolling(window, min_periods=window).sum() / ret.abs().rolling(window, min_periods=window).sum().replace(0, np.nan)
    if kind == "SUMN": return ret.clip(upper=0).abs().rolling(window, min_periods=window).sum() / ret.abs().rolling(window, min_periods=window).sum().replace(0, np.nan)
    if kind == "SUMD": return ret.rolling(window, min_periods=window).sum() / ret.abs().rolling(window, min_periods=window).sum().replace(0, np.nan)
    return volume.rolling(window, min_periods=window).mean() / volume.where(volume != 0)


def _compute_qlib_column(frame: pd.DataFrame, factor: str) -> pd.Series:
    output = pd.Series(np.nan, index=frame.index, dtype=float)
    for _, group in frame.groupby("symbol", sort=False):
        output.loc[group.index] = _qlib_series(group, factor).to_numpy()
    return output


def compute_factor(panel: pd.DataFrame, factor: str) -> pd.DataFrame:
    """Return date/symbol/factor values; invalid or unavailable observations are NaN.

    Price factors require a strictly positive close.  ``volume_ratio_20`` does
    not fill missing volume with zero: a missing current value or insufficient
    valid history produces NaN.  All rolling windows are per symbol and use
    only current/past rows in stable date order.
    """
    definition = _BY_NAME.get(factor)
    if definition is None:
        raise ValueError(f"unknown factor {factor!r}; choose one of {sorted(_BY_NAME)}")
    # Alpha360 includes VWAP identifiers.  Our OHLCV layer deliberately has
    # no synthetic VWAP; those features remain NaN instead of rejecting the
    # complete family.
    qlib = factor.startswith(("qlib158_", "qlib360_"))
    frame = _validate_panel(panel, () if qlib else definition.required_columns)
    close = frame["close"].where(frame["close"] > 0)
    grouped_close = close.groupby(frame["symbol"], sort=False)
    result = frame.loc[:, ["date", "symbol"]].copy()
    if qlib:
        result[factor] = _compute_qlib_column(frame, factor)
    elif definition.ta_column is not None:
        result[factor] = _compute_ta_column(frame, definition)
    elif factor.startswith("return_"):
        periods = int(factor.rsplit("_", 1)[1])
        result[factor] = grouped_close.pct_change(periods=periods, fill_method=None)
    elif factor == "sma_gap_20":
        mean = grouped_close.transform(lambda values: values.rolling(20, min_periods=20).mean())
        result[factor] = close / mean - 1.0
    elif factor == "sma_crossover_20_60":
        fast = grouped_close.transform(lambda values: values.rolling(20, min_periods=20).mean())
        slow = grouped_close.transform(lambda values: values.rolling(60, min_periods=60).mean())
        result[factor] = fast / slow - 1.0
    elif factor == "rsi_14":
        if RSIIndicator is None:
            raise RuntimeError("ta package is required for rsi_14; install the project dependencies")
        indicator = pd.Series(np.nan, index=frame.index, dtype=float)
        for _, group in frame.groupby("symbol", sort=False):
            indicator.loc[group.index] = RSIIndicator(
                close=group["close"].where(group["close"] > 0), window=14, fillna=False
            ).rsi()
        result[factor] = indicator
    elif factor == "atr_14_pct":
        high = frame["high"].where(frame["high"] > 0)
        low = frame["low"].where(frame["low"] > 0)
        if AverageTrueRange is None:
            raise RuntimeError("ta package is required for atr_14_pct; install the project dependencies")
        atr = pd.Series(np.nan, index=frame.index, dtype=float)
        inputs = frame.assign(_high=high, _low=low, _close=close)
        for _, group in inputs.groupby("symbol", sort=False):
            atr.loc[group.index] = AverageTrueRange(
                high=group["_high"], low=group["_low"], close=group["_close"], window=14, fillna=False
            ).average_true_range()
        result[factor] = atr / close
    elif factor == "volatility_20":
        returns = grouped_close.pct_change(fill_method=None)
        result[factor] = returns.groupby(frame["symbol"], sort=False).transform(lambda values: values.rolling(20, min_periods=20).std(ddof=1))
    else:  # volume_ratio_20
        volume = frame["volume"].where(frame["volume"] >= 0)
        mean = volume.groupby(frame["symbol"], sort=False).transform(lambda values: values.rolling(20, min_periods=20).mean())
        result[factor] = volume / mean
    return result


def evaluate_factor(
    panel: pd.DataFrame,
    factor: str,
    *,
    horizon: int = 5,
    quantiles: int = 5,
    min_cross_section: int = 20,
) -> dict[str, object]:
    """Evaluate one factor without look-ahead in factor construction.

    ``horizon`` means the next number of available bars *for each symbol*, not
    calendar days.  Rows missing price, factor, or future close are excluded;
    this is reported in ``summary``. Quantile portfolios are equal-weighted and
    gross of fees, slippage, corporate-action adjustment, and delisting return.
    """
    if horizon < 1 or quantiles < 2 or min_cross_section < 2:
        raise ValueError("horizon >= 1, quantiles >= 2, and min_cross_section >= 2 are required")
    values = compute_factor(panel, factor)
    clean = _validate_panel(panel)
    prices = clean.loc[:, ["date", "symbol", "close"]].copy()
    prices["close"] = prices["close"].where(prices["close"] > 0)
    prices["future_close"] = prices.groupby("symbol", sort=False)["close"].shift(-horizon)
    prices["forward_end_date"] = prices.groupby("symbol", sort=False)["date"].shift(-horizon)
    prices["forward_return"] = prices["future_close"] / prices["close"] - 1.0
    observations = values.merge(prices, on=["date", "symbol"], how="inner", validate="one_to_one")
    eligible = observations.dropna(subset=[factor, "forward_return"]).copy()
    eligible["cross_section_size"] = eligible.groupby("date")[factor].transform("size")
    eligible = eligible.loc[eligible["cross_section_size"] >= min_cross_section].copy()

    def _spearman(group: pd.DataFrame) -> float:
        # ``Series.corr(method="spearman")`` imports SciPy in pandas.  Ranking
        # both variables and using ordinary Pearson correlation is the same
        # Spearman statistic, avoids a large extra runtime dependency, and
        # keeps tie handling explicit (pandas' average ranks).
        return group[factor].rank(method="average").corr(
            group["forward_return"].rank(method="average"), method="pearson"
        )

    # Select numeric study columns before apply: works with pandas 2.1 and
    # avoids the pandas 2.2 grouping-column deprecation.
    daily_ic = eligible.groupby("date", sort=True)[[factor, "forward_return"]].apply(_spearman).rename("ic").reset_index()
    daily_ic["observations"] = eligible.groupby("date").size().reindex(daily_ic["date"]).to_numpy()

    def _assign_quantiles(values: pd.Series) -> pd.Series:
        # Ties can leave too few bins. In that case, mark date unavailable rather than
        # create arbitrary rank tie-breaking that changes with source row order.
        try:
            assigned = pd.qcut(values, q=quantiles, labels=False, duplicates="drop")
        except ValueError:
            return pd.Series(np.nan, index=values.index, dtype=float)
        if assigned.nunique(dropna=True) != quantiles:
            return pd.Series(np.nan, index=values.index, dtype=float)
        return assigned.astype("Int64") + 1

    # Keep ``date`` in each returned group; it is the explicit portfolio date
    # needed for the subsequent aggregation.  This also remains compatible
    # with pandas 2.1, the project's minimum supported version.
    assigned = eligible.copy()
    assigned["quantile"] = eligible.groupby("date", sort=True)[factor].transform(_assign_quantiles)
    assigned = assigned.dropna(subset=["quantile"]).copy()
    assigned["quantile"] = assigned["quantile"].astype(int)
    quantile_returns = assigned.groupby(["date", "quantile"], sort=True)["forward_return"].mean().rename("mean_forward_return").reset_index()
    membership = assigned.loc[:, ["date", "symbol", "quantile"]].copy()
    membership["previous_quantile"] = membership.groupby("symbol", sort=False)["quantile"].shift(1)
    membership["previous_date"] = membership.groupby("symbol", sort=False)["date"].shift(1)
    # One-way turnover: fraction of current holdings not held in the same bucket
    # at the prior available factor date for that symbol.
    turnover = membership.assign(changed=membership["previous_quantile"].ne(membership["quantile"]))
    turnover = turnover.groupby(["date", "quantile"], sort=True)["changed"].mean().rename("one_way_turnover").reset_index()
    quantile_returns = quantile_returns.merge(turnover, on=["date", "quantile"], how="left")

    summary = {
        "factor": factor, "horizon_available_bars": horizon, "quantiles": quantiles,
        "min_cross_section": min_cross_section, "input_rows": int(len(observations)),
        "eligible_rows": int(len(eligible)), "eligible_dates": int(eligible["date"].nunique()),
        "quantile_dates": int(assigned["date"].nunique()),
        "mean_daily_ic": float(daily_ic["ic"].mean()) if not daily_ic.empty else None,
        "ic_ir": float(daily_ic["ic"].mean() / daily_ic["ic"].std(ddof=1)) if len(daily_ic) > 1 and daily_ic["ic"].std(ddof=1) else None,
        "mean_one_way_turnover": float(turnover["one_way_turnover"].mean()) if not turnover.empty else None,
        "limitations": [
            "Forward horizon is per-symbol available-bar count, not calendar days.",
            "Returns are unadjusted where the input structural layer is unadjusted; dividends, splits, fees, slippage, and delisting outcomes are not included.",
            "Missing volume is not imputed; volume-based factor rows remain unavailable.",
        ],
    }
    return {"summary": summary, "observations": observations, "eligible": eligible, "daily_ic": daily_ic, "quantile_returns": quantile_returns, "membership": membership}
