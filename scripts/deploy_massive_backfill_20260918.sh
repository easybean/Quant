#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/massive-backfill-20260918-01
live=/home/davidou/quant/app/src/quant_data
backup=/home/davidou/quant/backups/massive-backfill-20260918-01
test ! -e "$backup"
mkdir "$backup"
cp "$live/massive_publish.py" "$backup/massive_publish.py"
cp "$live/massive_probe.py" "$backup/massive_probe.py"
cp "$live/massive_rate.py" "$backup/massive_rate.py"
cp "$live/sync_status.py" "$backup/sync_status.py"
/home/davidou/quant/venv/bin/python -m py_compile "$release/massive_publish.py" "$release/massive_backfill.py" "$release/massive_probe.py" "$release/massive_rate.py" "$release/sync_status.py"
systemd-analyze --user verify "$release/quant-massive-backfill.service" "$release/quant-massive-backfill.timer" "$release/quant-massive-code-audit.service"
success=0
resume() {
    result=$?
    trap - EXIT
    if [ "$success" -ne 1 ]; then
        systemctl --user disable --now quant-massive-backfill.timer 2>/dev/null || true
        systemctl --user stop quant-massive-backfill.service 2>/dev/null || true
        for name in massive_publish massive_probe massive_rate sync_status; do
            cp "$backup/$name.py" "$live/.$name.py.restore"
            mv "$live/.$name.py.restore" "$live/$name.py"
        done
        systemctl --user restart quant-workbench-api.service || true
    fi
    systemctl --user start quant-massive-code-audit.timer || true
    systemctl --user start --no-block quant-massive-code-audit.service || true
    exit "$result"
}
trap resume EXIT
systemctl --user stop quant-massive-code-audit.timer
systemctl --user stop quant-massive-code-audit.service
for name in massive_publish massive_backfill massive_probe massive_rate sync_status; do
    cp "$release/$name.py" "$live/.$name.py.new"
    mv "$live/.$name.py.new" "$live/$name.py"
done
cp "$release/quant-massive-backfill.service" /home/davidou/.config/systemd/user/quant-massive-backfill.service
cp "$release/quant-massive-backfill.timer" /home/davidou/.config/systemd/user/quant-massive-backfill.timer
cp "$release/quant-massive-code-audit.service" /home/davidou/.config/systemd/user/quant-massive-code-audit.service
systemctl --user daemon-reload
systemctl --user restart quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
systemctl --user start quant-massive-backfill.service
systemctl --user enable --now quant-massive-backfill.timer
systemctl --user show quant-massive-backfill.service -p Result -p ExecMainStatus
success=1
