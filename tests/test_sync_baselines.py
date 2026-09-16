import json

import pandas as pd

from quant_data.sync_baselines import build_sync_baselines


def test_inventory_uses_actual_dates_without_mixing_or_using_yahoo(tmp_path):
    root = tmp_path / "data"
    for relative, days, source in [
        ("symbol=TSLA-2ff6985a", ["2026-08-28", "2026-08-31"], "nasdaq_web_unadjusted"),
        ("provider=alpaca/namespace=retry/symbol=TSLA-2ff6985a", ["2026-08-28"], "alpaca"),
        ("provider=yfinance/namespace=yahoo-daily-v1/symbol=TSLA-2ff6985a", ["2026-09-15"], "yfinance"),
        ("provider=synthetic/symbol=FAKE-aabbccdd", ["2026-09-15"], "synthetic"),
    ]:
        path = root / "bars/daily" / relative / "bars.parquet"
        path.parent.mkdir(parents=True)
        pd.DataFrame({"date": days, "source": source}).to_parquet(path, index=False)
    bad = root / "bars/daily/symbol=BAD-aabbccdd/bars.parquet"
    bad.parent.mkdir(parents=True)
    bad.write_bytes(b"bad parquet")
    output = root / "manifests/yahoo-daily-v1/baselines.json"
    result = build_sync_baselines(root, output)
    payload = json.loads(output.read_text())
    assert result == {"symbols": 1, "rejected": 1}
    assert payload["symbols"]["TSLA"]["last_date"] == "2026-08-31"
    assert payload["symbols"]["TSLA"]["source"] == ["nasdaq_web_unadjusted"]
    assert "FAKE" not in payload["symbols"]
