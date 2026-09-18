#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/signal-backtest-20260919-01
live=/home/davidou/quant/app/src/quant_data
backup=/home/davidou/quant/backups/signal-backtest-20260919-01
test ! -e "$backup"
mkdir "$backup"
for name in jobs backtest backtest_reports; do cp "$live/$name.py" "$backup/$name.py"; done
cp -a /home/davidou/quant/app/ui-dist "$backup/ui-dist"
for name in jobs backtest backtest_reports signal_backtest; do /home/davidou/quant/venv/bin/python -m py_compile "$release/$name.py"; done
success=0
finish() {
  result=$?
  trap - EXIT
  if [ "$success" -ne 1 ]; then
    for name in jobs backtest backtest_reports; do cp "$backup/$name.py" "$live/.$name.restore"; mv "$live/.$name.restore" "$live/$name.py"; done
    if [ -d /home/davidou/quant/app/ui-dist.signal-previous ]; then
      mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.signal-failed
      mv /home/davidou/quant/app/ui-dist.signal-previous /home/davidou/quant/app/ui-dist
    fi
    systemctl --user restart quant-workbench-api.service || true
  fi
  exit "$result"
}
trap finish EXIT
for name in jobs backtest backtest_reports signal_backtest; do cp "$release/$name.py" "$live/.$name.new"; mv "$live/.$name.new" "$live/$name.py"; done
cp -a "$release/ui-dist" /home/davidou/quant/app/ui-dist.signal-new
mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.signal-previous
mv /home/davidou/quant/app/ui-dist.signal-new /home/davidou/quant/app/ui-dist
systemctl --user restart quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
/home/davidou/quant/venv/bin/python -c 'import urllib.request,json; a=json.load(urllib.request.urlopen("http://127.0.0.1:8511/api/v1/backtests/availability")); assert not a["formal_backtest_available"] and a["signal_acceptance"]["available"]; print("signal availability and formal gate passed")'
success=1
