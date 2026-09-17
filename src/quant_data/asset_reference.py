"""Read-only provider asset-reference snapshot; never accesses account/orders."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import uuid

import requests

from .daily_sync import _atomic_json
from .pipeline import load_alpaca_credentials

ENDPOINT = "https://paper-api.alpaca.markets/v2/assets"


def capture_asset_reference(root: Path, credential_file: Path, *, request_get=requests.get) -> dict:
    key, secret = load_alpaca_credentials(credential_file)
    response = request_get(ENDPOINT, params={"asset_class": "us_equity"},
                           headers={"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}, timeout=30, allow_redirects=False)
    # No endpoint fallback, authentication retries, account or order requests.
    if response.status_code != 200:
        raise ValueError(f"asset_reference_http_{response.status_code}")
    raw = response.content
    if len(raw) > 40_000_000:
        raise ValueError("asset_reference_too_large")
    rows = json.loads(raw)
    if not isinstance(rows, list) or not rows or not all(isinstance(row, dict) and isinstance(row.get("id"), str) and isinstance(row.get("symbol"), str) and row.get("class") == "us_equity" for row in rows):
        raise ValueError("asset_reference_invalid")
    try:
        ids = [str(uuid.UUID(row["id"])) for row in rows]
    except ValueError as exc:
        raise ValueError("asset_reference_invalid_identity") from exc
    if len(ids) != len(set(ids)):
        raise ValueError("asset_reference_duplicate_identity")
    now = datetime.now(timezone.utc)
    snapshot = root / "reference/provider-asset-metadata" / (now.strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    snapshot.mkdir(parents=True, exist_ok=False)
    with (snapshot / "assets.json").open("xb") as handle:
        handle.write(raw)
    manifest = {"schema_version": "provider-asset-reference-v1", "observed_at": now.isoformat(), "source": ENDPOINT,
                "snapshot_relative_path": snapshot.relative_to(root).as_posix(),
                "sha256": hashlib.sha256(raw).hexdigest(), "records": len(rows),
                "active": sum(row.get("status") == "active" for row in rows), "research_qualified": False,
                "warning": "Provider-specific current reference, not verified historical identity, market-data coverage, transaction permission or historical contract rules."}
    _atomic_json(snapshot / "manifest.json", manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(capture_asset_reference(args.data_root, args.credential_file)))


if __name__ == "__main__":
    main()
