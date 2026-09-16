import hashlib
import json

import pandas as pd

from quant_data.cleaning import CLEANING_STATUS, run_structural_clean


def _digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_structural_clean_writes_only_derived_and_preserves_raw_hash(tmp_path):
    raw = tmp_path / "raw" / "provider=nasdaq" / "namespace=default" / "symbol=ABC-test"
    raw.mkdir(parents=True)
    source = raw / "bars.parquet"
    pd.DataFrame({
        "date": ["2020-01-03", "bad-date", "2020-01-02", "2020-01-02"],
        "symbol": ["ABC"] * 4, "open": [10, 11, 9, 9.5], "high": [11, 12, 10, 10.5],
        "low": [9, 10, 8, 8.5], "close": [10.5, 11.5, 9.5, 10], "volume": [100, 120, 90, 95],
        "source": ["nasdaq_web_unadjusted"] * 4, "adjustment_status": ["unadjusted"] * 4,
    }).to_parquet(source, index=False)
    before = _digest(source)
    derived, audit = tmp_path / "derived" / "v1", tmp_path / "audit" / "v1"

    summary = run_structural_clean(raw.parents[2], output_root=derived, audit_root=audit)

    assert _digest(source) == before
    assert summary["derived_files_written"] == 1
    cleaned = pd.read_parquet(derived / "provider=nasdaq" / "namespace=default" / "symbol=ABC-test" / "bars.parquet")
    assert list(cleaned["date"].dt.strftime("%Y-%m-%d")) == ["2020-01-02", "2020-01-03"]
    assert cleaned.loc[0, "close"] == 10
    assert set(cleaned["cleaning_status"]) == {CLEANING_STATUS}
    assert {"invalid_date_dropped", "duplicate_date_keep_last"}.issubset(
        {json.loads(line)["kind"] for line in (audit / "anomalies.jsonl").read_text().splitlines()}
    )
    assert json.loads((audit / "quality-summary.json").read_text())["raw_files"] == 1


def test_cleaning_rejects_output_inside_raw_and_dry_run_does_not_write_derived(tmp_path):
    raw = tmp_path / "raw"
    raw.mkdir(parents=True)
    try:
        run_structural_clean(raw, output_root=raw / "derived")
    except ValueError as exc:
        assert "read-only" in str(exc)
    else:
        raise AssertionError("must reject an output under raw")
    summary = run_structural_clean(raw, dry_run=True)
    assert summary["raw_files"] == 0
