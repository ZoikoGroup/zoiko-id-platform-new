"""Verify API sessions, decisions and the sandbox (CR-150; ZID-EXT-IDV-001 section 10, WF-04, WF-09 to WF-14).

The partner's server (a bearer token from `/v1/verify-api/oauth2/token`, scoped):
GET  /v1/verify-api/workflows                                          the approved workflows and versions            workflows:read
GET  /v1/verify-api/products                                           the checks and where they are available        products:read
POST /v1/verify-api/verification-sessions                              create one session, safely repeatable          verifications:create
GET  /v1/verify-api/verification-sessions                              page through your sessions                      verifications:list
GET  /v1/verify-api/verification-sessions/{id}                         where one session is                            verifications:read
GET  /v1/verify-api/verification-sessions/{id}/decision                the minimized decision                          decisions:read
POST /v1/verify-api/verification-sessions/{id}:cancel                  end a session                                   verifications:create
POST /v1/verify-api/verification-sessions/{id}/resume-token            a fresh launch link for a person who left      verifications:resume
POST /v1/verify-api/verification-sessions/{id}:recheck                  a new session that follows a finished one, with Idempotency-Key  verifications:recheck
POST /v1/verify-api/sandbox/fixtures/{outcome}/sessions                create a session already at a simulated outcome verifications:create
POST /v1/verify-api/sandbox/sessions/{id}:simulate                     drive a sandbox session to a simulated outcome   verifications:create

Anyone (no sign-in): the contract a developer integrates against.
GET  /v1/verify-api/openapi.json                                       the partner-server operations as OpenAPI (CR-157)

The person opening the launch link (no sign-in; the link is the grant):
GET  /v1/verify-api/hosted/{token}                                     who is asking and why, whether this is a test
POST /v1/verify-api/hosted/{token}:open                                the person opened it
POST /v1/verify-api/hosted/{token}:simulate                            a sandbox test page picks a simulated outcome
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.verify_api.api.verify_api import VerifyApiDep
from zid_platform.modules.verify_api.application import partner_spec
from zid_platform.modules.verify_api.application import sessions_service as svc
from zid_platform.modules.verify_api.application.throttle import FailureLimiter
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store

router = APIRouter(tags=["verify-api"])
_hosted_limiter = FailureLimiter()

_STATUS = {
    "AUTH_INVALID": 401,
    "TENANT_SCOPE_DENIED": 403,
    "ENVIRONMENT_MISMATCH": 403,
    "PRODUCT_NOT_ENTITLED": 403,
    "WORKFLOW_NOT_ENTITLED": 403,
    "NOT_FOUND": 404,
    "DECISION_NOT_AVAILABLE": 404,
    "LAUNCH_INVALID": 404,
    "INVALID_IDEMPOTENCY_REUSE": 409,
    "SESSION_STATE_INVALID": 409,
    "VALIDATION_FAILED": 422,
    "WORKFLOW_VERSION_UNAVAILABLE": 422,
    "RETURN_ORIGIN_NOT_REGISTERED": 422,
    "RATE_LIMITED": 429,
}


def _fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=_STATUS.get(e.code, 422))


def _no_store(body: dict[str, Any], status: int = 200) -> JSONResponse:
    return JSONResponse(
        body, status_code=status, headers={"Cache-Control": "no-store", "Pragma": "no-cache"}
    )


def _origin(request: Request) -> str:
    """The address the launch link is on: the console's own public address, from configuration, never from the request."""
    value = getattr(request.app.state, "verify_hosted_origin", None) or getattr(
        request.app.state, "public_base_url", None
    )
    if not value:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE",
            "The hosted verification address is not configured.",
            http_status=503,
        )
    return str(value)


def _tx(request: Request, who: Any) -> Any:
    """A database transaction for the company this token belongs to. The company comes from the token, never from the request."""
    base = current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")
    return tenant_transaction(request_engine(request), base.with_identity(who.tenant_id, None))


# ------------------------------------------------------------------------------- the partner's server


class SessionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    customer_reference: str = Field(min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, max_length=64)
    workflow_version: str | None = Field(default=None, max_length=64)
    country: str = Field(min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=16)
    purpose_code: str = Field(max_length=64)
    locale: str | None = Field(default=None, max_length=10)
    return_origin: str | None = Field(default=None, max_length=200)
    metadata: dict[str, str] | None = None


