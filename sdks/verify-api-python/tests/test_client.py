"""The client against a fake transport: nothing here touches a network."""

from __future__ import annotations

import base64
import json
from typing import Any

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec, ed25519

from zoiko_verify import ASSERTION_TYPE, VerifyClient, VerifyError
from zoiko_verify._jwt import b64url_decode, verify_signature

BASE = "https://api.example.test"
TOKEN_URL = f"{BASE}/v1/verify-api/oauth2/token"


class Fake:
    """Records every call and answers from a script of (status, body) pairs, then falls back to a normal answer."""

    def __init__(self, script: list[tuple[int, Any]] | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.script = list(script or [])
        self.tokens = 0

    def __call__(
        self, method: str, url: str, headers: dict[str, str], data: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": json.loads(data) if data else None}
        )
        if self.script:
            status, body = self.script.pop(0)
            return status, json.dumps(body).encode()
        if url == TOKEN_URL:
            self.tokens += 1
            return 200, json.dumps(
                {"access_token": f"zpt_{self.tokens}", "token_type": "Bearer", "expires_in": 900}
            ).encode()
        return 200, json.dumps({"ok": True}).encode()


class Clock:
    def __init__(self, now: float = 1_800_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def secret_client(fake: Fake, clock: Clock | None = None) -> VerifyClient:
    return VerifyClient(BASE, client_id="zpc_abc", client_secret="zps_secret", transport=fake, clock=clock or Clock())


def pem(key: Any) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )


def test_a_client_needs_exactly_one_kind_of_credential_and_never_prints_it() -> None:
    with pytest.raises(ValueError):
        VerifyClient(BASE, client_id="c")
    with pytest.raises(ValueError):
        VerifyClient(BASE, client_id="c", client_secret="s", private_key_pem=pem(ed25519.Ed25519PrivateKey.generate()))
    c = secret_client(Fake())
    assert "zps_secret" not in repr(c) and "zps_secret" not in str(c)


def test_a_secret_client_gets_a_token_once_and_reuses_it_until_just_before_it_ends() -> None:
    fake, clock = Fake(), Clock()
    c = secret_client(fake, clock)
    c.whoami()
    c.workflows()
    assert fake.tokens == 1
    assert fake.calls[0]["body"] == {
        "grant_type": "client_credentials",
        "client_id": "zpc_abc",
        "client_secret": "zps_secret",
    }
    assert fake.calls[1]["headers"]["Authorization"] == "Bearer zpt_1"
    clock.now += 900 - 30  # inside the renewal margin
    c.products()
    assert fake.tokens == 2 and fake.calls[-1]["headers"]["Authorization"] == "Bearer zpt_2"


def test_a_401_renews_the_token_and_retries_that_call_once_and_never_loops() -> None:
    fake = Fake()
    c = secret_client(fake)
    c.whoami()  # token 1
    fake.script = [(401, {"error": {"code": "AUTH_INVALID", "message": "ended"}})]
    assert c.whoami() == {"ok": True} and fake.tokens == 2
    fake2 = Fake()
    c2 = secret_client(fake2)
    c2.whoami()
    fake2.script = [
        (401, {"error": {"code": "AUTH_INVALID"}}),
        (200, {"access_token": "zpt_x", "expires_in": 900}),
        (401, {"error": {"code": "AUTH_INVALID", "message": "still no"}}),
    ]
    with pytest.raises(VerifyError) as e:
        c2.whoami()
    assert e.value.code == "AUTH_INVALID" and e.value.status == 401
    assert (
        len([x for x in fake2.calls if x["url"].endswith("/whoami")]) == 3
    )  # the first ok, then one retry that also failed: no more


@pytest.mark.parametrize("make", [lambda: ec.generate_private_key(ec.SECP256R1()), ed25519.Ed25519PrivateKey.generate])
def test_a_private_key_client_signs_a_short_single_use_assertion_for_this_endpoint(make: Any) -> None:
    key = make()
    fake, clock = Fake(), Clock()
    c = VerifyClient(BASE, client_id="zpc_abc", private_key_pem=pem(key), key_id="k1", transport=fake, clock=clock)
    c.whoami()
    body = fake.calls[0]["body"]
    assert "client_secret" not in body and body["client_assertion_type"] == ASSERTION_TYPE
    head, payload, sig = body["client_assertion"].split(".")
    header, claims = json.loads(b64url_decode(head)), json.loads(b64url_decode(payload))
    assert header["alg"] in ("ES256", "EdDSA") and header["kid"] == "k1" and header["typ"] == "JWT"
    assert claims["iss"] == claims["sub"] == "zpc_abc" and claims["aud"] == TOKEN_URL
    assert claims["exp"] - claims["iat"] == 120 and len(claims["jti"]) == 32
    pub = key.public_key()
    if isinstance(key, ed25519.Ed25519PrivateKey):
        x = pub.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
        jwk = {"kty": "OKP", "crv": "Ed25519", "x": base64.urlsafe_b64encode(x).rstrip(b"=").decode()}
    else:
        n = pub.public_numbers()
        jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": base64.urlsafe_b64encode(n.x.to_bytes(32, "big")).rstrip(b"=").decode(),
            "y": base64.urlsafe_b64encode(n.y.to_bytes(32, "big")).rstrip(b"=").decode(),
        }
    verify_signature(jwk, f"{head}.{payload}".encode(), b64url_decode(sig))
    # the next token request carries a different assertion id, so no assertion is ever sent twice
    clock.now += 2000
    c.whoami()
    again = json.loads(b64url_decode(fake.calls[-2]["body"]["client_assertion"].split(".")[1]))
    assert again["jti"] != claims["jti"]
    with pytest.raises(Exception):  # noqa: B017 - any failure to verify a changed message is the point
        verify_signature(jwk, f"{head}.{payload}x".encode(), b64url_decode(sig))


