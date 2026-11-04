"""Enterprise federation API (ZID-AUTH-001 §9, §19, §22, §24; AUTH-W4/W5).

    GET  /federation/connections/{id}/login       start login at the upstream IdP (tenant from client_id)
    POST /federation/connections/{id}/link        explicit linking from a fresh, strong cookie session
    GET  /federation/callback                     OIDC callback -> identity session + cookie
    POST /federation/saml/acs                     SAML ACS (HTTP-POST) -> identity session + cookie
    POST /federation/saml/acs/{tenant}.{conn}     IdP-initiated SAML, only where an approved change enabled it
    GET  /federation/trust-anchors                list         (tenant.identity_provider.change, R3)
    GET  /federation/integrations                 list         (tenant.identity_provider.change, R3)
    POST /federation/integrations:discover        read a provider's published settings from its address (R3 to start)
    POST /federation/integrations                 create       (R3)
    POST /federation/integrations/{id}:rotate     new pinned keys/certificates/metadata (R3)
    POST /federation/integrations/{id}:idp-initiated   IdP-initiated SSO on (R3 + approval) / off
    POST /federation/integrations/{id}:disable    quarantine at once (R3)

`return_to` may only continue a pending /oauth2/authorize request on this issuer: there is no
open redirect (§6.2). Errors before a session exists never echo upstream detail.
"""

from __future__ import annotations

import uuid
from dataclasses import replace as dc_replace
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl, urlsplit

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import DBAPIError
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform import egress
from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.authentication.ports import AAL, effective_level, require_step_up
from zid_platform.modules.commercial.ports import require_entitlement, require_feature
from zid_platform.modules.federation.application import discovery
from zid_platform.modules.federation.application.oidc_login import (
    FederationSettings,
    LoggedIn,
    apply_federation,
    begin,
    complete,
)
from zid_platform.modules.federation.application.saml_login import (
    begin_saml,
    complete_saml,
    complete_unsolicited_saml,
    connection_acs_url,
)
from zid_platform.modules.federation.application.trust_chain import entity_configuration
from zid_platform.modules.federation.domain.connection import Connection, FederationError
from zid_platform.modules.federation.infrastructure import connection_store as store
from zid_platform.modules.federation.ports import count_active_connections
from zid_platform.modules.session.ports import (
    SessionStatus,
    client_tenant,
    load_session_by_handle,
)
from zid_platform.protected import protected_action

