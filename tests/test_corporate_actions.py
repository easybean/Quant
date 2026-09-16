from __future__ import annotations

import json
from pathlib import Path

import pytest

from quant_data import corporate_actions as ca


class Response:
    status_code = 200

    def __init__(self, body):
        self.body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self.body


def _event(event_id: str, **extra):
    return {"id": event_id, "process_date": "2020-08-01", **extra}


def test_capture_is_immutable_and_maps_actions_without_secrets(tmp_path, monkeypatch):
    monkeypatch.setattr(ca, "load_alpaca_credentials", lambda _: ("key", "secret"))
    pages = {
        "AAPL_2016": {"corporate_actions": {"cash_dividends": [{**_event("a2016", symbol="AAPL", rate=0.52), "process_date": "2016-02-04"}]}, "next_page_token": None},
        "AAPL_2020": {"corporate_actions": {"cash_dividends": [_event("ad", symbol="AAPL", rate=0.82)], "forward_splits": [_event("as", symbol="AAPL", new_rate=4, old_rate=1)]}, "next_page_token": None},
        "TSLA_2022": {"corporate_actions": {"forward_splits": [_event("ts", symbol="TSLA", new_rate=3, old_rate=1)]}, "next_page_token": None},
        "TWTR_2022": {"corporate_actions": {"cash_mergers": [_event("tw", acquiree_symbol="TWTR", rate=54.2)]}, "next_page_token": None},
        "ATVI_2023": {"corporate_actions": {"cash_mergers": [_event("at", acquiree_symbol="ATVI", rate=95)]}, "next_page_token": None},
    }
    def get(url, *, params, headers, timeout):
        assert headers["APCA-API-KEY-ID"] == "key"
        assert "secret" not in url
        window = next(name for name, symbol, start, end in ca.REPRESENTATIVE_WINDOWS if params["symbols"] == symbol and params["start"] == start)
        return Response(pages[window])
    result = ca.capture_representative_snapshot(data_root=tmp_path, snapshot_id="narrow-v1", request_get=get)
    assert result["event_count"] == 6 and result["golden_checks_passed"]
    raw = tmp_path / "reference/corporate-actions/alpaca/narrow-v1"
    curated = tmp_path / "curated/reference/corporate-actions/alpaca/narrow-v1"
    manifest = (raw / "manifest.json").read_text()
    assert '"key"' not in manifest and '"secret"' not in manifest
    report = json.loads((curated / "verification-report.json").read_text())
    assert report["eligible_for_actions_complete_scope"] is True
    assert len((curated / "events.jsonl").read_text().splitlines()) == 6
    with pytest.raises(FileExistsError):
        ca.capture_representative_snapshot(data_root=tmp_path, snapshot_id="narrow-v1", request_get=get)


def test_unknown_collection_is_rejected():
    with pytest.raises(ValueError, match="unsupported"):
        ca._events_from_response({"corporate_actions": {"future_actions": []}}, "now")
