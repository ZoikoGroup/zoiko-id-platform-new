"""The partner SDK against the platform's own code (CR-163): what the SDK signs, the platform accepts, and what the platform signs, the SDK accepts.

The SDK is a separate package with no dependency on the platform, so its tests cannot prove this alone. This one imports both and checks the byte-level agreement that matters: the RFC 9421 signature base of a webhook
delivery, and the claims and signature of a private-key client assertion.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from zid_crypto import LocalSigner, verify_external_jws, verify_signature
from zid_platform.modules.verify_api.domain import credential_options as opts
from zid_platform.modules.webhook.domain import webhooks as wh

SDK_SRC = Path(__file__).resolve().parents[4] / "sdks" / "verify-api-python" / "src"
sys.path.insert(0, str(SDK_SRC))
try:
    import zoiko_verify
    from zoiko_verify import VerifyClient, WebhookError, verify_webhook
    from zoiko_verify._jwt import b64url_decode
finally:
    sys.path.remove(str(SDK_SRC))

URL = "https://partner.example.com/zoikoid-webhooks"
BODY = (
    b'{"specversion":"1.0","id":"evt_1","type":"com.zoiko.id.verification.api.session_decided.v1"}'
)
NOW = 1_800_000_000


def delivery(signer: LocalSigner, **over: Any) -> dict[str, str]:
    signed = wh.sign_request(
        url=URL,
        body=over.get("body", BODY),
        event_id="evt_1",
        delivery_id="dlv_1",
        key_id=signer.kid,
        created=over.get("created", NOW),
        sign=signer.sign,
    )
    return signed.headers


def test_the_sdk_is_the_one_from_this_repository() -> None:
    assert Path(zoiko_verify.__file__).resolve().is_relative_to(SDK_SRC.resolve())


def test_a_delivery_the_platform_signs_verifies_in_the_sdk_and_the_sdk_refuses_what_the_platform_would() -> (
    None
):
    signer = LocalSigner("zid-whk-2026-11")
    jwk = signer.public_jwk()
    headers = delivery(signer)
    assert verify_webhook(url=URL, body=BODY, headers=headers, keys=[jwk], now=NOW + 5) == "evt_1"
    # the platform's own verifier and the SDK agree on every refusal
    for label, kw, expected in (
        ("tampered body", {"body": BODY + b" "}, "WEBHOOK_DIGEST_MISMATCH"),
        ("stale", {"now": NOW + 400}, "WEBHOOK_SIGNATURE_STALE"),
        ("unknown key", {"keys": []}, "WEBHOOK_KEY_UNKNOWN"),
    ):
        args: dict[str, Any] = {
            "url": URL,
            "body": BODY,
            "headers": headers,
            "keys": [jwk],
            "now": NOW,
            **kw,
        }
        with pytest.raises(WebhookError) as sdk:
            verify_webhook(**args)
        with pytest.raises(wh.WebhookError) as platform:
            wh.verify_request(
                url=URL,
                body=args["body"],
                headers=headers,
                keys=args["keys"],
                now=args["now"],
                verify=verify_signature,
            )
        assert sdk.value.code == platform.value.code == expected, label
    # and the other direction: a delivery the SDK accepts is one the platform's verifier accepts
    assert (
        wh.verify_request(
            url=URL, body=BODY, headers=headers, keys=[jwk], now=NOW, verify=verify_signature
        )
        == "evt_1"
    )


def test_a_client_assertion_the_sdk_signs_is_accepted_by_the_platforms_own_checks() -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    sent: list[dict[str, Any]] = []

    def transport(
        method: str, url: str, headers: Any, data: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        sent.append(json.loads(data) if data else {})
        return 200, json.dumps({"access_token": "zpt_1", "expires_in": 900}).encode()

    base = "https://id.example.test"
    client = VerifyClient(
        base,
        client_id="zpc_0123456789abcdef0123",
        private_key_pem=pem,
        key_id="k1",
        transport=transport,
        clock=lambda: float(NOW),
    )
    client.whoami()
    assertion = sent[0]["client_assertion"]
    n = key.public_key().public_numbers()

    def b64u(i: int) -> str:
        return base64.urlsafe_b64encode(i.to_bytes(32, "big")).rstrip(b"=").decode()

    registered = opts.check_public_jwk(
        {"kty": "EC", "crv": "P-256", "x": b64u(n.x), "y": b64u(n.y), "kid": "k1"}
    )
    header, claims = verify_external_jws(
        assertion, keys=[registered], algorithms=opts.ASSERTION_ALGS
    )
    assert header["alg"] == "ES256" and header["kid"] == "k1"
    jti, expires = opts.check_assertion_claims(
        claims,
        client_id="zpc_0123456789abcdef0123",
        audiences=[f"{base}/v1/verify-api/oauth2/token"],
        now=datetime.fromtimestamp(NOW, tz=UTC),
    )
    assert len(jti) == 32 and int(expires.timestamp()) == NOW + 120
    assert b64url_decode(assertion.split(".")[2])  # a real signature, not an empty one
