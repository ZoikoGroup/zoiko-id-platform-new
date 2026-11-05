"""Public Protocol interface of the session module. Other modules import ONLY this."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
)
from zid_platform.modules.session.application.authenticate import (
    Authenticated,
    TokenSettings,
    authenticate,
)
from zid_platform.modules.session.application.client_auth import (
    cert_thumbprint,
    parse_client_cert,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.application.oauth_server import ServerSettings
from zid_platform.modules.session.domain.client_registration import (
    ClientRegistrationError,
    check_public_jwks,
    check_registration,
    new_client_id,
)
from zid_platform.modules.session.domain.oauth import OAuthClient
from zid_platform.modules.session.domain.session import Session, SessionPolicy, SessionStatus
from zid_platform.modules.session.domain.tokens import TokenError
from zid_platform.modules.session.infrastructure.oauth_store import (
    client_tenant,
    list_clients,
    load_client,
    set_client_status,
)
from zid_platform.modules.session.infrastructure.oauth_store import (
    create_client as register_client,
)
from zid_platform.modules.session.infrastructure.session_store import (
    create_session,
    issue_handle,
    last_sign_in,
    load_session,
    load_session_by_handle,
    revoke_principal_sessions,
    revoke_tenant_sessions,
)
from zid_platform.modules.session.infrastructure.session_store import (
    set_status as set_session_status,
)


def _key_ids(c: OAuthClient) -> list[str]:
    keys = (c.jwks or {}).get("keys", [])
    return sorted(str(k.get("kid")) for k in keys) if isinstance(keys, list) else []


def _client_view(c: OAuthClient) -> dict[str, Any]:
    return {
        "client_id": c.client_id,
        "client_type": c.client_type,
        "redirect_uris": list(c.redirect_uris),
        "scopes": sorted(c.scopes),
        "token_auth_method": c.token_auth_method,
        "key_ids": _key_ids(c),  # ids, never key material
        "status": c.status,
        "first_party": c.first_party,
    }


class OAuthClientGateway:
    """Self-service OAuth client registry for the application registry (integration), wired in main.py."""

    async def register(
        self,
        conn: AsyncConnection,
        *,
        tenant_id: str,
        name: str,
        client_type: str,
        redirect_uris: Sequence[str],
        jwks: dict[str, Any] | None,
        scopes: Sequence[str],
        audience: str,
    ) -> dict[str, Any]:
        redirects, public_keys, granted, auth_method = check_registration(
            client_type, redirect_uris, jwks, scopes
        )
        client = OAuthClient(
            client_id=new_client_id(name),
            tenant_id=tenant_id,
            client_type=client_type,
            profile="STANDARD",
            redirect_uris=redirects,
            grant_types=frozenset({"authorization_code", "refresh_token"}),
            scopes=granted,
            audiences=(audience,),
            token_auth_method=auth_method,
            dpop_bound=False,
            status="ACTIVE",
            jwks=public_keys,
            require_par=False,
        )
        await register_client(conn, client)
        return _client_view(client)

    async def get(self, conn: AsyncConnection, client_id: str) -> dict[str, Any] | None:
        c = await load_client(conn, client_id)
        return _client_view(c) if c else None

    async def retire(self, conn: AsyncConnection, client_id: str) -> bool:
        return await set_client_status(conn, client_id, "RETIRED")

    async def listing(self, conn: AsyncConnection) -> list[dict[str, Any]]:
        return [_client_view(c) for c in await list_clients(conn)]


async def register_service_client(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    name: str,
    jwks: Mapping[str, Any],
    scopes: Sequence[str],
    audience: str,
    home_cell: str,
) -> dict[str, Any]:
    """A machine client for a connector (a SCIM source): CONFIDENTIAL, `client_credentials` only, proving itself
    with `private_key_jwt` over the PUBLIC keys given (a private key is refused, never stored). It acts as its own
    service principal, created here in the same transaction. Raises `ClientRegistrationError` for bad keys."""
    keys = check_public_jwks(jwks)
    principal_id = str(uuid.uuid4())
    await create_principal(
        conn,
        Principal(
            principal_id, tenant_id, PrincipalType.APPLICATION, PrincipalStatus.ACTIVE, home_cell
        ),
    )
    client = OAuthClient(
        client_id=new_client_id(name),
        tenant_id=tenant_id,
        client_type="CONFIDENTIAL",
        profile="STANDARD",
        redirect_uris=(),
        grant_types=frozenset({"client_credentials"}),
        scopes=frozenset(scopes),
        audiences=(audience,),
        token_auth_method="private_key_jwt",
        dpop_bound=False,
        status="ACTIVE",
        jwks=keys,
        service_principal_id=principal_id,
        require_par=False,
    )
    await register_client(conn, client)
    return _client_view(client)


async def usable_service_client(conn: AsyncConnection, client_id: str, *, scope: str) -> bool:
    """True when the caller's tenant (RLS) has this client ACTIVE with `client_credentials` and `scope`."""
    c = await load_client(conn, client_id)
    return (
        c is not None
        and c.status == "ACTIVE"
        and "client_credentials" in c.grant_types
        and scope in c.scopes
    )


__all__ = [
    "Authenticated",
    "ClientRegistrationError",
    "OAuthClient",
    "OAuthClientGateway",
    "ServerSettings",
    "Session",
    "SessionPolicy",
    "SessionStatus",
    "TokenError",
    "TokenSettings",
    "authenticate",
    "cert_thumbprint",
    "check_public_jwks",
    "client_tenant",
    "create_session",
    "issue_access_token",
    "issue_handle",
    "last_sign_in",
    "list_clients",
    "load_client",
    "load_session",
    "load_session_by_handle",
    "parse_client_cert",
    "register_client",
    "register_service_client",
    "revoke_principal_sessions",
    "revoke_tenant_sessions",
    "set_client_status",
    "set_session_status",
    "usable_service_client",
]


async def sessions_created_between(
    conn: AsyncConnection, start: datetime, end: datetime
) -> list[tuple[str, datetime]]:
    """(session id, created at) of this tenant's sessions in [start, end), for billing reconciliation."""
    rows = (
        await conn.execute(
            text(
                "SELECT session_id, created_at FROM authn.session "
                "WHERE created_at >= :s AND created_at < :e ORDER BY created_at"
            ),
            {"s": start, "e": end},
        )
    ).all()
    return [(str(r[0]), r[1]) for r in rows]
