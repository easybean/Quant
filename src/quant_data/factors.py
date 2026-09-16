"""Workbench-facing, read-only adapter for :mod:`quant_data.factor_lab`.

The small dict API is intentionally stable for the internal UI.  Core
calculations remain in ``factor_lab`` so scripts can also work directly with
an in-memory panel.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from .factor_lab import evaluate_factor as _evaluate_panel
from .factor_lab import list_factors as _list_core
from .factor_lab import load_derived_panel
from .factor_catalogue import availability


def list_factors() -> list[dict[str, object]]:
    """List individually researchable factors for the workbench UI.

    Alpha360 definitions remain available to :func:`evaluate_factor` and the
    core registry for future model training, but its 360 lagged inputs are not
    presented as a pick-one factor catalogue.  Most are only meaningful as a
    joint model input (and CLOSE0/VOLUME0 are constants by definition).
    """
    return [
        {
            "id": item["name"],
            "display_name": item["label"],
            "description": item["description"],
            "default_parameters": {},
            "required_columns": item["required_columns"],
            "data_requirements": item.get("data_requirements", item["required_columns"]),
            "research_family": item.get("research_family", "统计与回归"),
            "measurement_type": item.get("measurement_type", "未标注"),
            "source": item.get("source", "自定义"),
            "redundancy_group": item.get("redundancy_group", "未分组"),
            "intended_use": item.get("intended_use", "单因子研究"),
            "tags": item.get("tags", []),
            "runnable": availability(str(item["name"]))[0],
            "availability_note": availability(str(item["name"]))[1],
            # UI may group the full ta catalogue without parsing translated
            # display labels.  Existing consumers can safely ignore this key.
            "category": item.get("category", "自定义"),
        }
        for item in _list_core()
        if item.get("category") != "Alpha360"
    ]


def _table_records(frame: pd.DataFrame) -> list[dict[str, object]]:
    """Use JSON-friendly records; no files or dataframe references escape the API."""
    if frame.empty:
        return []
    value = frame.copy()
    for column in value.select_dtypes(include=["datetime", "datetimetz"]).columns:
        value[column] = value[column].dt.strftime("%Y-%m-%d")
    return value.where(pd.notna(value), None).to_dict(orient="records")


def evaluate_factor(request: dict[str, Any]) -> dict[str, object]:
    """Run one bounded factor experiment against an explicit derived root.

    Required request keys: ``derived_root`` and ``factor_id``. Optional keys:
    ``symbols``, ``start``, ``end``, ``holding_period`` (default 20),
    ``quantiles`` (default 5), and ``parameters``.  This first release has no
    parameterised indicators, so non-empty parameters are rejected instead of
    silently ignored.  ``read_only`` must not be false.
    """
    if not isinstance(request, dict):
        raise ValueError("factor request must be an object")
    if request.get("read_only") is False:
        raise ValueError("factor laboratory only supports read-only requests")
    derived_root = request.get("derived_root")
    if not derived_root:
        raise ValueError("derived_root is required; raw data is never an implicit fallback")
    factor = str(request.get("factor_id") or "").strip()
    if not factor:
        raise ValueError("factor_id is required")
    parameters = request.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise ValueError("parameters must be an object")
    if parameters:
        raise ValueError("built-in factors currently use fixed, documented windows; parameters must be empty")
    symbols = request.get("symbols")
    if symbols is not None and (not isinstance(symbols, list) or not all(isinstance(item, str) for item in symbols)):
        raise ValueError("symbols must be a list of ticker strings")
    panel = load_derived_panel(
        Path(derived_root), symbols=symbols, start=request.get("start"), end=request.get("end"), max_symbols=300
    )
    if panel.empty:
        raise ValueError("no usable derived bars matched this request")
    result = _evaluate_panel(
        panel, factor, horizon=int(request.get("holding_period", 20)),
        quantiles=int(request.get("quantiles", 5)),
        # A focused UI experiment may deliberately contain only five names;
        # requiring 20 would make the default page appear broken.
        min_cross_section=int(request.get("min_cross_section", request.get("quantiles", 5))),
    )
    summary = result["summary"]
    assert isinstance(summary, dict)
    coverage = pd.DataFrame([
        {"指标": "已加载证券数", "数值": len(panel.attrs.get("loaded_symbols", []))},
        {"指标": "输入行数", "数值": summary["input_rows"]},
        {"指标": "可用于 IC 的行数", "数值": summary["eligible_rows"]},
        {"指标": "因子/价格缺失或截面不足率", "数值": (1 - summary["eligible_rows"] / summary["input_rows"]) if summary["input_rows"] else None},
        {"指标": "可用于 IC 的日期数", "数值": summary["eligible_dates"]},
        {"指标": "可分组日期数", "数值": summary["quantile_dates"]},
    ])
    ic = result["daily_ic"]
    quantile = result["quantile_returns"]
    # Charts need a date index and one column per quantile.
    quantile_chart = quantile.pivot(index="date", columns="quantile", values="mean_forward_return").sort_index()
    return {
        "metrics": {
            "平均日 IC": summary["mean_daily_ic"], "IC IR": summary["ic_ir"],
            "平均单向换手": summary["mean_one_way_turnover"],
        },
        "ic_series": ic.set_index("date")[["ic"]] if not ic.empty else ic,
        "quantile_returns": quantile_chart,
        "coverage": _table_records(coverage),
        "diagnostics": {
            "factor": factor, "loaded_symbols": panel.attrs.get("loaded_symbols", []),
            "excluded_duplicate_symbols": panel.attrs.get("excluded_duplicate_symbols", []),
            "limitations": summary["limitations"],
        },
    }
