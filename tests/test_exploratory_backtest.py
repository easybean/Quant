from __future__ import annotations

from datetime import date
from decimal import Decimal
import hashlib
import json
from pathlib import Path

import pytest

from quant_data.exploratory_backtest import (
    SOURCE_RELATIVE_PATH,
    SOURCE_SHA256,
    ExploratoryBacktestRejected,
    _xnys_sessions,
    run,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_snapshot(root: Path, *, closes: list[Decimal] | None = None, opens: list[Decimal] | None = None) -> Path:
    root.mkdir()
    sessions, _ = _xnys_sessions()
    closes = closes or [Decimal("100") + Decimal(index) for index in range(len(sessions))]
    opens = opens or closes
    rows = []
    for session, close, opening in zip(sessions, closes, opens):
        rows.append({
            "symbol": "TSLA", "session_date": session.isoformat(), "open": str(opening),
            "high": str(max(opening, close) + Decimal("1")), "low": str(min(opening, close) - Decimal("1")),
            "close": str(close), "volume": "100000", "source": "nasdaq_web_unadjusted",
            "adjustment_status": "unadjusted", "event_time": None, "historical_available_at": None,
        })
    (root / "bars.json").write_text(json.dumps(rows), encoding="utf-8")
    report = {
        "schema_version": "exploratory-tsla-daily-snapshot-v1", "symbol": "TSLA",
        "range": {"start": "2026-01-02", "end": "2026-08-31"}, "rows": 166, "expected_rows": 166,
        "calendar_version": "XNYS-exchange_calendars-4.13.2", "source_relative_path": SOURCE_RELATIVE_PATH,
        "source_sha256": SOURCE_SHA256, "qualified": False, "formal_backtest_enabled": False,
        "limitations": ["event_time and historical_available_at unknown; next-session timing is an explicit exploratory assumption"],
    }
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")
    manifest = {
        "schema_version": "exploratory-tsla-daily-snapshot-v1",
        "files": {name: _sha(root / name) for name in ("bars.json", "report.json")},
        "source_relative_path": SOURCE_RELATIVE_PATH, "source_sha256": SOURCE_SHA256,
    }
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return root


def _rehash_manifest(root: Path) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"] = {name: _sha(root / name) for name in ("bars.json", "report.json")}
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_fixed_snapshot_runs_with_calendar_times_timing_and_explicit_costs(tmp_path):
    report = run(_write_snapshot(tmp_path / "snapshot"))
    buy_hold = report["strategies"]["buy_and_hold"]
    sma = report["strategies"]["sma_10_30"]
    sessions, schedule = _xnys_sessions()

    assert report["qualified"] is False
    assert report["formal_backtest_enabled"] is False
    assert report["warnings"] == ["exploratory_no_PIT_qualification"]
    assert report["execution_contract"]["max_participation"] == "0.01"
    assert buy_hold["orders"][0]["signal_session"] == sessions[0].isoformat()
    assert buy_hold["orders"][0]["execution_session"] == sessions[1].isoformat()
    assert buy_hold["fills"][0]["commission_usd"] == "1"
    assert buy_hold["fills"][0]["spread_cost_per_share"] == "0.02525"
    assert buy_hold["fills"][0]["slippage_cost_per_share"] == "0.0505"
    assert "decision_time" not in buy_hold["decisions"][0]
    assert buy_hold["decisions"][0]["scheduled_close_proxy_time"] == schedule[sessions[0]][1].isoformat()
    assert schedule[date(2026, 7, 2)][1].isoformat() == "2026-07-02T20:00:00+00:00"
    assert all(item["target_weight"] == "0" for item in sma["decisions"][:29])
    assert sma["decisions"][29]["target_weight"] == "1"
    assert sma["orders"][0]["signal_session"] == sessions[29].isoformat()
    assert sma["orders"][0]["execution_session"] == sessions[30].isoformat()
    assert buy_hold["ledger"]["reconciled"] is True
    assert report["report_hash"]


def test_gap_beyond_pre_set_limit_is_rejected_and_buy_hold_retries_next_session(tmp_path):
    sessions, _ = _xnys_sessions()
    closes = [Decimal("100") for _ in sessions]
    opens = list(closes)
    opens[1] = Decimal("111")  # buy limit was fixed from prior close at 110
    report = run(_write_snapshot(tmp_path / "snapshot", closes=closes, opens=opens))
    orders = report["strategies"]["buy_and_hold"]["orders"]
    assert orders[0]["execution_reason"] == "gap_exceeds_pre_set_limit"
    assert orders[0]["filled_quantity"] == "0"
    assert orders[1]["signal_session"] == sessions[1].isoformat()
    assert orders[1]["status"] in {"filled", "partially_filled"}
    assert Decimal(orders[1]["filled_quantity"]) > 0


def test_later_prices_do_not_change_an_earlier_signal_or_limit(tmp_path):
    original_root = _write_snapshot(tmp_path / "original")
    changed_root = _write_snapshot(tmp_path / "changed")
    bars = json.loads((changed_root / "bars.json").read_text(encoding="utf-8"))
    bars[100]["close"] = "999"
    bars[100]["high"] = "1000"
    bars[100]["low"] = "998"
    bars[100]["open"] = "999"
    (changed_root / "bars.json").write_text(json.dumps(bars), encoding="utf-8")
    _rehash_manifest(changed_root)
    original = run(original_root)["strategies"]["sma_10_30"]
    changed = run(changed_root)["strategies"]["sma_10_30"]
    assert changed["decisions"][:100] == original["decisions"][:100]
    cutoff = _xnys_sessions()[0][100].isoformat()
    earlier_original = [item for item in original["orders"] if item["signal_session"] < cutoff]
    earlier_changed = [item for item in changed["orders"] if item["signal_session"] < cutoff]
    assert earlier_changed == earlier_original


@pytest.mark.parametrize("mutator, message", [
    (lambda root: _set_report(root, "corporate_action_flags", {"dividends": "1"}), "corporate-action"),
    (lambda root: _set_bar(root, 1, "historical_available_at", "2026-01-05T21:00:00Z"), "historical timestamps"),
    (lambda root: _set_manifest(root, "source_sha256", "0" * 64), "fixed TSLA exploratory source"),
])
def test_contract_rejects_nonqualified_or_mutated_inputs(tmp_path, mutator, message):
    root = _write_snapshot(tmp_path / "snapshot")
    mutator(root)
    _rehash_manifest(root)
    with pytest.raises(ExploratoryBacktestRejected, match=message):
        run(root)


def test_optional_source_rehash_rejects_a_different_raw_file(tmp_path):
    snapshot = _write_snapshot(tmp_path / "snapshot")
    data_root = tmp_path / "data"
    raw = data_root / SOURCE_RELATIVE_PATH
    raw.parent.mkdir(parents=True)
    raw.write_bytes(b"not the frozen TSLA parquet")
    with pytest.raises(ExploratoryBacktestRejected, match="raw source SHA-256 mismatch"):
        run(snapshot, data_root)


def _set_report(root: Path, key: str, value: object) -> None:
    report = json.loads((root / "report.json").read_text(encoding="utf-8"))
    report[key] = value
    (root / "report.json").write_text(json.dumps(report), encoding="utf-8")


def _set_bar(root: Path, index: int, key: str, value: object) -> None:
    bars = json.loads((root / "bars.json").read_text(encoding="utf-8"))
    bars[index][key] = value
    (root / "bars.json").write_text(json.dumps(bars), encoding="utf-8")


def _set_manifest(root: Path, key: str, value: object) -> None:
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest[key] = value
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
