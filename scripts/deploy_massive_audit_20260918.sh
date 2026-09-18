#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/massive-audit-20260918-01
live=/home/davidou/quant/app/src/quant_data
backup=/home/davidou/quant/backups/massive-audit-20260918-01
test ! -e "$backup"
mkdir "$backup"
cp "$live/daily_quote_browser.py" "$backup/daily_quote_browser.py"
cp "$live/massive_daily.py" "$backup/massive_daily.py"
cp /home/davidou/.config/systemd/user/quant-massive-daily.service "$backup/quant-massive-daily.service"
/home/davidou/quant/venv/bin/python -m py_compile "$release/massive_probe.py" "$release/daily_quote_browser.py" "$release/massive_daily.py" "$release/massive_rate.py"
systemd-analyze --user verify "$release/quant-massive-code-audit.service" "$release/quant-massive-code-audit.timer" "$release/quant-massive-daily.service"
cp "$release/massive_probe.py" "$live/.massive_probe.py.new"
mv "$live/.massive_probe.py.new" "$live/massive_probe.py"
cp "$release/massive_rate.py" "$live/.massive_rate.py.new"
mv "$live/.massive_rate.py.new" "$live/massive_rate.py"
cp "$release/massive_daily.py" "$live/.massive_daily.py.new"
mv "$live/.massive_daily.py.new" "$live/massive_daily.py"
cp "$release/daily_quote_browser.py" "$live/.daily_quote_browser.py.new"
mv "$live/.daily_quote_browser.py.new" "$live/daily_quote_browser.py"
cp "$release/quant-massive-daily.service" /home/davidou/.config/systemd/user/quant-massive-daily.service
cp "$release/quant-massive-code-audit.service" /home/davidou/.config/systemd/user/quant-massive-code-audit.service
cp "$release/quant-massive-code-audit.timer" /home/davidou/.config/systemd/user/quant-massive-code-audit.timer
systemctl --user daemon-reload
systemctl --user restart quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=http://127.0.0.1:7890 /home/davidou/quant/venv/bin/python -m quant_data.massive_probe --data-root /home/davidou/quant/data --security-master /home/davidou/quant/data/metadata/security_master.parquet --credential-file /home/davidou/massivekey --date 2026-09-17 --limit 3
systemctl --user enable --now quant-massive-code-audit.timer
systemctl --user start --no-block quant-massive-code-audit.service
