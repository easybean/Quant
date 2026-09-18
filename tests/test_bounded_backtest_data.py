import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest
from quant_data import bounded_backtest_data as module

NOW = datetime(2026, 9, 18, 12, tzinfo=timezone.utc)
DAY = date(2026, 9, 17)


def setup(root, monkeypatch):
    path = root / "reference/massive-daily-v1/fixed"
    path.mkdir(parents=True)
    manifest = {"status": "captured", "request": {"date": DAY.isoformat()}, "observed_at": NOW.isoformat(),
                "response_sha256": "rawsha", "normalized_sha256": "normalizedsha"}
    (path / "manifest.json").write_text(json.dumps(manifest))
    rows = [{"symbol": s, "date": DAY.isoformat(), "open": 10., "high": 11., "low": 9., "close": 10., "volume": 100.} for s in module.SYMBOLS]
    monkeypatch.setattr(module, "_load_snapshot", lambda *a: (manifest, pd.DataFrame(rows)))
    reference = root / "reference/ref"
    reference.mkdir()
    (reference / "manifest.json").write_text("{}")
    catalogue = root / "catalogue"
    catalogue.mkdir()
    (catalogue / "current-massive-reference-v1.json").write_text(json.dumps({"snapshot_relative_path": "reference/ref"}))
    identities = {s: {"active": True, "type": "CS", "locale": "us", "ticker": s} for s in module.SYMBOLS}
    monkeypatch.setattr(module, "_load_reference", lambda *a: (identities, {"all_tickers_sha256": "refsha"}))
    return identities, rows


def test_freezes_cohort_calendar_without_backdating_or_qualification(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    out = tmp_path / "candidate"
    result = module.build(tmp_path, out, DAY, DAY, now=NOW)
    assert result["rows"] == result["expected_rows"] == 5
    assert result["historical_close_decision_unavailable_rows"] == 5
    assert result["qualified"] is False and result["formal_backtest_enabled"] is False
    assert all(not x["missing_sessions"] for x in result["coverage"])
    bars = json.loads((out / "bars.json").read_text())
    assert all(b["event_time"] is None and b["available_at"] == NOW.isoformat() for b in bars)
    assert len(result["prospective_observation"]["sessions"]) == 20
    assert result["prospective_observation"]["sessions"][0] == "2026-09-18"
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest["files"]["bars.json"] == module.sha(out / "bars.json")
    with pytest.raises(ValueError): module.build(tmp_path, out, DAY, DAY, now=NOW)


def test_missing_member_session_is_reported_not_silently_dropped(tmp_path, monkeypatch):
    _, rows = setup(tmp_path, monkeypatch)
    rows.pop()
    result = module.build(tmp_path, tmp_path / "candidate", DAY, DAY, now=NOW)
    assert result["rows"] == 4 and result["expected_rows"] == 5
    assert result["coverage"][-1]["missing_sessions"] == [DAY.isoformat()]
    assert "missing stock sessions" in result["blockers"]


def test_identity_and_future_range_rejected(tmp_path, monkeypatch):
    identities, _ = setup(tmp_path, monkeypatch)
    identities["AAPL"]["active"] = False
    with pytest.raises(ValueError, match="identity"):
        module.build(tmp_path, tmp_path / "candidate", DAY, DAY, now=NOW)
    with pytest.raises(ValueError, match="completed"):
        module.build(tmp_path, tmp_path / "future", DAY, date(2026, 9, 18), now=NOW)


def test_symlink_input_is_rejected(tmp_path, monkeypatch):
    setup(tmp_path, monkeypatch)
    path = tmp_path / "catalogue/current-massive-reference-v1.json"
    saved = path.read_text()
    target = tmp_path / "reference/linked.json"
    target.write_text(saved)
    path.unlink()
    path.symlink_to(target)
    with pytest.raises(ValueError, match="symlink"):
        module.build(tmp_path, tmp_path / "candidate", DAY, DAY, now=NOW)
