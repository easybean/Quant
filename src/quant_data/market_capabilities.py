"""Conservative, product-specific runner capability registry.

This is an evidence registry, not package detection.  A framework being
installed never turns a market/product/order/frequency requirement into a
supported claim.  All M0 candidates remain blocking until P2-04 supplies
product-specific evidence.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable, Mapping


class MarketProduct(str, Enum):
    US_EQUITY = "US_EQUITY"
    CN_FUTURE = "CN_FUTURE"
    GLOBAL_FUTURE = "GLOBAL_FUTURE"
    CRYPTO_SPOT = "CRYPTO_SPOT"
    CRYPTO_PERPETUAL = "CRYPTO_PERPETUAL"
    CRYPTO_DELIVERY = "CRYPTO_DELIVERY"


Product = MarketProduct


class CapabilityStatus(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNVERIFIED = "unverified"
    UNKNOWN = "unknown"


class Runner(str, Enum):
    BACKTRADER = "backtrader"
    NAUTILUS = "nautilus"
    VEIGHNA = "veighna"


# Values are deliberately concrete.  A request for merely "order_types" or
# "data_frequency" is rejected as underspecified rather than silently widened.
CAPABILITY_FIELDS = (
    "bar_frequency:1d", "bar_frequency:1h", "bar_frequency:1m", "bar_frequency:tick",
    "order_type:market", "order_type:limit", "order_type:stop", "order_type:stop_limit",
    "short:sell", "margin:cross", "margin:isolated", "position_mode:net", "position_mode:hedge",
    "open_close:open", "open_close:close", "open_close:close_today",
    "funding:perpetual", "expiry_settlement:delivery", "linear_inverse:linear", "linear_inverse:inverse",
    "reconciliation:account",
)


@dataclass(frozen=True)
class CapabilityRequirement:
    """One exact behavior needed by a strategy/run specification."""

    capability: str
    value: str

    @property
    def key(self) -> str:
        return f"{self.capability}:{self.value}"


@dataclass(frozen=True)
class CapabilityProfile:
    runner: str
    product: MarketProduct
    status: CapabilityStatus
    capabilities: Mapping[str, CapabilityStatus]
    intended_use: str
    evidence: str

    def capability_status(self, key: str) -> CapabilityStatus:
        return self.capabilities.get(key, CapabilityStatus.UNKNOWN)


@dataclass(frozen=True)
class RequirementIssue:
    capability: str
    required: CapabilityStatus
    actual: CapabilityStatus
    reason: str


@dataclass(frozen=True)
class ValidationResult:
    runner: str
    product: str
    profile_status: CapabilityStatus
    missing: tuple[RequirementIssue, ...] = ()
    unverified: tuple[RequirementIssue, ...] = ()
    unknown: tuple[RequirementIssue, ...] = ()

    @property
    def issues(self) -> tuple[RequirementIssue, ...]:
        """A display-ready complete list; preserves status grouping elsewhere."""
        return self.missing + self.unverified + self.unknown

    @property
    def supported(self) -> bool:
        return self.profile_status is CapabilityStatus.SUPPORTED and not self.issues

    @property
    def ok(self) -> bool:
        return self.supported


def _all(status: CapabilityStatus) -> dict[str, CapabilityStatus]:
    return {field: status for field in CAPABILITY_FIELDS}


def _candidate_profile(runner: Runner, product: MarketProduct) -> CapabilityProfile:
    if runner is Runner.NAUTILUS and product is MarketProduct.US_EQUITY:
        capabilities = _all(CapabilityStatus.UNVERIFIED)
        capabilities["bar_frequency:1d"] = CapabilityStatus.SUPPORTED
        capabilities["order_type:limit"] = CapabilityStatus.SUPPORTED
        return CapabilityProfile(
            runner=runner.value,
            product=product,
            status=CapabilityStatus.SUPPORTED,
            capabilities=capabilities,
            intended_use="synthetic US-equity daily limit-order harness only; market orders remain blocked",
            evidence="P2-05: NautilusTrader 1.221.0 target-Linux synthetic limit order: 4@100 partial fill, cancel 6, USD 1 fee, and p2-03-v1 account golden matched",
        )
    return CapabilityProfile(
        runner=runner.value,
        product=product,
        status=CapabilityStatus.UNVERIFIED,
        capabilities=_all(CapabilityStatus.UNVERIFIED),
        intended_use="candidate only; product, frequency and order semantics are unverified",
        evidence="M0 placeholder; package presence is not compatibility evidence",
    )


class CapabilityRegistry:
    """Registry whose missing or unverified declarations always block runs."""

    def __init__(self, profiles: Iterable[CapabilityProfile] = ()) -> None:
        self._profiles: dict[tuple[str, MarketProduct], CapabilityProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: CapabilityProfile) -> None:
        self._profiles[(profile.runner, profile.product)] = profile

    def get_profile(self, runner: Runner | str, product: MarketProduct | str) -> CapabilityProfile:
        runner_name, market = _runner_name(runner), _product(product)
        profile = self._profiles.get((runner_name, market))
        if profile is not None:
            return profile
        return CapabilityProfile(runner_name, market, CapabilityStatus.UNSUPPORTED, _all(CapabilityStatus.UNSUPPORTED), "no declared use", "No profile is registered for this runner/product pair")

    def validate_requirements(
        self,
        runner: Runner | str,
        product: MarketProduct | str,
        requirements: Iterable[CapabilityRequirement | str] | Mapping[str, object],
    ) -> ValidationResult:
        try:
            profile = self.get_profile(runner, product)
        except ValueError as exc:
            issue = RequirementIssue("registry", CapabilityStatus.SUPPORTED, CapabilityStatus.UNKNOWN, str(exc))
            return ValidationResult(_safe_name(runner), _safe_name(product), CapabilityStatus.UNKNOWN, unknown=(issue,))

        missing: list[RequirementIssue] = []
        unverified: list[RequirementIssue] = []
        unknown: list[RequirementIssue] = []
        for requested in _normalise_requirements(requirements):
            if isinstance(requested, RequirementIssue):
                unknown.append(requested)
                continue
            actual = profile.capability_status(requested.key)
            if actual is CapabilityStatus.SUPPORTED:
                continue
            issue = RequirementIssue(requested.key, CapabilityStatus.SUPPORTED, actual, _issue_reason(requested.key, actual))
            if actual is CapabilityStatus.UNVERIFIED:
                unverified.append(issue)
            elif actual is CapabilityStatus.UNKNOWN:
                unknown.append(issue)
            else:
                missing.append(issue)
        if profile.status is CapabilityStatus.UNVERIFIED:
            unverified.insert(0, RequirementIssue("profile", CapabilityStatus.SUPPORTED, profile.status, "runner/product profile has not been verified"))
        elif profile.status is not CapabilityStatus.SUPPORTED:
            missing.insert(0, RequirementIssue("profile", CapabilityStatus.SUPPORTED, profile.status, "runner/product profile is not supported"))
        return ValidationResult(profile.runner, profile.product.value, profile.status, tuple(missing), tuple(unverified), tuple(unknown))


def _normalise_requirements(requirements: Iterable[CapabilityRequirement | str] | Mapping[str, object]) -> tuple[CapabilityRequirement | RequirementIssue, ...]:
    if isinstance(requirements, str):
        requirements = (requirements,)
    if isinstance(requirements, Mapping):
        output: list[CapabilityRequirement | RequirementIssue] = []
        for capability, values in requirements.items():
            if isinstance(values, str):
                values = (values,)
            elif not isinstance(values, Iterable) or isinstance(values, (bytes, bool)):
                output.append(_underspecified(capability))
                continue
            for value in values:
                if not isinstance(value, str) or not value:
                    output.append(_underspecified(capability))
                else:
                    output.append(CapabilityRequirement(capability, value))
        return tuple(output)
    output = []
    for item in requirements:
        if isinstance(item, CapabilityRequirement):
            output.append(item)
        elif isinstance(item, str) and item.count(":") == 1 and all(item.split(":", 1)):
            capability, value = item.split(":", 1)
            output.append(CapabilityRequirement(capability, value))
        else:
            output.append(_underspecified(str(item)))
    return tuple(output)


def _underspecified(value: str) -> RequirementIssue:
    return RequirementIssue(value, CapabilityStatus.SUPPORTED, CapabilityStatus.UNKNOWN, "requirement must specify an exact value, e.g. 'bar_frequency:1d' or {'order_type': ['limit']}")


def _runner_name(value: Runner | str) -> str:
    name = value.value if isinstance(value, Runner) else str(value).lower()
    if name not in {runner.value for runner in Runner}:
        raise ValueError(f"unknown runner: {value}")
    return name


def _product(value: MarketProduct | str) -> MarketProduct:
    if isinstance(value, MarketProduct):
        return value
    try:
        return MarketProduct(str(value))
    except ValueError as exc:
        raise ValueError(f"unknown market product: {value}") from exc


def _safe_name(value: object) -> str:
    return value.value if isinstance(value, Enum) else str(value)


def _issue_reason(capability: str, actual: CapabilityStatus) -> str:
    if actual is CapabilityStatus.UNVERIFIED:
        return f"{capability} has not been verified for this exact product"
    if actual is CapabilityStatus.UNKNOWN:
        return f"{capability} is not declared by this profile"
    return f"{capability} is unsupported"


# Backtrader remains a candidate even for historical US equities: P2-04 must
# verify explicit behavior before a strategy can request it.
DEFAULT_REGISTRY = CapabilityRegistry(
    [_candidate_profile(runner, product) for runner in Runner for product in MarketProduct]
)


def validate_requirements(
    runner: Runner | str,
    product: MarketProduct | str,
    requirements: Iterable[CapabilityRequirement | str] | Mapping[str, object],
) -> ValidationResult:
    return DEFAULT_REGISTRY.validate_requirements(runner, product, requirements)
