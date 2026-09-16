from datetime import date

import pandas as pd

from quant_data.pipeline import (
    BenchmarkDownloadConfig,
    DownloadConfig,
    FatalProviderError,
    PermanentDownloadError,
    eligible_symbols,
    failed_symbols_for_provider,
    load_alpaca_credentials,
    make_alpaca_downloader,
    make_nasdaq_benchmark_downloader,
    run_download,
    run_benchmark_download,
    select_universe,
    state_directory,
)


def fake_bars(symbol, start, end):
    return pd.DataFrame(
        {
            "Open": [10.0, 11.0], "High": [11.0, 12.0], "Low": [9.0, 10.0],
            "Close": [10.5, 11.5], "Adj Close": [10.5, 11.5], "Volume": [100, 120],
        },
        index=pd.DatetimeIndex(["2020-01-02", "2020-01-03"], name="Date"),
    )


def test_eligible_symbols_includes_delisted_during_window():
    master = pd.DataFrame(
        {
            "symbol": ["OLD", "NEW", "BEFORE"],
            "ipo_date": ["2010-01-01", "2021-01-01", "2000-01-01"],
            "delisting_date": ["2019-01-01", "", "2010-01-01"],
        }
    )
    assert eligible_symbols(master, date(2016, 1, 1), date(2020, 1, 1)) == ["OLD"]


def test_eligible_symbols_uses_inclusive_timestamp_boundaries():
    master = pd.DataFrame(
        {
            "symbol": ["IPO_END", "DELIST_START", "TOO_NEW", "TOO_OLD", "NO_DATES"],
            "ipo_date": ["2020-12-31", "2010-01-01", "2021-01-01", "2010-01-01", ""],
            "delisting_date": ["", "2020-01-01", "", "2019-12-31", ""],
        }
    )
    result = eligible_symbols(master, date(2020, 1, 1), date(2020, 12, 31))
    assert result == ["DELIST_START", "IPO_END", "NO_DATES"]


def test_download_is_resumable(tmp_path):
    calls = []

    def tracked(symbol, start, end):
        calls.append(symbol)
        return fake_bars(symbol, start, end)

    config = DownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3),
        retries=0, request_delay_seconds=0,
    )
    first = run_download(["ABC"], config, downloader=tracked, sleep=lambda _: None)
    second = run_download(["ABC"], config, downloader=tracked, sleep=lambda _: None)
    assert first["success"] == 1
    assert second["skipped"] == 1
    assert calls == ["ABC"]
    bars = pd.read_parquet(next((tmp_path / "bars").rglob("bars.parquet")))
    assert list(bars["symbol"].unique()) == ["ABC"]


def test_failure_is_recorded_and_retried(tmp_path):
    attempts = []

    def broken(symbol, start, end):
        attempts.append(symbol)
        raise RuntimeError("provider unavailable")

    config = DownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3),
        retries=2, request_delay_seconds=0,
    )
    result = run_download(["BAD"], config, downloader=broken, sleep=lambda _: None)
    assert result["failed"] == 1
    assert len(attempts) == 3
    failures = pd.read_csv(tmp_path / "manifests" / "failures.csv")
    assert failures.loc[0, "symbol"] == "BAD"
    assert "provider unavailable" in failures.loc[0, "error"]


def test_permanent_failure_is_not_retried_and_is_resumable(tmp_path, capsys):
    attempts = []

    def missing(symbol, start, end):
        attempts.append(symbol)
        raise PermanentDownloadError("Nasdaq symbol does not exist")

    config = DownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3), provider="nasdaq",
        retries=3, request_delay_seconds=0,
    )
    first = run_download(["MISSING"], config, downloader=missing, sleep=lambda _: None)
    second = run_download(["MISSING"], config, downloader=missing, sleep=lambda _: None)
    assert first["failed"] == 1
    assert second["skipped"] == 1
    assert attempts == ["MISSING"]
    failures = pd.read_csv(tmp_path / "manifests" / "failures.csv")
    assert bool(failures.loc[0, "retryable"]) is False
    output = capsys.readouterr().out
    assert '"event": "symbol_failed"' in output
    assert '"event": "symbol_skipped"' in output


