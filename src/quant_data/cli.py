from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import pandas as pd

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

from .listings import (
    fetch_alpha_vantage_listing,
    merge_security_master_frames,
    merge_listing_files,
    normalize_nasdaq_trader_files,
)
from .pipeline import (
    BenchmarkDownloadConfig,
    DownloadConfig,
    eligible_symbols,
    failed_symbols_for_provider,
    make_alpaca_downloader,
    run_download,
    run_benchmark_download,
    select_universe,
    state_directory,
)
from .cleaning import run_structural_clean
from .quality_report import build_quality_report
from .constituents import (
    SOURCE_REPOSITORY,
    fetch_and_import_constituents,
    import_constituent_snapshots,
)
from .daily_quote_browser import build_us_daily_browser_catalogue


def _date(value: str) -> date:
    return date.fromisoformat(value)


def _write_master(frame: pd.DataFrame, output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(output, index=False)
    frame.to_csv(output.with_suffix(".csv"), index=False)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="quant-data")
    subparsers = parser.add_subparsers(dest="command", required=True)

    listing = subparsers.add_parser("listing", help="Build the security master")
    listing_sub = listing.add_subparsers(dest="listing_command", required=True)
    listing_import = listing_sub.add_parser("import", help="Import LISTING_STATUS CSV files")
    listing_import.add_argument("inputs", nargs="+", type=Path)
    listing_import.add_argument("--as-of", default="")
    listing_import.add_argument(
        "--existing", type=Path, help="Merge with an existing security-master Parquet"
    )
    listing_import.add_argument("--output", type=Path, default=Path("data/metadata/security_master.parquet"))

    listing_fetch = listing_sub.add_parser("fetch", help="Fetch Alpha Vantage LISTING_STATUS")
    listing_fetch.add_argument("--state", choices=["active", "delisted"], required=True)
    listing_fetch.add_argument("--as-of", default="")
    listing_fetch.add_argument("--api-key-env", default="ALPHAVANTAGE_API_KEY")
    listing_fetch.add_argument("--output", type=Path, required=True)

    nasdaq_import = listing_sub.add_parser(
        "import-nasdaq-dir", help="Import Nasdaq Trader pipe-delimited symbol files"
    )
    nasdaq_import.add_argument("--nasdaq-listed", type=Path, required=True)
    nasdaq_import.add_argument("--other-listed", type=Path, required=True)
    nasdaq_import.add_argument("--as-of", required=True)
    nasdaq_import.add_argument(
        "--output", type=Path, default=Path("data/metadata/security_master.parquet")
    )

    consolidate = listing_sub.add_parser(
        "consolidate", help="Consolidate an existing cross-source security master"
    )
    consolidate.add_argument("--input", type=Path, required=True)
    consolidate.add_argument("--output", type=Path, required=True)

    download = subparsers.add_parser("download", help="Download daily bars with yfinance")
    download.add_argument("--config", type=Path, default=Path("config/pipeline.toml"))
    download.add_argument("--security-master", type=Path)
    download.add_argument("--data-root", type=Path)
    download.add_argument("--provider", choices=["yfinance", "nasdaq", "alpaca"])
    download.add_argument("--feed", choices=["iex", "sip"], help="Alpaca stock data feed")
    download.add_argument(
        "--alpaca-credential-file", type=Path,
        help="Alpaca credentials file; its path and contents are never written to manifests",
    )
    download.add_argument(
        "--retry-failures-from", choices=["yfinance", "nasdaq", "alpaca"],
        help="Only download symbols whose latest record for this provider failed",
    )
    download.add_argument(
        "--status", choices=["active", "delisted"], action="append",
        help="Filter listing status; repeat to include both",
    )
    download.add_argument(
        "--asset-type", choices=["Stock", "ETF"], action="append",
        help="Filter asset type; repeat to include both",
    )
    download.add_argument(
        "--state-namespace",
        help="Isolate manifests and output files for this downloader instance",
    )
    download.add_argument(
        "--retry-failures-state-namespace",
        help="State namespace to read failures from; omitted means the legacy/default manifest root",
    )
    download.add_argument("--start")
    download.add_argument("--end")
    download.add_argument("--batch-size", type=int)
    download.add_argument("--retries", type=int)
    download.add_argument("--limit", type=int)
    download.add_argument("--force", action="store_true")

    benchmark = subparsers.add_parser(
        "benchmark", help="Download explicitly requested tradeable ETF benchmark daily bars"
    )
    benchmark_sub = benchmark.add_subparsers(dest="benchmark_command", required=True)
    benchmark_download = benchmark_sub.add_parser(
        "download", help="Download SPY and/or QQQ into the isolated benchmarks namespace"
    )
    benchmark_download.add_argument("--data-root", type=Path, required=True)
    benchmark_download.add_argument("--symbols", nargs="+", required=True, type=str.upper)
    benchmark_download.add_argument("--start", required=True)
    benchmark_download.add_argument("--end", required=True)
    benchmark_download.add_argument("--provider", choices=["alpaca", "nasdaq"], default="alpaca")
    benchmark_download.add_argument("--feed", choices=["iex", "sip"], default="iex")
    benchmark_download.add_argument(
        "--alpaca-credential-file", type=Path,
        help="Optional credential file; neither its path nor contents are written to manifests",
    )
    benchmark_download.add_argument("--retries", type=int, default=3)
    benchmark_download.add_argument("--request-delay-seconds", type=float, default=0.5)
    benchmark_download.add_argument("--force", action="store_true")

    clean = subparsers.add_parser("clean", help="Read-only structural cleaning of raw daily-bar Parquet")
    clean.add_argument("--raw-root", type=Path, default=Path("data/bars/daily"))
    clean.add_argument("--output", type=Path, help="Versioned derived output root (never raw input)")
    clean.add_argument("--audit-output", type=Path, help="Separate directory for quality reports")
    clean.add_argument("--dry-run", action="store_true", help="Scan only; no derived Parquet is written")

    quality = subparsers.add_parser("quality-report", help="Build a versioned read-only daily-bar quality report")
    quality.add_argument("--audit-root", type=Path, required=True)
    quality.add_argument("--derived-root", type=Path, required=True)
    quality.add_argument("--security-master", type=Path, required=True)
    quality.add_argument("--report-output", type=Path, required=True)
    quality.add_argument("--manifest", type=Path, action="append", default=[], help="Download manifest; repeat for isolated namespaces")

    browser_index = subparsers.add_parser("daily-browser-index", help="Build the fixed read-only US daily browser catalogue")
    browser_index.add_argument("--audit-root", type=Path, required=True, help="Existing structural audit root containing coverage.parquet")
    browser_index.add_argument("--output", type=Path, required=True, help="New catalogue JSON; never raw or derived bars")

    constituents = subparsers.add_parser(
        "constituents", help="Import an auditable historical S&P 500 / Nasdaq-100 reference"
    )
    constituent_sub = constituents.add_subparsers(dest="constituent_command", required=True)
    constituent_fetch = constituent_sub.add_parser(
        "fetch", help="Fetch commit-pinned community snapshot CSVs and import a new reference version"
    )
    constituent_fetch.add_argument("--data-root", type=Path, required=True)
    constituent_fetch.add_argument(
        "--revision", default="main",
        help="Git ref to resolve once; the output records the resulting immutable commit SHA",
    )
    constituent_import = constituent_sub.add_parser(
        "import", help="Import previously downloaded commit-pinned snapshot CSVs without network access"
    )
    constituent_import.add_argument("--data-root", type=Path, required=True)
    constituent_import.add_argument("--sp500-csv", type=Path, required=True)
    constituent_import.add_argument("--nasdaq100-csv", type=Path, required=True)
    constituent_import.add_argument("--source-revision", required=True)
    constituent_import.add_argument("--source-repository", default=SOURCE_REPOSITORY)
    return parser


