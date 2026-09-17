"""Daily read-only reference refresh for US quote acquisition, not trading."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .asset_reference import capture_asset_reference
from .daily_sync import _atomic_json
from .listing_reconciliation import capture_and_reconcile
from .symbol_mapping import build_symbol_mapping


def refresh(root: Path, credential_file: Path) -> dict:
    listing = capture_and_reconcile(root)
    assets = capture_asset_reference(root, credential_file)
    mapping = build_symbol_mapping(root / listing["snapshot_relative_path"],
                                   root / assets["snapshot_relative_path"], root)
    path = Path(mapping["path"])
    pointer = {"schema_version": "current-symbol-mapping-v1",
               "relative_path": path.relative_to(root).as_posix(),
               "sha256": mapping["mapping_sha256"]}
    _atomic_json(root / "catalogue/current-symbol-mapping-v1.json", pointer)
    return {"listing_observed_at": listing["observed_at"],
            "acquisition_master_added": listing["acquisition_master_added"],
            "asset_records": assets["records"], "mapping_accepted": mapping["accepted"],
            "mapping_rejected": mapping["rejected"], "research_qualified": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(refresh(args.data_root, args.credential_file), ensure_ascii=False))


if __name__ == "__main__":
    main()
