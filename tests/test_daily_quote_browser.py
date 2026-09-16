import json

import pandas as pd
import pytest

from quant_data.daily_quote_browser import (
    DailyQuoteInputError,
    DailyQuoteUnavailable,
    build_us_daily_browser_catalogue,
    read_us_daily_bars,
    search_us_daily_series,
    refresh_yahoo_browser_catalogue,
)


def _root(tmp_path):
    root = tmp_path / "data"
    audit = root / "audit" / "structural-v1"
    audit.mkdir(parents=True)
    relative = "provider=alpaca/namespace=default/symbol=AAA-cb1ad211/bars.parquet"
    pd.DataFrame([{
        "provider": "alpaca", "namespace": "default", "symbol_key": "AAA-cb1ad211",
        "raw_relative_path": relative, "status": "ok", "input_rows": 3,
        "first_date": "2024-01-02", "last_date": "2024-01-04",
    }]).to_parquet(audit / "coverage.parquet", index=False)
    raw = root / "bars" / "daily" / relative
    raw.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2024-01-02", "2024-01-03", "2024-01-04"], "open": [10, 11, 12],
        "high": [11, 12, 13], "low": [9, 10, 11], "close": [10.5, 11.5, 12.5],
        "volume": [100, 110, 120], "source": ["alpaca_stock_historical_v2"] * 3,
        "adjustment_status": ["raw"] * 3,
    }).to_parquet(raw, index=False)
    build_us_daily_browser_catalogue(audit_root=audit, output=root / "catalogue" / "us-daily-browser-v1.json")
    return root


def test_catalogue_search_and_one_raw_series_read_are_bounded(tmp_path):
    root = _root(tmp_path)
    search = search_us_daily_series("aa", 10, data_root=root)
    assert len(search["items"]) == 1
    assert "path" not in json.dumps(search)
    result = read_us_daily_bars(search["items"][0]["series_id"], "2024-01-02", "2024-01-04", data_root=root)
    assert result["returned_rows"] == 3
    assert result["price_basis"] == "display_composite_unverified"
    assert result["bars"][0]["close"] == 10.5
    assert "总回报" in result["limitations"][0]
    assert "path" not in json.dumps(result)


def test_browser_rejects_unbounded_or_unknown_requests(tmp_path):
    root = _root(tmp_path)
    with pytest.raises(DailyQuoteInputError):
        search_us_daily_series("", data_root=root)
    with pytest.raises(DailyQuoteInputError):
        search_us_daily_series("AAA", 21, data_root=root)
    with pytest.raises(DailyQuoteInputError):
        read_us_daily_bars("../../etc/passwd", "2024-01-01", "2024-01-02", data_root=root)
    with pytest.raises(DailyQuoteInputError):
        read_us_daily_bars("alpaca:default:AAA-cb1ad211", "2023-01-01", "2024-01-03", data_root=root)


def test_api_returns_422_for_bad_browser_request_and_no_raw_path(monkeypatch, tmp_path):
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from quant_data.api import create_app
    root = _root(tmp_path)
    monkeypatch.setenv("QUANT_DATA_ROOT", str(root))
    client = TestClient(create_app())
    assert client.get("/api/v1/market-data/us-daily/search", params={"query": "AAA", "limit": 21}).status_code == 422
    search = client.get("/api/v1/market-data/us-daily/search", params={"query": "AAA"})
    assert search.status_code == 200
    bars = client.get("/api/v1/market-data/us-daily/bars", params={"series_id": search.json()["items"][0]["series_id"], "start": "2024-01-02", "end": "2024-01-04"})
    assert bars.status_code == 200
    assert "raw_relative_path" not in bars.text


