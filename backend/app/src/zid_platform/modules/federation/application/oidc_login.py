"""Enterprise OIDC login and safe account linking (ZID-AUTH-001 §9, §9.1, §19, §20; AUTH-W4).

    begin()     -> authorization URL at the upstream IdP (code + PKCE S256 + state + nonce)
    complete()  callback: state (single use, same config revision) -> RFC 9207 `iss` -> code
                exchange (our private_key_jwt, or a brokered secret) -> ID token signature with the
                pinned keys -> exact iss/aud/azp/nonce/time -> acr/amr through the approved mapping
                -> account link -> new identity session

Account linking (§9.1, AUTH-09): an existing issuer+subject link decides the principal. Without
one, a link is created only (a) inside an explicit linking flow started from an authenticated,
fresh, strong session of that principal, or (b) for a brand-new principal when the connection's
policy is JIT. Email is never consulted, so the same address at another IdP cannot attach itself to
an existing account. The upstream call happens outside any database transaction; the state is
spent first, so a replayed callback finds nothing.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_app.resilience import budget_timeout
from zid_context import RequestContext
from zid_crypto import CryptoError, KeyRing, verify_external_jws
from zid_db import tenant_transaction
from zid_outbox import emit

from zid_platform import egress
from zid_platform.modules.authentication.ports import (
    AAL,
    MappingProfile,
    create_external_link,
    find_external_link,
    map_assurance,
    touch_external_link,
)
from zid_platform.modules.commercial.ports import key_auth, record_active_human, record_billable
from zid_platform.modules.configuration.ports import session_expiry
from zid_platform.modules.federation.application.trust_chain import Anchor, resolve
from zid_platform.modules.federation.domain.connection import (
    LOGIN_TTL,
    Connection,
    FederationError,
    UpstreamIdentity,
    usable,
    validate_id_token,
)
from zid_platform.modules.federation.domain.openid_federation import TrustChainError
from zid_platform.modules.federation.infrastructure import connection_store as store
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
    load_principal,
)
from zid_platform.modules.session.ports import (
    Session,
    SessionPolicy,
    SessionStatus,
    create_session,
    issue_handle,
)

SecretResolver = Callable[[str], str]


@dataclass(frozen=True, slots=True)
class FederationSettings:
    issuer: str  # ours: the callback lives under it
    keyring: KeyRing  # signs our private_key_jwt client assertions to the upstream IdP
    home_cell: str
    secrets: SecretResolver
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)
    transport: httpx.AsyncBaseTransport | None = None  # injected in tests; real network otherwise

    @property
    def callback(self) -> str:
        return self.issuer.rstrip("/") + "/federation/callback"


async def apply_federation(
    settings: FederationSettings,
    c: Connection,
    anchor_row: dict[str, Any] | None,
    now: datetime,
) -> Connection:
    """§12: endpoints and keys of a federation-backed connection come from a trust chain resolved
    now (never cached past the chain's expiry); the OP must assert exactly the expected issuer."""
    if c.federation_entity_id is None:
        return c
    if anchor_row is None:
        raise FederationError("TRUST_ANCHOR_NOT_FOUND")
    anchor = Anchor(
        anchor_id=str(anchor_row["anchor_id"]),
        entity_id=anchor_row["entity_id"],
        jwks=anchor_row["jwks"],
        allowed_domains=tuple(anchor_row["allowed_domains"]),
        status=anchor_row["status"],
    )
    try:
        resolved = await resolve(
            anchor, c.federation_entity_id, now=now, transport=settings.transport
        )
    except TrustChainError as e:
        raise FederationError(e.code) from e
    op = resolved.metadata.get("openid_provider") or {}
    if op.get("issuer") != c.issuer:
        raise FederationError("FEDERATION_ISSUER_MISMATCH")
    endpoints = (op.get("authorization_endpoint"), op.get("token_endpoint"))
    if not all(isinstance(u, str) and u.startswith("https://") for u in endpoints):
        raise FederationError("FEDERATION_METADATA_INVALID")
    if not isinstance(op.get("jwks"), dict):
        raise FederationError("FEDERATION_METADATA_INVALID", "inline jwks required")
    return replace(
        c,
        authorization_endpoint=endpoints[0],
        token_endpoint=endpoints[1],
        jwks=op["jwks"],
        metadata_expires_at=min(c.metadata_expires_at, resolved.expires_at),
    )


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


async def begin(
    conn: AsyncConnection,
    settings: FederationSettings,
    c: Connection,
    *,
    client_id: str | None,
    return_to: str | None,
    link_principal_id: str | None,
    now: datetime,
) -> str:
    usable(c, now)
    if c.protocol != "OIDC":
        raise FederationError("FEDERATION_PROTOCOL_MISMATCH")
    verifier, nonce = secrets.token_urlsafe(48), secrets.token_urlsafe(24)
    state = await store.start_login(
        conn,
        tenant_id=c.tenant_id,
        c=c,
        nonce=nonce,
        code_verifier=verifier,
        request_id=None,
        client_id=client_id,
        return_to=return_to,
        link_principal_id=link_principal_id,
        expires_at=now + LOGIN_TTL,
    )
    query = {
        "response_type": "code",
        "client_id": c.client_id or "",
        "redirect_uri": settings.callback,
        "scope": " ".join(c.scopes),
        "state": state,
        "nonce": nonce,
        "code_challenge": _b64(hashlib.sha256(verifier.encode()).digest()),
        "code_challenge_method": "S256",
    }
    endpoint = c.authorization_endpoint or ""
    return endpoint + ("&" if "?" in endpoint else "?") + urlencode(query)


@dataclass(frozen=True, slots=True)
class LoggedIn:
    session: Session
    handle: str
    return_to: str | None
    linked: bool


async def _exchange(
    settings: FederationSettings, c: Connection, login: store.Login, code: str, now: datetime
) -> dict[str, Any]:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.callback,
        "code_verifier": login.code_verifier or "",
        "client_id": c.client_id or "",
    }
    headers = {"Accept": "application/json"}
    if c.client_auth == "private_key_jwt":
        iat = int(now.timestamp())
        form["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
        form["client_assertion"] = settings.keyring.sign(
            {
                "iss": c.client_id,
                "sub": c.client_id,
                "aud": c.token_endpoint,
                "jti": str(uuid.uuid4()),
                "iat": iat,
                "exp": iat + 60,
            },
            typ="JWT",
        )
    else:  # client_secret_basic, by documented exception: the secret comes from the broker
        secret = settings.secrets(c.client_secret_ref or "")
        raw = f"{c.client_id}:{secret}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    try:
        # SEC-19: the token endpoint is tenant-configured or comes from remote metadata; never POST
        # (with a client secret) to a private, loopback or metadata address.
        await egress.guard(c.token_endpoint or "", resolve=settings.transport is None)
    except egress.EgressError as e:
        raise FederationError("FEDERATION_ENDPOINT_BLOCKED", e.code) from e
    try:
        async with httpx.AsyncClient(
            transport=settings.transport, timeout=budget_timeout(5.0), follow_redirects=False
        ) as client:
            response = await client.post(c.token_endpoint or "", data=form, headers=headers)
    except httpx.HTTPError as e:  # upstream outage: no fail-open, no weaker fallback (AUTH-16)
        raise FederationError("FEDERATION_UNAVAILABLE") from e
    if response.status_code != 200 or len(response.content) > 64 * 1024:
        raise FederationError("FEDERATION_TOKEN_EXCHANGE_FAILED", str(response.status_code))
    try:
        body: dict[str, Any] = response.json()
    except ValueError as e:
        raise FederationError("FEDERATION_TOKEN_EXCHANGE_FAILED", "not JSON") from e
    return body


async def complete(
    engine: AsyncEngine,
    settings: FederationSettings,
    *,
    params: dict[str, str],
    base_ctx: RequestContext,
    now: datetime,
) -> LoggedIn:
    state = params.get("state") or ""
    tenant = store.state_tenant(state)
    if tenant is None:
        raise FederationError("FEDERATION_STATE_INVALID")
    ctx = base_ctx.with_identity(tenant, None)
    async with tenant_transaction(engine, ctx) as conn:  # spend the state first, whatever follows
        login = await store.finish_login(conn, state, now)
        c = await store.load(conn, login.connection_id) if login else None
        anchor = (
            await store.load_anchor(conn, c.trust_anchor_id) if c and c.trust_anchor_id else None
        )
    if login is None or c is None:
        raise FederationError("FEDERATION_STATE_INVALID")
    usable(c, now)
    c = await apply_federation(settings, c, anchor, now)
    usable(c, now)
    if c.config_revision != login.config_revision:
        raise FederationError("FEDERATION_CONFIG_CHANGED")
    if "error" in params:
        raise FederationError("FEDERATION_DENIED", params["error"][:64])
    if "iss" in params and params["iss"] != c.issuer:
        raise FederationError("FEDERATION_ISSUER_MISMATCH")  # RFC 9207 mix-up defence
    code = params.get("code")
    if not code:
        raise FederationError("FEDERATION_CODE_MISSING")
    tokens = await _exchange(settings, c, login, code, now)
    id_token = tokens.get("id_token")
    if not isinstance(id_token, str):
        raise FederationError("ID_TOKEN_MISSING")
    try:
        _, claims = verify_external_jws(
            id_token, keys=(c.jwks or {}).get("keys", []), algorithms=c.algorithms
        )
    except CryptoError as e:
        raise FederationError("ID_TOKEN_SIGNATURE_INVALID", e.code) from e
    ident = validate_id_token(claims, c, nonce=login.nonce, now=now)
    return await establish(engine, settings, c, ident, login, ctx=ctx, now=now)


async def establish(
    engine: AsyncEngine,
    settings: FederationSettings,
    c: Connection,
    ident: UpstreamIdentity,
    login: store.Login | None,
    *,
    ctx: RequestContext,
    now: datetime,
) -> LoggedIn:
    """Shared by OIDC and SAML: link -> principal -> identity session."""
    aal = map_assurance(
        MappingProfile(
            profile_id=f"{c.connection_id}@{c.config_revision}",
            issuer=c.issuer,
            acr_levels={k: AAL(min(int(v), 2)) for k, v in c.acr_levels.items()},
            trust_amr=c.trust_amr,
        ),
        issuer=c.issuer,
        acr=ident.acr,
        amr=list(ident.amr),
    )
    async with tenant_transaction(engine, ctx) as conn:
        principal_id, linked = await _principal_for(conn, settings, c, ident, login, now)
        principal, _ = await load_principal(conn, principal_id)
        if principal.status is not PrincipalStatus.ACTIVE:
            raise FederationError("PRINCIPAL_NOT_ACTIVE")
        auth_time = ident.auth_time if ident.auth_time and ident.auth_time <= now else now
        session = Session(
            session_id=str(uuid.uuid4()),
            tenant_id=c.tenant_id,
            principal_id=principal_id,
            auth_time=auth_time,
            authentication_context=f"{c.protocol.lower()}:{c.issuer}"[:256],
            aal=int(min(aal, AAL.Z2)),
            observed_security_revision=principal.security_revision,
            risk_class=1,
            status=SessionStatus.ACTIVE,
            expires_at=await session_expiry(conn, now, settings.session_policy.absolute_ttl),
            last_seen_at=now,
        )
        await create_session(conn, session)
        await record_billable(
            conn,
            tenant_id=c.tenant_id,
            meter="AUTH_EVENT",
            key=key_auth(session.session_id),
            at=now,
            principal_class="HUMAN",
        )
        await record_active_human(conn, tenant_id=c.tenant_id, principal_id=principal_id, at=now)
        await emit(
            conn,
            name="authentication.succeeded",
            tenant_id=c.tenant_id,
            aggregate_type="authentication",
            aggregate_id=session.session_id,
            aggregate_version=1,
            data={
                "principal_id": principal_id,
                "session_id": session.session_id,
                "method": c.protocol.lower(),
                "connection_id": c.connection_id,
                "aal": session.aal,
                "linked": linked,
            },
        )
        handle = await issue_handle(conn, session.session_id)
    return LoggedIn(session, handle, login.return_to if login else None, linked)


async def _principal_for(
    conn: AsyncConnection,
    settings: FederationSettings,
    c: Connection,
    ident: UpstreamIdentity,
    login: store.Login | None,
    now: datetime,
) -> tuple[str, bool]:
    link = await find_external_link(conn, ident.issuer, ident.subject)
    target = login.link_principal_id if login else None
    if target is not None:  # explicit, authenticated linking
        if link is not None and link.principal_id != target:
            raise FederationError("FEDERATION_LINK_CONFLICT")  # governed resolution, never a move
        if link is None:
            await create_external_link(
                conn,
                tenant_id=c.tenant_id,
                principal_id=target,
                issuer=ident.issuer,
                subject=ident.subject,
                connection_id=c.connection_id,
                link_assurance="EXPLICIT_AUTHENTICATED",
                now=now,
            )
            return target, True
        await touch_external_link(conn, link.link_id, now)
        return target, False
    if link is not None:
        await touch_external_link(conn, link.link_id, now)
        return link.principal_id, False
    if c.link_policy != "JIT":
        raise FederationError("FEDERATION_ACCOUNT_NOT_LINKED")
    principal_id = str(uuid.uuid4())
    await create_principal(
        conn,
        Principal(
            principal_id,
            c.tenant_id,
            PrincipalType.HUMAN,
            PrincipalStatus.ACTIVE,
            settings.home_cell,
        ),
    )
    await create_external_link(
        conn,
        tenant_id=c.tenant_id,
        principal_id=principal_id,
        issuer=ident.issuer,
        subject=ident.subject,
        connection_id=c.connection_id,
        link_assurance="JIT",
        now=now,
    )
    return principal_id, True


def env_secrets(prefix: str = "ZID_SECRET__") -> SecretResolver:
    """Local/test resolver: `ref` -> environment variable. Deployed cells use the broker (DEC-F5)."""
    from zid_crypto.secret_broker import EnvSecretBroker, SecretError

    broker = EnvSecretBroker(prefix=prefix)

    def resolve(ref: str) -> str:
        try:
            return broker.get(ref)
        except SecretError as e:
            raise FederationError("FEDERATION_SECRET_UNAVAILABLE") from e

    return resolve


__all__ = ["FederationSettings", "LoggedIn", "begin", "complete", "env_secrets", "establish"]
