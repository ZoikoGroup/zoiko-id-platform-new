"""OAuth 2.0 / OpenID Connect authorization server (ZID-AUTH-001 §6, §7, §8, §13; AUTH-W2/W3).

    par()        POST /oauth2/par        RFC 9126 pushed authorization request
    authorize()  GET /oauth2/authorize   code + PKCE for the browser's authenticated identity session
    token()      POST /oauth2/token      authorization_code, refresh_token, client_credentials,
                                         RFC 8693 token exchange
    revoke()     POST /oauth2/revoke     RFC 7009 refresh-token (family) revocation
    introspect() POST /oauth2/introspect RFC 7662 for authorized resource servers

The tenant is derived from the client registration only. Confidential clients authenticate with
private_key_jwt or tls_client_auth on every call. Tokens are sender-constrained by DPoP or by the
mTLS certificate (never both). Failures that change state (a replayed code or refresh token
revokes what it produced; an expired session is marked so; a nonce is issued) are committed before
the error is returned, so an attacker's retry finds the revoked state.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlencode

from cryptography import x509
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_crypto import CryptoError, KeyRing
from zid_db import tenant_transaction

from zid_platform.modules.principal.ports import PrincipalStatus, load_principal
from zid_platform.modules.session.application.authenticate import verify_dpop_proof
from zid_platform.modules.session.application.client_auth import (
    ClientAuthenticated,
    authenticate_client,
)
from zid_platform.modules.session.application.issue import issue_access_token
from zid_platform.modules.session.domain.oauth import (
    AUTHORIZATION_CODE_TTL,
    DPOP_NONCE_TTL,
    GRANT_AUTHORIZATION_CODE,
    GRANT_CLIENT_CREDENTIALS,
    GRANT_REFRESH_TOKEN,
    GRANT_TOKEN_EXCHANGE,
    ID_TOKEN_TTL,
    ID_TOKEN_TYP,
    INTROSPECT_SCOPE,
    PAR_TTL,
    PAR_URI_PREFIX,
    SUPPORTED_GRANTS,
    TOKEN_TYPE_ACCESS,
    AuthorizationRequest,
    OAuthClient,
    OAuthError,
    client_usable,
    fapi2_token_rules,
    narrow_scope,
    resolve_audience,
    validate_authorization_request,
    verify_pkce,
)
from zid_platform.modules.session.domain.session import (
    Session,
    SessionPolicy,
    SessionStatus,
    check,
)
from zid_platform.modules.session.domain.tokens import (
    ACCESS_TOKEN_TYP,
    DPOP_MAX_AGE,
    MAX_ACCESS_LIFETIME,
    AccessClaims,
    TokenError,
    validate_access_claims,
)
from zid_platform.modules.session.infrastructure import oauth_store, session_store

CONSENT_TTL = timedelta(minutes=10)


@dataclass(frozen=True, slots=True)
class ServerSettings:
    issuer: str
    keyring: KeyRing
    default_audience: str
    session_policy: SessionPolicy = field(default_factory=SessionPolicy)
    consent_url: str | None = (
        None  # the consent UI; without one, third parties get consent_required
    )

    @property
    def token_endpoint(self) -> str:
        return self.issuer.rstrip("/") + "/oauth2/token"


@dataclass(frozen=True, slots=True)
class TokenResult:
    body: dict[str, Any]
    dpop_nonce: str | None = None


class TokenFailure(OAuthError):
    """An OAuth error plus the fresh DPoP nonce to send with it, when the client uses nonces."""

    def __init__(self, error: OAuthError, dpop_nonce: str | None) -> None:
        super().__init__(error.error, error.description, status=error.status)
        self.dpop_nonce = dpop_nonce


def acr_for(aal: int) -> str:
    return f"urn:zoiko:aal:z{aal}"


def with_query(uri: str, params: dict[str, str]) -> str:
    return uri + ("&" if "?" in uri else "?") + urlencode(params)


async def _session_ok(
    conn: AsyncConnection,
    session: Session,
    *,
    now: datetime,
    policy: SessionPolicy,
    idle: bool,
) -> str | None:
    """None when the session may be used now; otherwise the failure code (new state persisted)."""
    try:
        principal, _ = await load_principal(conn, session.principal_id)
        revision, active = principal.security_revision, principal.status is PrincipalStatus.ACTIVE
    except ValueError:
        revision, active = -1, False
    result = check(
        session,
        principal_revision=revision,
        principal_active=active,
        now=now,
        policy=policy,
        idle=idle,
    )
    if result.next_status is not None:
        await session_store.set_status(
            conn, session.session_id, result.next_status, reason=result.code or ""
        )
    return None if result.ok else result.code


async def _tenant_for(engine: AsyncEngine, client_id: str | None, *, status: int) -> str:
    """The client's tenant (from its registration, never from a header) or invalid_client."""
    tenant = await oauth_store.client_tenant(engine, client_id or "")
    if tenant is None:
        raise OAuthError(
            "invalid_client", "unknown or inactive client", status=status, redirectable=False
        )
    return tenant


