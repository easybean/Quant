from __future__ import annotations

import hashlib
import json
import stat

import pytest

from quant_data.massive_reference import ENDPOINT, MassiveReferenceError, _safe_next_cursor, capture_massive_reference, main


class Response:
    def __init__(self, content: bytes, status_code: int = 200):
        self.content = content
        self.status_code = status_code


def _payload(rows, next_url=None):
    value = {"status": "OK", "results": rows, "count": len(rows)}
    if next_url is not None:
        value["next_url"] = next_url
    return json.dumps(value).encode()


def _row(ticker="AAA", **updates):
    value = {"ticker": ticker, "name": "Alpha", "cik": "1", "composite_figi": "BBG", "share_class_figi": "SHARE", "primary_exchange": "XNYS", "type": "CS", "market": "stocks", "locale": "us", "active": True, "currency_name": "usd"}
    value.update(updates)
    return value


@pytest.fixture(autouse=True)
def no_rate_wait(monkeypatch):
    monkeypatch.setattr("quant_data.massive_reference.wait_for_slot", lambda _: None)


def test_captures_immutable_pages_and_sanitizes_polygon_cursor(tmp_path):
    key = tmp_path / "key"; key.write_text("secret")
    calls = []
    first = _payload([_row("AAA")], "https://api.polygon.io/v3/reference/tickers?cursor=next-1&apiKey=leak")
    second = _payload([_row("BBB", cik=None)])

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(first if len(calls) == 1 else second)

    report = capture_massive_reference(tmp_path, key, request_get=get, wait_fn=lambda _: None)
    assert report["status"] == "captured" and report["ticker_count"] == 2 and report["research_qualified"] is False
    assert [call[0] for call in calls] == [ENDPOINT, ENDPOINT]
    assert calls[0][1]["params"] == {"market": "stocks", "locale": "us", "active": "true", "limit": "1000", "sort": "ticker", "order": "asc"}
    assert calls[1][1]["params"] == {"cursor": "next-1"}
    assert all(call[1]["headers"] == {"Authorization": "Bearer secret"} for call in calls)
    assert "leak" not in json.dumps(report)
    snapshot = tmp_path / report["snapshot_relative_path"]
    assert (snapshot / "pages/001-response.json").read_bytes() == first
    assert stat.S_IMODE((snapshot / "pages/001-response.json").stat().st_mode) == 0o600
    assert stat.S_IMODE(snapshot.stat().st_mode) == 0o700
    assert report["pages"][0]["sha256"] == hashlib.sha256(first).hexdigest()
    assert report["observed_start"] <= report["observed_end"]
    tickers = json.loads((snapshot / "all-tickers.json").read_text())["tickers"]
    assert tickers == [_row("AAA"), _row("BBB", cik=None)]


def test_rejects_external_or_repeated_next_cursor_without_fake_complete(tmp_path):
    key = tmp_path / "key"; key.write_text("secret")
    raw = _payload([_row()], "https://evil.example/v3/reference/tickers?cursor=x")
    report = capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(raw), wait_fn=lambda _: None)
    snapshot = tmp_path / report["snapshot_relative_path"]
    assert report["status"] == "failed" and report["error_code"] == "next_url_invalid"
    assert (snapshot / "pages/001-response.json").read_bytes() == raw
    assert not (snapshot / "all-tickers.json").exists()
    with pytest.raises(MassiveReferenceError, match="next_url_invalid"):
        _safe_next_cursor("https://api.massive.com/v3/reference/tickers?cursor=x", {"x"})


def test_duplicate_ticker_and_http_auth_stop(tmp_path):
    key = tmp_path / "key"; key.write_text("secret")
    report = capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(_payload([_row("AAA"), _row("AAA")])), wait_fn=lambda _: None)
    assert report["error_code"] == "duplicate_ticker"
    failed = capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(b"do not archive", 401), wait_fn=lambda _: None)
    assert failed["status"] == "failed" and failed["error_code"] == "http_401"
    snapshot = tmp_path / failed["snapshot_relative_path"]
    assert not list((snapshot / "pages").glob("*")) if (snapshot / "pages").exists() else True


def test_status_count_market_and_empty_contracts_fail_closed(tmp_path):
    key = tmp_path / "key"; key.write_text("secret")
    bad_status = json.dumps({"status": "ERROR", "results": [_row()], "count": 1}).encode()
    assert capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(bad_status), wait_fn=lambda _: None)["error_code"] == "response_results_invalid"
    bad_count = json.dumps({"status": "OK", "results": [_row()], "count": True}).encode()
    assert capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(bad_count), wait_fn=lambda _: None)["error_code"] == "response_count_invalid"
    assert capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(_payload([_row(market="crypto")])), wait_fn=lambda _: None)["error_code"] == "result_market_or_locale_invalid"
    assert capture_massive_reference(tmp_path, key, request_get=lambda *_args, **_kwargs: Response(_payload([])), wait_fn=lambda _: None)["error_code"] == "response_results_empty"


def test_cli_help(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--credential-file" in capsys.readouterr().out
