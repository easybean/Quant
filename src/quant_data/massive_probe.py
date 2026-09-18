"""Durable per-code Massive coverage evidence, not a research qualification."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pandas as pd
import requests

from .massive_daily import _atomic_json, _load_api_key, _row_to_bar
from .sync_eligibility import select_sync_symbols
from .sync_scheduler import completed_session, resolve_sync_master


def assess(payload: object, symbol: str, start: date, end: date, observed: str) -> dict:
    if not isinstance(payload, dict) or payload.get("status") not in {"OK", "DELAYED"}:
        return {"status": "response_invalid"}
    if payload.get("ticker") != symbol or payload.get("adjusted") is not False:
        return {"status": "identity_or_adjustment_invalid"}
    rows = payload.get("results", [])
    count = payload.get("resultsCount", 0)
    if not isinstance(rows, list) or isinstance(count, bool) or not isinstance(count, int) or count != len(rows):
        return {"status": "response_invalid"}
    dates = []; valid = []
    for row in rows:
        try:
            day = datetime.fromtimestamp(row["t"] / 1000, tz=timezone.utc).astimezone(ZoneInfo("America/New_York")).date()
            bar, error = _row_to_bar({**row, "T": symbol}, day, observed)
        except (ValueError, TypeError, KeyError, OverflowError, OSError):
            return {"status": "bars_invalid"}
        if error or not start <= day <= end or day in dates:
            return {"status": "bars_invalid"}
        dates.append(day); valid.append(bar)
    if payload.get("next_url"):
        return {"status": "pagination_required"}
    return {"status": "returned" if dates else "empty_unknown", "rows": len(dates),
            "last_date": max(dates).isoformat() if dates else None,
            "target_returned": end in dates}


def run(root: Path, master: Path, credential: Path, *, limit: int = 60, target: date | None = None) -> dict:
    if limit < 1 or limit > 100:
        raise ValueError("limit_must_be_1_to_100")
    completed = completed_session(datetime.now(timezone.utc))
    target = target or completed
    if target > completed:
        raise ValueError("target_after_completed_session")
    folder = root / "manifests/massive-code-audit-v1" / target.isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    with (root / "manifests/massive-network.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "skipped", "reason": "network_lock_held"}
        master = resolve_sync_master(root, master)
        symbols = select_sync_symbols(pd.read_parquet(master))
        master_sha = hashlib.sha256(master.read_bytes()).hexdigest()
        records = folder / "records.jsonl"
        done = {}
        recent = []
        if records.exists():
            for line in records.read_text().splitlines():
                row = json.loads(line)
                if row.get("http_status") is not None or row.get("status") == "network_failed":
                    recent.append(row["status"])
                if row.get("target_date") == target.isoformat() and row.get("status") not in {"http_401", "http_403", "http_429", "network_failed", "http_500", "http_502", "http_503", "http_504"}:
                    done[row["symbol"]] = row
        retryable = {"http_401", "http_403", "http_429", "network_failed", "http_500", "http_502", "http_503", "http_504"}
        if len(recent) >= 3 and len(set(recent[-3:])) == 1 and recent[-1] in retryable:
            summary = summarize(symbols, done, target, master_sha)
            summary.update({"status": "blocked", "last_error_code": recent[-1], "reason": "three_consecutive_source_failures"})
            _atomic_json(folder / "latest.json", summary)
            _atomic_json(root / "manifests/massive-code-audit-v1/latest.json", summary)
            return summary
        # Validate every code in the real grouped response first; one HTTP
        # request really tests all returned codes, without 15k redundant GETs.
        pointer = root / "manifests/massive-daily-v1/published" / f"{target}.json"
        if not pointer.exists():
            return {"status": "blocked", "reason": "target_grouped_snapshot_missing"}
        publication = json.loads(pointer.read_text())
        from .massive_publish import _load_snapshot
        _, frame = _load_snapshot(root, root / publication["snapshot_relative_path"], target)
        grouped = set(frame.symbol)
        aliases, mapping_sha = {}, None
        if (root / "catalogue/current-massive-alias-report-v1.json").exists():
            from .massive_alias_publish import load_current_aliases
            aliases, mapping_sha = load_current_aliases(root, master_sha, target)
        start = target - timedelta(days=30)
        with records.open("a", encoding="utf-8") as output:
            def append(row):
                output.write(json.dumps(row, ensure_ascii=False) + "\n"); output.flush()
                done[row["symbol"]] = row
            for symbol in symbols:
                if symbol in grouped and symbol not in done:
                    append({"symbol": symbol, "status": "grouped_returned", "target_returned": True,
                            "master_sha256": master_sha, "target_date": target.isoformat(),
                            "snapshot_relative_path": publication["snapshot_relative_path"], "research_qualified": False})
                provider = aliases.get(symbol)
                if provider and provider in grouped and (symbol not in done or done[symbol].get("provider_symbol") != provider):
                    append({"symbol": symbol, "provider_symbol": provider, "status": "aliased_grouped_returned",
                            "target_returned": True, "mapping_report_sha256": mapping_sha,
                            "master_sha256": master_sha, "target_date": target.isoformat(),
                            "snapshot_relative_path": publication["snapshot_relative_path"], "research_qualified": False})
                elif provider and symbol in done and done[symbol].get("provider_symbol", symbol) != provider:
                    # Preserve old exact-query evidence, but test the now corroborated provider code.
                    del done[symbol]
            todo = [symbol for symbol in symbols if symbol not in done]
            evidence_path = root / "catalogue/current-listing-gap-evidence-v1.json"
            try:
                evidence = json.loads(evidence_path.read_text())
                present = {item["symbol"] for item in evidence.get("items", []) if item.get("listing_evidence") == "present_in_current_directory"}
            except (OSError, ValueError, TypeError, KeyError):
                present = set()
            # Prioritize currently corroborated listings, but keep every code.
            todo.sort(key=lambda symbol: (symbol not in present, symbol))
            summary = summarize(symbols, done, target, master_sha)
            _atomic_json(folder / "latest.json", summary)
            _atomic_json(root / "manifests/massive-code-audit-v1/latest.json", summary)
            if not todo:
                return summary
            key = _load_api_key(credential)
            attempted = 0
            for symbol in todo[:limit]:
                # Shared lock plus a persistent conservative 16 second gap;
                # and timer provide resume without any model wake-up.
                from .massive_rate import wait_for_slot
                wait_for_slot(root)
                observed = datetime.now(timezone.utc).isoformat()
                row = {"symbol": symbol, "master_sha256": master_sha, "target_date": target.isoformat(),
                       "requested_start": start.isoformat(), "observed_at": observed, "research_qualified": False}
                provider = aliases.get(symbol, symbol)
                row.update(provider_symbol=provider, mapping_report_sha256=mapping_sha if symbol in aliases else None)
                snapshot = root / "reference/massive-code-audit-v1" / uuid.uuid4().hex
                snapshot.mkdir(parents=True)
                endpoint = f"https://api.massive.com/v2/aggs/ticker/{quote(provider, safe='')}/range/1/day/{start}/{target}"
                row["request"] = {"url": endpoint, "adjusted": False, "method": "GET"}
                try:
                    response = requests.get(endpoint, params={"adjusted": "false", "sort": "asc", "limit": 50000},
                                            headers={"Authorization": f"Bearer {key}"}, timeout=30, allow_redirects=False)
                    observed = datetime.now(timezone.utc).isoformat()
                    row["observed_at"] = observed
                    raw = response.content
                    if len(raw) > 32 * 1024 * 1024:
                        row["status"] = "response_too_large"
                    else:
                        (snapshot / "response.json").write_bytes(raw)
                        row.update({"response_sha256": hashlib.sha256(raw).hexdigest(),
                                    "snapshot_relative_path": snapshot.relative_to(root).as_posix(), "http_status": response.status_code})
                        if response.status_code != 200:
                            row["status"] = f"http_{response.status_code}"
                        else:
                            row.update(assess(response.json(), provider, start, target, observed))
                except requests.RequestException:
                    row["status"] = "network_failed"
                except ValueError:
                    row["status"] = "response_invalid"
                _atomic_json(snapshot / "manifest.json", row)
                append(row); attempted += 1
                summary = summarize(symbols, done, target, master_sha)
                _atomic_json(folder / "latest.json", summary)
                _atomic_json(root / "manifests/massive-code-audit-v1/latest.json", summary)
                if row["status"] in {"http_429", "http_401", "http_403", "network_failed"}:
                    break
        summary = summarize(symbols, done, target, master_sha)
        summary["attempted_this_batch"] = attempted
        _atomic_json(folder / "latest.json", summary)
        _atomic_json(root / "manifests/massive-code-audit-v1/latest.json", summary)
        return summary


def summarize(symbols, done, target, master_sha):
    transient = {"http_401", "http_403", "http_429", "network_failed", "http_500", "http_502", "http_503", "http_504"}
    tested = {s: done[s] for s in symbols if s in done and done[s]["status"] not in transient}
    counts = {}
    for row in tested.values():
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return {"schema_version": "massive-code-audit-v1", "status": "complete" if len(tested) == len(symbols) else "partial",
            "target_date": target.isoformat(), "master_sha256": master_sha, "total": len(symbols),
            "tested": len(tested), "remaining": len(symbols) - len(tested), "counts": counts,
            "target_returned": sum(bool(r.get("target_returned")) for r in tested.values()),
            "research_qualified": False, "updated_at": datetime.now(timezone.utc).isoformat()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", required=True, type=Path)
    parser.add_argument("--security-master", required=True, type=Path)
    parser.add_argument("--credential-file", required=True, type=Path)
    parser.add_argument("--limit", type=int, default=60)
    parser.add_argument("--date", type=date.fromisoformat)
    args = parser.parse_args()
    print(json.dumps(run(args.data_root, args.security_master, args.credential_file, limit=args.limit, target=args.date)))
