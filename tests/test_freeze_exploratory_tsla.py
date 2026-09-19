from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import exchange_calendars as calendars
import pandas as pd
import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/freeze_exploratory_tsla_2026.py"
spec = spec_from_file_location("freeze_exploratory_tsla_2026", SCRIPT)
freeze_module = module_from_spec(spec)
spec.loader.exec_module(freeze_module)


def source(root: Path, *, missing: bool = False, action: bool = False) -> Path:
    dates = [x.date() for x in calendars.get_calendar("XNYS").sessions_in_range("2026-01-02", "2026-08-31")]
    if missing:
        dates.pop(12)
    rows = [{"date": pd.Timestamp(day), "symbol": "TSLA", "open": 100.0, "high": 102.0,
             "low": 98.0, "close": 101.0, "volume": 1000.0,
             "dividends": 1.0 if action and index == 10 else 0.0,
             "stock_splits": 0.0, "source": "nasdaq_web_unadjusted", "adjustment_status": "unadjusted"}
            for index, day in enumerate(dates)]
    path = root / freeze_module.SOURCE
    path.parent.mkdir(parents=True)
    pd.DataFrame(rows).to_parquet(path)
    return path


def test_freeze_preserves_source_and_records_unknown_availability(tmp_path):
    input_path = source(tmp_path)
    before = freeze_module.sha(input_path)
    output = tmp_path / "reference/exploratory-backtest/one"
    result = freeze_module.freeze(tmp_path, output)
    assert result["rows"] == 166 and result["qualified"] is False
    assert freeze_module.sha(input_path) == before
    import json
    rows = json.loads((output / "bars.json").read_text())
    report = json.loads((output / "report.json").read_text())
    manifest = json.loads((output / "manifest.json").read_text())
    assert all(row["event_time"] is None and row["historical_available_at"] is None for row in rows)
    assert report["formal_backtest_enabled"] is False
    assert manifest["source_sha256"] == before
    with pytest.raises(ValueError, match="output_exists"):
        freeze_module.freeze(tmp_path, output)


@pytest.mark.parametrize("missing,action", [(True, False), (False, True)])
def test_freeze_rejects_gaps_and_corporate_actions(tmp_path, missing, action):
    source(tmp_path, missing=missing, action=action)
    with pytest.raises(ValueError, match="missing_or_duplicate|corporate_action"):
        freeze_module.freeze(tmp_path, tmp_path / "reference/exploratory-backtest/blocked")