def test_yahoo_refresh_preserves_other_sources_and_is_idempotent(tmp_path):
    root = _root(tmp_path)
    path = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1/symbol=AAA-cb1ad211/bars.parquet"
    path.parent.mkdir(parents=True)
    pd.DataFrame({"date": ["2024-01-05"], "open": [10], "high": [11], "low": [9],
        "close": [10], "volume": [100], "source": ["yfinance"],
        "adjustment_status": ["raw_ohlc_with_adjusted_close_and_actions"]}).to_parquet(path, index=False)
    assert refresh_yahoo_browser_catalogue(root)["yahoo_series"] == 1
    assert refresh_yahoo_browser_catalogue(root)["series"] == 2
    items = search_us_daily_series("AAA", data_root=root)["items"]
    assert len(items) == 1 and items[0]["provider"] == "display_composite"
    assert read_us_daily_bars(items[0]["series_id"], "2024-01-05", "2024-01-05", data_root=root)["returned_rows"] == 1
    path.write_bytes(b"invalid parquet")
    result = refresh_yahoo_browser_catalogue(root)
    assert result["rejected_yahoo_files"] == 1 and result["series"] == 1


def test_yahoo_refresh_refuses_to_overwrite_invalid_catalogue(tmp_path):
    root = _root(tmp_path)
    path = root / "catalogue/us-daily-browser-v1.json"
    path.write_text('{"schema_version":"wrong","series":[]}', encoding="utf-8")
    old = path.read_bytes()
    from quant_data.daily_quote_browser import DailyQuoteUnavailable
    with pytest.raises(DailyQuoteUnavailable):
        refresh_yahoo_browser_catalogue(root)
    assert path.read_bytes() == old


