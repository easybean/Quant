"""Versioned, human-auditable P2-03 accounting golden inputs and outputs."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping


GOLDEN_VERSION = "p2-03-v1"


@dataclass(frozen=True)
class GoldenCase:
    case_id: str
    product: str
    inputs: Mapping[str, str]
    expected: Mapping[str, str]
    convention: str


GOLDEN_CASES: tuple[GoldenCase, ...] = (
    GoldenCase(
        "equity-partial-fill-cancel-fee",
        "US_EQUITY",
        {"opening_cash": "1000", "order_quantity": "10", "fills": "4@100 fee=1", "mark": "102"},
        {"free_cash": "599", "frozen_margin": "0", "position": "4", "realized_pnl": "0", "unrealized_pnl": "8", "fees": "1", "total_pnl": "7"},
        "A buy order is partially filled then cancelled; only the explicit fill changes cash or inventory.",
    ),
    GoldenCase(
        "equity-split-and-dividend",
        "US_EQUITY",
        {"opening_cash": "500", "position": "10@100", "split": "2-for-1", "dividend": "1 per post-split share", "mark": "55"},
        {"free_cash": "520", "frozen_margin": "0", "position": "20", "realized_pnl": "0", "unrealized_pnl": "100", "cash_distributions": "20", "total_pnl": "120"},
        "The dividend entitlement is explicitly after the split; withholding/tax is out of scope unless an explicit cashflow is supplied.",
    ),
    GoldenCase(
        "future-daily-settlement-no-double-count",
        "CN_FUTURE_REFERENCE",
        {"free_cash_after_margin": "9000", "frozen_margin": "1000", "position": "2", "multiplier": "10", "prior_settlement": "100", "new_settlement": "105"},
        {"free_cash": "9100", "frozen_margin": "1000", "position": "2", "realized_pnl": "0", "unrealized_pnl": "0", "settlement_pnl": "100", "total_pnl": "100"},
        "Variation settlement is credited once to free cash and resets unrealized PnL; it is not a second cash credit.",
    ),
    GoldenCase(
        "linear-perpetual-mark-pnl",
        "CRYPTO_PERPETUAL_LINEAR",
        {"free_cash": "900", "frozen_margin": "100", "quantity": "0.01", "multiplier": "1", "entry": "50000", "mark": "51000"},
        {"free_cash": "900", "frozen_margin": "100", "position": "0.01", "realized_pnl": "0", "unrealized_pnl": "10", "total_pnl": "10"},
        "Settlement currency is USDT; mark PnL does not release or consume margin in this isolated reference case.",
    ),
    GoldenCase(
        "inverse-perpetual-mark-pnl",
        "CRYPTO_PERPETUAL_INVERSE",
        {"free_cash": "0.99", "frozen_margin": "0.01", "contracts": "100", "contract_value_quote": "1", "entry": "50000", "mark": "40000"},
        {"free_cash": "0.99", "frozen_margin": "0.01", "position": "100", "realized_pnl": "0", "unrealized_pnl": "-0.0005", "total_pnl": "-0.0005"},
        "PnL is BTC: contracts × USD contract value × (1/entry − 1/mark); no FX translation is inferred.",
    ),
    GoldenCase(
        "linear-perpetual-funding-payment",
        "CRYPTO_PERPETUAL_LINEAR",
        {"free_cash": "900", "frozen_margin": "100", "position": "0.01", "venue_calculated_payment": "-0.05"},
        {"free_cash": "899.95", "frozen_margin": "100", "position": "0.01", "funding_pnl": "-0.05", "total_pnl": "-0.05"},
        "Negative payment means the account pays.  Rate-to-payment convention is intentionally not assumed.",
    ),
)


def decimal_expected(case_id: str, field: str) -> Decimal:
    """Expose expected numerical values without float conversion."""
    case = next((item for item in GOLDEN_CASES if item.case_id == case_id), None)
    if case is None or field not in case.expected:
        raise KeyError(f"unknown golden expectation: {case_id}.{field}")
    return Decimal(case.expected[field])
