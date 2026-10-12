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
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
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