def test_creating_a_session_sends_only_what_was_given_with_the_callers_own_idempotency_key() -> None:
    fake = Fake()
    c = secret_client(fake)
    c.create_session(
        idempotency_key="order-1001-verify",
        customer_reference="cust-1",
        country="US",
        purpose_code="customer_onboarding",
        locale="en-US",
        metadata={"k": "v"},
    )
    call = fake.calls[-1]
    assert call["method"] == "POST" and call["url"] == f"{BASE}/v1/verify-api/verification-sessions"
    assert call["headers"]["Idempotency-Key"] == "order-1001-verify"
    assert call["body"] == {
        "customer_reference": "cust-1",
        "country": "US",
        "purpose_code": "customer_onboarding",
        "locale": "en-US",
        "metadata": {"k": "v"},
    }
    with pytest.raises(TypeError):
        c.create_session(customer_reference="x", country="US", purpose_code="p")  # type: ignore[call-arg]  # no key, no call


def test_the_other_calls_use_the_documented_paths() -> None:
    fake = Fake()
    c = secret_client(fake)
    c.get_session("vfs_1")
    c.get_decision("vfs_1")
    c.cancel_session("vfs_1")
    c.resume_token("vfs_1")
    c.recheck("vfs_1", idempotency_key="re-1")
    c.list_sessions(status="completed", limit=5)
    c.sandbox_session("approved", idempotency_key="sb-1")
    c.simulate("vfs_1", "declined")
    paths = [(x["method"], x["url"].removeprefix(f"{BASE}/v1/verify-api")) for x in fake.calls if x["url"] != TOKEN_URL]
    assert paths == [
        ("GET", "/verification-sessions/vfs_1"),
        ("GET", "/verification-sessions/vfs_1/decision"),
        ("POST", "/verification-sessions/vfs_1:cancel"),
        ("POST", "/verification-sessions/vfs_1/resume-token"),
        ("POST", "/verification-sessions/vfs_1:recheck"),
        ("GET", "/verification-sessions?status=completed&limit=5"),
        ("POST", "/sandbox/fixtures/approved/sessions"),
        ("POST", "/sandbox/sessions/vfs_1:simulate"),
    ]


def test_an_error_answer_becomes_an_error_with_the_apis_own_code_and_never_a_guess() -> None:
    fake = Fake()
    c = secret_client(fake)
    c.whoami()
    fake.script = [
        (409, {"error": {"code": "INVALID_IDEMPOTENCY_REUSE", "message": "That key was used for a different request."}})
    ]
    with pytest.raises(VerifyError) as e:
        c.create_session(idempotency_key="k-1234567", customer_reference="c", country="US", purpose_code="p")
    assert e.value.code == "INVALID_IDEMPOTENCY_REUSE" and e.value.status == 409 and not e.value.retryable
    fake.script = [(429, {"error": {"code": "RATE_LIMITED", "message": "Try again in 5 seconds."}})]
    with pytest.raises(VerifyError) as e2:
        c.whoami()
    assert e2.value.code == "RATE_LIMITED" and e2.value.retryable
    fake.script = [(502, {})]
    with pytest.raises(VerifyError) as e3:
        c.whoami()
    assert e3.value.status == 502 and e3.value.retryable
    bad = Fake([(401, {"error": {"code": "AUTH_INVALID", "message": "The client id or secret is not valid."}})])
    with pytest.raises(VerifyError) as e4:
        secret_client(bad).whoami()  # the token request itself failed
    assert e4.value.code == "AUTH_INVALID"


def test_an_error_knows_its_documented_class_and_an_unknown_code_has_none() -> None:
    assert VerifyError("TENANT_SCOPE_DENIED", 403).error_class == "AUTH_SCOPE_DENIED"
    assert VerifyError("INVALID_IDEMPOTENCY_REUSE", 409).error_class == "IDEMPOTENCY_CONFLICT"
    assert VerifyError("RATE_LIMITED", 429).error_class == "RATE_LIMITED"
    assert VerifyError("SOMETHING_NEW", 400).error_class is None
