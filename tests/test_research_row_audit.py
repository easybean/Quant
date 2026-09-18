import json
import os
import subprocess
import sys

import pytest

from quant_data.research_row_audit import MAX_JSON_BYTES, audit_rows


def _payload():
    return {
        "schema_version": "bounded-pit-audit-v1",
        "cohort_locked_at": "2024-01-02T21:00:00Z",
        "decision_time": "2024-01-03T21:00:00+00:00",
        "members": [{"instrument_id": "US:ABC:1"}],
        "bars": [{
            "instrument_id": "US:ABC:1", "session_date": "2024-01-03",
            "event_time": "2024-01-03T20:00:00+00:00",
            "available_at": "2024-01-03T20:05:00+00:00",
            "ingest_time": "2024-01-03T20:10:00+00:00",
            "open": 10.0, "high": 12.0, "low": 9.0, "close": 11.0, "volume": 0,
        }],
    }


def _write(tmp_path, payload, name="audit.json"):
    path = tmp_path / name
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def _audit(tmp_path, mutate):
    payload = _payload()
    mutate(payload)
    return audit_rows(_write(tmp_path, payload))


def test_valid_rows_pass_only_bounded_checks_and_never_qualify(tmp_path):
    result = audit_rows(_write(tmp_path, _payload()))
    assert result["row_checks_passed"] is True
    assert result["qualified"] is False
    assert result["blockers"] == []
    assert result["warnings"] == [
        "Stable instrument identity was not independently verified.",
        "Corporate actions were not independently verified.",
        "Trading calendar and session validity were not independently verified.",
        "License and source permissions were not independently verified.",
    ]


def test_empty_members_or_bars_fail_closed(tmp_path):
    result = _audit(tmp_path, lambda payload: payload.update(members=[]))
    assert "members must contain at least one member" in result["blockers"]
    result = _audit(tmp_path, lambda payload: payload.update(bars=[]))
    assert "bars must contain at least one bar" in result["blockers"]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", True), ("high", False), ("low", float("nan")), ("close", float("inf")),
        ("volume", True), ("volume", float("nan")), ("volume", -1),
    ],
)
def test_bool_and_nonfinite_ohlcv_values_fail_closed(tmp_path, field, value):
    result = _audit(tmp_path, lambda payload: payload["bars"][0].__setitem__(field, value))
    assert result["row_checks_passed"] is False
    assert result["blockers"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["bars"][0].update(available_at="2024-01-03T19:59:59+00:00"),
        lambda payload: payload["bars"][0].update(ingest_time="2024-01-03T20:04:59+00:00"),
        lambda payload: payload["bars"][0].update(available_at="2024-01-03T21:00:01+00:00"),
        lambda payload: payload.update(decision_time="2024-01-02T20:59:59+00:00"),
        lambda payload: payload["bars"][0].update(session_date="2024-01-04"),
        lambda payload: payload["bars"][0].update(high=8.0),
    ],
)
def test_temporal_and_ohlc_boundaries_fail_closed(tmp_path, mutate):
    result = _audit(tmp_path, mutate)
    assert result["row_checks_passed"] is False


def test_unknown_member_and_duplicate_member_and_session_fail_closed(tmp_path):
    result = _audit(tmp_path, lambda payload: payload["bars"][0].update(instrument_id="US:UNKNOWN:1"))
    assert any("not a member" in blocker for blocker in result["blockers"])
    result = _audit(tmp_path, lambda payload: payload["members"].append({"instrument_id": "US:ABC:1"}))
    assert any("duplicates a member" in blocker for blocker in result["blockers"])
    result = _audit(tmp_path, lambda payload: payload["bars"].append(dict(payload["bars"][0])))
    assert any("duplicates instrument_id/session_date" in blocker for blocker in result["blockers"])


def test_huge_integer_and_noncanonical_date_and_instrument_id_fail_closed(tmp_path):
    result = _audit(tmp_path, lambda payload: payload["bars"][0].update(open=10**400))
    assert any("open must be a finite positive number" in blocker for blocker in result["blockers"])
    result = _audit(tmp_path, lambda payload: payload["bars"][0].update(session_date="20240103"))
    assert any("session_date must be an ISO session date" in blocker for blocker in result["blockers"])
    result = _audit(tmp_path, lambda payload: payload["members"][0].update(instrument_id=" US:ABC:1 "))
    assert any("without surrounding whitespace" in blocker for blocker in result["blockers"])


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload.update(cohort_locked_at="2024-01-02T21:00:00"),
        lambda payload: payload.update(decision_time="2024-01-03T22:00:00+01:00"),
        lambda payload: payload["bars"][0].update(event_time="2024-01-03T20:00:00"),
        lambda payload: payload["bars"][0].update(available_at="2024-01-03T21:05:00+01:00"),
    ],
)
def test_naive_or_non_utc_timestamps_fail_closed(tmp_path, mutate):
    result = _audit(tmp_path, mutate)
    assert result["row_checks_passed"] is False
    assert any("aware UTC timestamp" in blocker for blocker in result["blockers"])


def test_non_object_root_and_json_nan_constant_fail_closed(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("[]", encoding="utf-8")
    assert audit_rows(path)["row_checks_passed"] is False
    path.write_text('{"value": NaN}', encoding="utf-8")
    result = audit_rows(path)
    assert result["row_checks_passed"] is False
    assert "input cannot be read as valid JSON" in result["blockers"]


def test_symlink_and_oversize_file_are_rejected(tmp_path):
    target = _write(tmp_path, _payload(), "target.json")
    link = tmp_path / "input-link.json"
    try:
        os.symlink(target, link)
    except OSError as error:
        pytest.skip(f"symlinks unavailable: {error}")
    assert "input path must not be a symlink" in audit_rows(link)["blockers"]
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (MAX_JSON_BYTES + 1))
    assert "input JSON exceeds the 16 MiB limit" in audit_rows(oversized)["blockers"]


def test_cli_returns_json_and_nonzero_for_malformed_root(tmp_path):
    path = tmp_path / "root.json"
    path.write_text("null", encoding="utf-8")
    completed = subprocess.run(
        [sys.executable, "-m", "quant_data.research_row_audit", str(path)],
        capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 1
    result = json.loads(completed.stdout)
    assert result["row_checks_passed"] is False
    assert result["qualified"] is False