async def _authenticated_client(
    conn: AsyncConnection,
    settings: ServerSettings,
    form: dict[str, str],
    cert: x509.Certificate | None,
    now: datetime,
) -> tuple[OAuthClient, ClientAuthenticated]:
    client_id = form.get("client_id")
    client = client_usable(await oauth_store.load_client(conn, client_id or ""), client_id)
    auth = await authenticate_client(
        conn,
        client,
        form,
        issuer=settings.issuer,
        token_endpoint=settings.token_endpoint,
        cert=cert,
        now=now,
    )
    return client, auth


# ------------------------------------------------------------------ authorization endpoint (+PAR)


async def par(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    form: dict[str, str],
    dpop: str | None,
    url: str,
    cert: x509.Certificate | None,
    base_ctx: RequestContext,
    now: datetime,
) -> dict[str, Any]:
    """RFC 9126: authenticate the client, validate the request now, hand back a one-time URI."""
    tenant = await _tenant_for(engine, form.get("client_id"), status=401)
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        client, _ = await _authenticated_client(conn, settings, form, cert, now)
        params = {
            k: v
            for k, v in form.items()
            if k not in ("client_assertion", "client_assertion_type", "request_uri")
        }
        jkt = None
        if dpop is not None:  # RFC 9449 §10.1: a DPoP proof at PAR binds the code to that key
            try:
                jkt, _ = verify_dpop_proof(dpop, method="POST", url=url, access_token=None, now=now)
            except TokenError as e:
                raise OAuthError("invalid_dpop_proof", e.code) from e
            if params.get("dpop_jkt") not in (None, jkt):
                raise OAuthError("invalid_dpop_proof", "dpop_jkt differs from the proof key")
        try:
            validate_authorization_request(
                client, params, default_audience=settings.default_audience, pushed=True
            )
        except OAuthError as e:
            raise OAuthError(e.error, e.description, status=400, redirectable=False) from e
        request_uri = await oauth_store.save_par(
            conn,
            tenant_id=tenant,
            client_id=client.client_id,
            params=params,
            dpop_jkt=jkt or params.get("dpop_jkt"),
            expires_at=now + PAR_TTL,
        )
    return {"request_uri": request_uri, "expires_in": int(PAR_TTL.total_seconds())}


async def authorize(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    params: dict[str, str],
    session_handle: str | None,
    base_ctx: RequestContext,
    now: datetime,
) -> str:
    """The redirect location: the code, or an error the client may see. Raises only when the
    redirect URI cannot be trusted (unknown client, unregistered redirect) - those never redirect."""
    tenant = await _tenant_for(engine, params.get("client_id"), status=400)
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        client = await oauth_store.load_client(conn, params.get("client_id", ""))
        pushed = False
        request_uri = params.get("request_uri")
        if request_uri is not None:
            claimed = (
                await oauth_store.claim_par(
                    conn, request_uri, client_id=params.get("client_id", ""), now=now
                )
                if request_uri.startswith(PAR_URI_PREFIX)
                else None
            )
            if claimed is None:
                raise OAuthError(
                    "invalid_request_uri", "request_uri invalid or used", redirectable=False
                )
            stored, par_jkt = claimed
            params = {**stored, "client_id": params["client_id"]}  # only pushed values count
            if par_jkt:
                params["dpop_jkt"] = par_jkt
            pushed = True
        try:
            req = validate_authorization_request(
                client, params, default_audience=settings.default_audience, pushed=pushed
            )
        except OAuthError as e:
            if not e.redirectable:
                raise
            return _error_location(params["redirect_uri"], e, params.get("state"), settings.issuer)

        def fail(error: str, description: str) -> str:
            return _error_location(
                req.redirect_uri, OAuthError(error, description), req.state, settings.issuer
            )

        session = (
            await session_store.load_session_by_handle(conn, session_handle)
            if session_handle
            else None
        )
        if session is None or session.tenant_id != tenant:
            return fail("login_required", "no authenticated session")
        failure = await _session_ok(
            conn, session, now=now, policy=settings.session_policy, idle=True
        )
        if failure is not None:
            return fail("login_required", "session is not usable")
        if "login" in req.prompt or (
            req.max_age is not None and now - session.auth_time > timedelta(seconds=req.max_age)
        ):
            return fail("login_required", "fresh authentication required")
        if not req.client.first_party or "consent" in req.prompt:
            covered = "consent" not in req.prompt and await oauth_store.consent_covers(
                conn, session.principal_id, req.client.client_id, req.scope
            )
            if not covered:
                if "none" in req.prompt or settings.consent_url is None:
                    return fail("consent_required", "the user has not consented to this client")
                ref = await oauth_store.open_consent_request(
                    conn,
                    tenant_id=tenant,
                    principal_id=session.principal_id,
                    session_id=session.session_id,
                    client_id=req.client.client_id,
                    scopes=req.scope,
                    params=params,
                    pushed=pushed,
                    expires_at=now + CONSENT_TTL,
                )
                return with_query(settings.consent_url, {"consent_request": ref})
        return await _issue_code(conn, settings, req, session, tenant, now)


