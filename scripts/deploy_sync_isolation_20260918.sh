#!/usr/bin/env bash
set -euo pipefail
live=/home/davidou/quant/app/src/quant_data
stage=/home/davidou/quant/releases/us-sync-isolation-20260918-01/quant_data
backup=/home/davidou/quant/backups/us-sync-isolation-20260918-01
test -d "$live"
test -d "$stage"
test ! -e "$backup"
/home/davidou/quant/venv/bin/python -m compileall -q -x '/[.]_' "$stage"
PYTHONPATH=/home/davidou/quant/releases/us-sync-isolation-20260918-01 /home/davidou/quant/venv/bin/python -c 'import quant_data.sync_scheduler, quant_data.recovery_sync, quant_data.sip_batch'
mkdir "$backup"
cp -a "$live" "$backup/quant_data"
switched=0
success=0
finish() {
    result=$?
    trap - EXIT
    if [ "$success" -ne 1 ] && [ "$switched" -eq 1 ]; then
        systemctl --user stop quant-workbench-api.service || true
        mv "$live" "$backup/failed-package"
        mv "$backup/pre-switch-package" "$live"
    fi
    systemctl --user start quant-workbench-api.service || true
    systemctl --user start quant-us-daily-sync.timer || true
    systemctl --user start --no-block quant-us-daily-sync.service || true
    exit "$result"
}
trap finish EXIT
systemctl --user stop quant-us-daily-sync.timer
systemctl --user stop quant-us-daily-sync.service
systemctl --user stop quant-workbench-api.service
mv "$live" "$backup/pre-switch-package"
switched=1
if ! mv "$stage" "$live"; then
    mv "$backup/pre-switch-package" "$live"
    switched=0
    exit 1
fi
systemctl --user start quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
success=1
