"""Outside identity provider bindings, as evidence (ZID-CP-WF07 v2.0 .03; CR-169): what a token must show, what is kept, and that none of it is authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_crypto import LocalSigner, sign_jws, verify_external_jws
from zid_platform.modules.machine.domain import idp_binding as d
from zid_platform.modules.machine.domain.agents import MachineError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
ISS, AUD, SUB = "https://idp.example.com/tenant-1", "https://api.zoiko.test/agents", "agent-obj-77"


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(MachineError) as e:
        fn(*a, **k)
    return e.value.code


def claims(**over: Any) -> dict[str, Any]:
    ts = int(NOW.timestamp())
    base = {"iss": ISS, "aud": AUD, "sub": SUB, "iat": ts - 10, "exp": ts + 300, "jti": "j1"}
    return {k: v for k, v in {**base, **over}.items() if v is not None}


def problems(c: dict[str, Any], needs_jti: bool = False) -> list[str]:
    return d.claim_problems(c, issuer=ISS, audience=AUD, subject=SUB, now=NOW, needs_jti=needs_jti)


def test_a_token_proves_a_binding_only_when_every_claim_checks_and_every_failure_is_named() -> None:
    assert problems(claims()) == []
    assert problems(claims(iss="https://evil.example.com")) == ["IDP_ISSUER_MISMATCH"]
    assert problems(claims(aud="https://other.service")) == ["IDP_AUDIENCE_MISMATCH"]  # a token made for another service
    assert problems(claims(aud=["x", AUD])) == []
    assert problems(claims(sub="someone-else")) == ["IDP_SUBJECT_MISMATCH"]
    ts = int(NOW.timestamp())
    assert problems(claims(exp=ts - 3600, iat=ts - 3700)) == ["IDP_TOKEN_EXPIRED"]
    assert problems(claims(exp=None)) == ["IDP_TOKEN_NO_EXPIRY"] and problems(claims(iat=None)) == ["IDP_TOKEN_NO_ISSUE_TIME"]
    assert problems(claims(exp=ts + 100000)) == ["IDP_TOKEN_TOO_LONG_LIVED"]  # a long-lived agent token is refused
    assert problems(claims(iat=ts + 5000, exp=ts + 5200)) == ["IDP_TOKEN_FROM_THE_FUTURE"] and problems(claims(nbf=ts + 5000)) == ["IDP_TOKEN_NOT_YET_VALID"]
    assert problems(claims(exp="soon")) == ["IDP_TOKEN_NO_EXPIRY"] and problems(claims(exp=True)) == ["IDP_TOKEN_NO_EXPIRY"]
    assert problems(claims(jti=None), needs_jti=True) == ["IDP_TOKEN_NO_ID"] and problems(claims(jti=None), needs_jti=False) == []
    assert set(problems(claims(iss="x", aud="y", sub="z"))) == {"IDP_ISSUER_MISMATCH", "IDP_AUDIENCE_MISMATCH", "IDP_SUBJECT_MISMATCH"}  # every reason, not the first


def test_a_provider_is_pinned_by_exact_address_and_public_keys_only() -> None:
    assert d.check_issuer(ISS) == ISS
    for bad in ("http://idp.example.com", "https://idp.example.com/?x=1", "https://u@idp.example.com", "https://idp.example.com/#f", "idp.example.com", "https://" + "a" * 400):
        assert code(d.check_issuer, bad) == "IDP_BINDING_INVALID", bad
    pub = {"kid": "k1", "kty": "EC", "crv": "P-256", "x": "x", "y": "y"}
    keys, algs = d.check_keys([pub], [])
    assert keys == [pub] and algs == ["ES256"]
    for bad in ([], "keys", [{"kty": "EC"}], [{**pub, "d": "secret"}], [{**pub, "jku": "https://evil"}], [pub, pub], [{**pub, "kid": f"k{i}"} for i in range(5)], [{**pub, "k": "sym"}]):
        assert code(d.check_keys, bad, []) == "IDP_BINDING_INVALID", bad
    assert code(d.check_keys, [pub], ["none"]) == "IDP_BINDING_INVALID" and code(d.check_keys, [pub], ["HS256"]) == "IDP_BINDING_INVALID"
    assert code(d.check_audience, "ab") == "IDP_BINDING_INVALID" and code(d.check_subject, " ") == "IDP_BINDING_INVALID" and code(d.check_profile, "ldap") == "IDP_BINDING_INVALID"


def test_what_a_provider_says_is_kept_as_short_text_under_our_names_and_is_never_authority() -> None:
    assert d.external_claims("generic_oidc", {"blueprint": "daara", "sponsor": "alice", "lifecycle": "active", "is_admin": True, "scope": "pay:all"}) == {"blueprint": "daara", "sponsor": "alice", "lifecycle": "active"}
    assert d.external_claims("okta", {"agent_type": "T1", "owner": "u1", "status": "ACTIVE", "groups": ["root"]}) == {"blueprint": "T1", "sponsor": "u1", "lifecycle": "ACTIVE"}
    assert d.external_claims("entra", {"xms_blueprint_id": "bp-1", "xms_sponsor": "s-1", "xms_agent_state": "enabled"}) == {"blueprint": "bp-1", "sponsor": "s-1", "lifecycle": "enabled"}
    assert d.external_claims("okta", {"owner": {"nested": 1}, "agent_type": True}) == {}  # only plain text or numbers
    assert len(d.external_claims("okta", {"owner": "x" * 999})["sponsor"]) == 200
    assert all(p["certified"] is False for p in d.PROFILES.values()) and d.PROFILES["okta"]["needs_jti"] and "Not certified" in d.PROFILES["entra"]["note"]


def test_a_connector_gone_quiet_proves_nothing_and_a_token_is_remembered_only_as_a_digest() -> None:
    kw = {"expires_at": None, "now": NOW, "stale_after_s": 3600}
    assert d.binding_state(status="ACTIVE", last_ok_at=NOW - timedelta(minutes=5), **kw) == "ACTIVE"
    assert d.binding_state(status="ACTIVE", last_ok_at=NOW - timedelta(hours=5), **kw) == "STALE"
    assert d.binding_state(status="ACTIVE", last_ok_at=None, **kw) == "STALE"  # never heard from is not proven
    assert d.binding_state(status="REVOKED", last_ok_at=NOW, **kw) == "REVOKED"
    assert d.binding_state(status="ACTIVE", last_ok_at=NOW, **{**kw, "expires_at": NOW - timedelta(seconds=1)}) == "EXPIRED"
    assert d.token_digest("abc").startswith("sha256:") and "abc" not in d.token_digest("abc") and d.token_digest("abc") == d.token_digest("abc")


def test_a_token_signed_by_a_local_issuer_verifies_against_the_pinned_key_and_not_against_another() -> None:
    mine, other = LocalSigner("issuer-a"), LocalSigner("issuer-b")
    token = sign_jws(mine, claims(), typ="JWT")
    header, got = verify_external_jws(token, keys=[mine.public_jwk()], algorithms=["ES256"])
    assert got["sub"] == SUB and header["kid"] == "issuer-a"
    from zid_crypto import CryptoError

    with pytest.raises(CryptoError):  # the same kid but another key is a bad signature, not a match
        verify_external_jws(token, keys=[{**other.public_jwk(), "kid": "issuer-a"}], algorithms=["ES256"])
    with pytest.raises(CryptoError):
        verify_external_jws(token, keys=[other.public_jwk()], algorithms=["ES256"])  # an unpinned key
