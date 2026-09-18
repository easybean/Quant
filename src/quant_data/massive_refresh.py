"""Refresh current acquisition metadata; never qualify historical identity."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .massive_daily import _atomic_json
from .massive_reference import capture_massive_reference
from .massive_reference_audit import audit_massive_reference


def refresh(root: Path, master: Path, credential: Path) -> dict:
    captured = capture_massive_reference(root, credential, wait_for_lock=True)
    if captured.get("status") != "captured":
        raise ValueError("massive_reference_refresh_failed")
    evidence = json.loads((root / "catalogue/current-listing-gap-evidence-v1.json").read_text())
    relative = Path(evidence["snapshot_relative_path"])
    listing = root / relative
    if relative.is_absolute() or ".." in relative.parts or root.resolve() not in listing.resolve().parents:
        raise ValueError("listing_snapshot_path_invalid")
    audited = audit_massive_reference(root, master, root / captured["snapshot_relative_path"], listing)
    _atomic_json(root / "catalogue/current-massive-reference-v1.json", captured)
    _atomic_json(root / "catalogue/current-massive-alias-report-v1.json", audited)
    return {"status": "captured", "ticker_count": captured["ticker_count"],
            "classification_counts": audited["classification_counts"], "research_qualified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--security-master", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(refresh(args.data_root, args.security_master, args.credential_file)))
