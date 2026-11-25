"""Verify API: the console's view of an application's sessions and its Test Lab (CR-151).

A signed-in administrator, behind the PDP, not the partner's server:
POST /v1/verify-api/applications/{id}/try             run one call against a SANDBOX application (CR-173)       verify_api.application.manage
GET  /v1/verify-api/onboarding/cohort                                   median time to first verified webhook, this company (CR-173)           verify_api.application.read
GET  /v1/verify-api/applications/{id}/onboarding      the first-verification checklist and its time (CR-173)    verify_api.application.read
GET  /v1/verify-api/applications/{id}/sessions        the application's sessions, newest first           verify_api.application.read
POST /v1/verify-api/applications/{id}/test-sessions   make one sandbox session and get its launch link    verify_api.application.manage

The same session rules apply as for the partner's server (the service is the same); the console acts as the application with a short-lived stand-in caller that never leaves this process.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.api.sessions import _fail, _no_store, _origin
from zid_platform.modules.verify_api.application import certification_service, diagnostics_service, onboarding_service, service
from zid_platform.modules.verify_api.application import try_it as try_it_service
from zid_platform.modules.verify_api.application import sessions_service as svc
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain.model import SCOPES, PartnerError
from zid_platform.modules.verify_api.infrastructure import store
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"


class TestSessionBody(BaseModel):
    """Everything is optional: a tester should not have to invent a person."""

    __test__ = False  # not a pytest class
    model_config = ConfigDict(extra="forbid")
    customer_reference: str | None = Field(default=None, min_length=1, max_length=128)
    workflow_id: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, min_length=2, max_length=2)
    purpose_code: str | None = Field(default=None, max_length=64)


async def _acting_as(conn: Any, application_id: uuid.UUID) -> VerifyApiCaller:
    app = await store.get_application(conn, str(application_id))
    if app is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    return VerifyApiCaller(
        tenant_id=str(app["tenant_id"]),
        application_id=str(application_id),
        credential_id="console",
        environment=str(app["environment"]),
        scopes=tuple(SCOPES),
        expires_at=datetime.now(UTC) + timedelta(minutes=1),
    )


class ScopeChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scopes: list[str] = Field(min_length=1, max_length=20)
    reason: str = Field(min_length=3, max_length=300)
    effective_at: datetime | None = None  # a removal may be staged for later (up to 30 days); an addition takes effect when approved


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "An authenticated principal is required.", http_status=401)
    return caller.ctx.principal_id


@router.get("/v1/verify-api/applications/{application_id}/scope-changes")
async def scope_change_history(application_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Every version of what this application may do, newest first, with who proposed it, who approved it and when it takes or took effect (ZID-CP-WF10, WF-10.03)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        app = await store.get_application(conn, str(application_id))
        if app is None:
            raise ZidError("NOT_FOUND", "No such application.", http_status=404)
        now = datetime.now(UTC)
        in_force = await store.scopes_in_force(conn, str(application_id), now)
        rows = await store.scope_changes(conn, str(application_id))
        return {
            "application_id": str(application_id), "environment": str(app["environment"]).lower(), "scopes_now": in_force if in_force is not None else list(app["scopes"]),
            "changes": [
                {
                    "change_id": str(r["change_id"]), "version": r["version"], "kind": r["kind"], "scopes": list(r["resulting_scopes"]), "needs_approval": r["requires_approval"],
                    "proposed_by": str(r["proposed_by"]), "approved_by": list(r["approvers"]), "effective_at": r["effective_at"].isoformat(), "pending": r["effective_at"] > now, "reason": r["reason"],
                }
                for r in rows
            ],
        }  # fmt: skip


@router.post("/v1/verify-api/applications/{application_id}/scope-changes", status_code=201)
async def propose_scope_change(application_id: uuid.UUID, body: ScopeChangeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.change_scopes(conn, caller.ctx.tenant_id or "", str(application_id), _me(caller), body.scopes, body.reason, body.effective_at)
        except PartnerError as e:
            raise _fail(e) from e


class ScopeRollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to_version: int = Field(ge=0, le=100000)
    reason: str = Field(min_length=3, max_length=200)


@router.post("/v1/verify-api/applications/{application_id}/scope-changes:rollback", status_code=201)
async def rollback_scope_change(application_id: uuid.UUID, body: ScopeRollbackBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Restore an earlier scope set as a new version. Sessions and idempotency keys are untouched (ZID-CP-WF10, WF-10.12)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.rollback_scopes(conn, caller.ctx.tenant_id or "", str(application_id), _me(caller), body.to_version, body.reason)
        except PartnerError as e:
            raise _fail(e) from e


@router.post("/v1/verify-api/scope-changes/{change_id}:approve")
async def approve_scope_change(change_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """A person other than the proposer approves widening a production application. The proposer is refused."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await service.approve_scope_change(conn, caller.ctx.tenant_id or "", str(change_id), _me(caller))
        except PartnerError as e:
            raise _fail(e) from e


class CertificationRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observations: list[dict[str, Any]] = Field(min_length=1, max_length=40)


@router.post("/v1/verify-api/applications/{application_id}/certification-runs", status_code=201)
async def record_certification_run(application_id: uuid.UUID, body: CertificationRunBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Score a sandbox integration against every synthetic persona and keep the result with its digest. A test record: not a certification and not a go-live approval (ZID-CP-WF10, WF-10.12)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await certification_service.record_run(conn, caller.ctx.tenant_id or "", str(application_id), _me(caller), body.observations)
        except (PartnerError, KeyError, ValueError, TypeError) as e:
            raise _fail(e if isinstance(e, PartnerError) else PartnerError("VALIDATION_FAILED", "An observation is missing a field or has a wrong value.")) from e


@router.get("/v1/verify-api/applications/{application_id}/certification")
async def certification_status(application_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await certification_service.status(conn, str(application_id))
        except PartnerError as e:
            raise _fail(e) from e


class TryItBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["whoami", "products", "workflows", "list_sessions", "create_session"]
    body: dict[str, Any] | None = None
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=200)


@router.post("/v1/verify-api/applications/{application_id}/try")
async def try_it(application_id: uuid.UUID, body: TryItBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Run one partner call against a SANDBOX application and see the exact request and answer. A production application is refused: this can never reach production (ZID-CP-WF10, WF-10.05)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            return await try_it_service.run(conn, caller.ctx.tenant_id or "", str(application_id), body.operation, body=body.body, idempotency_key=body.idempotency_key, origin=_origin(request))
        except PartnerError as e:
            raise _fail(e) from e


@router.get("/v1/verify-api/diagnostics/endpoints")
async def endpoint_health(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Each webhook endpoint's health from its newest deliveries, with the main problem and what to do (ZID-CP-WF10, WF-10.09)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return await diagnostics_service.endpoints(conn)


@router.get("/v1/verify-api/applications/{application_id}/requests")
async def request_explorer(
    application_id: uuid.UUID, request: Request, caller: CallerDep, status: int | None = Query(default=None), route: str | None = Query(default=None, max_length=120),
    before: datetime | None = Query(default=None), limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:  # fmt: skip
    """What this application's server called, newest first, filtered by status or route. Redacted by construction: nothing but templates, codes and ids is kept (WF-10.10)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await diagnostics_service.requests(conn, str(application_id), status=status, route=route, before=before, limit=limit)
        except PartnerError as e:
            raise _fail(e) from e


@router.get("/v1/verify-api/sessions/{session_id}/trace")
async def session_trace(session_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """One verification followed through the request, the route decision, the decisions and the webhook deliveries, by canonical ids (WF-10.10)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await diagnostics_service.trace(conn, session_id)
        except PartnerError as e:
            raise _fail(e) from e


@router.get("/v1/verify-api/onboarding/cohort")
async def onboarding_cohort(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Median time to the first verified webhook over this company's applications; unfinished installs are counted, not averaged in (ZID-CP-WF10, WF-10.01)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        return await onboarding_service.cohort(conn)


@router.get("/v1/verify-api/applications/{application_id}/onboarding")
async def application_onboarding(application_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The first-verification checklist for one application: five steps, each with its evidence, and the time to the first verified webhook (ZID-CP-WF10, WF-10.01)."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await onboarding_service.checklist(conn, str(application_id))
        except PartnerError as e:
            raise _fail(e) from e


@router.get("/v1/verify-api/applications/{application_id}/sessions")
async def application_sessions(
    application_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    status: str | None = Query(default=None, max_length=24),
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            who = await _acting_as(conn, application_id)
            return await svc.list_sessions(conn, who, status=status, cursor=cursor, limit=limit)
        except PartnerError as e:
            raise _fail(e) from e


@router.post("/v1/verify-api/applications/{application_id}/test-sessions", status_code=201)
async def test_session(
    application_id: uuid.UUID, body: TestSessionBody, request: Request, caller: CallerDep
) -> JSONResponse:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            who = await _acting_as(conn, application_id)
            request_body = {
                "customer_reference": f"test-{uuid.uuid4().hex[:8]}",
                "country": "US",
                "purpose_code": "customer_onboarding",
                **body.model_dump(exclude_none=True),
            }
            # a new key every time: a test session is always a new session
            view, _ = await svc.create_session(
                conn,
                who,
                key=f"console-{uuid.uuid4().hex}",
                body=request_body,
                origin=_origin(request),
            )
        except PartnerError as e:
            raise _fail(e) from e
    return _no_store(view, 201)
