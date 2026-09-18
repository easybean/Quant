#!/usr/bin/env bash
set -euo pipefail
live=/home/davidou/quant/app/src/quant_data
ui=/home/davidou/quant/app/ui-dist
release=/home/davidou/quant/releases/massive-daily-live-20260918-01
backup=/home/davidou/quant/backups/massive-daily-live-20260918-01
test -d "$release/quant_data"
test -d "$release/ui-dist"
test ! -e "$backup"
test ! -e /home/davidou/.config/systemd/user/quant-massive-daily.service
test ! -e /home/davidou/.config/systemd/user/quant-massive-daily.timer
/home/davidou/quant/venv/bin/python -m compileall -q -x '/[.]_' "$release/quant_data"
PYTHONPATH="$release" /home/davidou/quant/venv/bin/python -c 'import quant_data.massive_publish, quant_data.api, quant_data.sync_scheduler'
systemd-analyze --user verify "$release/quant-massive-daily.service" "$release/quant-massive-daily.timer"
mkdir "$backup"
cp -a "$live" "$backup/quant_data"
cp -a "$ui" "$backup/ui-dist"
package_switched=0
ui_switched=0
success=0
finish() {
    result=$?
    trap - EXIT
    if [ "$success" -ne 1 ]; then
        systemctl --user disable --now quant-massive-daily.timer 2>/dev/null || true
        systemctl --user stop quant-massive-daily.service 2>/dev/null || true
        systemctl --user stop quant-workbench-api.service || true
        if [ "$package_switched" -eq 1 ]; then
            if [ -d "$live" ]; then mv "$live" "$backup/failed-package"; fi
            mv "$backup/pre-switch-package" "$live"
        fi
        if [ "$ui_switched" -eq 1 ]; then
            if [ -d "$ui" ]; then mv "$ui" "$backup/failed-ui"; fi
            mv "$backup/pre-switch-ui" "$ui"
        fi
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
package_switched=1
mv "$release/quant_data" "$live"
mv "$ui" "$backup/pre-switch-ui"
ui_switched=1
mv "$release/ui-dist" "$ui"
cp "$release/quant-massive-daily.service" /home/davidou/.config/systemd/user/quant-massive-daily.service
cp "$release/quant-massive-daily.timer" /home/davidou/.config/systemd/user/quant-massive-daily.timer
systemctl --user daemon-reload
systemctl --user start quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
/home/davidou/quant/venv/bin/python -m quant_data.massive_publish --data-root /home/davidou/quant/data --security-master /home/davidou/quant/data/metadata/security_master.parquet --credential-file /home/davidou/massivekey --date 2026-09-17 --snapshot reference/massive-daily-v1/20260918T063308Z-93cf56c9fbf44b799fb96be8053059b4
/home/davidou/quant/venv/bin/python -m quant_data.daily_quote_browser --data-root /home/davidou/quant/data
/home/davidou/quant/venv/bin/python -m quant_data.sync_coverage --data-root /home/davidou/quant/data --security-master /home/davidou/quant/data/metadata/security_master.parquet
systemctl --user enable --now quant-massive-daily.timer
success=1