router = APIRouter(tags=["federation"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
SESSION_COOKIE = "__Host-zid_session"
ADMIN_ACTION = "tenant.identity_provider.change"
MAX_METADATA_VALIDITY = timedelta(days=400)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _settings(request: Request) -> FederationSettings:
    s: FederationSettings | None = getattr(request.app.state, "federation_settings", None)
    if s is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "Federation is not configured.", http_status=503)
    return s


def _fail(e: FederationError) -> ZidError:
    status = {
        "FEDERATION_CONNECTION_NOT_FOUND": 404,
        "FEDERATION_UNAVAILABLE": 502,
        "FEDERATION_TOKEN_EXCHANGE_FAILED": 502,
        "FEDERATION_SECRET_UNAVAILABLE": 503,
        "FEDERATION_LINK_CONFLICT": 409,
    }.get(e.code, 401)
    return ZidError(e.code, "Federated authentication failed.", http_status=status)


def _return_to(value: str | None) -> str | None:
    if value is None:
        return None
    if not value.startswith("/oauth2/authorize?") or "//" in value or "\\" in value:
        raise ZidError(
            "RETURN_TO_REJECTED",
            "return_to must continue an authorization request.",
            http_status=400,
        )
    return value


@router.get("/federation/connections/{connection_id}/login")
async def login(
    connection_id: uuid.UUID,
    request: Request,
    client_id: Annotated[str, Query(min_length=1, max_length=256)],
    return_to: Annotated[str | None, Query(max_length=2048)] = None,
    reauth: Annotated[bool, Query()] = False,
) -> Response:
    """`reauth=true` makes the person authenticate again at the provider (OIDC `max_age=0`, SAML `ForceAuthn`) and refuses a cached answer."""
    settings = _settings(request)
    tenant = await client_tenant(request_engine(request), client_id)
    if tenant is None:
        raise ZidError("CLIENT_UNKNOWN", "Unknown client.", http_status=400)
    now = datetime.now(UTC)
    ctx = _ctx().with_identity(tenant, None)
    try:
        conn_ = await _effective(request, settings, ctx, str(connection_id), now)
        async with tenant_transaction(request_engine(request), ctx) as c:
            url = await _begin(
                c,
                settings,
                conn_,
                client_id=client_id,
                return_to=_return_to(return_to),
                link_principal_id=None,
                now=now,
                force_reauth=reauth,
            )
    except FederationError as e:
        raise _fail(e) from e
    return RedirectResponse(url, status_code=303, headers={"Cache-Control": "no-store"})


def _missing() -> Connection:
    raise FederationError("FEDERATION_CONNECTION_NOT_FOUND")


async def _effective(
    request: Request,
    settings: FederationSettings,
    ctx: RequestContext,
    connection_id: str,
    now: datetime,
) -> Connection:
    """Load the connection; for a federation-backed one, resolve its trust chain (outside any tx)."""
    async with tenant_transaction(request_engine(request), ctx) as c:
        conn_ = await store.load(c, connection_id) or _missing()
        anchor = (
            await store.load_anchor(c, conn_.trust_anchor_id) if conn_.trust_anchor_id else None
        )
    return await apply_federation(settings, conn_, anchor, now)


async def _begin(c: Any, settings: FederationSettings, conn: Connection, **kw: Any) -> str:
    start = begin_saml if conn.protocol == "SAML" else begin
    return await start(c, settings, conn, **kw)


class LinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)


@router.post("/federation/connections/{connection_id}/link")
async def link(connection_id: uuid.UUID, body: LinkIn, request: Request) -> dict[str, Any]:
    """§9.1: first federation of an existing account needs explicit, authenticated linking."""
    settings = _settings(request)
    webauthn = getattr(request.app.state, "webauthn_settings", None)
    if webauthn is None or request.headers.get("origin") not in webauthn.all_origins:
        raise ZidError("CSRF_ORIGIN_REJECTED", "Request origin is not allowed.", http_status=403)
    tenant = await client_tenant(request_engine(request), body.client_id)
    if tenant is None:
        raise ZidError("CLIENT_UNKNOWN", "Unknown client.", http_status=400)
    now = datetime.now(UTC)
    ctx = _ctx().with_identity(tenant, None)
    async with tenant_transaction(request_engine(request), ctx) as c:
        handle = request.cookies.get(SESSION_COOKIE)
        session = await load_session_by_handle(c, handle) if handle else None
    if (
        session is None
        or session.tenant_id != tenant
        or session.status is not SessionStatus.ACTIVE
        or now >= session.expires_at
    ):
        raise ZidError("SESSION_REQUIRED", "Authentication failed.", http_status=401)
    if effective_level(AAL(session.aal), auth_time=session.auth_time, now=now) < AAL.Z3:
        raise ZidError(
            "STEP_UP_REQUIRED", "A fresh strong authentication is required.", http_status=403
        )
    try:
        conn_ = await _effective(request, settings, ctx, str(connection_id), now)
        async with tenant_transaction(request_engine(request), ctx) as c:
            url = await _begin(
                c,
                settings,
                conn_,
                client_id=body.client_id,
                return_to=None,
                link_principal_id=session.principal_id,
                now=now,
                force_reauth=True,  # linking an identity provider to an account is a trust change: the person proves themselves there again
            )
    except FederationError as e:
        raise _fail(e) from e
    return {"authorization_url": url}