def test_alpaca_credentials_support_env_file_and_reject_single_line(tmp_path, monkeypatch):
    for name in (
        "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "ALPACA_API_KEY", "ALPACA_API_SECRET_KEY",
        "ALPACA_KEY_ID", "ALPACA_SECRET_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    env_file = tmp_path / "alpaca.env"
    env_file.write_text(
        "APCA_API_KEY_ID=test-id\nAPCA_API_SECRET_KEY=test-secret\n", encoding="utf-8"
    )
    assert load_alpaca_credentials(env_file) == ("test-id", "test-secret")
    incomplete = tmp_path / "incomplete"
    incomplete.write_text("only-one-value\n", encoding="utf-8")
    try:
        load_alpaca_credentials(incomplete)
    except FatalProviderError:
        pass
    else:
        raise AssertionError("single-line credential file must be rejected")


def test_alpaca_downloader_paginates_raw_daily_bars(monkeypatch):
    monkeypatch.setenv("APCA_API_KEY_ID", "test-id")
    monkeypatch.setenv("APCA_API_SECRET_KEY", "test-secret")
    calls = []

    class Response:
        status_code = 200
        headers = {}

        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    pages = [
        Response({"bars": [{"t": "2020-01-02T05:00:00Z", "o": 1, "h": 2, "l": .5, "c": 1.5, "v": 10}], "next_page_token": "next"}),
        Response({"bars": [{"t": "2020-01-03T05:00:00Z", "o": 2, "h": 3, "l": 1, "c": 2.5, "v": 12}], "next_page_token": None}),
    ]

    def request_get(url, params, headers, timeout):
        calls.append((params.copy(), headers.copy()))
        return pages.pop(0)

    downloader = make_alpaca_downloader(
        feed="iex", request_get=request_get, sleep=lambda _: None, page_delay_seconds=0
    )
    bars = downloader("AAPL", date(2020, 1, 1), date(2020, 1, 3))
    assert len(bars) == 2
    assert calls[0][0]["timeframe"] == "1Day"
    assert calls[0][0]["adjustment"] == "raw"
    assert calls[1][0]["page_token"] == "next"


def test_failed_symbols_for_provider_uses_latest_status(tmp_path):
    manifest = tmp_path / "download.jsonl"
    manifest.write_text(
        '{"symbol":"A","provider":"nasdaq","status":"failed"}\n'
        '{"symbol":"A","provider":"nasdaq","status":"success"}\n'
        '{"symbol":"B","provider":"nasdaq","status":"failed"}\n'
        '{"symbol":"C","provider":"alpaca","status":"failed"}\n',
        encoding="utf-8",
    )
    assert failed_symbols_for_provider(manifest, "nasdaq") == ["B"]


def test_universe_filters_retry_failures_and_keeps_delisted_lifecycle_audit():
    master = pd.DataFrame(
        {
            "symbol": ["OLD", "OLD", "LIVE", "ETFOLD"],
            "status": ["delisted", "delisted", "active", "delisted"],
            "asset_type": ["Stock", "Stock", "Stock", "ETF"],
            "ipo_date": ["2000-01-01", "2010-01-01", "2020-01-01", "2015-01-01"],
            "delisting_date": ["2008-01-01", "2020-01-01", "", "2021-01-01"],
        }
    )
    records, symbols, audit = select_universe(
        master, date(2000, 1, 1), date(2026, 1, 1), statuses=["delisted"],
        asset_types=["Stock"], restrict_symbols={"OLD", "LIVE"},
    )
    assert symbols == ["OLD"]
    assert len(records) == 2
    assert audit["selected_lifecycle_rows"] == 2
    assert audit["selected_unique_symbols"] == 1
    assert audit["collapsed_lifecycle_rows"] == 1


def test_active_filter_rejects_unconsolidated_duplicates():
    master = pd.DataFrame(
        {
            "symbol": ["A", "A"], "status": ["active", "active"],
            "asset_type": ["Stock", "Stock"], "ipo_date": ["", "2020-01-01"],
            "delisting_date": ["", ""],
        }
    )
    try:
        select_universe(master, date(2016, 1, 1), date(2026, 1, 1), statuses=["active"])
    except ValueError as exc:
        assert "listing consolidate" in str(exc)
    else:
        raise AssertionError("duplicate active records must require consolidation")


def test_concurrent_namespaces_have_disjoint_state_and_bar_paths(tmp_path):
    retry_config = DownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3), provider="alpaca", feed="iex",
        state_namespace="alpaca-retry", retries=0, request_delay_seconds=0,
    )
    delisted_config = DownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3), provider="alpaca", feed="iex",
        state_namespace="alpaca-delisted", retries=0, request_delay_seconds=0,
    )
    run_download(["ABC"], retry_config, downloader=fake_bars, sleep=lambda _: None)
    run_download(["ABC"], delisted_config, downloader=fake_bars, sleep=lambda _: None)
    retry_state = state_directory(tmp_path, "alpaca-retry")
    delisted_state = state_directory(tmp_path, "alpaca-delisted")
    assert retry_state != delisted_state
    assert (retry_state / "download.jsonl").exists()
    assert (delisted_state / "download.jsonl").exists()
    bar_paths = sorted((tmp_path / "bars").rglob("bars.parquet"))
    assert len(bar_paths) == 2
    assert "namespace=alpaca-retry" in str(bar_paths[1]) or "namespace=alpaca-retry" in str(bar_paths[0])
    assert "namespace=alpaca-delisted" in str(bar_paths[1]) or "namespace=alpaca-delisted" in str(bar_paths[0])


