import json

import pandas as pd

from quant_data.quality_report import QUALITY_RULE_VERSION, build_quality_report


def _bar(date, *, volume=1, high=11.0, low=9.0):
    return {"date": date, "symbol": "AAA", "open": 10.0, "high": high, "low": low, "close": 10.5, "volume": volume, "source": "fixture"}


def test_quality_report_expands_affected_rows_without_touching_derived(tmp_path):
    audit, derived, reports = tmp_path / "audit", tmp_path / "derived", tmp_path / "reports"
    relative = "provider=p/namespace=n/symbol=AAA-a/bars.parquet"
    target = derived / relative
    target.parent.mkdir(parents=True)
    pd.DataFrame([_bar("2024-01-02", volume=None), _bar("2024-01-03", high=9.0, low=10.0)]).to_parquet(target, index=False)
    audit.mkdir()
    second_relative = "provider=q/namespace=n/symbol=AAA-b/bars.parquet"
    second_target = derived / second_relative
    second_target.parent.mkdir(parents=True)
    pd.DataFrame([_bar("2024-01-04")]).to_parquet(second_target, index=False)
    pd.DataFrame([
        {"provider": "p", "namespace": "n", "symbol_key": "AAA-a", "raw_relative_path": relative},
        {"provider": "q", "namespace": "n", "symbol_key": "AAA-b", "raw_relative_path": second_relative},
    ]).to_parquet(audit / "coverage.parquet", index=False)
    (audit / "anomalies.jsonl").write_text("\n".join(json.dumps(x) for x in [
        {"kind": "missing_required_value", "provider": "p", "namespace": "n", "symbol_key": "AAA-a", "raw_relative_path": relative},
        {"kind": "ohlc_relation_invalid", "provider": "p", "namespace": "n", "symbol_key": "AAA-a", "raw_relative_path": relative},
    ]) + "\n")
    master = tmp_path / "master.parquet"
    pd.DataFrame([{"symbol": "AAA", "ipo_date": "2020-01-01", "delisting_date": None, "status": "active", "source": "fixture"}]).to_parquet(master, index=False)

    manifest = tmp_path / "download.jsonl"
    manifest.write_text(json.dumps({"status": "failed", "provider": "fixture", "symbol": "MISSING", "source": "fixture_api", "finished_at": "2024-01-05T00:00:00Z", "requested_start": "2024-01-01", "requested_end": "2024-01-04", "error": "ValueError: none"}) + "\n")
    result = build_quality_report(audit_root=audit, derived_root=derived, security_master=master, report_root=reports, manifests=[manifest])
    findings = pd.read_parquet(reports / "findings.parquet")
    assert result["rule_version"] == QUALITY_RULE_VERSION
    assert set(findings["kind"]) == {"missing_required_value", "ohlc_relation_invalid", "source_conflict_unmerged", "supplier_download_gap"}
    assert {"2024-01-02", "2024-01-03"}.issubset(set(findings["date"]))
    assert {"fixture", "fixture_api"}.issubset(set(findings["source"]))
    assert target.is_file()


def test_quality_report_rejects_derived_output_root(tmp_path):
    with __import__("pytest").raises(ValueError, match="separate from derived"):
        build_quality_report(audit_root=tmp_path, derived_root=tmp_path / "derived", security_master=tmp_path / "master", report_root=tmp_path / "derived")
