from __future__ import annotations

import hashlib
import json

import pandas as pd
import pytest

from quant_data.massive_reference_audit import MassiveReferenceAuditError, audit_massive_reference, classify_master_code, classify_unmatched_reference_ticker, main


def _reference(tickers):
    return {row["ticker"]: row for row in tickers}


def test_classifies_exact_alias_ambiguity_and_unknown_without_case_guessing():
    rows = [
        {"ticker": "AAA", "primary_exchange": "XNAS", "name": "Alpha"},
        {"ticker": "BRKpB", "primary_exchange": "XNYS", "name": "Berkshire"},
        {"ticker": "DUAL1", "primary_exchange": "XNYS", "name": "Dual"},
        {"ticker": "DUAL2", "primary_exchange": "XNYS", "name": "Dual"},
    ]
    listings = [
        {"primary_symbol": "BRK-B", "security_name": "Berkshire", "exchange": "NYSE", "aliases": {"CQS Symbol": "BRKpB", "NASDAQ Symbol": "BRK-B"}},
        {"primary_symbol": "DUAL", "security_name": "Dual", "exchange": "NYSE", "aliases": {"CQS Symbol": "DUAL1", "NASDAQ Symbol": "DUAL2"}},
    ]
    reference = _reference(rows)
    assert classify_master_code("AAA", reference, listings)["classification"] == "exact_reference_present"
    alias = classify_master_code("BRK-B", reference, listings)
    assert alias["classification"] == "authoritative_alias_candidate"
    assert alias["candidates"][0]["reference_ticker"] == "BRKpB"
    assert classify_master_code("brk-b", reference, listings)["classification"] == "absent_reference_unknown"
    assert classify_master_code("DUAL", reference, listings)["classification"] == "ambiguity"
    inverse = classify_unmatched_reference_ticker("BRKpB", reference["BRKpB"], {"BRK-B"}, listings)
    assert inverse["classification"] == "authoritative_alias_candidate"
    assert inverse["candidates"][0]["master_symbol"] == "BRK-B"


def test_audit_validates_reference_hashes_and_writes_all_master_codes(tmp_path, monkeypatch):
    snapshot = tmp_path / "reference"
    pages = snapshot / "pages"; pages.mkdir(parents=True)
    raw = b'{"status":"OK"}'
    page = pages / "001-response.json"; page.write_bytes(raw)
    tickers = {"tickers": [{"ticker": "AAA", "primary_exchange": "XNAS", "name": "Alpha"}]}
    all_path = snapshot / "all-tickers.json"; all_path.write_text(json.dumps(tickers))
    manifest = {"schema_version": "massive-ticker-reference-v1", "status": "captured", "all_tickers_sha256": hashlib.sha256(all_path.read_bytes()).hexdigest(),
                "pages": [{"raw_relative_path": "pages/001-response.json", "sha256": hashlib.sha256(raw).hexdigest()}]}
    (snapshot / "manifest.json").write_text(json.dumps(manifest))
    master = tmp_path / "master.csv"; pd.DataFrame({"symbol": ["AAA", "BRK-B"]}).to_csv(master, index=False)
    listing = tmp_path / "listing"; listing.mkdir()
    monkeypatch.setattr("quant_data.massive_reference_audit._listing_rows", lambda _: ([{"primary_symbol": "BRK-B", "security_name": "Berkshire", "exchange": "NYSE", "aliases": {"CQS Symbol": "none", "NASDAQ Symbol": "BRK-B"}}], {"fixture": "hash"}, "fixture-footer"))
    result = audit_massive_reference(tmp_path, master, snapshot, listing)
    report = json.loads((tmp_path / result["report_relative_path"]).read_text())
    assert result["master_code_count"] == 2
    assert {item["symbol"] for item in report["items"]} == {"AAA", "BRK-B"}
    assert report["classification_counts"]["exact_reference_present"] == 1
    (snapshot / "pages/001-response.json").write_bytes(b"tampered")
    with pytest.raises(MassiveReferenceAuditError, match="reference_page_hash_invalid"):
        audit_massive_reference(tmp_path, master, snapshot, listing)


def test_cli_help(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--help"])
    assert exc.value.code == 0
    assert "--reference-snapshot" in capsys.readouterr().out
