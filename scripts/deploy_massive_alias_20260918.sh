#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/massive-alias-20260918-01
live=/home/davidou/quant/app/src/quant_data
backup=/home/davidou/quant/backups/massive-alias-20260918-01
test ! -e "$backup"
mkdir "$backup"
cp -a /home/davidou/quant/app/ui-dist "$backup/ui-dist"
for name in massive_probe massive_reference massive_reference_audit daily_quote_browser sync_scheduler sync_coverage sync_status; do
    cp "$live/$name.py" "$backup/$name.py"
done
cp /home/davidou/.config/systemd/user/quant-massive-daily.service "$backup/quant-massive-daily.service"
cp /home/davidou/.config/systemd/user/quant-massive-backfill.service "$backup/quant-massive-backfill.service"
success=0
for name in massive_probe massive_reference massive_reference_audit massive_refresh massive_alias_publish daily_quote_browser sync_scheduler sync_coverage sync_status; do
    /home/davidou/quant/venv/bin/python -m py_compile "$release/$name.py"
done
systemd-analyze --user verify "$release/quant-massive-daily.service" "$release/quant-massive-backfill.service" "$release/quant-massive-reference.service" "$release/quant-massive-reference.timer"
finish() {
    result=$?
    trap - EXIT
    if [ "$success" -ne 1 ]; then
        for name in massive_probe massive_reference massive_reference_audit daily_quote_browser sync_scheduler sync_coverage sync_status; do
            cp "$backup/$name.py" "$live/.$name.py.restore"
            mv "$live/.$name.py.restore" "$live/$name.py"
        done
        cp "$backup/quant-massive-daily.service" /home/davidou/.config/systemd/user/quant-massive-daily.service
        cp "$backup/quant-massive-backfill.service" /home/davidou/.config/systemd/user/quant-massive-backfill.service
        if [ -d /home/davidou/quant/app/ui-dist.massive-previous ]; then
            mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.massive-failed
            mv /home/davidou/quant/app/ui-dist.massive-previous /home/davidou/quant/app/ui-dist
        fi
        systemctl --user disable --now quant-massive-reference.timer || true
        systemctl --user daemon-reload
        systemctl --user restart quant-workbench-api.service || true
    fi
    systemctl --user start quant-massive-backfill.timer || true
    systemctl --user start quant-massive-code-audit.timer || true
    systemctl --user start --no-block quant-massive-code-audit.service || true
    exit "$result"
}
trap finish EXIT
systemctl --user stop quant-massive-backfill.timer quant-massive-code-audit.timer
systemctl --user stop quant-massive-backfill.service quant-massive-code-audit.service
for name in massive_probe massive_reference massive_reference_audit massive_refresh massive_alias_publish daily_quote_browser sync_scheduler sync_coverage sync_status; do
    cp "$release/$name.py" "$live/.$name.py.new"
    mv "$live/.$name.py.new" "$live/$name.py"
done
for unit in quant-massive-daily.service quant-massive-backfill.service quant-massive-reference.service quant-massive-reference.timer; do
    cp "$release/$unit" "/home/davidou/.config/systemd/user/$unit"
done
umask 077
/home/davidou/quant/venv/bin/python -c 'import json; from pathlib import Path; from quant_data.massive_daily import _atomic_json; from quant_data.massive_reference_audit import audit_massive_reference; r=Path("/home/davidou/quant/data"); m=json.loads((r/"catalogue/current-massive-reference-v1.json").read_text()); e=json.loads((r/"catalogue/current-listing-gap-evidence-v1.json").read_text()); a=audit_massive_reference(r,r/"metadata/security_master.parquet",r/m["snapshot_relative_path"],r/e["snapshot_relative_path"]); _atomic_json(r/"catalogue/current-massive-alias-report-v1.json",a); print(json.dumps({"classification_counts":a["classification_counts"]}))'
/home/davidou/quant/venv/bin/python -m quant_data.massive_alias_publish --data-root /home/davidou/quant/data --security-master /home/davidou/quant/data/metadata/security_master.parquet --all-captured-recent
cp -a "$release/ui-dist" /home/davidou/quant/app/ui-dist.massive-new
mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.massive-previous
mv /home/davidou/quant/app/ui-dist.massive-new /home/davidou/quant/app/ui-dist
systemctl --user daemon-reload
systemctl --user enable --now quant-massive-reference.timer
systemctl --user restart quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
/home/davidou/quant/venv/bin/python -m quant_data.sync_coverage --data-root /home/davidou/quant/data --security-master /home/davidou/quant/data/metadata/security_master.parquet
systemctl --user start --no-block quant-massive-backfill.service
success=1