def _load_config(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Config not found: {path}; copy config/pipeline.example.toml first")
    with path.open("rb") as handle:
        return tomllib.load(handle)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "constituents" and args.constituent_command == "fetch":
        result = fetch_and_import_constituents(data_root=args.data_root, revision=args.revision)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "constituents" and args.constituent_command == "import":
        result = import_constituent_snapshots(
            data_root=args.data_root, sp500_csv=args.sp500_csv, nasdaq100_csv=args.nasdaq100_csv,
            source_revision=args.source_revision, source_repository=args.source_repository,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "benchmark" and args.benchmark_command == "download":
        config = BenchmarkDownloadConfig(
            data_root=args.data_root, start=_date(args.start), end=_date(args.end),
            provider=args.provider, feed=args.feed, retries=args.retries,
            request_delay_seconds=args.request_delay_seconds, force=args.force,
        )
        # Credentials are only needed for Alpaca.  The config/manifest only sees
        # non-sensitive operational parameters; Nasdaq uses its public endpoint.
        downloader = None
        if args.provider == "alpaca":
            downloader = make_alpaca_downloader(
                feed=args.feed, credential_file=args.alpaca_credential_file
            )
        totals = run_benchmark_download(args.symbols, config, downloader=downloader)
        print(json.dumps(totals, ensure_ascii=False))
        return 1 if totals["failed"] else 0
    if args.command == "clean":
        summary = run_structural_clean(
            args.raw_root, output_root=args.output, audit_root=args.audit_output,
            dry_run=args.dry_run or args.output is None,
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 0
    if args.command == "quality-report":
        result = build_quality_report(
            audit_root=args.audit_root, derived_root=args.derived_root,
            security_master=args.security_master, report_root=args.report_output, manifests=args.manifest,
        )
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "daily-browser-index":
        result = build_us_daily_browser_catalogue(audit_root=args.audit_root, output=args.output)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    if args.command == "listing" and args.listing_command == "import":
        master = merge_listing_files(args.inputs, source_as_of=args.as_of)
        if args.existing:
            existing = pd.read_parquet(args.existing)
            master = merge_security_master_frames([existing, master])
        _write_master(master, args.output)
        print(json.dumps({"rows": len(master), "output": str(args.output)}))
        return 0
    if args.command == "listing" and args.listing_command == "fetch":
        master = fetch_alpha_vantage_listing(
            state=args.state, api_key_env=args.api_key_env, as_of=args.as_of
        )
        _write_master(master, args.output)
        print(json.dumps({"rows": len(master), "output": str(args.output)}))
        return 0
    if args.command == "listing" and args.listing_command == "import-nasdaq-dir":
        master = normalize_nasdaq_trader_files(
            args.nasdaq_listed, args.other_listed, source_as_of=args.as_of
        )
        _write_master(master, args.output)
        print(json.dumps({"rows": len(master), "output": str(args.output)}))
        return 0
    if args.command == "listing" and args.listing_command == "consolidate":
        original = pd.read_parquet(args.input)
        master = merge_security_master_frames([original])
        _write_master(master, args.output)
        print(
            json.dumps(
                {"input_rows": len(original), "output_rows": len(master), "output": str(args.output)}
            )
        )
        return 0

    raw = _load_config(args.config)
    security_master_path = args.security_master or Path(raw["security_master"])
    data_root = args.data_root or Path(raw.get("data_root", "data"))
    start = _date(args.start or raw.get("start", "2016-01-01"))
    end_value = args.end or raw.get("end", "")
    end = _date(end_value) if end_value else date.today()
    provider = args.provider or raw.get("provider", "yfinance")
    feed = args.feed or (raw.get("alpaca_feed", "iex") if provider == "alpaca" else "")
    config = DownloadConfig(
        data_root=data_root,
        start=start,
        end=end,
        provider=provider,
        feed=feed,
        state_namespace=args.state_namespace or raw.get("state_namespace", ""),
        batch_size=args.batch_size or int(raw.get("batch_size", 100)),
        retries=args.retries if args.retries is not None else int(raw.get("retries", 3)),
        retry_base_seconds=float(raw.get("retry_base_seconds", 2.0)),
        request_delay_seconds=float(raw.get("request_delay_seconds", 0.5)),
        force=args.force,
    )
    master = pd.read_parquet(security_master_path)
    retry_symbols = None
    if args.retry_failures_from:
        retry_state = state_directory(
            data_root, args.retry_failures_state_namespace or ""
        )
        retry_symbols = set(
            failed_symbols_for_provider(retry_state / "download.jsonl", args.retry_failures_from)
        )
    universe_records, symbols, universe_audit = select_universe(
        master, start, end, statuses=args.status, asset_types=args.asset_type,
        restrict_symbols=retry_symbols, limit=args.limit,
    )
    universe_audit["retry_failures_from"] = args.retry_failures_from
    universe_audit["retry_failures_state_namespace"] = (
        args.retry_failures_state_namespace or "default"
    )
    downloader = None
    if provider == "alpaca":
        downloader = make_alpaca_downloader(
            feed=feed, credential_file=args.alpaca_credential_file
        )
    totals = run_download(
        symbols, config, downloader=downloader,
        universe_records=universe_records, universe_audit=universe_audit,
    )
    print(json.dumps(totals, ensure_ascii=False))
    return 1 if totals["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
