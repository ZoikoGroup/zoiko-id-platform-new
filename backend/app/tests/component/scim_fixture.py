"""A real machine client for a SCIM source: what registering a source now requires to exist."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_platform.modules.session.ports import OAuthClient, register_client

KEY = {
    "kty": "OKP",
    "crv": "Ed25519",
    "x": "11qYAYKxCrfVS_7TyWQHOg7hcvPapiMlrwIaaPcHURo",
    "kid": "k1",
}


async def make_scim_client(
    c: AsyncConnection, tenant: str, client_id: str, *, scope: str = "scim:provision"
) -> None:
    """CONFIDENTIAL, client_credentials, `scope`, private_key_jwt over a public key (no secret anywhere)."""
    await register_client(
        c,
        OAuthClient(
            client_id=client_id,
            tenant_id=tenant,
            client_type="CONFIDENTIAL",
            profile="STANDARD",
            redirect_uris=(),
            grant_types=frozenset({"client_credentials"}),
            scopes=frozenset({scope}),
            audiences=("https://api.zoiko.test",),
            token_auth_method="private_key_jwt",
            dpop_bound=False,
            status="ACTIVE",
            jwks={"keys": [KEY]},
        ),
    )
