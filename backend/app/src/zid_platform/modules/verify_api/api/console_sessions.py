"""Verify API: the console's view of an application's sessions and its Test Lab (CR-151).

A signed-in administrator, behind the PDP, not the partner's server:
GET  /v1/verify-api/applications/{id}/sessions        the application's sessions, newest first           verify_api.application.read
POST /v1/verify-api/applications/{id}/test-sessions   make one sandbox session and get its launch link    verify_api.application.manage

The same session rules apply as for the partner's server (the service is the same); the console acts as the application with a short-lived stand-in caller that never leaves this process.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.api.sessions import _fail, _no_store, _origin
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
