"""Stripe webhook rules (CR-040): the signature is the only credential, so every way of getting it wrong is refused."""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from zid_platform.modules.commercial.domain import stripe_webhook as hook

SECRET = "whsec_unit_current"
NOW = 1_800_000_000


def sign(raw: bytes, *, secret: str = SECRET, at: int = NOW) -> str:
    mac = hmac.new(secret.encode(), str(at).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return f"t={at},v1={mac}"


def event(kind: str = "payment_intent.succeeded", **obj: object) -> bytes:
    body = {"id": "evt_1ABC234", "type": kind, "data": {"object": {"id": "pi_1", **obj}}}
    return json.dumps(body).encode()


def test_a_correct_signature_passes() -> None:
    raw = event()
    hook.verify_signature(raw, sign(raw), [SECRET], now=NOW + 10)


@pytest.mark.parametrize(
    ("header", "code"),
    [
        (None, "SIGNATURE_MISSING"),
        ("", "SIGNATURE_MISSING"),
        ("v1=abcd", "SIGNATURE_MALFORMED"),
        ("t=notanumber,v1=abcd", "SIGNATURE_MALFORMED"),
        (f"t={NOW}", "SIGNATURE_MALFORMED"),
        (f"t={NOW},v1={'0' * 64}", "SIGNATURE_INVALID"),
    ],
)
def test_a_missing_malformed_or_wrong_signature_is_refused(header: str | None, code: str) -> None:
    with pytest.raises(hook.WebhookError) as e:
        hook.verify_signature(event(), header, [SECRET], now=NOW)
    assert e.value.code == code


def test_a_changed_body_or_another_secret_is_refused() -> None:
    raw = event()
    with pytest.raises(hook.WebhookError):
        hook.verify_signature(raw + b" ", sign(raw), [SECRET], now=NOW)
    with pytest.raises(hook.WebhookError):
        hook.verify_signature(raw, sign(raw, secret="whsec_other"), [SECRET], now=NOW)
    with pytest.raises(hook.WebhookError):
        hook.verify_signature(raw, sign(raw), [], now=NOW)


def test_an_old_signature_is_refused_so_a_captured_request_cannot_be_replayed() -> None:
    raw = event()
    header = sign(raw)
    hook.verify_signature(raw, header, [SECRET], now=NOW + hook.TOLERANCE_SECONDS)
    with pytest.raises(hook.WebhookError) as e:
        hook.verify_signature(raw, header, [SECRET], now=NOW + hook.TOLERANCE_SECONDS + 1)
    assert e.value.code == "SIGNATURE_STALE"


def test_either_secret_works_during_a_roll_and_any_v1_in_the_header_may_match() -> None:
    raw = event()
    hook.verify_signature(raw, sign(raw, secret="whsec_old"), ["whsec_new", "whsec_old"], now=NOW)
    both = sign(raw, secret="whsec_wrong") + "," + sign(raw).split(",", 1)[1]
    hook.verify_signature(raw, both, [SECRET], now=NOW)


def test_the_event_is_reduced_to_the_fields_the_platform_acts_on() -> None:
    raw = event(
        customer="cus_X",
        amount=1250,
        currency="usd",
        metadata={"invoice_id": "inv-1"},
    )
    e = hook.parse_event(raw)
    assert (e.event_id, e.event_type, e.customer_ref, e.object_id) == (
        "evt_1ABC234",
        "payment_intent.succeeded",
        "cus_X",
        "pi_1",
    )
    assert (e.invoice_id, e.amount_minor, e.currency) == ("inv-1", 1250, "USD")


def test_a_refund_reads_the_refunded_amount() -> None:
    e = hook.parse_event(event("charge.refunded", amount=5000, amount_refunded=700, currency="gbp"))
    assert e.amount_minor == 700


@pytest.mark.parametrize("raw", [b"not json", b"{}", b'{"id":"e","type":"t","data":{"object":1}}'])
def test_a_body_that_is_not_an_event_is_malformed(raw: bytes) -> None:
    with pytest.raises(hook.WebhookError) as e:
        hook.parse_event(raw)
    assert e.value.code == "EVENT_MALFORMED"
