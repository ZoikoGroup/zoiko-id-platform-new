"""Stripe webhook rules (CR-040): who may call, and what an event means. No network, no database.

An event is trusted only when its `Stripe-Signature` header carries a valid HMAC-SHA256 over `<timestamp>.<raw body>`
made with the endpoint's signing secret and the timestamp is recent. Nothing in the body is read before that."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any

TOLERANCE_SECONDS = 300
MAX_BODY_BYTES = 262_144


class WebhookError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class StripeEvent:
    event_id: str
    event_type: str
    customer_ref: str | None
    object_id: str | None
    invoice_id: str | None
    amount_minor: int | None
    currency: str | None
    data: dict[str, Any]
    livemode: bool = False
    merchant_ref: str = ""


def verify_signature(
    raw: bytes,
    header: str | None,
    secrets: list[str],
    *,
    now: float,
    tolerance: int = TOLERANCE_SECONDS,
) -> None:
    """Raises `WebhookError` unless one `v1` signature in the header matches one of the signing secrets (two during a
    secret roll) and the timestamp is within `tolerance` seconds."""
    if not header or not secrets:
        raise WebhookError("SIGNATURE_MISSING")
    timestamp: str | None = None
    signatures: list[str] = []
    for part in header.split(","):
        key, _, value = part.strip().partition("=")
        if key == "t":
            timestamp = value
        elif key == "v1":
            signatures.append(value)
    if timestamp is None or not timestamp.isdigit() or not signatures:
        raise WebhookError("SIGNATURE_MALFORMED")
    signed = timestamp.encode() + b"." + raw
    expected = [hmac.new(s.encode(), signed, hashlib.sha256).hexdigest() for s in secrets]
    if not any(hmac.compare_digest(e, got) for e in expected for got in signatures):
        raise WebhookError("SIGNATURE_INVALID")
    if abs(now - int(timestamp)) > tolerance:
        raise WebhookError("SIGNATURE_STALE")


def parse_event(raw: bytes) -> StripeEvent:
    """The few fields the platform acts on, read only after `verify_signature`."""
    try:
        body = json.loads(raw)
        obj = body["data"]["object"]
        event_id = str(body["id"])
        event_type = str(body["type"])
    except (ValueError, KeyError, TypeError) as e:
        raise WebhookError("EVENT_MALFORMED") from e
    if not isinstance(obj, dict):
        raise WebhookError("EVENT_MALFORMED")
    customer = obj.get("customer")
    if isinstance(customer, dict):
        customer = customer.get("id")
    raw_meta = obj.get("metadata")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    amount = obj.get("amount_refunded") if event_type == "charge.refunded" else obj.get("amount")
    return StripeEvent(
        event_id=event_id,
        event_type=event_type,
        customer_ref=str(customer) if customer else None,
        object_id=str(obj["id"]) if obj.get("id") else None,
        invoice_id=str(meta["invoice_id"]) if meta.get("invoice_id") else None,
        amount_minor=int(amount) if isinstance(amount, int) else None,
        currency=str(obj["currency"]).upper() if obj.get("currency") else None,
        data=obj,
        livemode=bool(body.get("livemode")),
        merchant_ref=str(body.get("account") or ""),
    )


# What each event type asks of the platform. A refund or a dispute moves money the ledger must account for through
# a credit note or a decision by a person, so it is recorded for review and never applied automatically.
PAYMENT_SUCCEEDED = "payment_intent.succeeded"
PAYMENT_FAILED = "payment_intent.payment_failed"
REVIEW_TYPES = frozenset(
    {
        "charge.refunded",
        "charge.dispute.created",
        "charge.dispute.closed",
        "charge.dispute.funds_withdrawn",
    }
)
