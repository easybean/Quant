#!/usr/bin/env python3
"""P1-04 narrow corporate-actions snapshot.  Never use for a bulk download."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from quant_data.corporate_actions import capture_representative_snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--snapshot-id", required=True)
    parser.add_argument("--credential-file", type=Path)
    args = parser.parse_args()
    result = capture_representative_snapshot(data_root=args.data_root, snapshot_id=args.snapshot_id,
                                             credential_file=args.credential_file)
    # Contains paths/counts/check status only; never secrets or API payloads.
    print(json.dumps(result, sort_keys=True))
    return 0 if result["golden_checks_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
