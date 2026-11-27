"""Verify API results for the company (CR-155; ZID-EXT-IDV-001 epic E16, WF-12). A signed-in administrator, behind the PDP.

GET  /v1/verify-api/sessions                        search the company's sessions across applications (fixed filters)        verify_api.application.read
GET  /v1/verify-api/sessions/{id}                   one session with its decision history and the rechecks that follow it   verify_api.application.read
POST /v1/verify-api/sessions/{id}:recheck           start a recheck (a new session that follows this one)                   verify_api.application.manage

The partner's server rechecks with `POST /v1/verify-api/verification-sessions/{id}:recheck` (scope verifications:recheck; see sessions.py).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.api.console_sessions import _acting_as
from zid_platform.modules.verify_api.api.sessions import _fail as fail
from zid_platform.modules.verify_api.api.sessions import _no_store, _origin
from zid_platform.modules.verify_api.application import media_service as media
from zid_platform.modules.verify_api.application import results_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import session_store as db
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

MANAGE = "verify_api.application.manage"
READ = "verify_api.application.read"
RESOURCE = "verify_api_application:*"


@router.get("/v1/verify-api/sessions")
async def search_sessions(
    request: Request,
    caller: CallerDep,
    application_id: uuid.UUID | None = None,
    environment: str | None = Query(default=None, max_length=12),
    status: str | None = Query(default=None, max_length=24),
    customer_reference: str | None = Query(default=None, max_length=128),
    cursor: str | None = Query(default=None, max_length=300),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await svc.search(
                conn,
                application_id=str(application_id) if application_id else None,
                environment=environment,
                status=status,
                customer_reference=customer_reference,
                cursor=cursor,
                limit=limit,
            )
        except PartnerError as e:
            raise fail(e) from e


@router.get("/v1/verify-api/sessions/{session_id}")
async def session_detail(session_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            return await svc.detail(conn, session_id)
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/sessions/{session_id}:recheck", status_code=201)
async def recheck(session_id: str, request: Request, caller: CallerDep) -> JSONResponse:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=MANAGE, resource=RESOURCE)
        try:
            row = await db.get_session_any(conn, session_id)
            if row is None:
                raise PartnerError("NOT_FOUND", "No such session.")
            who = await _acting_as(conn, row["application_id"])
            # a new key each time: a console recheck is always a new session
            view = await svc.recheck(
                conn,
                who,
                session_id,
                key=f"console-recheck-{uuid.uuid4().hex}",
                origin=_origin(request),
            )
        except PartnerError as e:
            raise fail(e) from e
    return _no_store(view, 201)


# ------------------------------------------------------------------------------ photographs kept for a session (migration verify_api_0020)


class MediaOpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=5, max_length=300)


@router.get("/v1/verify-api/sessions/{session_id}/media")
async def session_media(session_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """What was kept for this verification (never an image), and every time someone opened one: who, which, why and when."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            if await db.get_session_any(conn, session_id) is None:
                raise PartnerError("NOT_FOUND", "No such session.")
            return await media.listing(conn, session_id)
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/sessions/{session_id}/media/{media_id}:open")
async def open_session_media(
    session_id: str, media_id: str, body: MediaOpenBody, request: Request, caller: CallerDep
) -> JSONResponse:
    """One photograph, after the person says why. The reason, who and when are recorded before the image is returned, and the answer is never cached."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=READ, resource=RESOURCE)
        try:
            if await db.get_session_any(conn, session_id) is None:
                raise PartnerError("NOT_FOUND", "No such session.")
            view = await media.open_one(
                conn,
                tenant_id=str(caller.ctx.tenant_id or ""),
                session_id=session_id,
                media_id=media_id,
                principal_id=str(caller.ctx.principal_id),
                reason=body.reason,
            )
        except PartnerError as e:
            raise fail(e) from e
    return _no_store(view)
