"""Webhook delivery rules (ZID-EVENTS-001 §14-20; FR-API-007). Pure domain, no I/O.

- Endpoint safety (§15): HTTPS only, no userinfo/fragment, standard ports; every resolved address is
  checked at registration AND before each delivery (DNS rebinding), blocking loopback, link-local
  (incl. cloud metadata), private, shared/CGNAT, multicast, reserved and unspecified ranges.
- Signing (§16): RFC 9421 HTTP Message Signatures over method, authority, target URI, content type,
  RFC 9530 Content-Digest and the event id, with `created` and an explicit `keyid`. The key itself is
  held by the crypto broker (DEC-F5); this module only builds the signature base.
- Delivery (§18-19): state machine, response classification, the default retry ladder with bounded
  full jitter, and Retry-After honoured up to the platform maximum. Redirects are never followed.
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import random
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from urllib.parse import urlsplit

ALLOWED_PORTS = frozenset({None, 443, 8443})
SHARED_RANGES = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("192.0.0.0/24"))
RETRY_LADDER = (
    timedelta(0),
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
    timedelta(hours=12),
    timedelta(hours=24),
    timedelta(hours=48),
    timedelta(hours=72),
)
MAX_RETRY_AFTER = timedelta(hours=1)
SIGNED_COMPONENTS = (
    "@method",
    "@authority",
    "@target-uri",
    "content-type",
    "content-digest",
    "x-zoiko-event-id",
)


class WebhookError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def validate_url(url: str, *, allow_http: bool = False) -> str:
    """Normalize and check an endpoint URL (no DNS here; see `address_allowed`)."""
    parts = urlsplit(url.strip())
    if parts.scheme != "https" and not (allow_http and parts.scheme == "http"):
        raise WebhookError("WEBHOOK_URL_INVALID", "HTTPS is required")
    if parts.username or parts.password or parts.fragment or not parts.hostname:
        raise WebhookError("WEBHOOK_URL_INVALID", "no userinfo or fragment; a host is required")
    if parts.port not in ALLOWED_PORTS and not allow_http:
        raise WebhookError("WEBHOOK_URL_INVALID", "unsupported port")
    if _is_ip_literal(parts.hostname):
        raise WebhookError("WEBHOOK_URL_INVALID", "use a DNS name, not a literal IP")
    host = parts.hostname.lower()
    if (
        host.rsplit(".", 1)[-1].isdigit()
        or host.startswith("0x")
        or host.endswith((".local", ".internal"))
    ):
        # decimal/hex IP spellings and internal-only names (SEC-19); resolution is still checked
        raise WebhookError("WEBHOOK_URL_INVALID", "numeric or internal host name")
    return parts._replace(netloc=parts.netloc.lower()).geturl()


def _is_ip_literal(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return False
    return True


def address_allowed(address: str) -> bool:
    ip = ipaddress.ip_address(address)
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    blocked = (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or any(ip in net for net in SHARED_RANGES if ip.version == net.version)
    )
    return not blocked


def check_resolution(addresses: Iterable[str]) -> None:
    """Every resolved address must be public: one bad record poisons the whole name (rebinding)."""
    addrs = list(addresses)
    if not addrs or not all(address_allowed(a) for a in addrs):
        raise WebhookError("WEBHOOK_DESTINATION_BLOCKED")


# ---------------------------------------------------------------- signing (RFC 9421 / RFC 9530)


def content_digest(body: bytes) -> str:
    return "sha-256=:" + base64.b64encode(hashlib.sha256(body).digest()).decode() + ":"


@dataclass(frozen=True, slots=True)
class SignedRequest:
    headers: dict[str, str]
    signature_base: str


def sign_request(
    *,
    url: str,
    body: bytes,
    event_id: str,
    delivery_id: str,
    key_id: str,
    created: int,
    sign: Callable[[bytes], bytes],
    content_type: str = "application/cloudevents+json",
) -> SignedRequest:
    parts = urlsplit(url)
    values = {
        "@method": "POST",
        "@authority": parts.netloc.lower(),
        "@target-uri": url,
        "content-type": content_type,
        "content-digest": content_digest(body),
        "x-zoiko-event-id": event_id,
    }
    params = (
        "("
        + " ".join(f'"{c}"' for c in SIGNED_COMPONENTS)
        + f');created={created};keyid="{key_id}"'
    )
    base = (
        "\n".join(f'"{c}": {values[c]}' for c in SIGNED_COMPONENTS)
        + f'\n"@signature-params": {params}'
    )
    signature = base64.b64encode(sign(base.encode())).decode()
    headers = {
        "Content-Type": content_type,
        "Content-Digest": values["content-digest"],
        "Signature-Input": f"zid={params}",
        "Signature": f"zid=:{signature}:",
        "X-Zoiko-Event-ID": event_id,
        "X-Zoiko-Delivery-ID": delivery_id,
    }
    return SignedRequest(headers, base)


# ---------------------------------------------------------------- signing keys and rotation (EVT-24)

MAX_KEY_OVERLAP = timedelta(days=30)
MAX_SIGNATURE_AGE = (
    300  # seconds a receiver accepts a signature's `created` for (stale or replayed copies fail)
)


@dataclass(frozen=True, slots=True)
class SigningKeys:
    """The public keys a receiver may verify with: the key that signs now, and retiring keys that are still
    published until their retire time, so a receiver mid-rotation keeps working. A retired key is gone: a
    delivery naming it is refused, never accepted on a fallback."""

    current: Mapping[str, Any]
    retiring: tuple[tuple[Mapping[str, Any], datetime], ...] = ()

    def published(self, now: datetime) -> list[Mapping[str, Any]]:
        return [self.current, *(jwk for jwk, retire_at in self.retiring if retire_at > now)]


def build_signing_keys(
    current: Mapping[str, Any],
    retiring: Iterable[Mapping[str, Any]],
    *,
    now: datetime,
) -> SigningKeys:
    """`retiring`: `{"jwk": {...}, "retire_at": ISO-8601}`. The overlap is bounded and a key never overlaps itself."""
    out: list[tuple[Mapping[str, Any], datetime]] = []
    for entry in retiring:
        jwk, raw = entry.get("jwk"), entry.get("retire_at")
        try:
            retire_at = datetime.fromisoformat(str(raw))
        except ValueError as e:
            raise WebhookError("WEBHOOK_KEY_INVALID", "retire_at must be an ISO-8601 time") from e
        if (
            not isinstance(jwk, Mapping)
            or not jwk.get("kid")
            or jwk.get("kid") == current.get("kid")
        ):
            raise WebhookError("WEBHOOK_KEY_INVALID", "a retiring key needs its own kid")
        if retire_at.tzinfo is None or retire_at - now > MAX_KEY_OVERLAP:
            raise WebhookError("WEBHOOK_KEY_INVALID", "the overlap is at most 30 days")
        out.append((jwk, retire_at))
    return SigningKeys(current, tuple(out))


def verify_request(
    *,
    url: str,
    body: bytes,
    headers: Mapping[str, str],
    keys: Sequence[Mapping[str, Any]],
    now: int,
    verify: Callable[[Mapping[str, Any], bytes, bytes], None],
    max_age: int = MAX_SIGNATURE_AGE,
) -> str:
    """The receiver's check of a delivery: returns the event id when the signature verifies.

    Refuses an unsigned delivery, a stale `created`, a body that is not the signed digest, a `keyid` that is not in
    the published set (a retired key included), and a signature that does not verify. There is no unsigned path.

    `verify(jwk, data, signature)` raises when the signature is wrong (`zid_crypto.verify_signature`); it is passed
    in so the domain stays free of I/O and of the crypto library."""
    h = {k.lower(): v for k, v in headers.items()}
    signature_input, signature = h.get("signature-input"), h.get("signature")
    if not signature_input or not signature:
        raise WebhookError("WEBHOOK_SIGNATURE_MISSING")
    match = re.fullmatch(r'zid=(\(.*\));created=(\d+);keyid="([^"]+)"', signature_input)
    if match is None or not signature.startswith("zid=:") or not signature.endswith(":"):
        raise WebhookError("WEBHOOK_SIGNATURE_MALFORMED")
    components, created, key_id = match.group(1), int(match.group(2)), match.group(3)
    if abs(now - created) > max_age:
        raise WebhookError("WEBHOOK_SIGNATURE_STALE")
    if h.get("content-digest") != content_digest(body):
        raise WebhookError("WEBHOOK_DIGEST_MISMATCH")
    jwk = next((k for k in keys if k.get("kid") == key_id), None)
    if jwk is None:
        raise WebhookError("WEBHOOK_KEY_UNKNOWN")
    values = {
        "@method": "POST",
        "@authority": urlsplit(url).netloc.lower(),
        "@target-uri": url,
        "content-type": h.get("content-type", ""),
        "content-digest": h["content-digest"],
        "x-zoiko-event-id": h.get("x-zoiko-event-id", ""),
    }
    base = (
        "\n".join(f'"{c}": {values[c]}' for c in SIGNED_COMPONENTS)
        + f'\n"@signature-params": {components};created={created};keyid="{key_id}"'
    )
    try:
        verify(jwk, base.encode(), base64.b64decode(signature[len("zid=:") : -1]))
    except Exception as e:
        raise WebhookError("WEBHOOK_SIGNATURE_INVALID") from e
    return values["x-zoiko-event-id"]


# ---------------------------------------------------------------- delivery state machine (§18-19)


class DeliveryStatus(StrEnum):
    QUEUED = "QUEUED"
    IN_FLIGHT = "IN_FLIGHT"
    RETRY_SCHEDULED = "RETRY_SCHEDULED"
    DELIVERED = "DELIVERED"
    FAILED = "FAILED"
    EXHAUSTED = "EXHAUSTED"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    TRANSIENT = "TRANSIENT"
    CONTRACT = "CONTRACT"  # 400/404/422: bounded retries, then fail
    RECEIVER_AUTH = "RECEIVER_AUTH"  # 401/403: bounded retries + alert
    GONE = "GONE"  # 410: disable endpoint
    REDIRECT = "REDIRECT"  # 3xx: configuration failure, never followed


BOUNDED_RETRIES = {Outcome.CONTRACT: 3, Outcome.RECEIVER_AUTH: 3}


def classify(status_code: int | None) -> Outcome:
    """None = network/timeout (transient). Certificate policy failures are raised before this point."""
    if status_code is None or status_code in (408, 425, 429) or 500 <= status_code <= 599:
        return Outcome.TRANSIENT
    if 200 <= status_code <= 299:
        return Outcome.SUCCESS
    if status_code == 410:
        return Outcome.GONE
    if 300 <= status_code <= 399:
        return Outcome.REDIRECT
    if status_code in (401, 403):
        return Outcome.RECEIVER_AUTH
    return Outcome.CONTRACT


@dataclass(frozen=True, slots=True)
class NextStep:
    status: DeliveryStatus
    next_attempt_at: datetime | None
    disable_endpoint: bool = False


def next_step(
    outcome: Outcome,
    *,
    attempt: int,
    now: datetime,
    retry_after: timedelta | None = None,
    rng: random.Random | None = None,
) -> NextStep:
    """`attempt` = attempts made so far (1 after the first send)."""
    if outcome is Outcome.SUCCESS:
        return NextStep(DeliveryStatus.DELIVERED, None)
    if outcome is Outcome.GONE:
        return NextStep(DeliveryStatus.FAILED, None, disable_endpoint=True)
    if outcome is Outcome.REDIRECT:
        return NextStep(DeliveryStatus.FAILED, None)
    limit = BOUNDED_RETRIES.get(outcome, len(RETRY_LADDER))
    if attempt >= limit:
        return NextStep(
            DeliveryStatus.EXHAUSTED if outcome is Outcome.TRANSIENT else DeliveryStatus.FAILED,
            None,
        )
    base = RETRY_LADDER[min(attempt, len(RETRY_LADDER) - 1)]
    delay = timedelta(
        seconds=(rng or random.Random()).uniform(0, base.total_seconds())
    )  # full jitter
    if retry_after is not None:
        delay = max(delay, min(retry_after, MAX_RETRY_AFTER))
    return NextStep(DeliveryStatus.RETRY_SCHEDULED, now + delay)


# ---------------------------------------------------------------- subscribable event types (§17)

# Event types an endpoint may subscribe to (API-001 §17, EVENTS-001 §17 "exact allowlisted event
# types"): integration-facing facts only. Internal/evidence/operator events are never delivered.
SUBSCRIBABLE = {
    "com.zoiko.id.principal.security_revision.changed.v1": "Security-critical principal state",
    "com.zoiko.id.organization.status_changed.v1": "Organization status change",
    "com.zoiko.id.authority_root.activated.v1": "Authority root activated",
    "com.zoiko.id.authority_root.stale.v1": "Authority root became stale",
    "com.zoiko.id.authority_root.revoked.v1": "Authority root revoked",
    "com.zoiko.id.relationship.edge.changed.v1": "Relationship added, ended or superseded",
    "com.zoiko.id.authority.grant.activated.v1": "Authority grant active",
    "com.zoiko.id.authority.grant.revoked.v1": "Authority grant revoked",
    "com.zoiko.id.mandate.created.v1": "Mandate created",
    "com.zoiko.id.mandate.reservation.created.v1": "Consumable reservation",
    "com.zoiko.id.mandate.reservation.committed.v1": "Reservation committed",
    "com.zoiko.id.mandate.revoked.v1": "Mandate revoked",
    "com.zoiko.id.verification.completed.v1": "Verification final",
    "com.zoiko.id.eligibility.recomputed.v1": "A person's eligibility or engagement decision changed from the previous one",
    "com.zoiko.id.property.authority_changed.v1": "The authority answer for a property and host changed from the previous check",
    "com.zoiko.id.provider.entitlement_revoked.v1": "A provider entitlement was revoked and the path it enabled is closed",
    "com.zoiko.id.privacy.review.decided.v1": "An appeal or human review of an automated decision was decided by an independent person",
    "com.zoiko.id.assurance.case.expired.v1": "A UAT case's independent PASS no longer counts because an input it was pinned to changed",
    "com.zoiko.id.assurance.release.decided.v1": "A release role signed a decision and the release candidate's status was recomputed",
    "com.zoiko.id.assurance.release.rolled_back.v1": "A release scope was disabled by a release role holder",
    "com.zoiko.id.assurance.evidence.tampered.v1": "Signed UAT evidence failed verification and the releases built on it were locked",
    "com.zoiko.id.evidence.retention_action.v1": "Uploaded evidence was deleted or expired and its bytes erased",
    "com.zoiko.id.verification.idv.case_decided.v1": "An identity proofing case reached a decision (case id, decision, reason codes, the signal states and what may be asserted, policy version; no document or person data)",
    "com.zoiko.id.organization.kyb.assertion_issued.v1": "A company reached a verification grade it did not hold (grade, claims gained, next check; no company data)",
    "com.zoiko.id.organization.kyb.assertion_revoked.v1": "Claims a company held are no longer proven (grade now, claims revoked, reason codes; no company data)",
    "com.zoiko.id.verification.case.completed.v1": "A verification case finished: each check has its own outcome",
    "com.zoiko.id.screening.case.outcome_changed.v1": "A screening case moved between in review and complete (case id, market, outcome, revision; no name, list, candidate or reason: fetch the case for the calibrated status)",
    "com.zoiko.id.verification.api.session_decided.v1": "A verification started through your Verify API application got a decision (session id, outcome, checks passed or failed, reason codes, decision version and the one it replaces; no document, photograph or score)",
    "com.zoiko.id.verification.api.session_closed.v1": "A verification started through your Verify API application ended without a decision: canceled or ran out of time",
    "com.zoiko.id.credential.status.changed.v1": "Credential status update",
    "com.zoiko.id.credential.status_changed.v1": "Credential claim status changed (a source revoked, suspended or expired a checked claim)",
    "com.zoiko.id.privacy.consent.withdrawn.v1": "Consent withdrawn",
    "com.zoiko.id.policy.release.changed.v1": "Policy release drafted, reviewed or published",
}


def check_event_types(types: Iterable[str]) -> list[str]:
    chosen = sorted(set(types))
    if not chosen:
        raise WebhookError("WEBHOOK_EVENT_NOT_ALLOWED", "subscribe to at least one event type")
    unknown = [t for t in chosen if t not in SUBSCRIBABLE]
    if unknown:
        raise WebhookError("WEBHOOK_EVENT_NOT_ALLOWED", ", ".join(unknown))
    return chosen
