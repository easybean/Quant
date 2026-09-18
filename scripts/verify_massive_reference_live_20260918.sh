#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/massive-backfill-20260918-01
live=/home/davidou/quant/app/src/quant_data
finish() {
    result=$?
    trap - EXIT
    systemctl --user start quant-massive-backfill.timer || true
    systemctl --user start quant-massive-code-audit.timer || true
    systemctl --user start --no-block quant-massive-code-audit.service || true
    exit "$result"
}
trap finish EXIT
systemctl --user stop quant-massive-backfill.timer
systemctl --user stop quant-massive-code-audit.timer
systemctl --user stop quant-massive-backfill.service
systemctl --user stop quant-massive-code-audit.service
for name in massive_reference massive_reference_audit massive_backfill; do
    /home/davidou/quant/venv/bin/python -m py_compile "$release/$name.py"
    cp "$release/$name.py" "$live/.$name.py.new"
    mv "$live/.$name.py.new" "$live/$name.py"
done
umask 077
export HTTP_PROXY=http://127.0.0.1:7890
export HTTPS_PROXY=http://127.0.0.1:7890
/home/davidou/quant/venv/bin/python -c 'from pathlib import Path; import json; from quant_data.massive_reference import capture_massive_reference; from quant_data.massive_daily import _atomic_json; from quant_data.massive_reference_audit import audit_massive_reference; r=Path("/home/davidou/quant/data"); m=capture_massive_reference(r,Path("/home/davidou/massivekey")); print(json.dumps({k:m.get(k) for k in ["status","error_code","ticker_count","snapshot_relative_path"]}),flush=True); assert m.get("status")=="captured"; _atomic_json(r/"catalogue/current-massive-reference-v1.json",m); e=json.loads((r/"catalogue/current-listing-gap-evidence-v1.json").read_text()); a=audit_massive_reference(r,r/"metadata/security_master.parquet",r/m["snapshot_relative_path"],r/e["snapshot_relative_path"]); print(json.dumps(a),flush=True)'
