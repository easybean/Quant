import json

import pytest

from quant_data.asset_reference import ENDPOINT, capture_asset_reference


def test_reference_only_get_endpoint_and_immutable_archive(tmp_path, monkeypatch):
    monkeypatch.setattr("quant_data.asset_reference.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls = []
    raw = json.dumps([{"id": "ffaf81e6-4488-4e58-9e22-8088a4e4e4b1", "symbol": "AAA", "class": "us_equity", "status": "active"}]).encode()
    class Response:
        status_code = 200
        content = raw
    def get(url, **kwargs):
        calls.append(url)
        assert kwargs["allow_redirects"] is False
        return Response()
    report = capture_asset_reference(tmp_path, None, request_get=get)
    assert calls == [ENDPOINT]
    assert report["records"] == 1 and not report["research_qualified"]
    assert "secret" not in json.dumps(report)
    assert list((tmp_path / "reference").rglob("assets.json"))[0].read_bytes() == raw


def test_auth_failure_never_falls_back_or_retries(tmp_path, monkeypatch):
    monkeypatch.setattr("quant_data.asset_reference.load_alpaca_credentials", lambda _: ("key", "secret"))
    calls = []
    class Response:
        status_code = 401
    def get(url, **kwargs):
        calls.append(url)
        return Response()
    with pytest.raises(ValueError, match="http_401"):
        capture_asset_reference(tmp_path, None, request_get=get)
    assert calls == [ENDPOINT]
    assert not (tmp_path / "reference").exists()
