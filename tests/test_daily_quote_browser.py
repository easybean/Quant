import json

import pandas as pd
import pytest

from quant_data.daily_quote_browser import (
    DailyQuoteInputError,
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
    assert result["price_basis"] == "raw_or_unadjusted"
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
    assert items[0]["provider"] == "yfinance" and items[1]["provider"] == "alpaca"
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
