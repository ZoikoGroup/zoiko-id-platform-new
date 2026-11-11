"""The provider adapter contract and its guards (ZID-INT-002 section 08). Pure rules; NO adapter for any real provider exists.

What is here is the part that does not depend on any vendor: the shape every adapter must have, and the guards that sit in front of
every adapter so that a vendor can never widen what the platform will do.

- `entitlement_refusals` / `route`: geographic legality and the provider's exact entitlement outrank availability, speed and cost.
  An expired or revoked entitlement disables the path automatically (T17), and a failover that would LOWER the assurance is refused unless
  someone approved it (T18). Nothing is ever silently downgraded.
- `verify_webhook`: the exact received bytes, a timestamp window, a replay nonce, and the current and previous key during rotation (T16).
- `deletion_statement`: the platform never says a provider deleted data until the provider confirmed it.

A real adapter (Veriff, Yoti, Sumsub, a registry) needs a contract, an entitlement and a sandbox. None is available, so none was written:
a stub would imply a capability that does not exist.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Any, Protocol


class EntitlementStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class Entitlement:
    """What one provider may be used for: an exact service, places, purposes and processing regions, for a period."""

    provider_id: str
    service_code: str
    countries: frozenset[str]
    purposes: frozenset[str]
    processing_regions: frozenset[str]
    valid_from: date
    valid_until: date | None
    assurance: int  # higher is stronger; compared only between providers of the same service
    status: EntitlementStatus = EntitlementStatus.ACTIVE
    cost_rank: int = 0  # lower is cheaper; used only to choose among providers that are ALL lawful


@dataclass(frozen=True, slots=True)
class RouteRequest:
    country: str
    service_code: str
    purpose: str
    region: str  # where the data would be processed
    required_assurance: int


class Refusal(StrEnum):
    ENTITLEMENT_NOT_ACTIVE = "ENTITLEMENT_NOT_ACTIVE"
    ENTITLEMENT_NOT_IN_PERIOD = "ENTITLEMENT_NOT_IN_PERIOD"
    SERVICE_NOT_ENTITLED = "SERVICE_NOT_ENTITLED"
    COUNTRY_NOT_COVERED = "COUNTRY_NOT_COVERED"
    PURPOSE_NOT_PERMITTED = "PURPOSE_NOT_PERMITTED"
    REGION_NOT_PERMITTED = "REGION_NOT_PERMITTED"
    ASSURANCE_TOO_LOW = "ASSURANCE_TOO_LOW"
    FAILOVER_WOULD_LOWER_ASSURANCE = "FAILOVER_WOULD_LOWER_ASSURANCE"
    NO_PROVIDER = "NO_PROVIDER"


def entitlement_refusals(e: Entitlement, r: RouteRequest, now: datetime) -> list[Refusal]:
    """Every reason this provider cannot serve this request (empty means it is lawful to use). All are reported, not just the first."""
    out: list[Refusal] = []
    if e.service_code != r.service_code:
        return [Refusal.SERVICE_NOT_ENTITLED]
    if e.status is not EntitlementStatus.ACTIVE:
        out.append(Refusal.ENTITLEMENT_NOT_ACTIVE)
    today = now.astimezone(UTC).date()
    if today < e.valid_from or (e.valid_until is not None and today > e.valid_until):
        out.append(Refusal.ENTITLEMENT_NOT_IN_PERIOD)
    if r.country.upper() not in e.countries:
        out.append(Refusal.COUNTRY_NOT_COVERED)
    if r.purpose not in e.purposes:
        out.append(Refusal.PURPOSE_NOT_PERMITTED)
    if r.region not in e.processing_regions:
        out.append(Refusal.REGION_NOT_PERMITTED)
    if e.assurance < r.required_assurance:
        out.append(Refusal.ASSURANCE_TOO_LOW)
    return out


@dataclass(frozen=True, slots=True)
class RouteDecision:
    provider_id: str | None
    refusals: tuple[Refusal, ...] = ()
    considered: Mapping[str, tuple[Refusal, ...]] = field(default_factory=dict)
    approved_downgrade: str | None = None  # the approval that allowed a lower-assurance failover


def route(
    entitlements: Sequence[Entitlement],
    r: RouteRequest,
    now: datetime,
    *,
    failing_over_from: Entitlement | None = None,
    downgrade_approval_ref: str | None = None,
) -> RouteDecision:
    """Choose a provider. Legality first; then, among the lawful ones, the strongest assurance and then the cheapest.

    On a failover, a provider weaker than the one that failed is refused unless a named approval allows it: the platform never
    quietly reduces assurance because a provider is down."""
    considered = {e.provider_id: tuple(entitlement_refusals(e, r, now)) for e in entitlements}
    lawful = [e for e in entitlements if not considered[e.provider_id]]
    approved: str | None = None
    if failing_over_from is not None:
        lawful = [e for e in lawful if e.provider_id != failing_over_from.provider_id]
        stronger = [e for e in lawful if e.assurance >= failing_over_from.assurance]
        if stronger:
            lawful = stronger
        elif lawful:  # only weaker providers remain: never a quiet downgrade
            if not (downgrade_approval_ref or "").strip():
                return RouteDecision(None, (Refusal.FAILOVER_WOULD_LOWER_ASSURANCE,), considered)
            approved = downgrade_approval_ref
    if not lawful:
        reasons = sorted({x for refs in considered.values() for x in refs}, key=lambda x: x.value)
        return RouteDecision(None, tuple(reasons) or (Refusal.NO_PROVIDER,), considered)
    best = min(lawful, key=lambda e: (-e.assurance, e.cost_rank, e.provider_id))
    return RouteDecision(best.provider_id, (), considered, approved)


# ------------------------------------------------------------------------------------------------ webhooks


class WebhookRefusal(StrEnum):
    BAD_SIGNATURE = "BAD_SIGNATURE"
    STALE = "STALE"
    REPLAYED = "REPLAYED"
    MALFORMED = "MALFORMED"


@dataclass(frozen=True, slots=True)
class WebhookVerdict:
    ok: bool
    refusal: WebhookRefusal | None = None
    key_index: int | None = None  # 0 = the current key, 1 = the previous (rotation in progress)


def sign_webhook(secret: bytes, timestamp: int, nonce: str, raw: bytes) -> str:
    """The signature a provider adapter's counterpart computes: HMAC-SHA256 over timestamp, nonce and the EXACT bytes."""
    return hmac.new(secret, f"{timestamp}.{nonce}.".encode() + raw, hashlib.sha256).hexdigest()


