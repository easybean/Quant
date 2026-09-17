"""Acquisition eligibility, never research or historical universe qualification."""
from __future__ import annotations

import pandas as pd

# Exact exchange test symbols verified in NYSE testing notices/Nasdaq metadata.
# Not a substring rule: a real issuer containing 'TEST' must not be excluded.
EXCHANGE_TEST_SYMBOLS = frozenset({
    "ATEST", "ATEST-A", "ATEST-B", "ATEST-C", "ATEST-G", "ATEST-H", "ATEST-L", "ATEST-Z",
})
TEST_EVIDENCE = "https://www.nyse.com/publicdocs/nyse/markets/nyse-american/Pillar_Update_NYSE_American_Weekend_Test_Update_July21_2017.pdf"


def select_sync_symbols(master: pd.DataFrame) -> list[str]:
    required = {"symbol", "status", "asset_type"}
    if not required.issubset(master.columns):
        raise ValueError("security_master_missing_required_columns")
    rows = master.copy()
    symbols = rows.symbol.fillna("").astype(str).str.strip().str.upper()
    flagged = symbols.isin(EXCHANGE_TEST_SYMBOLS)
    for column in ("Test Issue", "test_issue", "is_test"):
        if column in rows:
            flagged |= rows[column].fillna("").astype(str).str.upper().isin({"Y", "TRUE", "1"})
    # A test flag from any source must survive cross-source duplicate rows.
    excluded = set(symbols[flagged])
    active = rows.status.fillna("").astype(str).str.casefold().eq("active")
    product = rows.asset_type.fillna("").astype(str).str.casefold().isin({"stock", "etf"})
    return sorted(set(symbols[active & product]) - excluded - {""})


def eligibility_report(master: pd.DataFrame) -> dict:
    accepted = set(select_sync_symbols(master))
    active = master.loc[master.status.fillna("").astype(str).str.casefold().eq("active")]
    rejected = sorted(set(active.symbol.fillna("").astype(str).str.strip().str.upper()) - accepted)
    return {"eligible": len(accepted), "excluded_active_symbols": rejected,
            "test_evidence": TEST_EVIDENCE, "research_qualified": False}