async def _issue_code(
    conn: AsyncConnection,
    settings: ServerSettings,
    req: AuthorizationRequest,
    session: Session,
    tenant: str,
    now: datetime,
) -> str:
    await session_store.touch(conn, session.session_id, now)
    code = await oauth_store.save_code(
        conn,
        tenant_id=tenant,
        client_id=req.client.client_id,
        principal_id=session.principal_id,
        session_id=session.session_id,
        redirect_uri=req.redirect_uri,
        scope=req.scope,
        audience=req.audience,
        code_challenge=req.code_challenge,
        nonce=req.nonce,
        dpop_jkt=req.dpop_jkt,
        expires_at=now + AUTHORIZATION_CODE_TTL,
    )
    return with_query(req.redirect_uri, {"code": code, "state": req.state, "iss": settings.issuer})


async def _consent_context(
    engine: AsyncEngine, ref: str, session_handle: str | None, base_ctx: RequestContext
) -> tuple[str, str, RequestContext]:
    tenant, _, request_id = ref.partition(".")
    if not request_id or session_handle is None:
        raise OAuthError(
            "invalid_request", "consent request unknown", status=404, redirectable=False
        )
    try:
        tenant = str(uuid.UUID(tenant))
    except ValueError as e:
        raise OAuthError(
            "invalid_request", "consent request unknown", status=404, redirectable=False
        ) from e
    return tenant, request_id, base_ctx.with_identity(tenant, None)


async def _consent_session(conn: AsyncConnection, row: dict[str, Any], handle: str) -> Session:
    """The decision must come from the very session that started the authorization."""
    session = await session_store.load_session_by_handle(conn, handle)
    if session is None or str(row["session_id"]) != session.session_id:
        raise OAuthError(
            "access_denied", "consent belongs to another session", status=403, redirectable=False
        )
    if session.status is not SessionStatus.ACTIVE:
        raise OAuthError("login_required", "session is not usable", status=401, redirectable=False)
    return session


async def consent_details(
    engine: AsyncEngine,
    *,
    ref: str,
    session_handle: str | None,
    base_ctx: RequestContext,
    now: datetime,
) -> dict[str, Any]:
    _, request_id, ctx = await _consent_context(engine, ref, session_handle, base_ctx)
    async with tenant_transaction(engine, ctx) as conn:
        row = await oauth_store.peek_consent_request(conn, request_id, now)
        if row is None:
            raise OAuthError(
                "invalid_request", "consent request unknown", status=404, redirectable=False
            )
        await _consent_session(conn, row, session_handle or "")
    return {
        "client_id": row["client_id"],
        "scopes": list(row["scopes"]),
        "expires_at": row["expires_at"].isoformat(),
    }


async def decide_consent(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    ref: str,
    session_handle: str | None,
    approve: bool,
    base_ctx: RequestContext,
    now: datetime,
) -> str:
    """Approve: record the consent and issue the code; deny: access_denied to the client."""
    tenant, request_id, ctx = await _consent_context(engine, ref, session_handle, base_ctx)
    async with tenant_transaction(engine, ctx) as conn:
        row = await oauth_store.claim_consent_request(conn, request_id, now)
        if row is None:
            raise OAuthError(
                "invalid_request", "consent request unknown", status=404, redirectable=False
            )
        session = await _consent_session(conn, row, session_handle or "")
        params = dict(row["params"])
        client = await oauth_store.load_client(conn, str(row["client_id"]))
        req = validate_authorization_request(  # re-validated: the client may have changed since
            client, params, default_audience=settings.default_audience, pushed=bool(row["pushed"])
        )
        if not approve:
            return _error_location(
                req.redirect_uri,
                OAuthError("access_denied", "the user declined"),
                req.state,
                settings.issuer,
            )
        await oauth_store.grant_consent(
            conn,
            tenant_id=tenant,
            principal_id=session.principal_id,
            client_id=req.client.client_id,
            scopes=req.scope,
            now=now,
        )
        return await _issue_code(conn, settings, req, session, tenant, now)