def test_canonical_view_stitches_only_yahoo_tail_and_keeps_overlap_base(tmp_path):
    root = _root(tmp_path)
    assert search_us_daily_series("ABR$D", data_root=root)["items"] == []
    legacy = root / "bars/daily/symbol=TSLA-0f3c1e2d/bars.parquet"
    legacy.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2026-08-30", "2026-08-31"], "open": [1, 2], "high": [2, 3], "low": [0, 1], "close": [1, 2],
        "volume": [10, 20], "source": ["nasdaq_web_unadjusted"] * 2, "adjustment_status": ["unadjusted"] * 2,
    }).to_parquet(legacy, index=False)
    yahoo = root / "bars/daily/provider=yfinance/namespace=yahoo-daily-v1/symbol=TSLA-0f3c1e2d/bars.parquet"
    yahoo.parent.mkdir(parents=True)
    pd.DataFrame({
        "date": ["2026-08-31", "2026-09-01", "2026-09-02"], "open": [99, 3, 4], "high": [99, 4, 5], "low": [99, 2, 3], "close": [99, 3, 4],
        "volume": [99, 30, 40], "source": ["yfinance"] * 3, "adjustment_status": ["yahoo_adjusted"] * 3,
    }).to_parquet(yahoo, index=False)
    catalogue = root / "catalogue/us-daily-browser-v1.json"
    payload = json.loads(catalogue.read_text())
    payload["series"].extend([
        {"series_id": "unknown:unknown:TSLA-0f3c1e2d", "symbol": "TSLA", "provider": "unknown", "namespace": "unknown",
         "raw_relative_path": "symbol=TSLA-0f3c1e2d/bars.parquet", "first_date": "2026-08-30", "last_date": "2026-08-31", "rows": 2},
        {"series_id": "yfinance:yahoo-daily-v1:TSLA-0f3c1e2d", "symbol": "TSLA", "provider": "yfinance", "namespace": "yahoo-daily-v1",
         "raw_relative_path": "provider=yfinance/namespace=yahoo-daily-v1/symbol=TSLA-0f3c1e2d/bars.parquet", "first_date": "2026-08-31", "last_date": "2026-09-02", "rows": 3},
    ])
    catalogue.write_text(json.dumps(payload), encoding="utf-8")
    items = search_us_daily_series("TSLA", data_root=root)["items"]
    assert len(items) == 1
    assert items[0]["first_date"] == "2026-08-30" and items[0]["last_date"] == "2026-09-02"
    result = read_us_daily_bars(items[0]["series_id"], "2026-08-30", "2026-09-02", data_root=root)
    assert [bar["date"] for bar in result["bars"]] == ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02"]
    assert result["bars"][1]["close"] == 2
    assert [bar["source"] for bar in result["bars"]] == ["nasdaq_web_unadjusted", "nasdaq_web_unadjusted", "yfinance", "yfinance"]
    assert result["source_segments"][0]["end"] == "2026-08-31"
    recovery = root / "bars/daily/provider=nasdaq/namespace=nasdaq-daily-recovery-v1/symbol=TSLA-0f3c1e2d/bars.parquet"
    recovery.parent.mkdir(parents=True)
    frame = pd.read_parquet(yahoo).iloc[1:].copy()
    frame["date"] = ["2026-09-02", "2026-09-03"]
    frame["close"] = [88, 5]
    frame["source"] = "nasdaq_web_unadjusted"
    frame.to_parquet(recovery, index=False)
    payload["series"].append({"series_id": "nasdaq:nasdaq-daily-recovery-v1:TSLA-0f3c1e2d", "symbol": "TSLA", "provider": "nasdaq", "namespace": "nasdaq-daily-recovery-v1", "raw_relative_path": str(recovery.relative_to(root / "bars/daily")), "first_date": "2026-09-02", "last_date": "2026-09-03", "rows": 2})
    catalogue.write_text(json.dumps(payload))
    result = read_us_daily_bars(items[0]["series_id"], "2026-08-30", "2026-09-03", data_root=root)
    assert [bar["close"] for bar in result["bars"]] == [1, 2, 3, 4, 5]
    assert result["bars"][-1]["source"] == "nasdaq_web_unadjusted"
    alpaca = root / "bars/daily/provider=alpaca/namespace=alpaca-sip-recovery-v1/symbol=TSLA-0f3c1e2d/bars.parquet"
    alpaca.parent.mkdir(parents=True)
    frame = pd.read_parquet(recovery).iloc[-1:].copy()
    frame["close"] = 6
    frame["source"] = "alpaca_stock_historical_v2"
    frame["adjustment_status"] = "raw"
    frame.to_parquet(alpaca, index=False)
    payload["series"].append({"series_id": "alpaca:alpaca-sip-recovery-v1:TSLA-0f3c1e2d", "symbol": "TSLA", "provider": "alpaca", "namespace": "alpaca-sip-recovery-v1", "raw_relative_path": str(alpaca.relative_to(root / "bars/daily")), "first_date": "2026-09-03", "last_date": "2026-09-03", "rows": 1})
    catalogue.write_text(json.dumps(payload))
    result = read_us_daily_bars(items[0]["series_id"], "2026-08-30", "2026-09-03", data_root=root)
    assert [bar["close"] for bar in result["bars"]] == [1, 2, 3, 4, 6]
    assert result["bars"][-1]["source"] == "alpaca_stock_historical_v2"


def test_legacy_series_id_remains_readable_and_member_cap_is_fail_closed(tmp_path):
    root = _root(tmp_path)
    old_id = "alpaca:default:AAA-cb1ad211"
    assert read_us_daily_bars(old_id, "2024-01-02", "2024-01-02", data_root=root)["price_basis"] == "raw_or_unadjusted"
    catalogue = root / "catalogue/us-daily-browser-v1.json"
    payload = json.loads(catalogue.read_text())
    original = payload["series"][0]
    for number in range(8):
        duplicate = dict(original)
        duplicate["series_id"] = f"p{number}:n{number}:AAA-cb1ad211"
        payload["series"].append(duplicate)
    catalogue.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(DailyQuoteUnavailable, match="过多来源"):
        read_us_daily_bars("us-daily:AAA-cb1ad211", "2024-01-02", "2024-01-02", data_root=root)
