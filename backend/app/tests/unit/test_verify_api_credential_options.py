"""The pure rules for the Verify API's stronger credentials (CR-158): the IP allowlist, the registered public key and the client assertion's claims."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_crypto import LocalSigner
from zid_platform.modules.verify_api.domain import credential_options as o
from zid_platform.modules.verify_api.domain.model import PartnerError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
CID = "zpc_0123456789abcdef0123"
AUD = ["https://api.example.test/v1/verify-api/oauth2/token"]


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


def test_an_allowlist_is_cleaned_and_an_empty_one_means_no_restriction() -> None:
    assert o.check_allowlist(None) == [] and o.check_allowlist([]) == []
    assert o.check_allowlist([" 203.0.113.7 ", "203.0.113.0/24", "203.0.113.7"]) == [
        "203.0.113.7/32",
        "203.0.113.0/24",
    ]
    assert o.check_allowlist(["2001:db8::/48"]) == ["2001:db8::/48"]
    assert o.ip_allowed([], None) and o.ip_allowed([], "anything")


def test_an_entry_that_allows_everything_or_is_not_an_address_is_refused() -> None:
    for bad in (["0.0.0.0/0"], ["::/0"], ["not-an-ip"], ["203.0.113.300"], [""], [5]):
        assert code(o.check_allowlist, bad) == "VALIDATION_FAILED", bad
    assert code(o.check_allowlist, "203.0.113.7") == "VALIDATION_FAILED"
    assert code(o.check_allowlist, [f"10.0.{i}.1" for i in range(21)]) == "VALIDATION_FAILED"


def test_an_address_passes_only_inside_a_listed_network_and_an_unknown_one_never_does() -> None:
    allow = ["203.0.113.0/24", "2001:db8::/48"]
    assert o.ip_allowed(allow, "203.0.113.9") and o.ip_allowed(allow, "2001:db8::5")
    assert not o.ip_allowed(allow, "203.0.114.9")
    assert not o.ip_allowed(allow, None) and not o.ip_allowed(allow, "testclient")
    assert o.ip_allowed(
        allow, "::ffff:203.0.113.9"
    )  # an IPv4 caller seen through a dual-stack socket


def test_a_public_key_is_accepted_only_as_a_public_es256_or_ed25519_key() -> None:
    ec = LocalSigner("k1").public_jwk()
    clean = o.check_public_jwk(ec)
    assert (
        clean["kty"] == "EC"
        and clean["alg"] == "ES256"
        and clean["use"] == "sig"
        and clean["kid"] == "k1"
    )
    bare = {"kty": "OKP", "crv": "Ed25519", "x": ec["x"]}
    named = o.check_public_jwk(bare)
    assert named["kid"] == o.thumbprint(named) and len(named["kid"]) == 32
    for bad in (
        {**bare, "d": "AAAA"},  # private material
        {**bare, "extra": "x"},
        {"kty": "RSA", "n": "AA", "e": "AQAB"},
        {"kty": "OKP", "crv": "Ed448", "x": "AA"},
        {"kty": "EC", "crv": "P-256", "x": "AA"},  # no y
        {**bare, "alg": "RS256"},
        {**bare, "use": "enc"},
        {**bare, "x": ""},
        "not a key",
    ):
        assert code(o.check_public_jwk, bad) == "VALIDATION_FAILED", bad


def _claims(**over: Any) -> dict[str, Any]:
    base = {
        "iss": CID,
        "sub": CID,
        "aud": AUD[0],
        "exp": int((NOW + timedelta(minutes=2)).timestamp()),
        "jti": "assertion-0001",
    }
    return {**base, **over}


def test_a_good_assertion_names_this_client_and_this_endpoint_and_is_short_lived() -> None:
    jti, expires = o.check_assertion_claims(_claims(), client_id=CID, audiences=AUD, now=NOW)
    assert jti == "assertion-0001" and expires == NOW + timedelta(minutes=2)
    # the audience may be a list that contains it
    o.check_assertion_claims(_claims(aud=["x", AUD[0]]), client_id=CID, audiences=AUD, now=NOW)


def test_an_assertion_for_another_client_audience_or_time_is_refused() -> None:
    far = int((NOW + timedelta(hours=1)).timestamp())
    past = int((NOW - timedelta(seconds=1)).timestamp())
    for bad in (
        _claims(iss="zpc_other"),
        _claims(sub="zpc_other"),
        _claims(aud="https://elsewhere.example/token"),
        _claims(aud=None),
        _claims(exp=far),  # lives too long
        _claims(exp=past),  # already over
        _claims(exp="soon"),
        _claims(exp=True),
        _claims(jti="short"),
        _claims(jti=None),
        _claims(iat=int((NOW + timedelta(hours=1)).timestamp())),  # issued in the future
        _claims(nbf="now"),
    ):
        assert (
            code(o.check_assertion_claims, bad, client_id=CID, audiences=AUD, now=NOW)
            == "AUTH_INVALID"
        ), bad
    missing = _claims()
    del missing["exp"]
    assert (
        code(o.check_assertion_claims, missing, client_id=CID, audiences=AUD, now=NOW)
        == "AUTH_INVALID"
    )