def verify_webhook(
    raw: bytes,
    headers: Mapping[str, str],
    secrets: Sequence[bytes],
    *,
    now: datetime,
    seen_nonces: Collection[str],
    max_age: timedelta = timedelta(minutes=5),
) -> WebhookVerdict:
    """Verify exact bytes, timestamp window and replay nonce, accepting the current and the previous key (rotation).
    The caller records an accepted nonce so it cannot be used twice. A failure never mutates anything."""
    try:
        ts = int(headers["x-zid-timestamp"])
        nonce = headers["x-zid-nonce"]
        sig = headers["x-zid-signature"]
    except (KeyError, ValueError):
        return WebhookVerdict(False, WebhookRefusal.MALFORMED)
    if not nonce or len(nonce) > 128:
        return WebhookVerdict(False, WebhookRefusal.MALFORMED)
    for i, secret in enumerate(secrets[:2]):  # current, then previous
        if hmac.compare_digest(sign_webhook(secret, ts, nonce, raw), sig):
            age = abs((now - datetime.fromtimestamp(ts, UTC)).total_seconds())
            if age > max_age.total_seconds():
                return WebhookVerdict(False, WebhookRefusal.STALE)
            if nonce in seen_nonces:
                return WebhookVerdict(False, WebhookRefusal.REPLAYED)
            return WebhookVerdict(True, None, i)
    return WebhookVerdict(False, WebhookRefusal.BAD_SIGNATURE)


# ------------------------------------------------------------------------------------------------ cancellation


@dataclass(frozen=True, slots=True)
class CancelResult:
    cancelled: bool
    deletion_confirmed: bool  # only the provider can set this
    provider_ref: str | None = None


def deletion_statement(result: CancelResult) -> str:
    """What the platform may tell a person. It never says data was deleted until the provider confirmed it."""
    if not result.cancelled:
        return "The provider has not confirmed the cancellation."
    if result.deletion_confirmed:
        return "The provider confirmed the case was cancelled and its data deleted."
    return "The case was cancelled. The provider has not yet confirmed that its copy of the data is deleted."


# ------------------------------------------------------------------------------------------------ the contract


class ProviderAdapter(Protocol):
    """Section 08. An adapter returns normalized, provider-neutral results; vendor field names never reach a consumer model,
    and device or provider secrets stay server-side. Every call is preceded by `route` (entitlement, purpose, region)."""

    provider_id: str

    async def discover_capabilities(self, ctx: Mapping[str, Any]) -> Sequence[Entitlement]: ...

    async def create_case(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def request_capture(self, request: Mapping[str, Any]) -> Mapping[str, Any]: ...

    async def fetch_result(self, provider_ref: str) -> Mapping[str, Any]: ...

    async def cancel_or_revoke(self, case_ref: str) -> CancelResult: ...

    async def health_and_entitlement(self) -> Entitlement: ...
