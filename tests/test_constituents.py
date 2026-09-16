from pathlib import Path

import pandas as pd

from quant_data.cli import main
from quant_data.constituents import import_constituent_snapshots, sha256_file


REVISION = "a" * 40


def _write_snapshots(path: Path, rows: list[str]) -> None:
    path.write_text("date,tickers\n" + "\n".join(rows) + "\n", encoding="utf-8")


def test_import_preserves_source_csv_and_builds_half_open_intervals(tmp_path):
    sp500 = tmp_path / "sp500.csv"
    nasdaq100 = tmp_path / "nasdaq100.csv"
    _write_snapshots(sp500, [
        '2020-01-02,"AAPL,MSFT"',
        '2020-02-03,"MSFT,NVDA"',
    ])
    _write_snapshots(nasdaq100, [
        '2020-01-02,"AAPL,MSFT"',
        '2020-03-02,"AAPL,TSLA"',
    ])
    result = import_constituent_snapshots(
        data_root=tmp_path / "data", sp500_csv=sp500, nasdaq100_csv=nasdaq100,
        source_revision=REVISION,
    )
    root = tmp_path / "data" / "reference" / "constituents-v1"
    assert result["interval_rows"] == 6
    raw_sp500 = root / "raw" / "sp500_components_history.csv"
    assert raw_sp500.read_bytes() == sp500.read_bytes()
    frame = pd.read_parquet(root / "membership_intervals.parquet")
    aapl = frame[(frame.index_id == "SP500") & (frame.symbol == "AAPL")].iloc[0]
    assert aapl.effective_date == pd.Timestamp("2020-01-02")
    assert aapl.end_date_exclusive == pd.Timestamp("2020-02-03")
    msft = frame[(frame.index_id == "SP500") & (frame.symbol == "MSFT")].iloc[0]
    assert pd.isna(msft.end_date_exclusive)
    manifest = (root / "source-manifest.json").read_text(encoding="utf-8")
    assert REVISION in manifest
    assert sha256_file(raw_sp500) in manifest
    assert "community_reconstructed" in manifest


def test_cli_import_never_overwrites_existing_reference(tmp_path):
    sp500 = tmp_path / "sp500.csv"
    nasdaq100 = tmp_path / "nasdaq100.csv"
    _write_snapshots(sp500, ['2020-01-02,"AAPL"'])
    _write_snapshots(nasdaq100, ['2020-01-02,"MSFT"'])
    arguments = [
        "constituents", "import", "--data-root", str(tmp_path / "data"),
        "--sp500-csv", str(sp500), "--nasdaq100-csv", str(nasdaq100),
        "--source-revision", REVISION,
    ]
    assert main(arguments) == 0
    try:
        main(arguments)
    except FileExistsError:
        pass
    else:
        raise AssertionError("the reference version must not be overwritten")


def test_rejects_duplicate_or_out_of_order_effective_dates(tmp_path):
    sp500 = tmp_path / "sp500.csv"
    nasdaq100 = tmp_path / "nasdaq100.csv"
    _write_snapshots(sp500, ['2020-01-02,"AAPL"', '2020-01-02,"MSFT"'])
    _write_snapshots(nasdaq100, ['2020-01-02,"MSFT"'])
    try:
        import_constituent_snapshots(
            data_root=tmp_path / "data", sp500_csv=sp500, nasdaq100_csv=nasdaq100,
            source_revision=REVISION,
        )
    except ValueError as exc:
        assert "strictly increasing" in str(exc)
    else:
        raise AssertionError("duplicate effective dates must be rejected")
