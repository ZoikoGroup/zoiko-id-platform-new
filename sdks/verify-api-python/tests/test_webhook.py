"""Webhook verification against deliveries signed here the way the platform signs them (RFC 9421, RFC 9530)."""

from __future__ import annotations

import base64
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, ed25519
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from zoiko_verify import WebhookError, content_digest, verify_webhook
from zoiko_verify.webhook import SIGNED_COMPONENTS

URL = "https://partner.example.com/zoikoid-webhooks"
BODY = b'{"id":"evt_1","type":"verification.api.session_decided"}'
NOW = 1_800_000_000


def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def ed_key(kid: str = "zid-whk-1") -> tuple[ed25519.Ed25519PrivateKey, dict[str, str]]:
    from cryptography.hazmat.primitives import serialization

    key = ed25519.Ed25519PrivateKey.generate()
    x = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return key, {"kty": "OKP", "crv": "Ed25519", "x": b64u(x), "kid": kid}


def sign(
    key: Any,
    *,
    body: bytes = BODY,
    url: str = URL,
    key_id: str = "zid-whk-1",
    created: int = NOW,
    event_id: str = "evt_1",
) -> dict[str, str]:
    values = {
        "@method": "POST",
        "@authority": url.split("/")[2].lower(),
        "@target-uri": url,
        "content-type": "application/cloudevents+json",
        "content-digest": content_digest(body),
        "x-zoiko-event-id": event_id,
    }
    params = "(" + " ".join(f'"{c}"' for c in SIGNED_COMPONENTS) + f');created={created};keyid="{key_id}"'
    base = "\n".join(f'"{c}": {values[c]}' for c in SIGNED_COMPONENTS) + f'\n"@signature-params": {params}'
    if isinstance(key, ed25519.Ed25519PrivateKey):
        sig = key.sign(base.encode())
    else:
        r, s = decode_dss_signature(key.sign(base.encode(), ec.ECDSA(hashes.SHA256())))
        sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return {
        "Content-Type": "application/cloudevents+json",
        "Content-Digest": values["content-digest"],
        "Signature-Input": f"zid={params}",
        "Signature": "zid=:" + base64.b64encode(sig).decode() + ":",
        "X-Zoiko-Event-ID": event_id,
    }


def code(**kw: Any) -> str:
    with pytest.raises(WebhookError) as e:
        verify_webhook(**kw)
    return e.value.code


def test_a_genuine_delivery_returns_its_event_id() -> None:
    key, jwk = ed_key()
    assert verify_webhook(url=URL, body=BODY, headers=sign(key), keys=[jwk], now=NOW + 10) == "evt_1"
    # header names are case-insensitive, as HTTP's are
    lowered = {k.lower(): v for k, v in sign(key).items()}
    assert verify_webhook(url=URL, body=BODY, headers=lowered, keys=[jwk], now=NOW) == "evt_1"


def test_an_es256_delivery_verifies_too() -> None:
    key = ec.generate_private_key(ec.SECP256R1())
    n = key.public_key().public_numbers()
    jwk = {
        "kty": "EC",
        "crv": "P-256",
        "x": b64u(n.x.to_bytes(32, "big")),
        "y": b64u(n.y.to_bytes(32, "big")),
        "kid": "zid-whk-1",
    }
    assert verify_webhook(url=URL, body=BODY, headers=sign(key), keys=[jwk], now=NOW) == "evt_1"


def test_every_way_a_delivery_can_be_wrong_is_refused() -> None:
    key, jwk = ed_key()
    good = sign(key)
    common: dict[str, Any] = {"url": URL, "keys": [jwk], "now": NOW}
    assert code(body=BODY, headers={}, **common) == "WEBHOOK_SIGNATURE_MISSING"
    assert code(body=BODY, headers={**good, "Signature-Input": "garbage"}, **common) == "WEBHOOK_SIGNATURE_MALFORMED"
    assert code(body=BODY + b" ", headers=good, **common) == "WEBHOOK_DIGEST_MISMATCH"  # a changed body
    assert code(body=BODY, headers=good, url=URL, keys=[jwk], now=NOW + 301) == "WEBHOOK_SIGNATURE_STALE"
    assert code(body=BODY, headers=good, url=URL, keys=[jwk], now=NOW - 301) == "WEBHOOK_SIGNATURE_STALE"
    assert code(body=BODY, headers=sign(key, key_id="retired"), **common) == "WEBHOOK_KEY_UNKNOWN"
    assert (
        code(body=BODY, headers={**good, "X-Zoiko-Event-ID": "evt_2"}, **common) == "WEBHOOK_SIGNATURE_INVALID"
    )  # the event id is signed
    assert (
        code(body=BODY, headers=good, url="https://evil.example.com/zoikoid-webhooks", keys=[jwk], now=NOW)
        == "WEBHOOK_SIGNATURE_INVALID"
    )  # another address
    other, _ = ed_key()
    assert code(body=BODY, headers=sign(other), **common) == "WEBHOOK_SIGNATURE_INVALID"  # signed by someone else
    # a signature that covers fewer parts than the full set is not accepted
    short = {**good, "Signature-Input": good["Signature-Input"].replace('"x-zoiko-event-id"', "").replace("  ", " ")}
    assert code(body=BODY, headers=short, **common) == "WEBHOOK_SIGNATURE_MALFORMED"


def test_a_rotation_overlap_verifies_with_whichever_published_key_signed_it() -> None:
    old_key, old_jwk = ed_key("zid-whk-old")
    new_key, new_jwk = ed_key("zid-whk-new")
    keys = [new_jwk, old_jwk]
    assert (
        verify_webhook(url=URL, body=BODY, headers=sign(old_key, key_id="zid-whk-old"), keys=keys, now=NOW) == "evt_1"
    )
    assert (
        verify_webhook(url=URL, body=BODY, headers=sign(new_key, key_id="zid-whk-new"), keys=keys, now=NOW) == "evt_1"
    )
    assert (
        code(url=URL, body=BODY, headers=sign(old_key, key_id="zid-whk-old"), keys=[new_jwk], now=NOW)
        == "WEBHOOK_KEY_UNKNOWN"
    )  # the old one is retired
