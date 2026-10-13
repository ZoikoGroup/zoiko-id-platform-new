"""Authentication rules (ZID-AUTH-001 §2, §5, §6.3, §7.1, §17; DEC-E1; DEC-F5): JWS policy, AAL mapping,
sessions, access-token claims and DPoP proofs."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

import pytest
from zid_crypto import (
    CryptoError,
    KeyRing,
    LocalSigner,
    b64url,
    jwk_thumbprint,
    sign_jws,
    verify_jws,
)
from zid_platform.modules.authentication.domain.assurance import (
    AAL,
    MappingProfile,
    effective_level,
    map_assurance,
    step_up_needed,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.domain.session import (
    Session,
    SessionError,
    SessionPolicy,
    SessionStatus,
    check,
    transition,
)
from zid_platform.modules.session.domain.tokens import (
    TokenError,
    access_token_hash,
    normalize_htu,
    validate_access_claims,
    validate_dpop_claims,
)

NOW = datetime(2026, 10, 12, 12, 0, tzinfo=UTC)
ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"


# ---------------------------------------------------------------- JWS policy (zid-crypto)


@pytest.mark.parametrize("alg", ["ES256", "Ed25519"])
def test_sign_and_verify_round_trip(alg: str) -> None:
    ring = KeyRing(LocalSigner("k1", alg))
    token = ring.sign({"a": 1}, typ="at+jwt")
    header, payload = ring.verify(token, typ="at+jwt")
    assert header["kid"] == "k1" and payload == {"a": 1}
    with pytest.raises(CryptoError) as e:
        ring.verify(token, typ="dpop+jwt")  # explicit typing: a token of one kind is never another
    assert e.value.code == "JWS_TYP_INVALID"


def _forge(header: dict[str, object], payload: dict[str, object], sig: bytes = b"") -> str:
    enc = lambda d: b64url(json.dumps(d).encode())
    return f"{enc(header)}.{enc(payload)}.{b64url(sig)}"


@pytest.mark.parametrize("alg", ["none", "HS256", "RS256", "ES512"])
def test_disallowed_algorithms_are_rejected_before_any_key_use(alg: str) -> None:
    ring = KeyRing(LocalSigner("k1"))
    with pytest.raises(CryptoError) as e:
        ring.verify(_forge({"alg": alg, "kid": "k1", "typ": "at+jwt"}, {"a": 1}), typ="at+jwt")
    assert e.value.code == "ALG_NOT_ALLOWED"


def test_tampering_unknown_keys_and_remote_key_refs_fail() -> None:
    ring = KeyRing(LocalSigner("k1"))
    h, _, s = ring.sign({"a": 1}, typ="at+jwt").split(".")
    forged_payload = b64url(json.dumps({"a": 2}).encode())
    with pytest.raises(CryptoError, match="SIGNATURE_INVALID"):
        ring.verify(f"{h}.{forged_payload}.{s}", typ="at+jwt")
    other = KeyRing(LocalSigner("k1"))  # same kid, different key
    with pytest.raises(CryptoError, match="SIGNATURE_INVALID"):
        other.verify(ring.sign({"a": 1}, typ="at+jwt"), typ="at+jwt")
    with pytest.raises(CryptoError, match="JWS_HEADER_REJECTED"):
        ring.verify(
            _forge({"alg": "ES256", "kid": "k1", "typ": "at+jwt", "jku": "https://x"}, {}),
            typ="at+jwt",
        )
    two = KeyRing(LocalSigner("a"), [LocalSigner("b").public_jwk()])
    with pytest.raises(CryptoError, match="KEY_UNKNOWN"):
        two.verify(_forge({"alg": "ES256", "kid": "zzz", "typ": "at+jwt"}, {}), typ="at+jwt")


def test_rotation_keeps_already_issued_tokens_verifiable() -> None:
    ring = KeyRing(LocalSigner("old"))
    old_token = ring.sign({"a": 1}, typ="at+jwt")
    ring.rotate(LocalSigner("new"))
    assert ring.verify(old_token, typ="at+jwt")[1] == {"a": 1}
    assert [k["kid"] for k in ring.jwks()["keys"]] == ["new", "old"]
    assert all("d" not in k for k in ring.jwks()["keys"])  # JWKS never carries private material


def test_private_jwk_and_cross_algorithm_key_use_are_refused() -> None:
    signer = LocalSigner("c")
    token = sign_jws(signer, {"a": 1}, typ="dpop+jwt")
    with pytest.raises(CryptoError, match="JWK_PRIVATE_MATERIAL"):
        verify_jws(token, keys=[{**signer.public_jwk(), "d": "AAAA"}], typ="dpop+jwt")
    with pytest.raises(CryptoError, match="ALG_KEY_MISMATCH"):
        verify_jws(token, keys=[{**signer.public_jwk(), "alg": "Ed25519"}], typ="dpop+jwt")


def test_jwk_thumbprint_ignores_non_required_members() -> None:
    jwk = LocalSigner("x").public_jwk()
    assert jwk_thumbprint(jwk) == jwk_thumbprint({k: jwk[k] for k in ("kty", "crv", "x", "y")})
    assert len(base64.urlsafe_b64decode(jwk_thumbprint(jwk) + "=")) == 32


# ---------------------------------------------------------------- assurance


IDP = MappingProfile("acme-v1", "https://idp.acme", {"phr": AAL.Z2, "basic": AAL.Z1})


@pytest.mark.parametrize(
    ("issuer", "acr", "amr", "level"),
    [
        ("https://idp.acme", None, ["hwk", "user"], AAL.Z2),
        ("https://idp.acme", "basic", ["pwd"], AAL.Z1),
        ("https://idp.acme", "phr", ["pwd", "sms"], AAL.Z1),  # acr claims Z2, every method is weak
        ("https://idp.acme", "phr", None, AAL.Z2),
        ("https://idp.acme", "unknown", ["face?"], AAL.Z0),
        ("https://idp.evil", "phr", ["hwk"], AAL.Z0),  # profile never applies to another issuer
    ],
)
def test_assurance_mapping(issuer: str, acr: str | None, amr: list[str] | None, level: AAL) -> None:
    assert map_assurance(IDP, issuer=issuer, acr=acr, amr=amr) is level


def test_profiles_cannot_map_to_z3_and_z3_is_freshness() -> None:
    with pytest.raises(ValueError, match="at most AAL-Z2"):
        MappingProfile("x", "i", {"max": AAL.Z3})
    assert effective_level(AAL.Z2, auth_time=NOW - timedelta(minutes=2), now=NOW) is AAL.Z3
    assert effective_level(AAL.Z2, auth_time=NOW - timedelta(minutes=20), now=NOW) is AAL.Z2
    assert effective_level(AAL.Z1, auth_time=NOW, now=NOW) is AAL.Z1
    assert (
        step_up_needed(AAL.Z2, auth_time=NOW - timedelta(hours=1), now=NOW, risk_class=3)
        == "STEP_UP_REQUIRED"
    )
    assert step_up_needed(AAL.Z1, auth_time=NOW, now=NOW, risk_class=2) == "ASSURANCE_INSUFFICIENT"
    assert step_up_needed(AAL.Z1, auth_time=NOW, now=NOW, risk_class=3) == "ASSURANCE_INSUFFICIENT"
    assert step_up_needed(AAL.Z2, auth_time=NOW, now=NOW, risk_class=3) is None


# ---------------------------------------------------------------- sessions


def _session(**kw: object) -> Session:
    base: dict[str, object] = {
        "session_id": "s",
        "tenant_id": "t",
        "principal_id": "p",
        "auth_time": NOW - timedelta(minutes=5),
        "authentication_context": "native:webauthn",
        "aal": 2,
        "observed_security_revision": 4,
        "risk_class": 2,
        "status": SessionStatus.ACTIVE,
        "expires_at": NOW + timedelta(hours=1),
        "last_seen_at": NOW - timedelta(minutes=1),
    }
    return Session(**{**base, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("kw", "rev", "active", "code", "nxt"),
    [
        ({}, 4, True, None, None),
        ({}, 5, True, "SESSION_REAUTH_REQUIRED", SessionStatus.REAUTH_REQUIRED),
        (
            {},
            3,
            True,
            "SESSION_REAUTH_REQUIRED",
            SessionStatus.REAUTH_REQUIRED,
        ),  # fail closed both ways
        ({"status": SessionStatus.REAUTH_REQUIRED}, 4, True, "SESSION_REAUTH_REQUIRED", None),
        ({}, 4, False, "PRINCIPAL_NOT_ACTIVE", SessionStatus.REVOKED),
        ({"expires_at": NOW}, 4, True, "SESSION_EXPIRED", SessionStatus.EXPIRED),
        (
            {"last_seen_at": NOW - timedelta(hours=1)},
            4,
            True,
            "SESSION_IDLE_TIMEOUT",
            SessionStatus.EXPIRED,
        ),
        ({"status": SessionStatus.REVOKED}, 4, True, "SESSION_REVOKED", None),
    ],
)
def test_session_check(
    kw: dict[str, object], rev: int, active: bool, code: str | None, nxt: SessionStatus | None
) -> None:
    r = check(
        _session(**kw),
        principal_revision=rev,
        principal_active=active,
        now=NOW,
        policy=SessionPolicy(),
    )
    assert (r.ok, r.code, r.next_status) == (code is None, code, nxt)


def test_session_transitions_are_one_way() -> None:
    with pytest.raises(SessionError):
        transition(SessionStatus.REAUTH_REQUIRED, SessionStatus.ACTIVE)
    with pytest.raises(SessionError):
        transition(SessionStatus.REVOKED, SessionStatus.ACTIVE)
    with pytest.raises(ValueError):
        SessionPolicy(absolute_ttl=timedelta(minutes=5), idle_timeout=timedelta(minutes=10))


# ---------------------------------------------------------------- access tokens


def _claims(**kw: object) -> dict[str, object]:
    ring = KeyRing(LocalSigner("k"))
    token = issue_access_token(
        ring,
        issuer=ISS,
        audience=AUD,
        subject="p",
        tenant_id="t",
        client_id="c",
        now=NOW,
        scope=frozenset({"grants:write"}),
        session_id="s",
        dpop_jkt="jkt",
    )
    payload = ring.verify(token, typ="at+jwt")[1]
    return {k: v for k, v in {**payload, **kw}.items() if v is not None}


def test_issued_token_validates() -> None:
    c = validate_access_claims(_claims(), issuer=ISS, audience=AUD, now=NOW)
    assert (c.subject, c.tenant_id, c.cnf_jkt, c.scope) == (
        "p",
        "t",
        "jkt",
        frozenset({"grants:write"}),
    )
    assert c.expires_at - c.issued_at == timedelta(minutes=15)


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"iss": "https://evil"}, "TOKEN_ISSUER_INVALID"),
        ({"aud": "https://other"}, "TOKEN_AUDIENCE_INVALID"),
        ({"aud": [AUD, "https://other"]}, "TOKEN_AUDIENCE_INVALID"),
        ({"tenant_id": None}, "TOKEN_CLAIM_INVALID"),
        ({"jti": None}, "TOKEN_CLAIM_INVALID"),
        ({"mandate_balance": "10"}, "TOKEN_CLAIM_FORBIDDEN"),
        ({"exp": int(NOW.timestamp()) + 3600}, "TOKEN_LIFETIME_INVALID"),
        ({"risk_class": 3}, "TOKEN_LIFETIME_INVALID"),  # 15 min is too long for R3
        ({"cnf": {"jkt": "a", "x5t#S256": "b"}}, "TOKEN_CLAIM_INVALID"),  # one binding only
        ({"cnf": {"x5t#S256": 7}}, "TOKEN_CLAIM_INVALID"),
        ({"act": "someone"}, "TOKEN_CLAIM_INVALID"),
        ({"iat": True}, "TOKEN_CLAIM_INVALID"),
    ],
)
def test_bad_access_claims(kw: dict[str, object], code: str) -> None:
    with pytest.raises(TokenError) as e:
        validate_access_claims(_claims(**kw), issuer=ISS, audience=AUD, now=NOW)
    assert e.value.code == code


def test_token_time_window() -> None:
    with pytest.raises(TokenError, match="TOKEN_EXPIRED"):
        validate_access_claims(_claims(), issuer=ISS, audience=AUD, now=NOW + timedelta(minutes=16))
    with pytest.raises(TokenError, match="TOKEN_NOT_YET_VALID"):
        validate_access_claims(_claims(), issuer=ISS, audience=AUD, now=NOW - timedelta(minutes=5))


# ---------------------------------------------------------------- DPoP


def _proof(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "htm": "POST",
        "htu": "https://api.zoiko.test/v1/x",
        "jti": "jti-12345678",
        "iat": int(NOW.timestamp()),
        "ath": access_token_hash("tok"),
    }
    return {k: v for k, v in {**base, **kw}.items() if v is not None}


HDR = {"typ": "dpop+jwt", "alg": "ES256", "jwk": {"kty": "EC"}}


def test_dpop_proof_accepted() -> None:
    p = validate_dpop_claims(
        HDR,
        _proof(),
        method="post",
        url="https://API.zoiko.test:443/v1/x?page=2",
        access_token="tok",
        now=NOW,
    )
    assert p.jti == "jti-12345678"


@pytest.mark.parametrize(
    ("kw", "code"),
    [
        ({"htm": "GET"}, "DPOP_PROOF_INVALID"),
        ({"htu": "https://api.zoiko.test/v1/y"}, "DPOP_PROOF_INVALID"),
        ({"htu": "https://evil.test/v1/x"}, "DPOP_PROOF_INVALID"),
        ({"ath": access_token_hash("other")}, "DPOP_PROOF_INVALID"),
        ({"ath": None}, "DPOP_PROOF_INVALID"),
        ({"jti": "short"}, "DPOP_PROOF_INVALID"),
        ({"iat": int((NOW - timedelta(minutes=5)).timestamp())}, "DPOP_PROOF_STALE"),
        ({"iat": int((NOW + timedelta(minutes=5)).timestamp())}, "DPOP_PROOF_STALE"),
    ],
)
def test_bad_dpop_proofs(kw: dict[str, object], code: str) -> None:
    with pytest.raises(TokenError) as e:
        validate_dpop_claims(
            HDR,
            _proof(**kw),
            method="POST",
            url="https://api.zoiko.test/v1/x",
            access_token="tok",
            now=NOW,
        )
    assert e.value.code == code


def test_dpop_nonce_and_htu_normalization() -> None:
    with pytest.raises(TokenError, match="DPOP_NONCE_REQUIRED"):
        validate_dpop_claims(
            HDR,
            _proof(),
            method="POST",
            url="https://api.zoiko.test/v1/x",
            access_token="tok",
            now=NOW,
            nonce="n-1",
        )
    assert normalize_htu("HTTPS://Api.Zoiko.test:443/a?b#c") == "https://api.zoiko.test/a"
    assert normalize_htu("https://api.zoiko.test:8443/a") == "https://api.zoiko.test:8443/a"
