"""Bounded real-request acceptance; the full scheduler continues separately."""
import json
from pathlib import Path

from quant_data.recovery_sync import run_recovery
from quant_data.sync_scheduler import resolve_sync_master

ROOT = Path("/home/davidou/quant/data")
RELEASE = Path("/home/davidou/quant/releases/us-sync-isolation-20260918-01")
queue = json.loads((ROOT / "manifests/us-daily-sync/alpaca-due.json").read_text())
wanted = {"BC/PB", "BC/PC", "CAPTW(EXP20260807)", "NXT(EXP20091224)", "NYCB- PR-U", "TSLA", "MSFT", "SUPX"}
tasks = [t for t in queue["tasks"] if t["symbol"] in wanted]
if not any(t["symbol"] == "TSLA" for t in tasks):
    raise ValueError("acceptance queue no longer contains TSLA; inspect current coverage")
path = RELEASE / "acceptance-queue.json"
with path.open("x") as handle:
    json.dump({**queue, "tasks": tasks}, handle)
master = resolve_sync_master(ROOT, ROOT / "metadata/security_master.parquet")
result = run_recovery(master, ROOT, gap_queue=path, recovery_provider="alpaca", credential_file=Path("/home/davidou/alpacakey"),
                      max_runtime_seconds=60, request_delay=0)
with (RELEASE / "acceptance-result.json").open("x") as handle:
    json.dump(result, handle, indent=2)
print(json.dumps(result, sort_keys=True))
