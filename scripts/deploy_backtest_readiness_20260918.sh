#!/usr/bin/env bash
set -euo pipefail
release=/home/davidou/quant/releases/backtest-readiness-20260918-01
live=/home/davidou/quant/app/src/quant_data
backup=/home/davidou/quant/backups/backtest-readiness-20260918-01
test ! -e "$backup"
mkdir "$backup"
for name in api backtest backtest_reports; do cp "$live/$name.py" "$backup/$name.py"; done
cp -a /home/davidou/quant/app/ui-dist "$backup/ui-dist"
for name in api backtest backtest_reports backtest_readiness; do /home/davidou/quant/venv/bin/python -m py_compile "$release/$name.py"; done
success=0
finish() {
  result=$?
  trap - EXIT
  if [ "$success" -ne 1 ]; then
    for name in api backtest backtest_reports; do cp "$backup/$name.py" "$live/.$name.restore"; mv "$live/.$name.restore" "$live/$name.py"; done
    if [ -d /home/davidou/quant/app/ui-dist.readiness-previous ]; then
      mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.readiness-failed
      mv /home/davidou/quant/app/ui-dist.readiness-previous /home/davidou/quant/app/ui-dist
    fi
    systemctl --user restart quant-workbench-api.service || true
  fi
  exit "$result"
}
trap finish EXIT
for name in api backtest backtest_reports backtest_readiness; do cp "$release/$name.py" "$live/.$name.new"; mv "$live/.$name.new" "$live/$name.py"; done
cp -a "$release/ui-dist" /home/davidou/quant/app/ui-dist.readiness-new
mv /home/davidou/quant/app/ui-dist /home/davidou/quant/app/ui-dist.readiness-previous
mv /home/davidou/quant/app/ui-dist.readiness-new /home/davidou/quant/app/ui-dist
systemctl --user restart quant-workbench-api.service
curl --fail --silent --retry 5 --retry-delay 1 --retry-connrefused http://127.0.0.1:8511/api/v1/health
/home/davidou/quant/venv/bin/python -c 'import urllib.request,json; base="http://127.0.0.1:8511/api/v1/backtests/"; r=json.load(urllib.request.urlopen(base+"data-readiness")); assert r["available"] and len(r["checks"])==7 and not r["qualified"]; a=json.load(urllib.request.urlopen(base+"availability")); assert not a["formal_backtest_available"]; print("readiness and fail-closed gate passed")'
success=1