class FixtureBody(BaseModel):
    """Every field is optional here: a test suite should not need to invent a person to see an outcome."""

    model_config = ConfigDict(extra="forbid")
    customer_reference: str | None = Field(default=None, min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    region: str | None = Field(default=None, max_length=16)
    purpose_code: str | None = Field(default=None, max_length=64)
    locale: str | None = Field(default=None, max_length=10)
    metadata: dict[str, str] | None = None


class SandboxOutcomeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(max_length=20)


@router.get("/v1/verify-api/openapi.json")
async def partner_openapi(request: Request) -> JSONResponse:
    """The OpenAPI for a partner's server: only the operations its server calls, and only the schemas they use. Public, like any API reference; it describes the interface and holds no data."""
    return JSONResponse(
        partner_spec.build(request.app.openapi()), headers={"Cache-Control": "public, max-age=300"}
    )


@router.get("/v1/verify-api/workflows")
async def workflows(request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        who.require("workflows:read")
        async with _tx(request, who) as conn:
            app = await store.get_application(conn, who.application_id)
    except PartnerError as e:
        raise _fail(e) from e
    return svc.catalogue(app)


@router.get("/v1/verify-api/products")
async def products(who: VerifyApiDep) -> dict[str, Any]:
    try:
        who.require("products:read")
    except PartnerError as e:
        raise _fail(e) from e
    from zid_platform.modules.verify_api.domain.sessions import PRODUCTS

    return {"items": PRODUCTS}


@router.post("/v1/verify-api/verification-sessions", status_code=201)
async def create_session(
    body: SessionBody,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view, created = await svc.create_session(
                conn,
                who,
                key=idempotency_key,
                body=body.model_dump(exclude_none=True),
                origin=_origin(request),
            )
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(view, 201 if created else 200)


@router.get("/v1/verify-api/verification-sessions")
async def list_sessions(
    request: Request,
    who: VerifyApiDep,
    status: str | None = Query(default=None, max_length=24),
    customer_reference: str | None = Query(default=None, max_length=128),
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.list_sessions(
                conn,
                who,
                status=status,
                customer_reference=customer_reference,
                created_from=created_from,
                created_to=created_to,
                cursor=cursor,
                limit=limit,
            )
    except PartnerError as e:
        raise _fail(e) from e


@router.get("/v1/verify-api/verification-sessions/{session_id}")
async def get_session(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.get_session(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.get("/v1/verify-api/verification-sessions/{session_id}/decision")
async def get_decision(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.get_decision(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.post("/v1/verify-api/verification-sessions/{session_id}:cancel")
async def cancel_session(session_id: str, request: Request, who: VerifyApiDep) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.cancel_session(conn, who, session_id)
    except PartnerError as e:
        raise _fail(e) from e


@router.post("/v1/verify-api/verification-sessions/{session_id}/resume-token")
async def resume_token(session_id: str, request: Request, who: VerifyApiDep) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view = await svc.resume_token(conn, who, session_id, origin=_origin(request))
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(view)


@router.post("/v1/verify-api/verification-sessions/{session_id}:recheck", status_code=201)
async def recheck_session(
    session_id: str,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            view, created = await svc.recheck_session(
                conn, who, session_id, key=idempotency_key, origin=_origin(request)
            )
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(view, 201 if created else 200)


@router.post("/v1/verify-api/sandbox/fixtures/{outcome}/sessions", status_code=201)
async def fixture_session(
    outcome: str,
    body: FixtureBody,
    request: Request,
    who: VerifyApiDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> JSONResponse:
    try:
        async with _tx(request, who) as conn:
            result = await svc.fixture_session(
                conn,
                who,
                outcome,
                key=idempotency_key,
                body=body.model_dump(exclude_none=True),
                origin=_origin(request),
            )
    except PartnerError as e:
        raise _fail(e) from e
    return _no_store(result, 201 if result["created"] else 200)


@router.post("/v1/verify-api/sandbox/sessions/{session_id}:simulate")
async def simulate_session(
    session_id: str, body: SandboxOutcomeBody, request: Request, who: VerifyApiDep
) -> dict[str, Any]:
    try:
        async with _tx(request, who) as conn:
            return await svc.simulate_for_partner(conn, who, session_id, body.outcome)
    except PartnerError as e:
        raise _fail(e) from e


# ------------------------------------------------------------------------------- the person opening the link


async def _hosted(request: Request, token: str, fn: Any) -> JSONResponse:
    """One answer for every wrong link, and a limit on wrong ones from one address, so links cannot be guessed. The link is the only thing the person holds."""
    who = request.client.host if request.client else "unknown"
    wait = _hosted_limiter.blocked(who)
    if wait:
        raise ZidError(
            "RATE_LIMITED", f"Too many attempts. Try again in {wait} seconds.", http_status=429
        )
    try:
        async with request_engine(request).begin() as conn, store.auth_scope(conn):
            out = await fn(conn)
    except PartnerError as e:
        if e.code == "LAUNCH_INVALID":
            _hosted_limiter.failed(who)
        raise _fail(e) from e
    return _no_store(out)


@router.get("/v1/verify-api/hosted/{token}")
async def hosted_context(token: str, request: Request) -> JSONResponse:
    return await _hosted(request, token, lambda conn: svc.hosted_context(conn, token))


@router.post("/v1/verify-api/hosted/{token}:open")
async def hosted_open(token: str, request: Request) -> JSONResponse:
    return await _hosted(request, token, lambda conn: svc.hosted_open(conn, token))


@router.post("/v1/verify-api/hosted/{token}:simulate")
async def hosted_simulate(token: str, body: SandboxOutcomeBody, request: Request) -> JSONResponse:
    return await _hosted(
        request, token, lambda conn: svc.hosted_simulate(conn, token, body.outcome)
    )
