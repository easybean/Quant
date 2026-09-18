"""Mock-only Massive transport-contract tests; not live source validation."""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone

import pandas as pd
import pytest

from quant_data.massive_daily import (
    ENDPOINT_TEMPLATE,
    MassiveDailyError,
    _finite_number,
    capture_massive_daily,
    main,
)


REQUESTED = date(2026, 9, 16)
NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


class Response:
    def __init__(self, status_code: int = 200, content: bytes = b"") -> None:
        self.status_code = status_code
        self.content = content


def _payload(rows: list[object], **overrides: object) -> bytes:
    body: dict[str, object] = {
        "status": "OK",
        "adjusted": False,
        "resultsCount": len(rows),
        "results": rows,
    }
    body.update(overrides)
    return json.dumps(body).encode()


def _row(symbol: str = "AAA", **overrides: object) -> dict[str, object]:
    # Midnight UTC is still the requested New York trading date in September.
    row: dict[str, object] = {"T": symbol, "t": 1789603200000, "o": 10, "h": 11, "l": 9, "c": 10.5, "v": 100}
    row.update(overrides)
    return row


@pytest.fixture(autouse=True)
def fixed_completed_session(monkeypatch):
    monkeypatch.setattr("quant_data.massive_daily.completed_session", lambda now: REQUESTED)


def test_one_read_only_get_writes_isolated_raw_manifest_and_normalized_rows(tmp_path):
    credential = tmp_path / "massive.key"
    credential.write_text("test-key\n", encoding="utf-8")
    calls = []
    raw = _payload([_row()])

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return Response(content=raw)

    report = capture_massive_daily(tmp_path, REQUESTED, credential, now=NOW, request_get=get)

    assert [url for url, _ in calls] == [ENDPOINT_TEMPLATE.format(date="2026-09-16")]
    kwargs = calls[0][1]
    assert kwargs["headers"] == {"Authorization": "Bearer test-key"}
    assert kwargs["params"] == {"adjusted": "false", "include_otc": "false"}
    assert kwargs["timeout"] == 30 and kwargs["allow_redirects"] is False
    assert report["status"] == "captured" and report["qualified"] is False
    assert "test-key" not in json.dumps(report)

    snapshot = tmp_path / report["snapshot_relative_path"]
    assert snapshot.parent == tmp_path / "reference" / "massive-daily-v1"
    assert (snapshot / "response.json").read_bytes() == raw
    assert report["response_sha256"] == hashlib.sha256(raw).hexdigest()
    diagnostics = json.loads((snapshot / "diagnostics.json").read_text())
    assert report["diagnostics_sha256"] == hashlib.sha256((snapshot / "diagnostics.json").read_bytes()).hexdigest()
    assert report["normalized_sha256"] == hashlib.sha256((snapshot / "bars.parquet").read_bytes()).hexdigest()
    assert diagnostics["accepted_rows"] == [0] and diagnostics["rejected_rows"] == []
    bars = pd.read_parquet(snapshot / "bars.parquet")
    assert list(bars.columns) == ["symbol", "date", "open", "high", "low", "close", "volume", "available_at", "retrieved_at", "actions_status"]
    assert bars.loc[0, "symbol"] == "AAA" and bars.loc[0, "actions_status"] == "unknown"
    assert bars.loc[0, "available_at"] == bars.loc[0, "retrieved_at"]
    assert not list((tmp_path / "bars").rglob("*")) if (tmp_path / "bars").exists() else True


def test_future_date_is_refused_before_credentials_or_network(tmp_path):
    with pytest.raises(MassiveDailyError, match="requested_date_after_completed_session"):
        capture_massive_daily(
            tmp_path,
            date(2026, 9, 17),
            tmp_path / "missing.key",
            now=NOW,
            request_get=lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("network called")),
        )


def test_http_error_is_static_and_never_archives_response_body(tmp_path):
    credential = tmp_path / "massive.key"
    credential.write_text("test-key", encoding="utf-8")
    with pytest.raises(MassiveDailyError, match="http_401"):
        capture_massive_daily(
            tmp_path, REQUESTED, credential, now=NOW,
            request_get=lambda *_args, **_kwargs: Response(401, b'{"error":"do not save"}'),
        )
    assert not (tmp_path / "reference").exists()


def test_invalid_response_is_preserved_but_never_normalized(tmp_path):
    credential = tmp_path / "massive.key"
    credential.write_text("test-key", encoding="utf-8")
    raw = _payload([_row()], adjusted=True)
    report = capture_massive_daily(tmp_path, REQUESTED, credential, now=NOW, request_get=lambda *_args, **_kwargs: Response(content=raw))
    snapshot = tmp_path / report["snapshot_relative_path"]
    assert report["status"] == "validation_failed"
    assert report["error_code"] == "response_adjusted_not_false"
    assert (snapshot / "response.json").read_bytes() == raw
    assert not (snapshot / "bars.parquet").exists()


def test_rows_are_rejected_individually_without_filling_or_repair(tmp_path):
    credential = tmp_path / "massive.key"
    credential.write_text("test-key", encoding="utf-8")
    raw = _payload([
        _row("AAA"),
        _row("BAD", h=8),
        _row("BOOL", v=True),
        _row("AAA"),
        _row("WRONG_DAY", t=1789689600000),
        _row("GOOD"),
    ])
    report = capture_massive_daily(tmp_path, REQUESTED, credential, now=NOW, request_get=lambda *_args, **_kwargs: Response(content=raw))
    snapshot = tmp_path / report["snapshot_relative_path"]
    diagnostics = json.loads((snapshot / "diagnostics.json").read_text())
    assert report["status"] == "captured"
    assert diagnostics["accepted_rows"] == [5]
    assert [item["code"] for item in diagnostics["rejected_rows"]] == [
        "duplicate_symbol", "inconsistent_ohlc", "invalid_ohlcv_type_or_finiteness", "duplicate_symbol", "timestamp_not_requested_ny_session"
    ]
    assert pd.read_parquet(snapshot / "bars.parquet")["symbol"].tolist() == ["GOOD"]


def test_results_count_and_json_failure_keep_raw_without_bars(tmp_path):
    credential = tmp_path / "massive.key"
    credential.write_text("test-key", encoding="utf-8")
    report = capture_massive_daily(
        tmp_path, REQUESTED, credential, now=NOW,
        request_get=lambda *_args, **_kwargs: Response(content=b"not-json"),
    )
    snapshot = tmp_path / report["snapshot_relative_path"]
    assert report["error_code"] == "response_json_invalid"
    assert (snapshot / "response.json").read_bytes() == b"not-json"
    assert not (snapshot / "bars.parquet").exists()


def test_out_of_range_integer_is_rejected_without_float_overflow():
    assert _finite_number(10 ** 100_000) is None


def test_cli_reports_local_output_error_without_an_oserror_path(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        "quant_data.massive_daily.capture_massive_daily",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError(f"{tmp_path}/private-output")),
    )
    assert main(["--date", "2026-09-16", "--data-root", str(tmp_path), "--credential-file", "key"]) == 2
    assert json.loads(capsys.readouterr().out) == {"status": "failed", "error_code": "local_output_error"}
