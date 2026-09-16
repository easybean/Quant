"""Conservative, venue-scoped instrument and rule definitions.

``instrument_id`` is an opaque stable identity. A trading symbol is an
attribute with an effective period, never the identity itself: this permits a
symbol migration and a later security to reuse an old ticker.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from math import isfinite
from numbers import Real
from typing import Iterable, Optional

from quant_data.market_capabilities import MarketProduct


class AssetClass(str, Enum):
    EQUITY = "equity"
    FUTURE = "future"
    CRYPTO = "crypto"


def make_instrument_id(venue: str, identity_key: str) -> str:
    """Make an ID from venue plus non-ticker identity key (ISIN/contract ID)."""
    return f"{venue}:{identity_key}"


@dataclass(frozen=True)
class SymbolAssignment:
    """One venue symbol over an effective interval for a stable instrument."""

    venue_symbol: str
    valid_from: Optional[date] = None
    valid_to: Optional[date] = None


@dataclass(frozen=True)
class InstrumentRule:
    """A versioned, time-bounded set of tradability parameters."""

    valid_from: Optional[date] = None
    valid_to: Optional[date] = None
    rule_version: Optional[str] = None
    multiplier: Optional[Decimal | Real] = None
    quantity_unit: Optional[str] = None
    tick_size: Optional[Decimal | Real] = None
    lot_size: Optional[Decimal | Real] = None
    min_notional: Optional[Decimal | Real] = None


@dataclass(frozen=True)
class InstrumentDefinition:
    """Stable identity, product facts, symbol lifecycle and effective rules."""

    instrument_id: str
    identity_key: Optional[str]
    asset: AssetClass | str
    product: MarketProduct | str
    venue: str
    venue_symbol: str
    base_currency: Optional[str] = None
    quote_currency: Optional[str] = None
    settlement_currency: Optional[str] = None
    expiry: Optional[date] = None
    timezone: Optional[str] = None
    calendar_id: Optional[str] = None
    listing_date: Optional[date] = None
    delisting_date: Optional[date] = None
    last_trade: Optional[date] = None
    first_notice: Optional[date] = None
    linear: bool = False
    inverse: bool = False
    symbol_history: tuple[SymbolAssignment, ...] = field(default_factory=tuple)
    rules: tuple[InstrumentRule, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class ValidationIssue:
    field: str
    code: str
    message: str


@dataclass(frozen=True)
class InstrumentValidationResult:
    instrument_id: str
    issues: tuple[ValidationIssue, ...] = ()

    @property
    def valid(self) -> bool:
        return not self.issues

    @property
    def ok(self) -> bool:
        return self.valid


def validate_instrument(definition: InstrumentDefinition) -> InstrumentValidationResult:
    """Return every input problem without silently filling missing data."""
    issues: list[ValidationIssue] = []
    _validate_identity(definition, issues)
    product = _coerce_product(definition.product, issues)
    asset = _coerce_asset(definition.asset, issues)
    _validate_dates(definition, issues)
    _validate_product_asset(product, asset, issues)
    if definition.linear and definition.inverse:
        _add(issues, "linear", "linear_inverse_conflict", "linear and inverse cannot both be true")

    if product is MarketProduct.CRYPTO_SPOT:
        _require(definition.base_currency, "base_currency", "spot instruments require base_currency", issues)
        _require(definition.quote_currency, "quote_currency", "spot instruments require quote_currency", issues)
        _require(definition.settlement_currency, "settlement_currency", "spot instruments require settlement_currency", issues)
        if definition.expiry is not None:
            _add(issues, "expiry", "spot_has_expiry", "spot instruments cannot have expiry")
        if definition.linear or definition.inverse:
            _add(issues, "linear", "spot_contract_model", "spot instruments cannot declare linear or inverse contract PnL")
    elif product in {MarketProduct.CRYPTO_PERPETUAL, MarketProduct.CRYPTO_DELIVERY}:
        for field_name in ("base_currency", "quote_currency", "settlement_currency"):
            _require(getattr(definition, field_name), field_name, f"crypto derivatives require {field_name}", issues)
        if not definition.linear and not definition.inverse:
            _add(issues, "linear", "contract_model_required", "crypto derivatives require exactly one of linear or inverse")
        if product is MarketProduct.CRYPTO_PERPETUAL and definition.expiry is not None:
            _add(issues, "expiry", "perpetual_has_expiry", "perpetual instruments cannot have expiry")
        if product is MarketProduct.CRYPTO_DELIVERY:
            _require(definition.expiry, "expiry", "crypto delivery instruments require expiry", issues)
    elif product in _EXPIRING_PRODUCTS:
        _require(definition.expiry, "expiry", "expiring futures require expiry", issues)

    _validate_symbol_history(definition, issues)
    _validate_rules(definition.rules, product in _DERIVATIVE_PRODUCTS, issues)
    return InstrumentValidationResult(definition.instrument_id, tuple(issues))


def validate_instrument_set(definitions: Iterable[InstrumentDefinition]) -> tuple[InstrumentValidationResult, ...]:
    """Also reject overlapping reuse of one venue ticker across lifecycles."""
    items = tuple(definitions)
    extras: list[list[ValidationIssue]] = [[] for _ in items]
    for left, first in enumerate(items):
        for right in range(left + 1, len(items)):
            second = items[right]
            if first.instrument_id == second.instrument_id:
                _add(extras[right], "instrument_id", "duplicate_instrument_id", "instrument_id must identify one lifecycle")
                continue
            if first.venue != second.venue:
                continue
            for a in first.symbol_history:
                for b in second.symbol_history:
                    if a.venue_symbol == b.venue_symbol and _intervals_overlap(a.valid_from, a.valid_to, b.valid_from, b.valid_to):
                        message = f"{a.venue_symbol} overlaps another lifecycle on {first.venue}; split or correct effective periods"
                        _add(extras[left], "symbol_history", "ticker_lifecycle_overlap", message)
                        _add(extras[right], "symbol_history", "ticker_lifecycle_overlap", message)
    return tuple(
        InstrumentValidationResult(item.instrument_id, validate_instrument(item).issues + tuple(extras[index]))
        for index, item in enumerate(items)
    )


def effective_rule_at(definition: InstrumentDefinition, on_date: date) -> InstrumentRule | None:
    """Return the sole rule effective on a trading date, never the latest rule.

    Consumers must pass the historical trading date that they are evaluating.
    An invalid or overlapping rule set fails closed instead of selecting an
    arbitrary version; absence means the instrument was not rule-verified on
    that date.
    """
    if isinstance(on_date, datetime) or not isinstance(on_date, date):
        raise TypeError("on_date must be a date")
    relevant = tuple(
        rule for rule in definition.rules
        if isinstance(rule.valid_from, date)
        and rule.valid_from <= on_date
        and (rule.valid_to is None or on_date <= rule.valid_to)
    )
    if len(relevant) > 1:
        raise ValueError("multiple effective rules; resolve rule_effectivity_overlap before use")
    return relevant[0] if relevant else None


_DERIVATIVE_PRODUCTS = frozenset({MarketProduct.CN_FUTURE, MarketProduct.GLOBAL_FUTURE, MarketProduct.CRYPTO_PERPETUAL, MarketProduct.CRYPTO_DELIVERY})
_EXPIRING_PRODUCTS = frozenset({MarketProduct.CN_FUTURE, MarketProduct.GLOBAL_FUTURE, MarketProduct.CRYPTO_DELIVERY})
_PRODUCT_ASSET = {
    MarketProduct.US_EQUITY: AssetClass.EQUITY,
    MarketProduct.CN_FUTURE: AssetClass.FUTURE,
    MarketProduct.GLOBAL_FUTURE: AssetClass.FUTURE,
    MarketProduct.CRYPTO_SPOT: AssetClass.CRYPTO,
    MarketProduct.CRYPTO_PERPETUAL: AssetClass.CRYPTO,
    MarketProduct.CRYPTO_DELIVERY: AssetClass.CRYPTO,
}


def _validate_identity(definition: InstrumentDefinition, issues: list[ValidationIssue]) -> None:
    _require(definition.venue, "venue", "venue is required", issues)
    _require(definition.venue_symbol, "venue_symbol", "venue_symbol is required", issues)
    _require(definition.identity_key, "identity_key", "identity_key is required and cannot be a ticker", issues)
    _require(definition.timezone, "timezone", "timezone is required", issues)
    _require(definition.calendar_id, "calendar_id", "calendar_id is required", issues)
    if definition.identity_key:
        expected = make_instrument_id(definition.venue, definition.identity_key)
        if definition.instrument_id != expected:
            _add(issues, "instrument_id", "stable_id_required", "instrument_id must equal 'venue:identity_key'; venue_symbol is not a stable identity")


def _validate_dates(definition: InstrumentDefinition, issues: list[ValidationIssue]) -> None:
    for name in ("expiry", "listing_date", "delisting_date", "last_trade", "first_notice"):
        value = getattr(definition, name)
        if value is not None and not isinstance(value, date):
            _add(issues, name, "invalid_date", f"{name} must be a date")
    if _ordered(definition.listing_date, definition.delisting_date) is False:
        _add(issues, "delisting_date", "before_listing", "delisting_date cannot be before listing_date")
    if _ordered(definition.last_trade, definition.expiry) is False:
        _add(issues, "last_trade", "after_expiry", "last_trade cannot be after expiry")
    if _ordered(definition.first_notice, definition.last_trade) is False:
        _add(issues, "first_notice", "after_last_trade", "first_notice cannot be after last_trade")


def _validate_product_asset(product: Optional[MarketProduct], asset: Optional[AssetClass], issues: list[ValidationIssue]) -> None:
    if product is not None and asset is not None and _PRODUCT_ASSET[product] is not asset:
        _add(issues, "asset", "product_asset_mismatch", f"{product.value} requires asset={_PRODUCT_ASSET[product].value}")


def _validate_symbol_history(definition: InstrumentDefinition, issues: list[ValidationIssue]) -> None:
    assignments = definition.symbol_history
    if not assignments:
        _add(issues, "symbol_history", "missing_symbol_history", "at least one effective venue symbol assignment is required")
        return
    has_current = False
    for index, assignment in enumerate(assignments):
        prefix = f"symbol_history[{index}]"
        _require(assignment.venue_symbol, f"{prefix}.venue_symbol", "venue_symbol is required", issues)
        _validate_interval(assignment.valid_from, assignment.valid_to, prefix, issues)
        has_current = has_current or assignment.venue_symbol == definition.venue_symbol
    if not has_current:
        _add(issues, "venue_symbol", "symbol_not_in_history", "venue_symbol must appear in symbol_history")
    for index, assignment in enumerate(assignments):
        for later in assignments[index + 1:]:
            if _intervals_overlap(assignment.valid_from, assignment.valid_to, later.valid_from, later.valid_to):
                _add(issues, "symbol_history", "symbol_lifecycle_overlap", "symbol assignments for one instrument cannot overlap")
                return


def _validate_rules(rules: tuple[InstrumentRule, ...], derivative: bool, issues: list[ValidationIssue]) -> None:
    if not rules:
        _add(issues, "rules", "missing_rules", "at least one effective InstrumentRule is required")
        return
    for index, rule in enumerate(rules):
        prefix = f"rules[{index}]"
        _validate_interval(rule.valid_from, rule.valid_to, prefix, issues)
        _require(rule.rule_version, f"{prefix}.rule_version", "rule_version is required", issues)
        _require(rule.quantity_unit, f"{prefix}.quantity_unit", "quantity_unit is required", issues)
        for name in ("tick_size", "lot_size"):
            if not _positive(getattr(rule, name)):
                _add(issues, f"{prefix}.{name}", "positive_finite_required", f"{name} must be a finite positive number")
        if derivative and not _positive(rule.multiplier):
            _add(issues, f"{prefix}.multiplier", "positive_finite_required", "derivatives require a finite positive multiplier")
        if rule.min_notional is not None and not _positive(rule.min_notional):
            _add(issues, f"{prefix}.min_notional", "positive_finite_required", "min_notional must be finite and positive when provided")
    for index, rule in enumerate(rules):
        for later in rules[index + 1:]:
            if _intervals_overlap(rule.valid_from, rule.valid_to, later.valid_from, later.valid_to):
                _add(issues, "rules", "rule_effectivity_overlap", "rule effective intervals cannot overlap")
                return


def _validate_interval(start: object, end: object, prefix: str, issues: list[ValidationIssue]) -> None:
    if start is None:
        _add(issues, f"{prefix}.valid_from", "effective_from_required", "valid_from is required for historical lookup")
    elif not isinstance(start, date):
        _add(issues, f"{prefix}.valid_from", "invalid_date", "valid_from must be a date")
    if end is not None and not isinstance(end, date):
        _add(issues, f"{prefix}.valid_to", "invalid_date", "valid_to must be a date")
    if _ordered(start, end) is False:
        _add(issues, f"{prefix}.valid_to", "before_valid_from", "valid_to cannot be before valid_from")


def _coerce_product(value: MarketProduct | str, issues: list[ValidationIssue]) -> Optional[MarketProduct]:
    try:
        return value if isinstance(value, MarketProduct) else MarketProduct(str(value))
    except ValueError:
        _add(issues, "product", "unknown_product", f"unknown product: {value}")
        return None


def _coerce_asset(value: AssetClass | str, issues: list[ValidationIssue]) -> Optional[AssetClass]:
    try:
        return value if isinstance(value, AssetClass) else AssetClass(str(value))
    except ValueError:
        _add(issues, "asset", "unknown_asset", f"unknown asset: {value}")
        return None


def _ordered(first: object, second: object) -> Optional[bool]:
    if first is None or second is None or not isinstance(first, date) or not isinstance(second, date):
        return None
    return first <= second


def _intervals_overlap(first_start: object, first_end: object, second_start: object, second_end: object) -> bool:
    if not isinstance(first_start, date) or not isinstance(second_start, date):
        return False
    return (first_end is None or second_start <= first_end) and (second_end is None or first_start <= second_end)


def _positive(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (Decimal, Real)):
        return False
    try:
        return value.is_finite() and value > 0 if isinstance(value, Decimal) else isfinite(float(value)) and value > 0
    except (ValueError, OverflowError, InvalidOperation):
        return False


def _require(value: object, field_name: str, message: str, issues: list[ValidationIssue]) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        _add(issues, field_name, "required", message)


def _add(issues: list[ValidationIssue], field_name: str, code: str, message: str) -> None:
    issues.append(ValidationIssue(field_name, code, message))