def test_provider_paths_are_disjoint_even_with_same_namespace(tmp_path):
    for provider in ("nasdaq", "alpaca"):
        config = DownloadConfig(
            tmp_path, date(2020, 1, 1), date(2020, 1, 3), provider=provider,
            state_namespace=f"{provider}-job", retries=0, request_delay_seconds=0,
        )
        run_download(["ABC"], config, downloader=fake_bars, sleep=lambda _: None)
    paths = [str(path) for path in (tmp_path / "bars").rglob("bars.parquet")]
    assert len(paths) == 2
    assert any("provider=nasdaq" in path for path in paths)
    assert any("provider=alpaca" in path for path in paths)


def test_benchmark_download_is_isolated_and_auditable(tmp_path):
    config = BenchmarkDownloadConfig(
        data_root=tmp_path, start=date(2020, 1, 1), end=date(2020, 1, 3),
        retries=0, request_delay_seconds=0,
    )
    result = run_benchmark_download(["SPY", "QQQ"], config, downloader=fake_bars, sleep=lambda _: None)
    assert result == {"requested": 2, "success": 2, "failed": 0, "skipped": 0}
    paths = sorted((tmp_path / "bars" / "daily").rglob("bars.parquet"))
    assert len(paths) == 2
    assert all("provider=alpaca" in str(path) and "namespace=benchmarks" in str(path) for path in paths)
    bars = pd.read_parquet(paths[0])
    assert {"source", "feed", "adjustment_status", "actions_status", "collected_at"}.issubset(bars.columns)
    assert set(bars["adjustment_status"]) == {"raw"}
    manifest_root = tmp_path / "manifests" / "benchmarks" / "provider=alpaca"
    run = next(manifest_root.glob("run-*.json"))
    contents = run.read_text(encoding="utf-8")
    assert '"symbols": [' in contents
    assert "credential" not in contents.lower()
    assert "SPY" in (manifest_root / "download.jsonl").read_text(encoding="utf-8")


def test_benchmark_requires_explicit_supported_symbols(tmp_path):
    config = BenchmarkDownloadConfig(tmp_path, date(2020, 1, 1), date(2020, 1, 3))
    try:
        run_benchmark_download(["SPY", "^GSPC"], config, downloader=fake_bars, sleep=lambda _: None)
    except ValueError as exc:
        assert "unsupported benchmark" in str(exc)
    else:
        raise AssertionError("index price symbols must not be silently treated as ETF benchmarks")


def test_nasdaq_benchmark_uses_fixed_assetclass_and_preserves_missing_volume(tmp_path):
    calls = []

    class Response:
        status_code = 200
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {"tradesTable": {"rows": [{
                    "date": "01/02/2020", "open": "$10.00", "high": "$11.00",
                    "low": "$9.00", "close": "$10.50", "volume": "--",
                }]}},
            }

    def request_get(url, params, headers, timeout):
        calls.append(params.copy())
        return Response()

    downloader = make_nasdaq_benchmark_downloader(
        {"SPY": "etf", "QQQ": "etf", "NDX": "index"}, request_get=request_get
    )
    config = BenchmarkDownloadConfig(
        tmp_path, date(2020, 1, 1), date(2020, 1, 3), provider="nasdaq",
        retries=0, request_delay_seconds=0,
    )
    result = run_benchmark_download(["SPY", "NDX"], config, downloader=downloader, sleep=lambda _: None)
    assert result["success"] == 2
    assert [call["assetclass"] for call in calls] == ["etf", "index"]
    ndx_path = next((tmp_path / "bars").rglob("symbol=NDX-*/bars.parquet"))
    ndx = pd.read_parquet(ndx_path)
    assert pd.isna(ndx.loc[0, "volume"])
    assert set(ndx["adjustment_status"]) == {"unadjusted"}
    assert set(ndx["actions_status"]) == {"not_available"}
