"""Publish one offline, non-qualified TSLA strategy replay as a new artifact."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from quant_data.exploratory_backtest import run


def sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def publish(snapshot: Path, output: Path, data_root: Path) -> dict:
    if output.exists():
        raise ValueError("output already exists; immutable reports must not be overwritten")
    report = run(snapshot, data_root=data_root)
    if report["qualified"] is not False or report["formal_backtest_enabled"] is not False:
        raise ValueError("exploratory report claimed qualification")
    output.mkdir(parents=True, exist_ok=False)
    path = output / "report.json"
    path.write_text(json.dumps(report, sort_keys=True, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    manifest = {"schema_version": "exploratory-tsla-backtest-artifact-v1",
                "snapshot_manifest_sha256": sha(snapshot / "manifest.json"), "report_sha256": sha(path),
                "qualified": False, "formal_backtest_enabled": False}
    (output / "manifest.json").write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return {"output": str(output), "snapshot_manifest_sha256": manifest["snapshot_manifest_sha256"],
            "report_sha256": manifest["report_sha256"], "strategies": {name: item["metrics"] for name, item in report["strategies"].items()}}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(publish(args.snapshot, args.output, args.data_root), ensure_ascii=False))