def _error_location(uri: str, e: OAuthError, state: str | None, issuer: str) -> str:
    params = {"error": e.error}
    if e.description:
        params["error_description"] = e.description
    if state:
        params["state"] = state
    params["iss"] = issuer  # RFC 9207: lets the client detect mix-up on errors too
    return with_query(uri, params)


# --------------------------------------------------------------------------------- token endpoint


@dataclass(frozen=True, slots=True)
class _Sender:
    """The key the issued tokens are bound to: a DPoP key, else the mTLS certificate, else none."""

    jkt: str | None
    x5t: str | None

    @property
    def bound(self) -> bool:
        return self.jkt is not None or self.x5t is not None

    @property
    def family_binding(self) -> str | None:
        """How a refresh family records its sender (DPoP thumbprint, or the certificate's)."""
        return self.jkt if self.jkt else (f"x5t#S256:{self.x5t}" if self.x5t else None)


async def _sender(
    conn: AsyncConnection,
    client: OAuthClient,
    auth: ClientAuthenticated,
    *,
    tenant: str,
    dpop: str | None,
    url: str,
    now: datetime,
) -> _Sender | OAuthError:
    """Verify the token request's DPoP proof. An OAuthError is returned (not raised) so the nonce
    issued with it is committed."""
    if dpop is None:
        if client.dpop_bound:
            return OAuthError("invalid_dpop_proof", "a DPoP proof is required for this client")
        return _Sender(None, auth.cert_thumbprint)
    try:
        jkt, proof = verify_dpop_proof(dpop, method="POST", url=url, access_token=None, now=now)
    except TokenError as e:
        return OAuthError("invalid_dpop_proof", e.code)
    if client.require_dpop_nonce and not await oauth_store.nonce_valid(conn, proof.nonce, now):
        return OAuthError("use_dpop_nonce", "a server nonce is required")
    if not await session_store.record_dpop_proof(
        conn,
        tenant_id=tenant,
        jkt=jkt,
        jti=proof.jti,
        issued_at=proof.issued_at,
        window=DPOP_MAX_AGE * 2,
    ):
        return OAuthError("invalid_dpop_proof", "DPOP_PROOF_REPLAYED")
    return _Sender(jkt, None)


async def token(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    form: dict[str, str],
    dpop: str | None,
    url: str,
    base_ctx: RequestContext,
    now: datetime,
    cert: x509.Certificate | None = None,
) -> TokenResult:
    grant_type = form.get("grant_type")
    if grant_type not in SUPPORTED_GRANTS:
        raise OAuthError("unsupported_grant_type", "grant type not supported")
    tenant = await _tenant_for(engine, form.get("client_id"), status=401)
    nonce: str | None = None
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        client, auth = await _authenticated_client(conn, settings, form, cert, now)
        if grant_type not in client.grant_types:
            raise OAuthError("unauthorized_client", "grant type not registered for this client")
        if client.require_dpop_nonce:
            nonce = await oauth_store.issue_nonce(
                conn, tenant_id=tenant, expires_at=now + DPOP_NONCE_TTL
            )
        sender = await _sender(conn, client, auth, tenant=tenant, dpop=dpop, url=url, now=now)
        outcome: TokenResult | OAuthError
        if isinstance(sender, OAuthError):
            outcome = sender
        else:
            try:
                fapi2_token_rules(client, sender_constrained=sender.bound)
                if grant_type == GRANT_AUTHORIZATION_CODE:
                    outcome = await _redeem_code(conn, settings, client, tenant, form, sender, now)
                elif grant_type == GRANT_REFRESH_TOKEN:
                    outcome = await _redeem_refresh(
                        conn, settings, client, tenant, form, sender, now
                    )
                elif grant_type == GRANT_CLIENT_CREDENTIALS:
                    outcome = await _client_credentials(
                        conn, settings, client, tenant, form, sender, now
                    )
                elif grant_type == GRANT_TOKEN_EXCHANGE:
                    outcome = await _exchange(conn, settings, client, tenant, form, sender, now)
                else:  # unreachable: grant_type was checked against SUPPORTED_GRANTS
                    outcome = OAuthError("unsupported_grant_type")
            except OAuthError as e:
                outcome = e
    if isinstance(outcome, OAuthError):
        raise TokenFailure(outcome, nonce)
    return TokenResult(outcome.body, nonce)


