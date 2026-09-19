"""Run staged offline TSLA replay against installed server dependencies."""
from importlib.util import module_from_spec, spec_from_file_location
import json
from pathlib import Path
import sys

release = Path("/home/davidou/quant/releases/exploratory-tsla-20260919-01")
root = Path("/home/davidou/quant/data")
snapshot = root / "reference/exploratory-backtest/tsla-2026-jan-aug-v1"
output = root / "reference/exploratory-backtest/tsla-2026-sma10-30-report-v1"

spec = spec_from_file_location("quant_data.exploratory_backtest", release / "exploratory_backtest.py")
runner = module_from_spec(spec)
sys.modules[spec.name] = runner
spec.loader.exec_module(runner)
wrapper_spec = spec_from_file_location("exploratory_tsla_publish", release / "run_exploratory_tsla_2026.py")
wrapper = module_from_spec(wrapper_spec)
sys.modules[wrapper_spec.name] = wrapper
wrapper_spec.loader.exec_module(wrapper)
result = wrapper.publish(snapshot, output, root)
print(json.dumps(result, ensure_ascii=False))
