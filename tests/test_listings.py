import json

import pandas as pd

from quant_data.listings import (
    merge_security_master_frames,
    normalize_listing_frame,
    normalize_nasdaq_trader_files,
)


def test_normalize_alpha_vantage_listing():
    source = pd.DataFrame(
        [{
            "symbol": "abc", "name": "ABC Inc", "exchange": "NYSE",
            "assetType": "Stock", "ipoDate": "2012-01-03",
            "delistingDate": "2020-02-04", "status": "Delisted",
        }]
    )
    result = normalize_listing_frame(source, "2020-02-04")
    assert result.loc[0, "symbol"] == "ABC"
    assert result.loc[0, "delisting_date"] == "2020-02-04"
    assert result.loc[0, "status"] == "delisted"


def test_normalize_nasdaq_trader_files(tmp_path):
    nasdaq = tmp_path / "nasdaqlisted.txt"
    nasdaq.write_text(
        "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n"
        "AAPL|Apple Inc.|Q|N|N|100|N|N\n"
        "ZZTEST|Test Security|Q|Y|N|100|N|N\n"
        "File Creation Time: 0831202618|||||||\n",
        encoding="utf-8",
    )
    other = tmp_path / "otherlisted.txt"
    other.write_text(
        "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n"
        "SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY\n"
        "File Creation Time: 0831202618|||||||\n",
        encoding="utf-8",
    )
    result = normalize_nasdaq_trader_files(nasdaq, other, "2026-08-31")
    assert result["symbol"].tolist() == ["AAPL", "SPY"]
    assert result.set_index("symbol").loc["SPY", "asset_type"] == "ETF"
    assert result.set_index("symbol").loc["SPY", "exchange"] == "NYSE ARCA"
    assert result.set_index("symbol").loc["AAPL", "raw_symbol"] == "AAPL"


def test_merge_active_cross_source_but_preserve_delisted_lifecycles():
    nasdaq_active = pd.DataFrame(
        [{
            "symbol": "ABC", "raw_symbol": "ABC", "name": "ABC Current, Inc.",
            "exchange": "NASDAQ", "asset_type": "Stock", "ipo_date": "",
            "delisting_date": "", "status": "active",
            "source": "nasdaq_trader_symbol_directory", "source_as_of": "2026-08-31",
        }]
    )
    alpha = pd.DataFrame(
        [
            {
                "symbol": "ABC", "raw_symbol": "ABC", "name": "ABC Current Inc",
                "exchange": "NASDAQ", "asset_type": "Stock", "ipo_date": "2022-03-04",
                "delisting_date": "", "status": "active",
                "source": "alpha_vantage_listing_status", "source_as_of": "2026-08-31",
            },
            {
                "symbol": "ABC", "raw_symbol": "ABC", "name": "ABC Old Corp",
                "exchange": "NYSE", "asset_type": "Stock", "ipo_date": "2010-01-01",
                "delisting_date": "2018-06-01", "status": "delisted",
                "source": "alpha_vantage_listing_status", "source_as_of": "2026-08-31",
            },
            {
                "symbol": "ABC", "raw_symbol": "ABC", "name": "ABC Older Corp",
                "exchange": "NYSE", "asset_type": "Stock", "ipo_date": "2000-01-01",
                "delisting_date": "2008-05-01", "status": "delisted",
                "source": "alpha_vantage_listing_status", "source_as_of": "2026-08-31",
            },
        ]
    )
    result = merge_security_master_frames([nasdaq_active, alpha])
    assert len(result) == 3
    active = result[result["status"] == "active"].iloc[0]
    assert active["name"] == "ABC Current, Inc."
    assert active["ipo_date"] == "2022-03-04"
    assert json.loads(active["sources"]) == [
        "alpha_vantage_listing_status", "nasdaq_trader_symbol_directory"
    ]
    assert len(json.loads(active["provenance"])) == 2
    assert sorted(result[result["status"] == "delisted"]["delisting_date"]) == [
        "2008-05-01", "2018-06-01"
    ]


def test_reimport_of_merged_active_record_is_idempotent():
    rows = pd.DataFrame(
        [
            {"symbol": "A", "status": "active", "source": "nasdaq_trader_symbol_directory"},
            {"symbol": "A", "status": "active", "source": "alpha_vantage_listing_status"},
        ]
    )
    first = merge_security_master_frames([rows])
    second = merge_security_master_frames([first, rows.iloc[[1]]])
    assert len(second) == 1
    assert json.loads(second.loc[0, "sources"]) == [
        "alpha_vantage_listing_status", "nasdaq_trader_symbol_directory"
    ]
    assert len(json.loads(second.loc[0, "provenance"])) == 2
