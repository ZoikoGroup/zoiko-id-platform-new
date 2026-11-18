"""Licensed screening provider controls (ZID-DTV-WF-06 S11, section 10 integrations, ENG-13, ENG-15; CR-113). Pure domain, no I/O.

No paid call is made, and no data leaves for a provider, unless every gate says yes, checked in this order and before any charge:

  provider approved (not DISABLED or SANDBOX in production) -> Finance authorised the price schedule -> the class and the market are in the contract ->
  the processing region is signed for this tenant (no failover to another region, ever) -> the circuit is closed -> the quota and the budget have room.

An outage opens the circuit and the case reads UNAVAILABLE; it never reads clear and never retries into another region. A provider's callback is trusted only if its signature
verifies over the exact bytes, the timestamp is inside the window, it names this provider and this tenant, and its event id has not been seen: a replay changes nothing and
bills nothing.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum


class ProviderState(StrEnum):
    DISABLED = "DISABLED"
    SANDBOX = "SANDBOX"
    APPROVED = "APPROVED"


@dataclass(frozen=True, slots=True)
class Provider:
    provider_id: str
    state: ProviderState
    classes: frozenset[
        str
    ]  # the check classes the contract covers: PEP_RCA, ADVERSE_MEDIA, SANCTIONS, MONITORING...
    markets: frozenset[str]
    regions: frozenset[str]  # processing regions signed with the provider and approved by Privacy
    contract_version: str | None
    price_version: str | None
    finance_authorised_by: str | None  # who in Finance or Procurement approved the price schedule
    unit_cost: Decimal = Decimal(0)
    monthly_budget: Decimal | None = None
    monthly_quota: int | None = None


@dataclass(frozen=True, slots=True)
class Usage:
    """What this provider has been used for in the current period, for this tenant."""

    spend: Decimal = Decimal(0)
    calls: int = 0


@dataclass(frozen=True, slots=True)
class Circuit:
    failures: tuple[datetime, ...] = ()
    opened_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class CircuitPolicy:
    threshold: int = 5
    window: timedelta = timedelta(minutes=5)
    cooldown: timedelta = timedelta(minutes=2)


@dataclass(frozen=True, slots=True)
class Gate:
    allowed: bool
    reasons: tuple[str, ...]
    http_status: int = (
        200  # 422 unsupported coverage, 429 quota or cost limit, 503 a required source down
    )


def activation_problems(provider: Provider) -> list[str]:
    """Why a provider cannot be used yet, in codes. Empty means every written control is in place, not that the provider is right: Legal, Privacy and Finance decide that. A provider that is not APPROVED
    reports only that, because nothing else about it matters until someone enables it."""
    if provider.state is not ProviderState.APPROVED:
        return [f"STATE_{provider.state.value}"]
    out: list[str] = []
    if not provider.contract_version:
        out.append("CONTRACT_VERSION_MISSING")
    if not provider.price_version:
        out.append("PRICE_SCHEDULE_MISSING")
    if not provider.finance_authorised_by:
        out.append("FINANCE_AUTHORISATION_MISSING")
    if not provider.classes:
        out.append("NO_CONTRACTED_CLASSES")
    if not provider.markets:
        out.append("NO_CONTRACTED_MARKETS")
    if not provider.regions:
        out.append("NO_APPROVED_REGIONS")
    if provider.unit_cost <= 0:
        out.append("UNIT_COST_NOT_SET")
    if provider.monthly_budget is None:
        out.append(
            "NO_MONTHLY_BUDGET"
        )  # an uncapped paid provider is a runaway cost waiting to happen
    if provider.monthly_quota is None:
        out.append("NO_MONTHLY_QUOTA")
    return out


def circuit_open(circuit: Circuit, policy: CircuitPolicy, now: datetime) -> bool:
    return circuit.opened_at is not None and now - circuit.opened_at < policy.cooldown


def record_failure(circuit: Circuit, policy: CircuitPolicy, now: datetime) -> Circuit:
    """One failed call. Failures older than the window are forgotten; the threshold opens the circuit."""
    recent = tuple(f for f in circuit.failures if now - f <= policy.window) + (now,)
    opened = now if len(recent) >= policy.threshold else circuit.opened_at
    return Circuit(recent, opened)


def record_success(circuit: Circuit) -> Circuit:
    return Circuit()


def may_call(
    provider: Provider | None,
    *,
    klass: str,
    market: str,
    region: str,
    tenant_regions: Collection[str],
    usage: Usage,
    circuit: Circuit,
    now: datetime,
    production: bool = True,
    policy: CircuitPolicy | None = None,
) -> Gate:
    """Whether one billable provider request may be made. Every refusal names its reason, and none of them lets the caller try a different provider or region."""
    policy = policy or CircuitPolicy()
    if provider is None:
        return Gate(False, ("PROVIDER_NOT_REGISTERED",), 422)
    if provider.state is ProviderState.DISABLED:
        return Gate(False, ("PROVIDER_DISABLED",), 422)
    if provider.state is ProviderState.SANDBOX and production:
        return Gate(False, ("PROVIDER_SANDBOX_ONLY",), 422)
    if (
        not provider.finance_authorised_by
        or not provider.price_version
        or not provider.contract_version
    ):
        return Gate(False, ("FINANCE_AUTHORISATION_MISSING",), 422)
    if klass not in provider.classes:
        return Gate(False, ("CLASS_NOT_CONTRACTED",), 422)
    if market not in provider.markets:
        return Gate(False, ("MARKET_NOT_CONTRACTED",), 422)
    if region not in provider.regions or region not in set(tenant_regions):
        return Gate(False, ("REGION_NOT_APPROVED",), 422)
    if circuit_open(circuit, policy, now):
        return Gate(False, ("CIRCUIT_OPEN",), 503)
    if provider.monthly_quota is not None and usage.calls + 1 > provider.monthly_quota:
        return Gate(False, ("QUOTA_EXCEEDED",), 429)
    if (
        provider.monthly_budget is not None
        and usage.spend + provider.unit_cost > provider.monthly_budget
    ):
        return Gate(False, ("BUDGET_EXHAUSTED",), 429)
    return Gate(True, ())


def next_region(region: str, provider: Provider, tenant_regions: Collection[str]) -> str | None:
    """The region to fail over to. There is none: a failover to another region is a new transfer that needs its own signed route, so the answer is always None (QA-041)."""
    return None


# ---- a provider's callback ---------------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Event:
    provider_id: str
    tenant_id: str
    event_id: str
    case_ref: str
    issued_at: datetime
    body: bytes


class EventError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def sign(
    secret: bytes, provider_id: str, tenant_id: str, event_id: str, issued_at: datetime, body: bytes
) -> str:
    message = b"\n".join(
        (
            provider_id.encode(),
            tenant_id.encode(),
            event_id.encode(),
            issued_at.isoformat().encode(),
            body,
        )
    )
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def verify_event(
    event: Event,
    signature: str,
    *,
    secret: bytes,
    expected_provider: str,
    expected_tenant: str,
    seen: Collection[str],
    now: datetime,
    window: timedelta = timedelta(minutes=10),
) -> str:
    """The event id to record, or EventError. Order: who it names, then the signature over the exact bytes, then the clock, then replay. A replay is refused (DUPLICATE_EVENT), so
    nothing changes and nothing is billed twice."""
    if event.provider_id != expected_provider:
        raise EventError("PROVIDER_NOT_RECOGNISED")
    if event.tenant_id != expected_tenant:
        raise EventError("TENANT_MISMATCH")
    expected = sign(
        secret, event.provider_id, event.tenant_id, event.event_id, event.issued_at, event.body
    )
    if not hmac.compare_digest(expected, signature):
        raise EventError("SIGNATURE_INVALID")
    if abs(now - event.issued_at) > window:
        raise EventError("TIMESTAMP_OUTSIDE_WINDOW")
    if event.event_id in seen:
        raise EventError("DUPLICATE_EVENT")
    return event.event_id


def call_key(tenant_id: str, case_id: str, provider_id: str, klass: str) -> str:
    """One request, one key: the same case asking the same provider for the same class is the same call, so a retry or a replay is never billed twice."""
    return hashlib.sha256(f"{tenant_id}|{case_id}|{provider_id}|{klass}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class Charge:
    key: str
    cost: Decimal
    billable: bool


def charge_for(
    key: str, provider: Provider, billed: Mapping[str, Decimal], *, outage: bool
) -> Charge:
    """What a call costs. A call already recorded under the same key is free, and an outage is the provider's, never chargeable."""
    if key in billed or outage:
        return Charge(key, Decimal(0), False)
    return Charge(key, provider.unit_cost, provider.unit_cost > 0)
