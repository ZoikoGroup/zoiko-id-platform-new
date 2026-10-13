"""Signing keys by purpose (ZID-AUTH-001 §17; DEC-F5)."""

from __future__ import annotations

import json

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from zid_crypto import CryptoError, KeyRing, LocalSigner, verify_jws
from zid_platform.entrypoints.consumer import transmitter_keys
from zid_platform.keys import KeyRings
from zid_platform.main import create_app
from zid_platform.settings import Settings


def pem() -> str:
    key = ec.generate_private_key(ec.SECP256R1())
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    ).decode()


def test_each_purpose_gets_its_own_key() -> None:
    rings = KeyRings.development("c1")
    assert rings.distinct()
    assert not KeyRings.single(KeyRing(LocalSigner("x"))).distinct()


def test_shared_development_key_is_the_same_key_in_two_processes() -> None:
    shared = {"set": pem()}
    a, b = KeyRings.development("c1", shared).set, KeyRings.development("c1", shared).set
    token = a.sign({"x": 1}, typ="secevent+jwt")
    assert verify_jws(token, keys=b.jwks()["keys"], typ="secevent+jwt")[1] == {"x": 1}
    with pytest.raises(CryptoError):
        LocalSigner.from_pem(
            "k",
            ec.generate_private_key(ec.SECP384R1())
            .private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            .decode(),
        )


def test_transmitter_only_with_a_shared_set_key() -> None:
    env = {"ZID_TOKEN_ISSUER": "https://id.example", "ZID_ENVIRONMENT": "local"}
    assert transmitter_keys(env, "c1") is None
    with_key = {**env, "ZID_DEV_SIGNING_KEYS": json.dumps({"set": pem()})}
    out = transmitter_keys(with_key, "c1")
    assert out is not None and out[1] == "https://id.example"
    assert transmitter_keys({**with_key, "ZID_ENVIRONMENT": "production"}, "c1") is None


def test_published_key_sets_are_separate() -> None:
    app = create_app(
        Settings(database_url=None, token_issuer="https://id.example", environment="test")
    )
    with TestClient(app) as http:
        token_keys = {k["kid"] for k in http.get("/oauth2/jwks").json()["keys"]}
        set_keys = {k["kid"] for k in http.get("/ssf/jwks").json()["keys"]}
        vc_keys = {k["kid"] for k in http.get("/.well-known/jwt-vc-issuer").json()["jwks"]["keys"]}
    assert token_keys and set_keys and vc_keys
    assert not token_keys & set_keys and not token_keys & vc_keys
    with pytest.raises(RuntimeError):
        create_app(
            Settings(
                database_url=None,
                token_issuer="https://id.example",
                environment="production",
                dev_signing_keys={"set": pem()},
            )
        )


def test_verifier_certificate_must_certify_the_verifier_key() -> None:
    import base64

    from cryptography import x509

    rings = KeyRings.development("c1")
    signer = rings.verifier.active
    assert isinstance(signer, LocalSigner)
    der = signer.development_certificate("verifier")
    pem = x509.load_der_x509_certificate(der).public_bytes(serialization.Encoding.PEM).decode()
    assert rings.verifier_x5c([pem], development=False) == (base64.b64encode(der).decode(),)
    other = rings.token.active
    assert isinstance(other, LocalSigner)
    wrong = x509.load_der_x509_certificate(other.development_certificate("x"))
    with pytest.raises(RuntimeError):
        rings.verifier_x5c(
            [wrong.public_bytes(serialization.Encoding.PEM).decode()], development=False
        )
    with pytest.raises(RuntimeError):
        rings.verifier_x5c([], development=False)  # deployed: a real chain is required
    assert len(rings.verifier_x5c([], development=True)) == 1