def _session_response(done: LoggedIn) -> Response:
    s = done.session
    out: Response
    if done.return_to:
        out = RedirectResponse(done.return_to, status_code=303)
    else:
        out = JSONResponse(
            {
                "session_id": s.session_id,
                "principal_id": s.principal_id,
                "aal": s.aal,
                "authentication_context": s.authentication_context,
                "linked": done.linked,
                "expires_at": s.expires_at.isoformat(),
            }
        )
    out.headers["Cache-Control"] = "no-store"
    out.set_cookie(
        SESSION_COOKIE,
        done.handle,
        max_age=max(int((s.expires_at - datetime.now(UTC)).total_seconds()), 0),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return out


@router.get("/federation/callback")
async def callback(request: Request) -> Response:
    settings = _settings(request)
    params = {k: v for k, v in request.query_params.items()}
    try:
        done = await complete(
            request_engine(request), settings, params=params, base_ctx=_ctx(), now=datetime.now(UTC)
        )
    except FederationError as e:
        raise _fail(e) from e
    return _session_response(done)


async def _saml_form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip() != (
        "application/x-www-form-urlencoded"
    ):
        raise ZidError("SAML_RESPONSE_INVALID", "Federated authentication failed.", http_status=400)
    body = await request.body()
    if len(body) > 400 * 1024:
        raise ZidError("SAML_RESPONSE_INVALID", "Federated authentication failed.", http_status=400)
    return dict(parse_qsl(body.decode("ascii", errors="replace")))


@router.post("/federation/saml/acs")
async def saml_acs(request: Request) -> Response:
    settings = _settings(request)
    form = await _saml_form(request)
    try:
        done = await complete_saml(
            request_engine(request),
            settings,
            saml_response=form.get("SAMLResponse"),
            relay_state=form.get("RelayState"),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except FederationError as e:
        raise _fail(e) from e
    return _session_response(done)


@router.post("/federation/saml/acs/{connection_ref}")
async def saml_acs_unsolicited(connection_ref: str, request: Request) -> Response:
    """Governed IdP-initiated SSO: only for a connection an approved R3 change enabled."""
    settings = _settings(request)
    form = await _saml_form(request)
    try:
        done = await complete_unsolicited_saml(
            request_engine(request),
            settings,
            connection_ref=connection_ref,
            saml_response=form.get("SAMLResponse"),
            relay_state=form.get("RelayState"),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except FederationError as e:
        raise _fail(e) from e
    return _session_response(done)


@router.get("/.well-known/openid-federation")
async def our_entity_configuration(request: Request) -> Response:
    """§18: Zoiko's self-signed entity configuration (relying party + OpenID provider)."""
    settings = _settings(request)
    base = settings.issuer.rstrip("/")
    rings = request.app.state.keyrings
    token = entity_configuration(
        rings.federation,  # entity statements have their own key set (AUTH §17)
        issuer=settings.issuer,
        authority_hints=getattr(request.app.state, "federation_authority_hints", ()),
        metadata={
            "federation_entity": {"organization_name": "Zoiko iD"},
            "openid_relying_party": {
                "redirect_uris": [settings.callback],
                "response_types": ["code"],
                "grant_types": ["authorization_code"],
                "token_endpoint_auth_method": "private_key_jwt",
                "client_registration_types": ["explicit"],
                "jwks": rings.token.jwks(),
            },
            "openid_provider": {
                "issuer": settings.issuer,
                "authorization_endpoint": f"{base}/oauth2/authorize",
                "token_endpoint": f"{base}/oauth2/token",
                "jwks": rings.token.jwks(),
                "client_registration_types_supported": ["explicit"],
            },
        },
        now=datetime.now(UTC),
    )
    return Response(token, media_type="application/entity-statement+jwt")


class AnchorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    entity_id: str = Field(pattern=r"^https://", max_length=512)
    jwks: dict[str, Any]
    allowed_domains: list[str] = Field(min_length=1, max_length=20)
    approval_request_id: str | None = None


def _anchor_view(a: dict[str, Any]) -> dict[str, Any]:
    """Pinned keys are counted, not returned."""
    return {
        "anchor_id": str(a["anchor_id"]),
        "entity_id": a["entity_id"],
        "allowed_domains": list(a["allowed_domains"]),
        "pinned_keys": a["pinned_keys"],
        "status": a["status"],
        "revision": a["revision"],
        "created_at": a["created_at"].isoformat(),
    }


@router.get("/federation/trust-anchors")
async def list_anchors(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        await protected_action(c, caller, action=ADMIN_ACTION, resource="federation_trust_anchor:*")
        return {"items": [_anchor_view(a) for a in await store.list_anchors(c)]}


@router.post("/federation/trust-anchors", status_code=201)
async def create_anchor(body: AnchorIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Configured, pinned trust: a federation can only be joined through an anchor added here."""
    if not isinstance(body.jwks.get("keys"), list) or not body.jwks["keys"]:
        raise http_error("VALIDATION_FAILED", "an anchor pins at least one key")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource="federation_trust_anchor:*",
            change=f"anchor:{body.entity_id}",
            approval_request_id=body.approval_request_id,
        )
        aid = await store.create_anchor(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            entity_id=body.entity_id,
            jwks=body.jwks,
            allowed_domains=[d.lower() for d in body.allowed_domains],
        )
    return {"anchor_id": aid, "entity_id": body.entity_id, "status": "ACTIVE"}


@router.post("/federation/trust-anchors/{anchor_id}:disable")
async def disable_anchor(
    anchor_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource="federation_trust_anchor:*",
            change="disable",
        )
        if await store.set_anchor_status(conn, str(anchor_id), "QUARANTINED") is None:
            raise http_error("TRUST_ANCHOR_NOT_FOUND")
    return {"anchor_id": str(anchor_id), "status": "QUARANTINED"}


# ------------------------------------------------------------------------------- administration


Alg = Literal["ES256", "Ed25519", "RS256", "PS256"]


def _default_algs() -> list[Alg]:
    return ["ES256", "RS256"]


class IntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["OIDC", "SAML"]
    display_name: str = Field(min_length=1, max_length=128)
    issuer: str = Field(min_length=1, max_length=512)
    metadata_expires_at: datetime
    acr_levels: dict[str, int] = Field(default_factory=dict)
    trust_amr: bool = True
    link_policy: Literal["EXISTING_ONLY", "JIT"] = "EXISTING_ONLY"
    client_id: str | None = None
    client_auth: Literal["private_key_jwt", "client_secret_basic"] | None = None
    client_secret_ref: str | None = Field(default=None, max_length=128)
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    jwks: dict[str, Any] | None = None
    algorithms: list[Alg] = Field(default_factory=_default_algs)
    scopes: list[str] = Field(default_factory=lambda: ["openid"])
    sso_url: str | None = None
    idp_certificates: list[str] = Field(default_factory=list)
    sp_entity_id: str | None = None
    sp_decryption_key_ref: str | None = Field(default=None, max_length=128)
    require_encrypted_assertions: bool = False
    allow_idp_initiated: bool = False
    trust_anchor_id: uuid.UUID | None = None
    federation_entity_id: str | None = Field(default=None, pattern=r"^https://")
    approval_request_id: str | None = None


class RotateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    metadata_expires_at: datetime
    jwks: dict[str, Any] | None = None
    idp_certificates: list[str] | None = None
    client_auth: Literal["private_key_jwt", "client_secret_basic"] | None = None
    client_secret_ref: str | None = None
    sp_decryption_key_ref: str | None = Field(default=None, max_length=128)
    approval_request_id: str | None = None


class IdpInitiatedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    targets: list[str] = Field(default_factory=list, max_length=10)
    approval_request_id: str | None = None


def _target(url: str) -> str:
    u = urlsplit(url)
    if u.scheme != "https" or not u.hostname or u.fragment or u.username or len(url) > 512:
        raise http_error("VALIDATION_FAILED", "targets are exact https URLs without fragments")
    return url


def idp_initiated_change(enabled: bool, targets: list[str]) -> str:
    """What the approval binds to: on/off and exactly these landing URLs."""
    return f"idp_initiated:{'on' if enabled else 'off'}:{','.join(sorted(targets))}"


def _view(c: Connection) -> dict[str, Any]:
    return {
        "connection_id": c.connection_id,
        "protocol": c.protocol,
        "display_name": c.display_name,
        "issuer": c.issuer,
        "status": c.status,
        "config_revision": c.config_revision,
        "metadata_expires_at": c.metadata_expires_at.isoformat(),
        "link_policy": c.link_policy,
        "allow_idp_initiated": c.allow_idp_initiated,
        "idp_initiated_targets": list(c.idp_initiated_targets),
        "require_encrypted_assertions": c.require_encrypted_assertions,
    }


def _validity(expires: datetime) -> None:
    now = datetime.now(UTC)
    if not now < expires <= now + MAX_METADATA_VALIDITY:
        raise http_error("VALIDATION_FAILED", "metadata validity must be in the future and bounded")


def _endpoints_safe(*urls: str | None) -> None:
    """SEC-19: a provider URL the platform will call must be a public https DNS name."""
    for url in urls:
        if url is None:
            continue
        try:
            egress.check_url(url)
        except egress.EgressError as e:
            raise http_error("VALIDATION_FAILED", f"provider endpoint refused: {e.detail}") from e


class DiscoverIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    protocol: Literal["OIDC", "SAML"]
    # OIDC: the issuer (https://login.example.com). SAML: the provider's metadata address.
    url: str = Field(min_length=8, max_length=2048)


@router.post("/federation/integrations:discover")
async def discover_provider(
    body: DiscoverIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Read a provider's published settings so a person types an address, not endpoints, keys and certificates. The
    result is only returned: it is pinned when the integration is created, exactly as if it had been typed in."""
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        await protected_action(c, caller, action=ADMIN_ACTION, resource="federation_connection:*")
    transport = getattr(request.app.state, "federation_transport", None)
    async with discovery.client(transport) as http:
        try:
            if body.protocol == "OIDC":
                return {
                    "protocol": "OIDC",
                    **await discovery.discover_oidc(body.url, http, resolve=transport is None),
                }
            return {
                "protocol": "SAML",
                **await discovery.discover_saml(body.url, http, resolve=transport is None),
            }
        except discovery.DiscoveryError as e:
            raise ZidError(e.code, e.detail, http_status=422) from e


@router.get("/federation/integrations")
async def list_integrations(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        await protected_action(c, caller, action=ADMIN_ACTION, resource="federation_connection:*")
        return {"items": [_view(x) for x in await store.list_all(c)]}


@router.post("/federation/integrations", status_code=201)
async def create_integration(
    body: IntegrationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _validity(body.metadata_expires_at)
    _endpoints_safe(body.authorization_endpoint, body.token_endpoint)
    if body.allow_idp_initiated:
        raise http_error(
            "VALIDATION_FAILED", "IdP-initiated SAML is enabled only by a separate R3 change"
        )
    cid = str(uuid.uuid4())
    c = Connection(
        connection_id=cid,
        tenant_id=caller.ctx.tenant_id or "",
        protocol=body.protocol,
        display_name=body.display_name,
        issuer=body.issuer,
        status="ACTIVE",
        config_revision=1,
        metadata_expires_at=body.metadata_expires_at,
        acr_levels={k: min(v, 2) for k, v in body.acr_levels.items()},
        trust_amr=body.trust_amr,
        link_policy=body.link_policy,
        client_id=body.client_id,
        client_auth=body.client_auth,
        client_secret_ref=body.client_secret_ref,
        authorization_endpoint=body.authorization_endpoint,
        token_endpoint=body.token_endpoint,
        jwks=body.jwks,
        algorithms=tuple(body.algorithms),
        scopes=tuple(body.scopes) if "openid" in body.scopes else ("openid", *body.scopes),
        sso_url=body.sso_url,
        idp_certificates=tuple(body.idp_certificates),
        sp_entity_id=body.sp_entity_id,
        sp_decryption_key_ref=body.sp_decryption_key_ref,
        require_encrypted_assertions=body.require_encrypted_assertions,
        trust_anchor_id=str(body.trust_anchor_id) if body.trust_anchor_id else None,
        federation_entity_id=body.federation_entity_id,
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource=f"federation_connection:{cid}",
            change=f"create:{body.issuer}",
            approval_request_id=body.approval_request_id,
        )
        # A paid extra: an enterprise SSO connection beyond the plan (CR-024). Never on a login path.
        await require_entitlement(
            conn,
            "saml_sso",
            used=await count_active_connections(conn),
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        await require_feature(
            conn,
            "advanced.enterprise-sso-saml-oidc-federation",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        await require_step_up(
            request,
            conn,
            caller,
            action="change-sso-federation-trust-or-scim-privilege-mapping",
            resource=f"federation_connection:{cid}",
            change={"operation": "CREATE", "issuer": body.issuer},
        )
        try:
            async with conn.begin_nested():
                await store.create(conn, c)
        except DBAPIError as e:  # a profile CHECK failed: a bad request, not a 500
            raise http_error(
                "VALIDATION_FAILED", "integration violates the federation profile"
            ) from e
    return _view(c)


@router.post("/federation/integrations/{connection_id}:rotate")
async def rotate(
    connection_id: uuid.UUID, body: RotateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """New pinned trust material: the retired key/certificate stops verifying at once (AUTH-15)."""
    _validity(body.metadata_expires_at)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource=f"federation_connection:{connection_id}",
            change="rotate",
            approval_request_id=body.approval_request_id,
        )
        old = await store.load(conn, str(connection_id), lock=True)
        if old is None:
            raise http_error("FEDERATION_CONNECTION_NOT_FOUND")
        new = dc_replace(
            old,
            config_revision=old.config_revision + 1,
            metadata_expires_at=body.metadata_expires_at,
            jwks=body.jwks if body.jwks is not None else old.jwks,
            idp_certificates=tuple(body.idp_certificates)
            if body.idp_certificates is not None
            else old.idp_certificates,
            client_auth=body.client_auth or old.client_auth,
            client_secret_ref=body.client_secret_ref or old.client_secret_ref,
            sp_decryption_key_ref=body.sp_decryption_key_ref or old.sp_decryption_key_ref,
        )
        try:
            async with conn.begin_nested():
                await store.replace(conn, old, new, "rotated")
        except DBAPIError as e:
            raise http_error("VALIDATION_FAILED", "rotation violates the federation profile") from e
    return _view(new)


@router.post("/federation/integrations/{connection_id}:idp-initiated")
async def set_idp_initiated(
    connection_id: uuid.UUID, body: IdpInitiatedIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Enabling IdP-initiated SAML is an R3 change that always needs a second administrator's
    approval bound to these exact targets; switching it off needs no approval."""
    targets = sorted({_target(t) for t in body.targets}) if body.enabled else []
    if body.enabled and not targets:
        raise http_error("VALIDATION_FAILED", "at least one landing target is required")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource=f"federation_connection:{connection_id}",
            change=idp_initiated_change(body.enabled, targets),
            approval_request_id=body.approval_request_id,
            require_approval=body.enabled,
        )
        old = await store.load(conn, str(connection_id), lock=True)
        if old is None:
            raise http_error("FEDERATION_CONNECTION_NOT_FOUND")
        if old.protocol != "SAML":
            raise http_error("VALIDATION_FAILED", "IdP-initiated SSO is a SAML profile")
        new = dc_replace(
            old,
            config_revision=old.config_revision + 1,
            allow_idp_initiated=body.enabled,
            idp_initiated_targets=tuple(targets),
        )
        await store.replace(
            conn, old, new, "idp_initiated_enabled" if body.enabled else "idp_initiated_disabled"
        )
    out = _view(new)
    if new.allow_idp_initiated:
        out["acs_url"] = connection_acs_url(_settings(request), new)
    return out


@router.post("/federation/integrations/{connection_id}:disable")
async def disable(connection_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action=ADMIN_ACTION,
            resource=f"federation_connection:{connection_id}",
            change="disable",
        )
        old = await store.load(conn, str(connection_id), lock=True)
        if old is None:
            raise http_error("FEDERATION_CONNECTION_NOT_FOUND")
        new = dc_replace(old, status="QUARANTINED", config_revision=old.config_revision + 1)
        await store.replace(conn, old, new, "quarantined")
    return _view(new)
