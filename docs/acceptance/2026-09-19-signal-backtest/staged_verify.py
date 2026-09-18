"""Load the new runner in isolation against installed server dependencies."""
import importlib.util
from pathlib import Path
import sys

path = Path("/home/davidou/quant/releases/signal-backtest-20260919-01/signal_backtest.py")
spec = importlib.util.spec_from_file_location("quant_data.staged_signal_backtest", path)
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
fixture = module.public_availability()
for template, parameters in [("buy_and_hold", {"symbol": "ACME", "start_date": "2024-01-02"}), ("dual_moving_average", {"symbol": "ACME", "fast_window": 2, "slow_window": 3})]:
    strategy = {"template": template, "parameters": parameters, "data_requirements": {"dataset_version": fixture["dataset_version"], "universe_version": fixture["asset_pool_version"], "calendar_version": fixture["calendar_version"], "price_basis": "raw"}}
    report = module.run_from_job(fixture["parameters"], strategy, fixture["dataset_version"])
    assert report["ledger"]["reconciled"]
    assert report["equity_curve"][0]["nav"] == "1"
    assert report["signals"][-1]["order_status"] == "not_submitted_no_next_session"
    if template == "dual_moving_average": assert report["ledger"]["total_pnl"] == "-42"
    print(template, report["ledger"]["total_pnl"], "isolated dependency and reconciliation passed")
