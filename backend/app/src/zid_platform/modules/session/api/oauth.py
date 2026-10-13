"""OAuth 2.0 / OpenID Connect endpoints (ZID-AUTH-001 §6, §22; RFC 6749, 7009, 8414, 9449; AUTH-W2).

    POST /oauth2/par                                RFC 9126 pushed authorization request
    GET  /oauth2/authorize                          code + PKCE (browser, identity-session cookie)
    POST /oauth2/token                              authorization_code | refresh_token |
                                                    client_credentials | token exchange
    POST /oauth2/revoke                             RFC 7009
    POST /oauth2/introspect                         RFC 7662 (authorized confidential clients)
    GET  /oauth2/userinfo                           OIDC UserInfo (access token with `openid`)
    GET|POST /oauth2/consent/{tenant}.{request}     consent UI support (session cookie, CSRF origin)
    GET  /oauth2/consents | POST /oauth2/consents/{client}:revoke   the user's consents
    GET  /oauth2/jwks                               issuer verification keys (rotation window)
    GET  /.well-known/oauth-authorization-server    RFC 8414 metadata
    GET  /.well-known/openid-configuration          OIDC Discovery

Errors here use the OAuth wire format (`{"error", "error_description"}`), not the platform envelope,
because OAuth clients and conformance suites expect it. Token responses are never cached.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl

from cryptography import x509
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.session.application.client_auth import parse_client_cert
from zid_platform.modules.session.application.oauth_server import (
    ServerSettings,
    authorize,
    consent_details,
    decide_consent,
    introspect,
    metadata,
    par,
    revoke,
    token,
)
from zid_platform.modules.session.domain.oauth import OAuthError
from zid_platform.modules.session.infrastructure import oauth_store

router = APIRouter(tags=["oauth"])
SESSION_COOKIE = "__Host-zid_session"
NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _settings(request: Request) -> ServerSettings:
    settings: ServerSettings | None = getattr(request.app.state, "oauth_settings", None)
    if settings is None or getattr(request.app.state, "engine", None) is None:
        raise OAuthError(
            "temporarily_unavailable", "authorization server not configured", status=503
        )
    return settings


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _oauth_error(e: OAuthError) -> JSONResponse:
    body = {"error": e.error}
    if e.description:
        body["error_description"] = e.description
    headers = dict(NO_STORE)
    if e.status == 401:
        headers["WWW-Authenticate"] = 'Basic realm="zoiko-id"'  # RFC 6749 §5.2 for invalid_client
    nonce = getattr(e, "dpop_nonce", None)
    if nonce:
        headers["DPoP-Nonce"] = nonce
    return JSONResponse(body, status_code=e.status, headers=headers)


def _single(pairs: list[tuple[str, str]]) -> dict[str, str]:
    """RFC 6749 §3.1/§3.2: parameters must not be included more than once."""
    out: dict[str, str] = {}
    for k, v in pairs:
        if k in out:
            raise OAuthError("invalid_request", f"duplicate parameter {k}", redirectable=False)
        out[k] = v
    return out


async def _form(request: Request) -> dict[str, str]:
    if request.headers.get("content-type", "").split(";")[0].strip() != (
        "application/x-www-form-urlencoded"
    ):
        raise OAuthError("invalid_request", "body must be application/x-www-form-urlencoded")
    body = await request.body()
    if len(body) > 16384:
        raise OAuthError("invalid_request", "request too large")
    try:
        pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=False, strict_parsing=False)
    except UnicodeDecodeError as e:
        raise OAuthError("invalid_request", "body must be UTF-8") from e
    return _single(pairs)


def _cert(request: Request) -> x509.Certificate | None:
    """The mTLS client certificate, only through the header the deployment configured."""
    header: str | None = getattr(request.app.state, "mtls_cert_header", None)
    return parse_client_cert(request.headers.get(header)) if header else None


def _endpoint_url(settings: ServerSettings, request: Request) -> str:
    """The URL a DPoP proof's htu must name: the issuer's endpoint as published in metadata."""
    return settings.issuer.rstrip("/") + request.url.path


@router.get("/oauth2/authorize", include_in_schema=True, response_class=RedirectResponse)
async def authorize_endpoint(request: Request) -> Response:
    try:
        settings = _settings(request)
        params = _single(list(request.query_params.multi_items()))
        location = await authorize(
            request.app.state.engine,
            settings,
            params=params,
            session_handle=request.cookies.get(SESSION_COOKIE),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except OAuthError as e:
        return _oauth_error(e)  # shown to the user agent, never redirected (untrusted redirect)
    return RedirectResponse(location, status_code=303, headers=NO_STORE)


@router.post("/oauth2/token")
async def token_endpoint(request: Request) -> Response:
    try:
        settings = _settings(request)
        result = await token(
            request.app.state.engine,
            settings,
            form=await _form(request),
            dpop=request.headers.get("dpop"),
            url=_endpoint_url(settings, request),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
            cert=_cert(request),
        )
    except OAuthError as e:  # TokenFailure included: it carries the fresh DPoP nonce
        return _oauth_error(e)
    headers = dict(NO_STORE)
    if result.dpop_nonce:
        headers["DPoP-Nonce"] = result.dpop_nonce
    return JSONResponse(result.body, headers=headers)


@router.post("/oauth2/revoke")
async def revoke_endpoint(request: Request) -> Response:
    try:
        await revoke(
            request.app.state.engine,
            _settings(request),
            form=await _form(request),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
            cert=_cert(request),
        )
    except OAuthError as e:
        return _oauth_error(e)
    return Response(status_code=200, headers=NO_STORE)


@router.post("/oauth2/par", status_code=201)
async def par_endpoint(request: Request) -> Response:
    try:
        settings = _settings(request)
        out = await par(
            request.app.state.engine,
            settings,
            form=await _form(request),
            dpop=request.headers.get("dpop"),
            url=_endpoint_url(settings, request),
            cert=_cert(request),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(out, status_code=201, headers=NO_STORE)


@router.post("/oauth2/introspect")
async def introspect_endpoint(request: Request) -> Response:
    try:
        out = await introspect(
            request.app.state.engine,
            _settings(request),
            form=await _form(request),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
            cert=_cert(request),
        )
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(out, headers=NO_STORE)


@router.get("/oauth2/userinfo")
async def userinfo_endpoint(caller: Annotated[Caller, Depends(authenticated_caller)]) -> Response:
    """OIDC Core §5.3. Zoiko keeps no profile PII in tokens or here: subject and tenant only."""
    if "openid" not in caller.scope:
        return JSONResponse(
            {"error": "insufficient_scope"},
            status_code=403,
            headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="openid"'},
        )
    return JSONResponse(
        {"sub": caller.ctx.principal_id, "tenant_id": caller.ctx.tenant_id}, headers=NO_STORE
    )


@router.get("/oauth2/jwks")
async def jwks_endpoint(request: Request) -> dict[str, Any]:
    settings: ServerSettings | None = getattr(request.app.state, "oauth_settings", None)
    return settings.keyring.jwks() if settings else {"keys": []}


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata(request: Request) -> Response:
    try:
        settings = _settings(request)
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(metadata(settings.issuer, oidc=False))


@router.get("/.well-known/openid-configuration")
async def oidc_metadata(request: Request) -> Response:
    try:
        settings = _settings(request)
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(metadata(settings.issuer, oidc=True))


# ---------------------------------------------------------------------- consent (third parties)


def _origin_ok(request: Request) -> bool:
    webauthn = getattr(request.app.state, "webauthn_settings", None)
    return webauthn is not None and request.headers.get("origin") in webauthn.all_origins


@router.get("/oauth2/consent/{ref}")
async def consent_request(ref: str, request: Request) -> Response:
    """For the consent UI: which client asks for which scopes (the user's own session only)."""
    try:
        details = await consent_details(
            request.app.state.engine,
            ref=ref,
            session_handle=request.cookies.get(SESSION_COOKIE),
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse(details, headers=NO_STORE)


class ConsentDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "deny"]


@router.post("/oauth2/consent/{ref}")
async def consent_decision(ref: str, body: ConsentDecision, request: Request) -> Response:
    if not _origin_ok(request):  # cookie-authenticated, state-changing: CSRF origin check
        return _oauth_error(OAuthError("access_denied", "origin not allowed", status=403))
    try:
        location = await decide_consent(
            request.app.state.engine,
            _settings(request),
            ref=ref,
            session_handle=request.cookies.get(SESSION_COOKIE),
            approve=body.decision == "approve",
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except OAuthError as e:
        return _oauth_error(e)
    return JSONResponse({"redirect_to": location}, headers=NO_STORE)


@router.get("/oauth2/consents")
async def my_consents(
    request: Request, caller: Annotated[Caller, Depends(authenticated_caller)]
) -> dict[str, Any]:
    async with tenant_transaction(request.app.state.engine, caller.ctx) as conn:
        return {"items": await oauth_store.list_consents(conn, caller.ctx.principal_id or "")}


@router.post("/oauth2/consents/{client_id}:revoke")
async def revoke_my_consent(
    client_id: str, request: Request, caller: Annotated[Caller, Depends(authenticated_caller)]
) -> Response:
    async with tenant_transaction(request.app.state.engine, caller.ctx) as conn:
        n = await oauth_store.revoke_consent(
            conn, caller.ctx.principal_id or "", client_id, datetime.now(UTC)
        )
    if n == 0:
        return JSONResponse({"error": "not_found"}, status_code=404)
    return JSONResponse({"client_id": client_id, "status": "REVOKED"}, headers=NO_STORE)