def _id_token(
    settings: ServerSettings,
    *,
    client_id: str,
    session: Session,
    nonce: str | None,
    now: datetime,
) -> str:
    iat = int(now.timestamp())
    claims: dict[str, Any] = {
        "iss": settings.issuer,
        "sub": session.principal_id,
        "aud": client_id,
        "azp": client_id,
        "iat": iat,
        "exp": iat + int(ID_TOKEN_TTL.total_seconds()),
        "auth_time": int(session.auth_time.timestamp()),
        "acr": acr_for(session.aal),
        "sid": session.session_id,
    }
    if nonce:
        claims["nonce"] = nonce
    return settings.keyring.sign(claims, typ=ID_TOKEN_TYP)


def _access_response(
    settings: ServerSettings,
    *,
    client: OAuthClient,
    tenant: str,
    session: Session | None,
    principal_id: str,
    scope: frozenset[str],
    audience: str,
    sender: _Sender,
    now: datetime,
    actor: dict[str, object] | None = None,
    lifetime: timedelta | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    risk = session.risk_class if session else 1
    ttl = min(lifetime or MAX_ACCESS_LIFETIME[risk], MAX_ACCESS_LIFETIME[risk])
    access = issue_access_token(
        settings.keyring,
        issuer=settings.issuer,
        audience=audience,
        subject=principal_id,
        tenant_id=tenant,
        client_id=client.client_id,
        now=now,
        scope=scope,
        session_id=session.session_id if session else session_id,
        acr=acr_for(session.aal) if session else None,
        auth_time=session.auth_time if session else None,
        dpop_jkt=sender.jkt,
        cert_thumbprint=None if sender.jkt else sender.x5t,
        actor=actor,
        risk_class=risk,
        lifetime=ttl,
    )
    return {
        "access_token": access,
        "token_type": "DPoP" if sender.jkt else "Bearer",
        "expires_in": int(ttl.total_seconds()),
        "scope": " ".join(sorted(scope)),
    }


async def _redeem_code(
    conn: AsyncConnection,
    settings: ServerSettings,
    client: OAuthClient,
    tenant: str,
    form: dict[str, str],
    sender: _Sender,
    now: datetime,
) -> TokenResult | OAuthError:
    code = form.get("code") or ""
    grant = await oauth_store.claim_code(conn, code, now) if code else None
    if grant is None or grant.client_id != client.client_id:
        return OAuthError("invalid_grant", "authorization code invalid")
    if grant.used:  # replay: whatever the first redemption produced is revoked (RFC 6749 §4.1.2)
        if grant.family_id:
            await oauth_store.end_family(
                conn, grant.family_id, status="REVOKED", reason="code_replay", now=now
            )
        return OAuthError("invalid_grant", "authorization code invalid")
    if now >= grant.expires_at:
        return OAuthError("invalid_grant", "authorization code expired")
    if form.get("redirect_uri") != grant.redirect_uri:
        return OAuthError("invalid_grant", "redirect_uri does not match the authorization request")
    try:
        verify_pkce(form.get("code_verifier"), grant.code_challenge)
    except OAuthError as e:
        return e
    if grant.dpop_jkt is not None and sender.jkt != grant.dpop_jkt:
        return OAuthError("invalid_dpop_proof", "proof key differs from the one authorized")
    session = await session_store.load_session(conn, grant.session_id)
    if session is None or session.principal_id != grant.principal_id:
        return OAuthError("invalid_grant", "session invalid")
    if await _session_ok(conn, session, now=now, policy=settings.session_policy, idle=True):
        return OAuthError("invalid_grant", "session is not usable")
    body = _access_response(
        settings,
        client=client,
        tenant=tenant,
        session=session,
        principal_id=grant.principal_id,
        scope=grant.scope,
        audience=grant.audience,
        sender=sender,
        now=now,
    )
    if GRANT_REFRESH_TOKEN in client.grant_types:
        family_id, refresh = await oauth_store.create_family(
            conn,
            tenant_id=tenant,
            client_id=client.client_id,
            principal_id=grant.principal_id,
            session_id=session.session_id,
            sender_jkt=sender.family_binding,
            scope=grant.scope,
            audience=grant.audience,
            expires_at=min(now + client.refresh_ttl, session.expires_at),
            now=now,
        )
        await oauth_store.link_code_family(conn, code, family_id)
        body["refresh_token"] = refresh
    if "openid" in grant.scope:
        body["id_token"] = _id_token(
            settings, client_id=client.client_id, session=session, nonce=grant.nonce, now=now
        )
    return TokenResult(body)


async def _redeem_refresh(
    conn: AsyncConnection,
    settings: ServerSettings,
    client: OAuthClient,
    tenant: str,
    form: dict[str, str],
    sender: _Sender,
    now: datetime,
) -> TokenResult | OAuthError:
    presented = form.get("refresh_token") or ""
    state = await oauth_store.load_refresh(conn, presented) if presented else None
    invalid = OAuthError("invalid_grant", "refresh token invalid")
    if state is None or state.client_id != client.client_id:
        return invalid
    if state.family_status != "ACTIVE":
        return invalid
    if now >= state.family_expires_at:
        await oauth_store.end_family(
            conn, state.family_id, status="EXPIRED", reason="expired", now=now
        )
        return invalid
    if state.sender_jkt is not None and sender.family_binding != state.sender_jkt:
        # A thief without the key cannot redeem; the family stays usable by its owner.
        return OAuthError("invalid_dpop_proof", "refresh token is bound to another key")
    if state.token_status != "ACTIVE" or state.generation != state.current_generation:
        return await _reuse_detected(conn, tenant, state, now)
    session = await session_store.load_session(conn, state.session_id) if state.session_id else None
    if state.session_id is not None:
        failure = (
            "SESSION_INVALID"
            if session is None or session.principal_id != state.principal_id
            else await _session_ok(
                conn, session, now=now, policy=settings.session_policy, idle=False
            )
        )
        if failure is not None:
            await oauth_store.end_family(
                conn, state.family_id, status="REVOKED", reason=failure, now=now
            )
            return invalid
    try:
        scope = narrow_scope(form.get("scope"), state.scope)
    except OAuthError as e:
        return e
    rotated = await oauth_store.rotate(
        conn, tenant_id=tenant, state=state, token=presented, now=now
    )
    if rotated is None:  # lost a race with another redemption of the same token: that is reuse
        return await _reuse_detected(conn, tenant, state, now)
    body = _access_response(
        settings,
        client=client,
        tenant=tenant,
        session=session,
        principal_id=state.principal_id,
        scope=scope,
        audience=state.audience or settings.default_audience,
        sender=sender,
        now=now,
    )
    body["refresh_token"] = rotated
    return TokenResult(body)


async def _reuse_detected(
    conn: AsyncConnection, tenant: str, state: oauth_store.RefreshState, now: datetime
) -> OAuthError:
    """Probable theft (§8): revoke the family and its session, and signal it."""
    if await oauth_store.end_family(
        conn, state.family_id, status="REVOKED", reason="refresh_reuse", now=now
    ):
        await oauth_store.report_reuse(conn, tenant_id=tenant, state=state)
    if state.session_id is not None:
        session = await session_store.load_session(conn, state.session_id)
        if session is not None and session.status in (
            SessionStatus.ACTIVE,
            SessionStatus.REAUTH_REQUIRED,
        ):
            await session_store.set_status(
                conn, state.session_id, SessionStatus.REVOKED, reason="refresh_reuse"
            )
    return OAuthError("invalid_grant", "refresh token invalid")


async def _service_principal(conn: AsyncConnection, client: OAuthClient) -> str:
    if not client.confidential or not client.service_principal_id:
        raise OAuthError("unauthorized_client", "client has no service principal")
    try:
        principal, _ = await load_principal(conn, client.service_principal_id)
    except ValueError as e:
        raise OAuthError("unauthorized_client", "service principal unknown") from e
    if principal.status is not PrincipalStatus.ACTIVE:
        raise OAuthError("unauthorized_client", "service principal not active")
    return principal.principal_id


async def _client_credentials(
    conn: AsyncConnection,
    settings: ServerSettings,
    client: OAuthClient,
    tenant: str,
    form: dict[str, str],
    sender: _Sender,
    now: datetime,
) -> TokenResult:
    """§6.1: machine-to-machine; the subject is the client's own service principal, never a user,
    and no refresh token is issued (the client simply authenticates again)."""
    subject = await _service_principal(conn, client)
    body = _access_response(
        settings,
        client=client,
        tenant=tenant,
        session=None,
        principal_id=subject,
        scope=narrow_scope(form.get("scope"), client.scopes),
        audience=resolve_audience(client, form.get("resource"), settings.default_audience),
        sender=sender,
        now=now,
    )
    return TokenResult(body)


def _verify_own_token(
    settings: ServerSettings, token: str, now: datetime
) -> tuple[AccessClaims, dict[str, Any]]:
    """One of our access tokens, still valid (any of our audiences). Returns (claims, payload)."""
    try:
        _, payload = settings.keyring.verify(token, typ=ACCESS_TOKEN_TYP)
    except CryptoError as e:
        raise OAuthError("invalid_grant", "subject_token invalid") from e
    aud = payload.get("aud")
    if not isinstance(aud, str):
        raise OAuthError("invalid_grant", "subject_token audience invalid")
    try:
        return validate_access_claims(
            payload, issuer=settings.issuer, audience=aud, now=now
        ), payload
    except TokenError as e:
        raise OAuthError("invalid_grant", f"subject_token {e.code}") from e


async def _exchange(
    conn: AsyncConnection,
    settings: ServerSettings,
    client: OAuthClient,
    tenant: str,
    form: dict[str, str],
    sender: _Sender,
    now: datetime,
) -> TokenResult:
    """RFC 8693, delegation only (§6.1 "controlled delegation/actor-chain"): a registered,
    authenticated client acting for the subject of one of *our* access tokens, for one of its own
    registered audiences, never with more scope or a longer life than the subject token, always
    naming itself as actor (nested onto any existing chain). The session stays referenced, so
    revoking it still stops the exchanged token."""
    if form.get("subject_token_type") != TOKEN_TYPE_ACCESS or not form.get("subject_token"):
        raise OAuthError("invalid_request", "subject_token must be an access token")
    if form.get("actor_token") or form.get("requested_token_type") not in (
        None,
        TOKEN_TYPE_ACCESS,
    ):
        raise OAuthError("invalid_request", "actor tokens and other token types are not supported")
    subject, subject_payload = _verify_own_token(settings, form["subject_token"], now)
    if subject.tenant_id != tenant:
        raise OAuthError("invalid_grant", "subject_token belongs to another tenant")
    actor_principal = await _service_principal(conn, client)
    session = None
    if subject.session_id is not None:
        session = await session_store.load_session(conn, subject.session_id)
        if session is None or session.principal_id != subject.subject:
            raise OAuthError("invalid_grant", "subject session invalid")
        if await _session_ok(conn, session, now=now, policy=settings.session_policy, idle=False):
            raise OAuthError("invalid_grant", "subject session is not usable")
    else:
        try:
            p, _ = await load_principal(conn, subject.subject)
        except ValueError as e:
            raise OAuthError("invalid_grant", "subject unknown") from e
        if p.status is not PrincipalStatus.ACTIVE:
            raise OAuthError("invalid_grant", "subject not active")
    scope = narrow_scope(form.get("scope"), subject.scope & client.scopes)
    audience = resolve_audience(
        client, form.get("resource") or form.get("audience"), settings.default_audience
    )
    actor: dict[str, object] = {"sub": actor_principal, "client_id": client.client_id}
    previous = subject_payload.get("act")
    if isinstance(previous, dict):
        actor["act"] = previous  # the chain grows; earlier actors stay visible
    body = _access_response(
        settings,
        client=client,
        tenant=tenant,
        session=session,
        principal_id=subject.subject,
        scope=scope,
        audience=audience,
        sender=sender,
        now=now,
        actor=actor,
        lifetime=max(subject.expires_at - now, timedelta(seconds=1)),
        session_id=subject.session_id,
    )
    body["issued_token_type"] = TOKEN_TYPE_ACCESS
    return TokenResult(body)


# ----------------------------------------------------------------------- revocation, introspection


async def revoke(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    form: dict[str, str],
    base_ctx: RequestContext,
    now: datetime,
    cert: x509.Certificate | None = None,
) -> None:
    """RFC 7009: revoking a refresh token ends its family. Unknown tokens and access tokens (short-
    lived, self-contained) answer success too, so the endpoint reveals nothing about token validity."""
    tenant = await _tenant_for(engine, form.get("client_id"), status=401)
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        client, _ = await _authenticated_client(conn, settings, form, cert, now)
        presented = form.get("token")
        if not presented:  # the hint is only a hint (RFC 7009 §2.1): always look the token up
            return
        state = await oauth_store.load_refresh(conn, presented)
        if state is not None and state.client_id == client.client_id:
            await oauth_store.end_family(
                conn, state.family_id, status="REVOKED", reason="client_revocation", now=now
            )


async def introspect(
    engine: AsyncEngine,
    settings: ServerSettings,
    *,
    form: dict[str, str],
    base_ctx: RequestContext,
    now: datetime,
    cert: x509.Certificate | None = None,
) -> dict[str, Any]:
    """RFC 7662 for confidential clients registered with the `zid:introspect` scope. An access
    token is active only while its signature, lifetime, tenant and session all still hold."""
    tenant = await _tenant_for(engine, form.get("client_id"), status=401)
    async with tenant_transaction(engine, base_ctx.with_identity(tenant, None)) as conn:
        client, _ = await _authenticated_client(conn, settings, form, cert, now)
        if not client.confidential or INTROSPECT_SCOPE not in client.scopes:
            raise OAuthError("unauthorized_client", "client may not introspect", status=403)
        presented = form.get("token") or ""
        try:
            claims, _ = _verify_own_token(settings, presented, now)
        except OAuthError:
            state = await oauth_store.load_refresh(conn, presented) if presented else None
            if state is None or state.family_status != "ACTIVE" or state.token_status != "ACTIVE":
                return {"active": False}
            if now >= state.family_expires_at:
                return {"active": False}
            return {
                "active": True,
                "token_type": "refresh_token",
                "client_id": state.client_id,
                "sub": state.principal_id,
                "scope": " ".join(sorted(state.scope)),
                "exp": int(state.family_expires_at.timestamp()),
            }
        if claims.tenant_id != tenant:
            return {"active": False}
        if claims.session_id is not None:
            session = await session_store.load_session(conn, claims.session_id)
            if session is None or await _session_ok(
                conn, session, now=now, policy=settings.session_policy, idle=False
            ):
                return {"active": False}
        out: dict[str, Any] = {
            "active": True,
            "token_type": "access_token",
            "client_id": claims.client_id,
            "sub": claims.subject,
            "scope": " ".join(sorted(claims.scope)),
            "iat": int(claims.issued_at.timestamp()),
            "exp": int(claims.expires_at.timestamp()),
            "iss": settings.issuer,
            "tenant_id": claims.tenant_id,
        }
        if claims.cnf_jkt:
            out["cnf"] = {"jkt": claims.cnf_jkt}
        elif claims.cnf_x5t:
            out["cnf"] = {"x5t#S256": claims.cnf_x5t}
        if claims.actor:
            out["act"] = {"sub": claims.actor}
        return out


def metadata(issuer: str, *, oidc: bool) -> dict[str, Any]:
    """RFC 8414 / OpenID Connect Discovery 1.0 for what this server really supports."""
    base = issuer.rstrip("/")
    doc: dict[str, Any] = {
        "issuer": issuer,
        "authorization_endpoint": f"{base}/oauth2/authorize",
        "token_endpoint": f"{base}/oauth2/token",
        "pushed_authorization_request_endpoint": f"{base}/oauth2/par",
        "revocation_endpoint": f"{base}/oauth2/revoke",
        "introspection_endpoint": f"{base}/oauth2/introspect",
        "jwks_uri": f"{base}/oauth2/jwks",
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": sorted(SUPPORTED_GRANTS),
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none", "private_key_jwt", "tls_client_auth"],
        "token_endpoint_auth_signing_alg_values_supported": ["ES256", "Ed25519"],
        "revocation_endpoint_auth_methods_supported": [
            "none",
            "private_key_jwt",
            "tls_client_auth",
        ],
        "introspection_endpoint_auth_methods_supported": ["private_key_jwt", "tls_client_auth"],
        "dpop_signing_alg_values_supported": ["ES256", "Ed25519"],
        "tls_client_certificate_bound_access_tokens": True,
        "require_pushed_authorization_requests": False,
        "authorization_response_iss_parameter_supported": True,
        "prompt_values_supported": ["none", "login", "consent"],
    }
    if oidc:
        doc |= {
            "userinfo_endpoint": f"{base}/oauth2/userinfo",
            "subject_types_supported": ["public"],
            "id_token_signing_alg_values_supported": ["ES256", "Ed25519"],
            "claims_supported": [
                "sub",
                "iss",
                "aud",
                "exp",
                "iat",
                "auth_time",
                "acr",
                "nonce",
                "sid",
            ],
            "scopes_supported": ["openid"],
        }
    return doc
