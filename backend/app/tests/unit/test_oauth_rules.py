"""OAuth authorization server rules (ZID-AUTH-001 §6.1-6.2, §8; RFC 6749, 7636, 8252, 8707)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from zid_platform.modules.session.domain.oauth import (
    OAuthClient,
    OAuthError,
    client_usable,
    fapi2_token_rules,
    narrow_scope,
    opaque_token,
    pkce_challenge,
    redirect_matches,
    resolve_audience,
    token_hash,
    validate_authorization_request,
    verify_pkce,
)

AUD = "https://api.zoiko.test"
CB = "https://app.example/cb"
CHALLENGE = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


def client(**kw: object) -> OAuthClient:
    args: dict[str, object] = {
        "client_id": "spa",
        "tenant_id": "t",
        "client_type": "PUBLIC",
        "profile": "STANDARD",
        "redirect_uris": (CB, "http://127.0.0.1/cb"),
        "grant_types": frozenset({"authorization_code", "refresh_token"}),
        "scopes": frozenset({"openid", "grants:read"}),
        "audiences": (),
        "token_auth_method": "none",
        "dpop_bound": False,
        "status": "ACTIVE",
        "refresh_ttl": timedelta(days=14),
    }
    return OAuthClient(**{**args, **kw})  # type: ignore[arg-type]


def params(**kw: str) -> dict[str, str]:
    return {
        "client_id": "spa",
        "redirect_uri": CB,
        "response_type": "code",
        "state": "xyz",
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        "scope": "grants:read",
        **kw,
    }


def test_pkce_s256_matches_rfc_7636_appendix_b() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == CHALLENGE
    verify_pkce(verifier, CHALLENGE)
    for bad in (None, "", "short", verifier[:-1] + "A", verifier + " ", "x" * 129):
        with pytest.raises(OAuthError) as e:
            verify_pkce(bad, CHALLENGE)
        assert e.value.error == "invalid_grant"


def test_redirects_match_exactly_except_loopback_port() -> None:
    assert redirect_matches(CB, CB)
    for other in (
        CB + "/",
        CB + "?a=1",
        CB + "#f",
        "https://APP.example/cb",
        "https://app.example:444/cb",
        "http://app.example/cb",
        "https://app.example/cb/../cb",
    ):
        assert not redirect_matches(CB, other), other
    assert redirect_matches("http://127.0.0.1/cb", "http://127.0.0.1:53111/cb")
    assert redirect_matches("http://[::1]/cb", "http://[::1]:8080/cb")
    assert not redirect_matches("http://127.0.0.1/cb", "http://127.0.0.1:1/other")
    assert not redirect_matches("http://127.0.0.1/cb", "http://localhost:1/cb")
    assert not redirect_matches("http://127.0.0.1/cb", "http://u@127.0.0.1:1/cb")
    assert not redirect_matches("https://127.0.0.1/cb", "https://127.0.0.1:1/cb")


def test_untrusted_client_or_redirect_is_never_redirected() -> None:
    for c, p in (
        (None, params()),
        (client(status="SUSPENDED"), params()),
        (client(), params(client_id="other")),
        (client(), params(redirect_uri="https://evil.example/cb")),
        (client(), {k: v for k, v in params().items() if k != "redirect_uri"}),
    ):
        with pytest.raises(OAuthError) as e:
            validate_authorization_request(c, p, default_audience=AUD)
        assert not e.value.redirectable


@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),  # implicit does not exist
        ({"response_type": "code id_token"}, "unsupported_response_type"),
        ({"state": ""}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"code_challenge": "too-short"}, "invalid_request"),
        ({"scope": "grants:read admin"}, "invalid_scope"),
        ({"scope": "openid"}, "invalid_request"),  # ID token without a nonce
        ({"prompt": "none login"}, "invalid_request"),
        ({"prompt": "select_account"}, "invalid_request"),
        ({"max_age": "-1"}, "invalid_request"),
        ({"response_mode": "fragment"}, "invalid_request"),
        ({"request": "eyJhbGciOiJub25lIn0.e30."}, "request_not_supported"),
        ({"resource": "https://other.api"}, "invalid_target"),
        ({"dpop_jkt": "nope"}, "invalid_request"),
    ],
)
def test_bad_requests_redirect_with_an_error(change: dict[str, str], error: str) -> None:
    with pytest.raises(OAuthError) as e:
        validate_authorization_request(client(), params(**change), default_audience=AUD)
    assert e.value.error == error and e.value.redirectable


def test_client_registration_limits_the_request() -> None:
    with pytest.raises(OAuthError) as e:
        validate_authorization_request(
            client(grant_types=frozenset({"refresh_token"})), params(), default_audience=AUD
        )
    assert e.value.error == "unauthorized_client"
    ok = validate_authorization_request(
        client(), params(scope="openid grants:read", nonce="n", max_age="300"), default_audience=AUD
    )
    assert ok.scope == {"openid", "grants:read"} and ok.audience == AUD and ok.max_age == 300


def test_scope_and_resource_only_narrow() -> None:
    allowed = frozenset({"a", "b"})
    assert narrow_scope(None, allowed) == allowed
    assert narrow_scope("a", allowed) == {"a"}
    assert narrow_scope("", allowed) == frozenset()
    with pytest.raises(OAuthError):
        narrow_scope("a c", allowed)
    c = client(audiences=("https://x", "https://y"))
    assert resolve_audience(c, None, AUD) == "https://x"
    assert resolve_audience(c, "https://y", AUD) == "https://y"
    with pytest.raises(OAuthError):
        resolve_audience(c, AUD, AUD)  # the platform default is not implied for a scoped client
    assert resolve_audience(client(), None, AUD) == AUD


def test_only_supported_client_authentication_is_accepted() -> None:
    assert client_usable(client(), "spa").client_id == "spa"
    assert client_usable(client(token_auth_method="private_key_jwt"), "spa").confidential
    with pytest.raises(OAuthError) as e:  # client secrets are not supported (no exception granted)
        client_usable(client(token_auth_method="client_secret_basic"), "spa")
    assert e.value.error == "invalid_client" and e.value.status == 401


def test_par_is_required_where_the_client_says_so() -> None:
    fapi = client(profile="FAPI2", token_auth_method="private_key_jwt", require_par=True)
    with pytest.raises(OAuthError) as e:
        validate_authorization_request(fapi, params(), default_audience=AUD)
    assert e.value.error == "invalid_request"
    assert validate_authorization_request(fapi, params(), default_audience=AUD, pushed=True)
    with pytest.raises(OAuthError):
        validate_authorization_request(client(require_par=True), params(), default_audience=AUD)
    with pytest.raises(OAuthError):
        fapi2_token_rules(fapi, sender_constrained=False)
    fapi2_token_rules(fapi, sender_constrained=True)
    fapi2_token_rules(client(), sender_constrained=False)


def test_opaque_tokens_are_random_and_stored_as_sha256() -> None:
    a, b = opaque_token(), opaque_token()
    assert a != b and len(a) >= 43
    assert len(token_hash(a)) == 32 and token_hash(a) != token_hash(b)
