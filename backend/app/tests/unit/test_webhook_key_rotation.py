"""Webhook signing-key rotation and the receiver's verification (ZID-EVENTS-001 EVT-24; RFC 9421).

During a rotation both keys are published for a bounded overlap, so a receiver that has not refreshed yet still
verifies. Once the old key retires, a delivery naming it is refused. Nothing unsigned is ever accepted, before,
during or after a rotation.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from zid_crypto import LocalSigner, verify_signature
from zid_platform.main import create_app
from zid_platform.modules.webhook.domain.webhooks import (
    SigningKeys,
    WebhookError,
    build_signing_keys,
    sign_request,
    verify_request,
)
from zid_platform.settings import Settings

URL = "https://hooks.example.com/zoiko"
BODY = b'{"id":"019f","type":"com.zoiko.id.mandate.revoked.v1"}'
NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
T0 = int(NOW.timestamp())


def deliver(signer: LocalSigner, *, created: int = T0, body: bytes = BODY) -> dict[str, str]:
    return sign_request(
        url=URL,
        body=body,
        event_id="019f",
        delivery_id="d1",
        key_id=signer.kid,
        created=created,
        sign=signer.sign,
    ).headers


def check(headers: dict[str, str], keys: list[Any], *, now: int = T0, body: bytes = BODY) -> str:
    return verify_request(
        url=URL, body=body, headers=headers, keys=keys, now=now, verify=verify_signature
    )


def code_of(headers: dict[str, str], keys: list[Any], **kw: Any) -> str:
    with pytest.raises(WebhookError) as e:
        check(headers, keys, **kw)
    return e.value.code


def test_a_receiver_verifies_with_the_old_and_the_new_key_during_the_overlap_and_only_the_new_one_after() -> (
    None
):
    """EVT-24: both keys verify while both are published; at retire time the old keyid is simply unknown."""
    old, new = LocalSigner("whk-2026-10"), LocalSigner("whk-2026-11")
    retire_at = NOW + timedelta(days=7)
    keys = build_signing_keys(
        new.public_jwk(), [{"jwk": old.public_jwk(), "retire_at": retire_at.isoformat()}], now=NOW
    )
    during = keys.published(NOW + timedelta(days=1))
    assert [k["kid"] for k in during] == ["whk-2026-11", "whk-2026-10"]
    assert (
        check(deliver(old), during) == "019f"
    )  # a delivery signed before the switch still verifies
    assert check(deliver(new), during) == "019f"
    after = keys.published(retire_at)  # the retire time itself is already past the overlap
    assert [k["kid"] for k in after] == ["whk-2026-11"]
    assert code_of(deliver(old), after) == "WEBHOOK_KEY_UNKNOWN"  # the old key retires
    assert check(deliver(new), after) == "019f"


def test_nothing_unsigned_or_tampered_is_accepted_at_any_point_of_a_rotation() -> None:
    """EVT-24: no unsigned fallback. Missing, stale, altered, wrong-key and malformed deliveries are refused."""
    old, new = LocalSigner("whk-old"), LocalSigner("whk-new")
    keys = [new.public_jwk(), old.public_jwk()]
    good = deliver(new)
    assert check(good, keys) == "019f"
    unsigned = {k: v for k, v in good.items() if k.lower() not in {"signature", "signature-input"}}
    assert code_of(unsigned, keys) == "WEBHOOK_SIGNATURE_MISSING"
    assert code_of({**good, "Signature": ""}, keys) == "WEBHOOK_SIGNATURE_MISSING"
    assert code_of(good, keys, now=T0 + 301) == "WEBHOOK_SIGNATURE_STALE"
    assert code_of(deliver(new, created=T0 - 400), keys) == "WEBHOOK_SIGNATURE_STALE"
    assert code_of(good, keys, body=BODY + b" ") == "WEBHOOK_DIGEST_MISMATCH"
    forged = {**good, "Signature": "zid=:" + base64.b64encode(b"x" * 64).decode() + ":"}
    assert code_of(forged, keys) == "WEBHOOK_SIGNATURE_INVALID"
    stranger = LocalSigner("whk-new")  # same keyid, a different key: not the one that is published
    assert code_of(deliver(stranger), keys) == "WEBHOOK_SIGNATURE_INVALID"
    assert code_of({**good, "Signature-Input": "garbage"}, keys) == "WEBHOOK_SIGNATURE_MALFORMED"
    assert code_of(good, []) == "WEBHOOK_KEY_UNKNOWN"  # no keys published: nothing verifies


def test_a_rotation_cannot_overlap_a_key_with_itself_or_for_longer_than_thirty_days() -> None:
    """EVT-24: the overlap is bounded and the retiring key has its own identity."""
    current, old = LocalSigner("whk-new").public_jwk(), LocalSigner("whk-old").public_jwk()
    far = (NOW + timedelta(days=31)).isoformat()
    for entry in (
        {"jwk": old, "retire_at": far},  # too long
        {"jwk": current, "retire_at": (NOW + timedelta(days=1)).isoformat()},  # the same key
        {"jwk": old, "retire_at": "not-a-time"},
        {"jwk": old, "retire_at": "2026-11-05T00:00:00"},  # no timezone
        {"jwk": {"kty": "EC"}, "retire_at": (NOW + timedelta(days=1)).isoformat()},  # no kid
    ):
        with pytest.raises(WebhookError) as e:
            build_signing_keys(current, [entry], now=NOW)
        assert e.value.code == "WEBHOOK_KEY_INVALID"
    ok = build_signing_keys(
        current, [{"jwk": old, "retire_at": (NOW + timedelta(days=30)).isoformat()}], now=NOW
    )
    assert isinstance(ok, SigningKeys) and len(ok.retiring) == 1


def test_the_signing_keys_endpoint_publishes_the_current_and_the_unexpired_retiring_keys() -> None:
    """EVT-24: receivers fetch the published set; the retiring key is listed until it retires."""
    old = LocalSigner("whk-old").public_jwk()
    soon = (datetime.now(UTC) + timedelta(days=5)).isoformat()
    app = create_app(Settings(webhook_retiring_keys=[{"jwk": old, "retire_at": soon}]))
    with TestClient(app) as client:
        keys = client.get("/v1/webhooks/signing-keys").json()["keys"]
    kids = [k["kid"] for k in keys]
    assert len(kids) == 2 and "whk-old" in kids and "d" not in {m for k in keys for m in k}
    expired = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    app = create_app(Settings(webhook_retiring_keys=[{"jwk": old, "retire_at": expired}]))
    with TestClient(app) as client:
        kids = [k["kid"] for k in client.get("/v1/webhooks/signing-keys").json()["keys"]]
    assert "whk-old" not in kids and len(kids) == 1
