"""Native passkey authentication API (ZID-AUTH-001 §3, §5.1, §22; AUTH-W1).

    POST /authn/webauthn/registration/options     session cookie or enrollment ticket
    POST /authn/webauthn/registration/verify
    POST /authn/webauthn/authentication/options   discoverable passkey login
    POST /authn/webauthn/authentication/verify    -> identity session + __Host- cookie
    POST /authn/step-up                           fresh strong authentication for the cookie session
    POST /authn/logout                            revoke the cookie session
    GET  /authn/authenticators                    the caller's authenticators (access token)
    POST /authn/authenticators/{id}:revoke        protected: AAL-Z3 (fresh step-up), never the last

The tenant comes from the OAuth client the login is for (`client_id`), never from a header.
Cookie-authenticated, state-changing requests must carry an allowlisted Origin (CSRF, §5.1).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.application import authenticators as manage
from zid_platform.modules.authentication.application import recovery
from zid_platform.modules.authentication.application.webauthn import (
    WebAuthnSettings,
    authentication_options,
    authentication_verify,
    record_failure,
    registration_options,
    registration_verify,
)
from zid_platform.modules.authentication.domain.authenticator import AuthnError
from zid_platform.modules.authentication.domain.recovery import RecoveryError
from zid_platform.modules.authentication.infrastructure import authenticator_store as store
from zid_platform.modules.session.ports import (
    Session,
    SessionStatus,
    client_tenant,
    load_session_by_handle,
    set_session_status,
)

router = APIRouter(tags=["authentication"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
SESSION_COOKIE = "__Host-zid_session"

_UNAUTHENTICATED = frozenset(
    {
        "WEBAUTHN_CHALLENGE_INVALID",
        "WEBAUTHN_RESPONSE_INVALID",
        "WEBAUTHN_VERIFICATION_FAILED",
        "AUTHENTICATOR_NOT_ACTIVE",
        "PRINCIPAL_NOT_ACTIVE",
        "ENROLLMENT_TICKET_INVALID",
        "NO_STRONG_AUTHENTICATOR",
        "SESSION_REQUIRED",
    }
)


def _error(e: AuthnError) -> ZidError:
    if e.code in _UNAUTHENTICATED:
        return ZidError(e.code, "Authentication failed.", http_status=401)
    if e.code == "STEP_UP_REQUIRED":
        return ZidError(
            e.code,
            "A fresh strong authentication is required.",
            http_status=403,
            headers={
                "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", '
                'acr_values="urn:zoiko:aal:z3", max_age=300'
            },
        )
    if e.code.endswith("NOT_FOUND"):
        return ZidError(e.code, "Not found.", http_status=404)
    if e.code == "CSRF_ORIGIN_REJECTED":
        return ZidError(e.code, "Request origin is not allowed.", http_status=403)
    return ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=409)


def _settings(request: Request) -> WebAuthnSettings:
    s: WebAuthnSettings | None = getattr(request.app.state, "webauthn_settings", None)
    if s is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "Passkey authentication is not configured.", http_status=503
        )
    return s


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _check_origin(request: Request, settings: WebAuthnSettings) -> None:
    if request.headers.get("origin") not in settings.origins:
        raise _error(AuthnError("CSRF_ORIGIN_REJECTED"))


async def _tenant(request: Request, client_id: str) -> str:
    tenant = await client_tenant(request_engine(request), client_id)
    if tenant is None:
        raise ZidError("CLIENT_UNKNOWN", "Unknown client.", http_status=400)
    return tenant


async def _cookie_session(request: Request, conn: Any, tenant: str) -> Session | None:
    handle = request.cookies.get(SESSION_COOKIE)
    session = await load_session_by_handle(conn, handle) if handle else None
    now = datetime.now(UTC)
    if (
        session is None
        or session.tenant_id != tenant
        or session.status is not SessionStatus.ACTIVE
        or now >= session.expires_at
    ):
        return None
    return session


def _set_cookie(response: Response, handle: str, session: Session) -> None:
    max_age = int((session.expires_at - datetime.now(UTC)).total_seconds())
    response.set_cookie(
        SESSION_COOKIE,
        handle,
        max_age=max(max_age, 0),
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )


class ClientRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)


class RegistrationOptionsIn(ClientRef):
    enrollment_ticket: str | None = Field(default=None, max_length=128)
    user_name: str | None = Field(default=None, max_length=64)


class CredentialIn(ClientRef):
    credential: dict[str, Any]
    label: str | None = Field(default=None, max_length=64)


@router.post("/authn/webauthn/registration/options")
async def registration_options_endpoint(
    body: RegistrationOptionsIn, request: Request
) -> dict[str, Any]:
    settings = _settings(request)
    _check_origin(request, settings)
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        try:
            if body.enrollment_ticket:
                ticket = await store.peek_ticket(c, body.enrollment_ticket, now)
                if ticket is None:
                    raise AuthnError("ENROLLMENT_TICKET_INVALID")
                principal_id, session_id, ticket_hash = (
                    ticket.principal_id,
                    None,
                    ticket.ticket_hash,
                )
            else:
                session = await _cookie_session(request, c, tenant)
                if session is None:
                    raise AuthnError("SESSION_REQUIRED")
                principal_id, session_id, ticket_hash = (
                    session.principal_id,
                    session.session_id,
                    None,
                )
            return await registration_options(
                c,
                settings,
                tenant_id=tenant,
                principal_id=principal_id,
                session_id=session_id,
                ticket_hash=ticket_hash,
                user_name=body.user_name or principal_id,
                now=now,
            )
        except AuthnError as e:
            raise _error(e) from e


@router.post("/authn/webauthn/registration/verify", status_code=201)
async def registration_verify_endpoint(body: CredentialIn, request: Request) -> dict[str, Any]:
    settings = _settings(request)
    _check_origin(request, settings)
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    engine = request_engine(request)
    try:
        async with tenant_transaction(engine, _ctx().with_identity(tenant, None)) as c:
            session = await _cookie_session(request, c, tenant)
            done = await registration_verify(
                c,
                settings,
                tenant_id=tenant,
                credential=body.credential,
                session_id=session.session_id if session else None,
                label=body.label,
                now=now,
            )
            if done.ticket_purpose == "RECOVERY" and done.recovery_id:
                await recovery.complete(
                    c, recovery_id=done.recovery_id, authenticator=done.authenticator, now=now
                )
    except RecoveryError as e:
        await _failed(engine, tenant, e.code, "registration", now)
        raise ZidError(e.code, "Recovery could not proceed.", http_status=409) from e
    except AuthnError as e:
        await _failed(engine, tenant, e.code, "registration", now)
        raise _error(e) from e
    a = done.authenticator
    return {
        "authenticator_id": a.authenticator_id,
        "authenticator_type": a.authenticator_type,
        "backup_eligible": a.backup_eligible,
        "status": a.status.value,
    }


async def _failed(engine: Any, tenant: str, code: str, ceremony: str, now: datetime) -> None:
    async with tenant_transaction(engine, _ctx().with_identity(tenant, None)) as c:
        await record_failure(c, tenant_id=tenant, code=code, ceremony=ceremony, now=now)


@router.post("/authn/webauthn/authentication/options")
async def authentication_options_endpoint(body: ClientRef, request: Request) -> dict[str, Any]:
    settings = _settings(request)
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        return await authentication_options(
            c, settings, tenant_id=tenant, step_up_session=None, now=now
        )


@router.post("/authn/step-up")
async def step_up_endpoint(body: ClientRef, request: Request) -> dict[str, Any]:
    settings = _settings(request)
    _check_origin(request, settings)
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        try:
            session = await _cookie_session(request, c, tenant)
            if session is None:
                raise AuthnError("SESSION_REQUIRED")
            return await authentication_options(
                c, settings, tenant_id=tenant, step_up_session=session, now=now
            )
        except AuthnError as e:
            raise _error(e) from e


@router.post("/authn/webauthn/authentication/verify")
async def authentication_verify_endpoint(
    body: CredentialIn, request: Request, response: Response
) -> dict[str, Any]:
    settings = _settings(request)
    _check_origin(request, settings)
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    engine = request_engine(request)
    try:
        async with tenant_transaction(engine, _ctx().with_identity(tenant, None)) as c:
            done = await authentication_verify(
                c, settings, tenant_id=tenant, credential=body.credential, now=now
            )
    except AuthnError as e:
        await _failed(engine, tenant, e.code, "authentication", now)
        raise _error(e) from e
    _set_cookie(response, done.handle, done.session)
    s = done.session
    return {
        "session_id": s.session_id,
        "principal_id": s.principal_id,
        "aal": s.aal,
        "amr": list(done.amr),
        "auth_time": s.auth_time.isoformat(),
        "expires_at": s.expires_at.isoformat(),
        "step_up_recommended": done.sign_count_anomaly,
    }


@router.post("/authn/logout", status_code=204)
async def logout_endpoint(body: ClientRef, request: Request, response: Response) -> Response:
    settings = _settings(request)
    _check_origin(request, settings)
    tenant = await _tenant(request, body.client_id)
    async with tenant_transaction(request_engine(request), _ctx().with_identity(tenant, None)) as c:
        session = await _cookie_session(request, c, tenant)
        if session is not None:
            await set_session_status(c, session.session_id, SessionStatus.REVOKED, reason="logout")
    out = Response(status_code=204)
    out.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="lax")
    return out


@router.get("/authn/authenticators")
async def list_authenticators(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        items = await store.for_principal(c, caller.ctx.principal_id or "")
    return {
        "items": [
            {
                "authenticator_id": a.authenticator_id,
                "authenticator_type": a.authenticator_type,
                "status": a.status.value,
                "label": a.label,
                "backup_eligible": a.backup_eligible,
                "registered_at": a.registered_at.isoformat(),
                "last_used_at": a.last_used_at.isoformat() if a.last_used_at else None,
            }
            for a in items
        ]
    }


@router.post("/authn/authenticators/{authenticator_id}:revoke")
async def revoke_authenticator(
    authenticator_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        try:
            await manage.revoke_own(
                c,
                principal_id=caller.ctx.principal_id or "",
                authenticator_id=str(authenticator_id),
                aal=caller.authn.level,
                auth_time=caller.authn.auth_time,
                now=now,
            )
        except AuthnError as e:
            raise _error(e) from e
    return {"authenticator_id": str(authenticator_id), "status": "REVOKED"}
