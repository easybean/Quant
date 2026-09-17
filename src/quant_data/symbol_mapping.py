"""Auditable current-directory mappings for provider-specific US symbols.

This module is intentionally an acquisition aid, not an identity service.  A
mapping proves only that two *current* directory descriptions matched at the
time their immutable snapshots were observed.  It never asserts a historical
lifecycle and all consumers must retain ``research_qualified=false``.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re
import unicodedata
import uuid
from typing import Any

from .daily_sync import _atomic_json
from .listings import NASDAQ_TRADER_EXCHANGES
from .listing_reconciliation import validate_directory
from .asset_reference import ENDPOINT as ASSET_ENDPOINT


SCHEMA_VERSION = "alpaca-current-symbol-mapping-v1"
MAPPING_NAMESPACE = "alpaca-sip-symbol-mapping-recovery-v1"
CURRENT_MAPPING_WINDOW_DAYS = 31
_SYMBOL = re.compile(r"[A-Z0-9.$_-]{1,32}")
_PREFERRED_ACT = re.compile(r"^(?P<base>[A-Z0-9.-]+)\$(?P<series>[A-Z0-9]+)$")
_PREFERRED_CQS = re.compile(r"^(?P<base>[A-Z0-9.-]+)p(?P<series>[A-Z0-9]+)$")
_CORPORATE_SUFFIX = re.compile(r"(,\s*)(?:inc(?:orporated)?|corporation|corp|ltd|plc)\.?\b", re.IGNORECASE)
_LIMITED_SUFFIX = re.compile(r"(,\s*)limited\.?\s*(?=,|$)", re.IGNORECASE)


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _name_tokens(value: object) -> tuple[str, ...]:
    """Normalise punctuation/case and only enumerated legal entity suffixes.

    Numeric, class, series, coupon and security-description tokens are kept;
    unlike a fuzzy similarity score this is an exact equality predicate.
    """
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    # Legal-entity deletion is deliberately contextual.  In particular,
    # ``Limited Duration`` is a fund/security descriptor, not a suffix.
    text = _CORPORATE_SUFFIX.sub(r"\1", text)
    text = _LIMITED_SUFFIX.sub(r"\1", text)
    tokens = re.findall(r"[\w]+(?:\.\d+)?", text, flags=re.UNICODE)
    return tuple(tokens)


def _provider_exchange_matches(official: str, provider: object) -> bool:
    # Alpaca uses exchange identifiers which differ from the Nasdaq Trader
    # display labels for a small, explicitly enumerated set only.
    equivalents = {
        "NASDAQ": {"NASDAQ"}, "NYSE": {"NYSE"},
        "NYSE AMERICAN": {"AMEX", "NYSE AMERICAN"},
        "NYSE ARCA": {"ARCA", "NYSE ARCA"},
        "CBOE BZX": {"BATS", "CBOE BZX"}, "IEX": {"IEX"},
    }
    return str(provider).strip().upper() in equivalents.get(official, set())


def _listing_rows(snapshot: Path) -> tuple[list[dict[str, str]], dict[str, str], str]:
    files = (snapshot / "nasdaqlisted.txt", snapshot / "otherlisted.txt")
    if not all(path.is_file() for path in files):
        raise ValueError("listing_snapshot_files_missing")
    import csv
    rows: list[dict[str, str]] = []
    hashes = {path.name: _sha256(path) for path in files}
    try:
        evidence = json.loads((snapshot / "gap-evidence.json").read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("listing_snapshot_evidence_missing") from exc
    if evidence.get("schema_version") != "current-listing-gap-evidence-v1" or evidence.get("source_hashes") != hashes:
        raise ValueError("listing_snapshot_evidence_invalid")
    footers: dict[str, str] = {}
    for path in files:
        contents = path.read_bytes()
        validated_footer = validate_directory(contents, path.name)
        if evidence.get("source_footers", {}).get(path.name) != validated_footer:
            raise ValueError("listing_snapshot_evidence_invalid")
        footers[path.name] = validated_footer
        with path.open(encoding="utf-8-sig", newline="") as handle:
            raw = list(csv.DictReader(handle, delimiter="|"))
        if not raw:
            raise ValueError("listing_snapshot_empty")
        primary = "Symbol" if path.name == "nasdaqlisted.txt" else "ACT Symbol"
        for row in raw:
            symbol = str(row.get(primary, "")).strip().upper()
            if symbol.startswith("FILE CREATION TIME:"):
                continue
            if not _SYMBOL.fullmatch(symbol) or str(row.get("Test Issue", "")).strip().upper() != "N":
                continue
            exchange = "NASDAQ" if path.name == "nasdaqlisted.txt" else NASDAQ_TRADER_EXCHANGES.get(str(row.get("Exchange", "")).strip().upper(), "")
            if not exchange or not str(row.get("Security Name", "")).strip():
                continue
            aliases = {primary: symbol}
            if path.name == "otherlisted.txt":
                # CQS uses a meaningful lower-case preferred marker (``p``),
                # so retain raw spelling for structural validation below.
                aliases.update({"CQS Symbol": str(row.get("CQS Symbol", "")).strip(), "NASDAQ Symbol": str(row.get("NASDAQ Symbol", "")).strip().upper()})
            rows.append({"primary_symbol": symbol, "security_name": str(row["Security Name"]).strip(), "exchange": exchange,
                         "file": path.name, "aliases": aliases})
    if not rows:
        raise ValueError("listing_snapshot_no_usable_rows")
    return rows, hashes, "|".join(f"{name}:{footers[name]}" for name in sorted(footers))


def _preferred_candidates(row: dict[str, Any]) -> tuple[set[str], bool]:
    aliases = {str(value).upper() for value in row["aliases"].values() if value}
    result = set(aliases)
    act = str(row["aliases"].get("ACT Symbol", ""))
    cqs = str(row["aliases"].get("CQS Symbol", ""))
    act_match, cqs_match = _PREFERRED_ACT.fullmatch(act), _PREFERRED_CQS.fullmatch(cqs)
    # The special provider spelling is considered only when official current
    # symbologies themselves agree on the preferred-series structure.
    if act_match and cqs_match and act_match.group("base") == cqs_match.group("base") and act_match.group("series") == cqs_match.group("series"):
        result.add(f"{act_match.group('base')}.PR{act_match.group('series')}")
        return result, True
    return result, False


def _load_assets(snapshot: Path) -> tuple[list[dict[str, Any]], dict[str, str], str]:
    assets_path, manifest_path = snapshot / "assets.json", snapshot / "manifest.json"
    if not assets_path.is_file() or not manifest_path.is_file():
        raise ValueError("provider_snapshot_files_missing")
    try:
        assets, manifest = json.loads(assets_path.read_text()), json.loads(manifest_path.read_text())
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("provider_snapshot_invalid") from exc
    asset_hash = _sha256(assets_path)
    if (not isinstance(assets, list) or not isinstance(manifest, dict) or manifest.get("sha256") != asset_hash
            or manifest.get("schema_version") != "provider-asset-reference-v1" or manifest.get("source") != ASSET_ENDPOINT):
        raise ValueError("provider_snapshot_hash_invalid")
    observed = str(manifest.get("observed_at", ""))
    try:
        observed_at = datetime.fromisoformat(observed)
    except ValueError as exc:
        raise ValueError("provider_snapshot_observation_missing") from exc
    if observed_at.tzinfo is None or datetime.now(timezone.utc) - observed_at.astimezone(timezone.utc) > timedelta(days=7) or observed_at > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise ValueError("provider_snapshot_observation_missing")
    return assets, {"assets.json": asset_hash, "manifest.json": _sha256(manifest_path)}, observed


def build_symbol_mapping(listing_snapshot: Path, provider_snapshot: Path, output_root: Path) -> dict[str, Any]:
    """Publish a new immutable mapping artifact from explicit fixed snapshots."""
    rows, listing_hashes, listing_footer = _listing_rows(listing_snapshot)
    assets, provider_hashes, provider_observed = _load_assets(provider_snapshot)
    active: dict[str, list[dict[str, Any]]] = {}
    active_by_name: dict[tuple[str, ...], list[dict[str, Any]]] = {}
    for asset in assets:
        if (isinstance(asset, dict) and asset.get("class") == "us_equity" and asset.get("status") == "active"
                and isinstance(asset.get("symbol"), str) and isinstance(asset.get("id"), str)):
            active.setdefault(asset["symbol"].upper(), []).append(asset)
            active_by_name.setdefault(_name_tokens(asset.get("name", "")), []).append(asset)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen_original: set[str] = set()
    for row in rows:
        original = row["primary_symbol"]
        if original in seen_original:
            rejected.append({"original_symbol": original, "reason": "duplicate_official_primary_symbol", "research_qualified": False})
            continue
        seen_original.add(original)
        candidates, special_symbology = _preferred_candidates(row)
        # Keep the artifact focused on provider-symbol remediation.  Plain
        # alphanumeric listings are already valid provider query candidates;
        # special current spellings (preferreds, units, warrants) may require
        # the exact-metadata fallback below.
        if not special_symbology and re.fullmatch(r"[A-Z0-9]+", original):
            continue
        matches = [asset for candidate in candidates for asset in active.get(candidate, [])]
        candidate_basis = "official_current_symbology_pr" if special_symbology else "official_current_symbology"
        if not matches:
            # Candidate generation is only lookup assistance.  Acceptance
            # below still requires a complete exact current description and an
            # explicitly equivalent authoritative exchange identifier.
            matches = [asset for asset in active_by_name.get(_name_tokens(row["security_name"]), [])
                       if _provider_exchange_matches(row["exchange"], asset.get("exchange"))]
            candidate_basis = "exact_current_description_exchange"
        unique = {str(asset["id"]): asset for asset in matches}
        if not unique:
            rejected.append({"original_symbol": original, "reason": "no_active_provider_candidate", "candidate_provider_symbols": sorted(candidates),
                             "candidate_basis": candidate_basis, "research_qualified": False})
            continue
        if len(matches) != 1 or len(unique) != 1:
            rejected.append({"original_symbol": original, "reason": "provider_candidate_not_unique", "candidate_provider_symbols": sorted(candidates),
                             "provider_asset_ids": sorted(unique), "candidate_basis": candidate_basis, "research_qualified": False})
            continue
        asset = next(iter(unique.values()))
        provider_symbol = str(asset["symbol"]).upper()
        if provider_symbol == original:
            # No remediation is needed; keep this artifact narrowly limited to
            # different provider spellings.
            continue
        if not _provider_exchange_matches(row["exchange"], asset.get("exchange")):
            rejected.append({"original_symbol": original, "reason": "exchange_mismatch", "official_exchange": row["exchange"], "provider_exchange": asset.get("exchange"),
                             "candidate_basis": candidate_basis, "research_qualified": False})
            continue
        if _name_tokens(row["security_name"]) != _name_tokens(asset.get("name", "")):
            rejected.append({"original_symbol": original, "reason": "description_mismatch", "official_description": row["security_name"], "provider_description": asset.get("name"),
                             "candidate_basis": candidate_basis, "research_qualified": False})
            continue
        accepted.append({"original_symbol": original, "provider_symbol": provider_symbol, "provider_asset_id": str(asset["id"]),
                         "official_listing": {"file": row["file"], "symbology": row["aliases"], "description": row["security_name"], "exchange": row["exchange"]},
                         "provider_asset": {"symbol": provider_symbol, "description": asset.get("name"), "exchange": asset.get("exchange"), "status": asset.get("status"), "class": asset.get("class")},
                         "candidate_basis": candidate_basis,
                         "observation": {"provider_observed_at": provider_observed, "listing_footer": listing_footer},
                         "historical_identity": "unknown", "research_qualified": False,
                         "warning": "Current-directory/provider match only; it does not establish historical identity, lifecycle, corporate actions or research eligibility."})
    accepted.sort(key=lambda item: item["original_symbol"])
    if len({item["provider_symbol"] for item in accepted}) != len(accepted):
        raise ValueError("accepted_provider_symbol_not_unique")
    inputs = {"listing_snapshot": str(listing_snapshot), "provider_snapshot": str(provider_snapshot), "listing_sha256": listing_hashes, "provider_sha256": provider_hashes,
              "provider_observed_at": provider_observed}
    version_payload = {"schema_version": SCHEMA_VERSION, "inputs": inputs, "mappings": accepted}
    version = hashlib.sha256(json.dumps(version_payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    for item in accepted:
        item["mapping_id"] = hashlib.sha256(f"{version}|{item['original_symbol']}|{item['provider_asset_id']}".encode()).hexdigest()
    payload = {"schema_version": SCHEMA_VERSION, "mapping_version": version, "inputs": inputs, "mappings": accepted, "rejected": rejected,
               "mapping_window_days": CURRENT_MAPPING_WINDOW_DAYS, "research_qualified": False,
               "warning": "Only current symbol acquisition mappings. Historical identity is unknown; mappings are confined to a recent acquisition window and do not qualify research or backtests."}
    destination = output_root / "reference" / "symbol-mappings" / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8])
    destination.mkdir(parents=True, exist_ok=False)
    _atomic_json(destination / "mapping.json", payload)
    manifest = {"schema_version": "symbol-mapping-manifest-v1", "mapping_version": version, "mapping_sha256": _sha256(destination / "mapping.json"),
                "accepted": len(accepted), "rejected": len(rejected), "research_qualified": False}
    _atomic_json(destination / "manifest.json", manifest)
    return {**manifest, "path": str(destination / "mapping.json")}


def load_symbol_mapping(path: Path | str) -> dict[str, Any]:
    path = Path(path)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError("symbol_mapping_invalid") from exc
    if (not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION or payload.get("research_qualified") is not False
            or not isinstance(payload.get("mapping_version"), str) or not isinstance(payload.get("mappings"), list)
            or payload.get("mapping_window_days") != CURRENT_MAPPING_WINDOW_DAYS):
        raise ValueError("symbol_mapping_invalid")
    try:
        observed_at = datetime.fromisoformat(str(payload.get("inputs", {}).get("provider_observed_at", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("symbol_mapping_invalid") from exc
    if observed_at.tzinfo is None:
        raise ValueError("symbol_mapping_invalid")
    observed_at = observed_at.astimezone(timezone.utc)
    now = datetime.now(timezone.utc)
    if observed_at > now + timedelta(minutes=5) or now - observed_at > timedelta(days=7):
        raise ValueError("symbol_mapping_stale")
    seen_original: set[str] = set(); seen_provider: set[str] = set()
    for item in payload["mappings"]:
        if not isinstance(item, dict) or item.get("research_qualified") is not False or item.get("historical_identity") != "unknown":
            raise ValueError("symbol_mapping_invalid")
        original, provider = str(item.get("original_symbol", "")), str(item.get("provider_symbol", ""))
        if not _SYMBOL.fullmatch(original) or not _SYMBOL.fullmatch(provider) or original in seen_original or provider in seen_provider:
            raise ValueError("symbol_mapping_invalid")
        seen_original.add(original); seen_provider.add(provider)
    return payload


def mapping_tail_tasks(tasks: list[dict[str, Any]], mapping: dict[str, Any], *, current_target: str) -> list[dict[str, Any]]:
    """Split current-code work into <=31-day mapped tails, retaining history metadata."""
    by_symbol = {item["original_symbol"]: item for item in mapping["mappings"]}
    output: list[dict[str, Any]] = []
    for task in tasks:
        entry = by_symbol.get(str(task.get("symbol", "")).upper())
        if entry is None:
            continue
        try:
            start, end = date.fromisoformat(str(task["requested_start"])), date.fromisoformat(str(task["requested_end"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("mapping_task_invalid") from exc
        # A present-day mapping is not permitted to walk backward through a
        # durable historical backlog.  It may only acquire the current plan's
        # endpoint tail; the earlier task remains explicitly unverified.
        if end.isoformat() != current_target:
            continue
        tail_start = max(start, end - timedelta(days=CURRENT_MAPPING_WINDOW_DAYS - 1))
        mapping_task_id = hashlib.sha256(f"{task['task_id']}|{mapping['mapping_version']}|{tail_start}|{end}".encode()).hexdigest()
        remaining = None if tail_start == start else {"requested_start": start.isoformat(), "requested_end": (tail_start - timedelta(days=1)).isoformat(), "status": "unverified"}
        output.append({**task, "task_id": mapping_task_id, "original_task_id": task["task_id"], "requested_start": tail_start.isoformat(),
                       "provider_symbol": entry["provider_symbol"], "symbol_mapping": {"mapping_id": entry["mapping_id"], "mapping_version": mapping["mapping_version"],
                       "provider_asset_id": entry["provider_asset_id"], "historical_identity": "unknown", "research_qualified": False},
                       "remaining_history_unverified": remaining})
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--listing-snapshot", type=Path, required=True)
    parser.add_argument("--provider-snapshot", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build_symbol_mapping(args.listing_snapshot, args.provider_snapshot, args.data_root), sort_keys=True))


if __name__ == "__main__":
    main()
