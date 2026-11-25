"""Verify API: applications, credentials and the token endpoint (CR-149; ZID-EXT-IDV-001 WF-05, section 10, SEC-02 to SEC-04).

Company side (a signed-in administrator, behind the PDP):
GET  /v1/verify-api/applications                                         the company's applications
POST /v1/verify-api/applications                                         create one (SANDBOX; production opens after the production gate)
GET  /v1/verify-api/applications/{id}                                    one application with its credentials (never a secret)
POST /v1/verify-api/applications/{id}:suspend | :resume | :retire        stop, restart or end it; its tokens stop at once
PUT  /v1/verify-api/applications/{id}/settings                           allowed workflows and default, hosted page look, return origins (CR-156)
POST /v1/verify-api/applications/{id}/credentials                        a new credential: the client id and the secret, shown once
POST /v1/verify-api/applications/{id}/credentials/{cid}:rotate           a new credential now; the old one works for a bounded overlap
POST /v1/verify-api/applications/{id}/credentials/{cid}:revoke           end a credential and every token it issued

Server side (the partner's own server, no signed-in person):
POST /v1/verify-api/oauth2/token                                         client credentials (a secret, or a signed private-key JWT assertion) in, a 15-minute bearer token out
GET  /v1/verify-api/whoami                                               what a token is: application, environment, scopes (a quick check for a quickstart)
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import timedelta
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import edge_cert, observe, service
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.application.throttle import FailureLimiter
from zid_platform.modules.verify_api.domain import credential_options as opts
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"
_limiter = FailureLimiter()

_STATUS = {
    "AUTH_INVALID": 401,
    "TENANT_SCOPE_DENIED": 403,
    "PRODUCTION_NOT_APPROVED": 403,
    "INVALID_SCOPE": 400,
    "VALIDATION_FAILED": 422,
    "NOT_FOUND": 404,
    "APPLICATION_NAME_TAKEN": 409,
    "APPLICATION_STATE_INVALID": 409,
    "WORKFLOW_NOT_ENTITLED": 403,
    "CREDENTIAL_STATE_INVALID": 409,
    "LIMIT_REACHED": 409,
    "RATE_LIMITED": 429,
    "IP_NOT_ALLOWED": 403,
    "MTLS_NOT_CONFIGURED": 409,
    "APPROVAL_REQUIRED": 403,
    "APPROVAL_SELF_DECISION": 409,
    "APPROVAL_STATE_INVALID": 409,
    "NOTE_REQUIRED": 422,
}


def _fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=_STATUS.get(e.code, 422))


def _no_store(body: dict[str, Any], status: int = 200) -> JSONResponse:
    """A secret or a token is in this answer: nothing may keep a copy of it."""
    return JSONResponse(
        body, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )


# ------------------------------------------------------------------------------- company side


class ApplicationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=80)
    environment: str = Field(default="SANDBOX", max_length=12)
    scopes: list[str] | None = Field(default=None, max_length=20)
    redirect_origins: list[str] | None = Field(default=None, max_length=20)


class RotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    overlap_hours: int | None = Field(default=None, ge=1, le=168)
    public_jwk: dict[str, Any] | None = None
    certificate_pem: str | None = Field(
        default=None, max_length=8192
    )  # a client-certificate credential (enterprise option; the cell must be configured for it)
    approval_id: str | None = Field(
        default=None, max_length=64
    )  # a production credential needs one


class CredentialIssueBody(BaseModel):
    """With a public key the credential is a private-key one: the company's server signs an assertion and no secret exists. Without a body it is a secret credential."""

    model_config = ConfigDict(extra="forbid")
    public_jwk: dict[str, Any] | None = None
    certificate_pem: str | None = Field(
        default=None, max_length=8192
    )  # a client-certificate credential (enterprise option; the cell must be configured for it)
    approval_id: str | None = Field(
        default=None, max_length=64
    )  # a production credential needs one


