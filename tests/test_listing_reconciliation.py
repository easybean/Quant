import pytest
import pandas as pd
import hashlib

from quant_data.listing_reconciliation import classify_tasks, validate_directory
from quant_data.listing_reconciliation import publish_acquisition_master


def test_directory_requires_valid_footer_flags_and_unique_symbols():
    value = b"Symbol|Security Name|Test Issue|ETF\nAAA|Company|N|N\nFile Creation Time: 0917202621:31|||\n"
    assert validate_directory(value, "nasdaqlisted.txt").startswith("File Creation Time:")
    for wrong in (value.replace(b"|N|N", b"|X|N"), value.split(b"File Creation")[0], value.replace(b"File Creation", b"AAA|Company|N|N\nFile Creation")):
        with pytest.raises(ValueError):
            validate_directory(wrong, "nasdaqlisted.txt")


def test_absence_is_unknown_not_delisted_or_excluded():
    report = classify_tasks([{"symbol": "AAA"}, {"symbol": "OLD"}], {"AAA"})
    assert len(report["items"]) == 2
    assert report["items"][1]["listing_evidence"] == "absent_from_current_directory_unknown"
    assert not report["items"][1]["research_qualified"]


def test_master_append_preserves_existing_rows_and_source(tmp_path):
    (tmp_path / "metadata").mkdir()
    snapshot = tmp_path / "reference/snapshot"
    snapshot.mkdir(parents=True)
    base = tmp_path / "metadata/security_master.parquet"
    original = pd.DataFrame([{"symbol": "OLD", "status": "active", "name": "Old"},
                             {"symbol": "REUSE", "status": "delisted", "name": "Prior"}])
    original.to_parquet(base, index=False)
    before = base.read_bytes()
    current = pd.DataFrame([{"symbol": "NEW", "status": "active", "name": "New"},
                            {"symbol": "REUSE", "status": "active", "name": "Different"}])
    pointer = publish_acquisition_master(tmp_path, snapshot, current, "2026-09-18T00:00:00+00:00")
    output = tmp_path / pointer["relative_path"]
    result = pd.read_parquet(output)
    pd.testing.assert_frame_equal(result.iloc[:2].reset_index(drop=True), original)
    assert pointer["added_symbols"] == ["NEW"]
    assert pointer["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    assert base.read_bytes() == before
    assert not pointer["research_qualified"]
    next_snapshot = tmp_path / "reference/next"
    next_snapshot.mkdir()
    next_pointer = publish_acquisition_master(tmp_path, next_snapshot, current.iloc[1:], "2026-09-19T00:00:00+00:00")
    assert "NEW" in pd.read_parquet(tmp_path / next_pointer["relative_path"]).symbol.tolist()
    assert next_pointer["added_symbols"] == []
    assert next_pointer["inherited_input"] == {"relative_path": pointer["relative_path"], "sha256": pointer["sha256"]}
