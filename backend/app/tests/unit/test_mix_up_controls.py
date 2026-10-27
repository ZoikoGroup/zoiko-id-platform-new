"""Authorization-server mix-up and audience confusion (ZID-MACHINE-001 MACH-16; RFC 9207, RFC 8707).

The platform has no MCP-specific client: an MCP server is registered as an `MCP` connector and reaches the
platform through the same OAuth client and resource-server code as any other integration. MACH-16 is therefore
proved on that code: a client takes a token or ID token only from the issuer it started with and for itself,
and a resource accepts a token only when it was minted for exactly that resource.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from zid_crypto import KeyRing, LocalSigner
from zid_platform.modules.federation.domain.connection import (
    Connection,
    FederationError,
    validate_id_token,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.domain.oauth import OAuthClient, OAuthError, resolve_audience
from zid_platform.modules.session.domain.tokens import TokenError, validate_access_claims

NOW = datetime(2026, 10, 25, 12, tzinfo=UTC)
ISS, AUD = "https://id.example", "https://api.example"
UPSTREAM, CLIENT = "https://idp.example", "zoiko-client"


def connection() -> Connection:
    return Connection(
        connection_id="c1",
        tenant_id="t1",
        protocol="OIDC",
        display_name="IdP",
        issuer=UPSTREAM,
        status="ACTIVE",
        config_revision=1,
        metadata_expires_at=NOW + timedelta(days=1),
        acr_levels={},
        trust_amr=False,
        link_policy="EXISTING_ONLY",
        client_id=CLIENT,
    )


def id_token(**kw: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": UPSTREAM,
        "aud": CLIENT,
        "sub": "user-1",
        "nonce": "n-1",
        "iat": int(NOW.timestamp()),
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    return {**base, **kw}


def code_of(claims: dict[str, Any]) -> str:
    with pytest.raises(FederationError) as e:
        validate_id_token(claims, connection(), nonce="n-1", now=NOW)
    return str(e.value.code)


def test_the_client_refuses_an_id_token_from_another_issuer_or_for_another_client() -> None:
    """MACH-16: a token minted by a different authorization server, or for a different client, is refused."""
    assert validate_id_token(id_token(), connection(), nonce="n-1", now=NOW).subject == "user-1"
    assert code_of(id_token(iss="https://evil.example")) == "ID_TOKEN_ISSUER_MISMATCH"
    assert code_of(id_token(aud="another-client")) == "ID_TOKEN_AUDIENCE_MISMATCH"
    assert code_of(id_token(aud=[CLIENT, "another-client"])) == "ID_TOKEN_AZP_MISMATCH"
    assert code_of(id_token(nonce="replayed")) == "ID_TOKEN_NONCE_MISMATCH"


def _access_claims(**kw: Any) -> dict[str, Any]:
    ring = KeyRing(LocalSigner("k"))
    token = issue_access_token(
        ring,
        issuer=ISS,
        audience=AUD,
        subject="p",
        tenant_id="t",
        client_id="c",
        now=NOW,
        scope=frozenset({"grants:read"}),
        session_id="s",
        dpop_jkt="jkt",
    )
    return {**ring.verify(token, typ="at+jwt")[1], **kw}


def test_a_resource_accepts_only_a_token_minted_for_it_by_its_own_issuer() -> None:
    """MACH-16: a token for another resource server, or from another issuer, is not accepted here."""
    validate_access_claims(_access_claims(), issuer=ISS, audience=AUD, now=NOW)
    for kw, code in (
        ({"aud": "https://mcp-server.example"}, "TOKEN_AUDIENCE_INVALID"),
        ({"aud": [AUD, "https://mcp-server.example"]}, "TOKEN_AUDIENCE_INVALID"),
        ({"iss": "https://evil.example"}, "TOKEN_ISSUER_INVALID"),
    ):
        with pytest.raises(TokenError) as e:
            validate_access_claims(_access_claims(**kw), issuer=ISS, audience=AUD, now=NOW)
        assert e.value.code == code


def test_a_client_cannot_ask_for_a_resource_it_is_not_registered_for() -> None:
    """MACH-16 (RFC 8707): the audience of a token is chosen from the client's registered resources only."""
    client = OAuthClient(
        client_id="c",
        tenant_id="t",
        client_type="CONFIDENTIAL",
        profile="STANDARD",
        redirect_uris=(),
        grant_types=frozenset(),
        scopes=frozenset(),
        audiences=(AUD,),
        token_auth_method="private_key_jwt",
        dpop_bound=False,
        status="ACTIVE",
    )
    assert resolve_audience(client, None, AUD) == AUD
    with pytest.raises(OAuthError) as e:
        resolve_audience(client, "https://mcp-server.example", AUD)
    assert e.value.error == "invalid_target"