def _require_mtls(request: Request, certificate_pem: str | None) -> None:
    """A certificate credential can be registered only where the cell can actually honour it; otherwise a partner would register something that can never sign in."""
    cfg = getattr(request.app.state, "partner_mtls", None)
    if certificate_pem is not None and not (cfg is not None and cfg.enabled):
        raise ZidError(
            "MTLS_NOT_CONFIGURED",
            "Client-certificate sign-in is not enabled in this cell. Use a private key, or ask Zoiko iD about enabling it.",
            http_status=409,
        )


@router.get("/v1/verify-api/applications")
async def list_applications(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return {"items": await service.list_applications(conn)}


@router.post("/v1/verify-api/applications", status_code=201)
async def create_application(
    body: ApplicationBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.create_application(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                name=body.name,
                environment=body.environment,
                scopes=body.scopes,
                origins=body.redirect_origins,
            )
        except PartnerError as e:
            raise _fail(e) from e


@router.get("/v1/verify-api/applications/{application_id}")
async def get_application(
    application_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await service.application_detail(conn, str(application_id))
        except PartnerError as e:
            raise _fail(e) from e


class SettingsBody(BaseModel):
    """Only the fields sent are changed. Each is checked again by the cell."""

    model_config = ConfigDict(extra="forbid")
    redirect_origins: list[str] | None = Field(default=None, max_length=20)
    allowed_workflows: list[str] | None = Field(default=None, max_length=20)
    default_workflow_id: str | None = Field(default=None, max_length=64)
    branding: dict[str, Any] | None = None
    ip_allowlist: list[str] | None = Field(default=None, max_length=20)


@router.put("/v1/verify-api/applications/{application_id}/settings")
async def change_settings(
    application_id: uuid.UUID, body: SettingsBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.update_settings(
                conn, str(application_id), body.model_dump(exclude_unset=True)
            )
        except PartnerError as e:
            raise _fail(e) from e


@router.post("/v1/verify-api/applications/{application_id}:suspend")
@router.post("/v1/verify-api/applications/{application_id}:resume")
@router.post("/v1/verify-api/applications/{application_id}:retire")
async def change_application(
    application_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    action = request.url.path.rsplit(":", 1)[-1]
    target = {"suspend": "SUSPENDED", "resume": "ACTIVE", "retire": "RETIRED"}[action]
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.set_status(conn, str(application_id), target)
        except PartnerError as e:
            raise _fail(e) from e


@router.post("/v1/verify-api/applications/{application_id}/credentials", status_code=201)
async def issue_credential(
    application_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    body: CredentialIssueBody | None = None,
) -> JSONResponse:
    _require_mtls(request, body.certificate_pem if body else None)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            issued = await service.issue_credential(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                str(application_id),
                public_jwk=body.public_jwk if body else None,
                certificate_pem=body.certificate_pem if body else None,
                approval_id=body.approval_id if body else None,
            )
        except PartnerError as e:
            raise _fail(e) from e
    return _no_store(issued, 201)


@router.post(
    "/v1/verify-api/applications/{application_id}/credentials/{credential_id}:rotate",
    status_code=201,
)
async def rotate_credential(
    application_id: uuid.UUID,
    credential_id: uuid.UUID,
    body: RotateBody,
    request: Request,
    caller: CallerDep,
) -> JSONResponse:
    _require_mtls(request, body.certificate_pem)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            rotated = await service.rotate_credential(
                conn,
                caller.ctx.tenant_id or "",
                caller.ctx.principal_id or "",
                str(application_id),
                str(credential_id),
                overlap=timedelta(hours=body.overlap_hours) if body.overlap_hours else None,
                public_jwk=body.public_jwk,
                certificate_pem=body.certificate_pem,
                approval_id=body.approval_id,
            )
        except PartnerError as e:
            raise _fail(e) from e
    return _no_store(rotated, 201)


@router.post("/v1/verify-api/applications/{application_id}/credentials/{credential_id}:revoke")
async def revoke_credential(
    application_id: uuid.UUID, credential_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.revoke_credential(conn, str(application_id), str(credential_id))
        except PartnerError as e:
            raise _fail(e) from e


# ------------------------------------------------------------------------------- server side


async def _credentials_from(request: Request) -> dict[str, str]:
    """The client id, the secret and the optional scope, from an HTTP Basic header, a JSON body or a form body, as OAuth clients send them. The body is small and read once."""
    fields: dict[str, str] = {}
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        import base64

        try:
            cid, _, secret = base64.b64decode(auth[6:], validate=True).decode().partition(":")
            fields.update(client_id=cid, client_secret=secret)
        except ValueError:
            pass
    raw = await request.body()
    if len(raw) > 4096:
        raise ZidError("VALIDATION_FAILED", "The request is too large.", http_status=413)
    if raw:
        ctype = request.headers.get("content-type", "").split(";")[0].strip().lower()
        try:
            if ctype == "application/x-www-form-urlencoded":
                body = {k: v[0] for k, v in parse_qs(raw.decode()).items()}
            else:
                parsed = json.loads(raw)
                body = (
                    {k: v for k, v in parsed.items() if isinstance(v, str)}
                    if isinstance(parsed, dict)
                    else {}
                )
        except (ValueError, UnicodeDecodeError) as e:
            raise ZidError(
                "VALIDATION_FAILED", "The request body could not be read.", http_status=422
            ) from e
        for key in (
            "grant_type",
            "client_id",
            "client_secret",
            "scope",
            "client_assertion",
            "client_assertion_type",
        ):
            if key in body and key not in fields:
                fields[key] = body[key]
    return fields


@router.post("/v1/verify-api/oauth2/token")
async def token(request: Request) -> JSONResponse:
    """Exchange a client id and secret for a short-lived bearer token. One answer for every failure, and a limit on wrong secrets for one client id."""
    f = await _credentials_from(request)
    if f.get("grant_type", "client_credentials") != "client_credentials":
        raise ZidError(
            "VALIDATION_FAILED", "The grant type is client_credentials.", http_status=400
        )
    assertion = f.get("client_assertion")
    if assertion is not None:
        return await _token_by_assertion(request, f, assertion)
    client_id, secret = f.get("client_id", ""), f.get("client_secret", "")
    if client_id and not secret:
        cfg = getattr(request.app.state, "partner_mtls", None)
        presented = (
            edge_cert.presented_fingerprint(cfg, peer=_caller_ip(request), headers=request.headers)
            if cfg is not None
            else None
        )
        if presented is not None:
            return await _token_by_certificate(request, f, client_id, presented)
    if not client_id or not secret:
        observe.AUTH.inc(
            method="none", result="denied"
        )  # no credentials the cell could check, or a certificate the edge did not vouch for
        raise ZidError("AUTH_INVALID", service.AUTH_FAILED, http_status=401)
    wait = _limiter.blocked(client_id)
    if wait:
        observe.AUTH.inc(method="secret", result="rate_limited")
        raise ZidError(
            "RATE_LIMITED",
            f"Too many failed attempts. Try again in {wait} seconds.",
            http_status=429,
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            issued = await service.exchange(
                conn, client_id, secret, f.get("scope"), caller_ip=_caller_ip(request)
            )
    except PartnerError as e:
        if e.code == "AUTH_INVALID":
            _limiter.failed(client_id)
        observe.AUTH.inc(method="secret", result=observe.auth_result(e.code))
        raise _fail(e) from e
    _limiter.succeeded(client_id)
    observe.AUTH.inc(method="secret", result="issued")
    return _no_store(issued)


def _caller_ip(request: Request) -> str | None:
    """The address the server called from, as the platform's own proxy settings report it (the supervisor believes X-Forwarded-For only from named proxies)."""
    return request.client.host if request.client else None


def _audiences(request: Request) -> list[str]:
    """What an assertion may name as its audience: this token endpoint, as seen by the cell and as published at the public base address."""
    path = "/v1/verify-api/oauth2/token"
    out = [str(request.url.replace(query="", fragment=""))]
    base = getattr(request.app.state, "public_base_url", None)
    if base:
        out.append(base.rstrip("/") + path)
    return out


async def _token_by_certificate(
    request: Request, f: dict[str, str], client_id: str, presented: bytes
) -> JSONResponse:
    """A certificate client: the edge vouched for a certificate and its fingerprint must be the one pinned on this credential. One answer for every failure, and the same limit on wrong attempts per client id."""
    wait = _limiter.blocked(client_id)
    if wait:
        observe.AUTH.inc(method="mtls", result="rate_limited")
        raise ZidError(
            "RATE_LIMITED",
            f"Too many failed attempts. Try again in {wait} seconds.",
            http_status=429,
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            issued = await service.exchange_certificate(
                conn, client_id, presented, f.get("scope"), caller_ip=_caller_ip(request)
            )
    except PartnerError as e:
        if e.code == "AUTH_INVALID":
            _limiter.failed(client_id)
        observe.AUTH.inc(method="mtls", result=observe.auth_result(e.code))
        raise _fail(e) from e
    _limiter.succeeded(client_id)
    observe.AUTH.inc(method="mtls", result="issued")
    return _no_store(issued)


async def _token_by_assertion(request: Request, f: dict[str, str], assertion: str) -> JSONResponse:
    if f.get("client_assertion_type") != opts.ASSERTION_TYPE:
        raise ZidError(
            "VALIDATION_FAILED",
            f"The client assertion type is {opts.ASSERTION_TYPE}.",
            http_status=400,
        )
    claimed = f.get("client_id") or service.assertion_client_id(assertion) or ""
    wait = _limiter.blocked(claimed)
    if wait:
        observe.AUTH.inc(method="private_key_jwt", result="rate_limited")
        raise ZidError(
            "RATE_LIMITED",
            f"Too many failed attempts. Try again in {wait} seconds.",
            http_status=429,
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            issued = await service.exchange_assertion(
                conn,
                assertion,
                _audiences(request),
                f.get("scope"),
                client_id=f.get("client_id"),
                caller_ip=_caller_ip(request),
            )
    except PartnerError as e:
        if e.code == "AUTH_INVALID":
            _limiter.failed(claimed)
        observe.AUTH.inc(method="private_key_jwt", result=observe.auth_result(e.code))
        raise _fail(e) from e
    _limiter.succeeded(claimed)
    observe.AUTH.inc(method="private_key_jwt", result="issued")
    return _no_store(issued)


async def verify_api_caller(request: Request) -> VerifyApiCaller:
    """The caller a bearer token says it is, or 401. For every route a partner's server calls."""
    header = request.headers.get("authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        raise ZidError(
            "AUTH_INVALID",
            "Send the access token as Authorization: Bearer <token>.",
            http_status=401,
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            return await service.authenticate(conn, value.strip(), caller_ip=_caller_ip(request))
    except PartnerError as e:
        observe.AUTH.inc(method="bearer", result=observe.auth_result(e.code))
        raise _fail(e) from e


VerifyApiDep = Annotated[VerifyApiCaller, Depends(verify_api_caller)]


@router.get("/v1/verify-api/whoami")
async def whoami(who: VerifyApiDep) -> dict[str, Any]:
    """What this token is: the application, its environment and scopes. A partner uses it to prove its credentials work. The company is named only by an opaque reference."""
    return {
        "application_id": who.application_id,
        "environment": who.environment.lower(),
        "scopes": list(who.scopes),
        "tenant_reference": "tnt_" + hashlib.sha256(who.tenant_id.encode()).hexdigest()[:20],
        "token_expires_at": who.expires_at.isoformat(),
    }
