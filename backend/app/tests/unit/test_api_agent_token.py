"""CR-094: the exact rules of the API agent's credential path, with real signatures and no database."""

from __future__ import annotations

import base64
import json

from zid_crypto import LocalSigner, jwk_thumbprint, sign_jws, verify_external_jws
from zid_platform.modules.machine.application.api_agent_token import unverified_claims
from zid_platform.modules.machine.domain import api_agents as a

CLIENT = "zid-api-0123456789abcdef0123"
AUDIENCE = "https://id.zoiko.test/v1/machine/api-agent-token"
RESOURCE = "https://api.client.example.test"
NOW = 1_800_000_000


def claims(**over):
    base = {
        "iss": CLIENT, "sub": CLIENT, "aud": AUDIENCE, "resource": RESOURCE, "jti": "assertion-0001",
        "iat": NOW, "exp": NOW + 30,
    }  # fmt: skip
    return {**base, **over}


def check(c):
    return a.assertion_problems(c, client_id=CLIENT, token_audience=AUDIENCE, resources=[RESOURCE], now_ts=NOW)


def test_an_exact_short_assertion_is_accepted():
    assert check(claims()) == []


def test_every_claim_is_checked_exactly():
    assert "ASSERTION_ISSUER_MISMATCH" in check(claims(iss="someone-else"))
    assert "ASSERTION_ISSUER_MISMATCH" in check(claims(sub="someone-else"))
    assert "ASSERTION_AUDIENCE_MISMATCH" in check(claims(aud=AUDIENCE + "/"))
    assert "ASSERTION_AUDIENCE_MISMATCH" in check(claims(aud=[AUDIENCE]))  # a list is not the exact string
    assert "ASSERTION_RESOURCE_NOT_REGISTERED" in check(claims(resource=RESOURCE + "/x"))
    assert "ASSERTION_RESOURCE_NOT_REGISTERED" in check({k: v for k, v in claims().items() if k != "resource"})
    assert "ASSERTION_ID_REQUIRED" in check(claims(jti="short"))
    assert "ASSERTION_ID_REQUIRED" in check(claims(jti=12345678))
    assert "ASSERTION_TIMES_REQUIRED" in check({k: v for k, v in claims().items() if k != "exp"})


def test_the_lifetime_is_a_minute_at_most_and_time_is_checked_both_ways():
    assert "ASSERTION_TOO_LONG_LIVED" in check(claims(exp=NOW + 61))
    assert "ASSERTION_TOO_LONG_LIVED" in check(claims(exp=NOW))
    assert "ASSERTION_EXPIRED" in check(claims(iat=NOW - 200, exp=NOW - 150))
    assert "ASSERTION_FROM_THE_FUTURE" in check(claims(iat=NOW + 100, exp=NOW + 130))
    assert check(claims(iat=NOW - 20, exp=NOW + 10)) == []


def test_the_attestation_cap_is_read_by_the_gate_and_the_default_class_passes_it():
    ok = dict(agent_active=True, binding_status="ACTIVE", binding_valid=True, build_revoked=False, digest_matches_release=True, frozen=False)
    assert a.api_credential_blockers(**ok) == []
    # a class whose floor is above what an API client can show is refused: the cap is read here
    assert a.api_credential_blockers(**ok, risk_level=2) == ["ATTESTATION_INSUFFICIENT"]
    assert a.api_credential_blockers(**ok, risk_level=3) == ["ATTESTATION_INSUFFICIENT"]
    assert a.API_ATTESTATION_CAP == 1 and a.API_TOKEN_RISK_CLASS == 1


def test_each_state_that_must_stop_a_credential_does_so():
    ok = dict(agent_active=True, binding_status="ACTIVE", binding_valid=True, build_revoked=False, digest_matches_release=True, frozen=False)
    assert a.api_credential_blockers(**{**ok, "frozen": True}) == ["EMERGENCY_FROZEN"]
    assert a.api_credential_blockers(**{**ok, "agent_active": False}) == ["AGENT_NOT_ACTIVE"]
    assert a.api_credential_blockers(**{**ok, "binding_status": "SUSPENDED"}) == ["API_BINDING_INACTIVE"]
    assert a.api_credential_blockers(**{**ok, "binding_valid": False}) == ["API_BINDING_INACTIVE"]
    assert a.api_credential_blockers(**{**ok, "build_revoked": True}) == ["BLUEPRINT_REVOKED"]
    assert a.api_credential_blockers(**{**ok, "digest_matches_release": False}) == ["API_CLIENT_NOT_APPROVED"]


def test_unverified_claims_never_raise_and_only_return_an_object():
    good = sign_jws(LocalSigner("k1"), claims(), typ=a.ASSERTION_TYP)
    assert unverified_claims(good)["iss"] == CLIENT
    for junk in ("", "a.b", "a.b.c", "x" * 5000, "a." + base64.urlsafe_b64encode(b"[1]").decode() + ".c", "a." + base64.urlsafe_b64encode(b"nope").decode() + ".c"):
        assert unverified_claims(junk) == {}


def test_a_real_signature_verifies_only_against_its_own_public_key_and_type():
    signer, other = LocalSigner("k1"), LocalSigner("k1")  # same kid, different key
    token = sign_jws(signer, claims(), typ=a.ASSERTION_TYP)
    _, got = verify_external_jws(token, keys=[signer.public_jwk()], algorithms=a.ASSERTION_ALGORITHMS, typ=a.ASSERTION_TYP)
    assert got["jti"] == "assertion-0001"
    assert a.jwk_problems(signer.public_jwk()) == []
    for keys, typ in (([other.public_jwk()], a.ASSERTION_TYP), ([signer.public_jwk()], "JWT")):
        try:
            verify_external_jws(token, keys=keys, algorithms=a.ASSERTION_ALGORITHMS, typ=typ)
        except Exception as e:  # noqa: BLE001
            assert "SIGNATURE_INVALID" in str(e) or "JWS_TYP_INVALID" in str(e)
        else:
            raise AssertionError("a wrong key or type must not verify")
    assert jwk_thumbprint(signer.public_jwk()) in a.descriptor(CLIENT, {"keys": [signer.public_jwk()]}, [RESOURCE])["thumbprints"]
    json.dumps(a.descriptor(CLIENT, {"keys": [signer.public_jwk()]}, [RESOURCE]))
