"""Enterprise service requests (plan features dedicated isolation, sovereign service, key custody, named support).

GET  /v1/service-requests                  the company's requests and where each stands
POST /v1/service-requests                  ask for one; only a plan that includes the service may ask
POST /v1/service-requests/{id}:withdraw    withdraw one that is still open

These services are delivered by Zoiko iD people under a contract, not switched on by software. A request records what was asked and by whom; Zoiko iD
moves its status. A key for customer-held key custody is never sent here: the request says what is wanted and Zoiko iD arranges the exchange.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/service-requests", tags=["commercial"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Service = Literal["DEDICATED_ISOLATION", "SOVEREIGN_SERVICE", "KEY_CUSTODY", "NAMED_SUPPORT"]


class RequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    service: Service
    details: str = Field(min_length=10, max_length=2000)
    contact: str = Field(min_length=3, max_length=200)


def _view(r: Any) -> dict[str, Any]:
    return {
        "request_id": str(r.request_id),
        "service": r.service,
        "status": r.status,
        "details": r.details,
        "contact": r.contact,
        "note": r.note,
        "created_at": r.created_at.isoformat(),
        "updated_at": r.updated_at.isoformat(),
    }


async def _plan_allows(conn: AsyncConnection, service: str, enforcement: str) -> None:
    if service == "DEDICATED_ISOLATION":
        await require_feature(conn, "advanced.dedicated-isolation-private-deployment", enforcement=enforcement)
    elif service == "SOVEREIGN_SERVICE":
        await require_feature(conn, "advanced.sovereign-service-configuration", enforcement=enforcement)
    elif service == "KEY_CUSTODY":
        await require_feature(conn, "advanced.byok-hyok-and-specialized-key-custody", enforcement=enforcement)
    else:
        await require_feature(conn, "advanced.custom-sla-named-support-onboarding", enforcement=enforcement)


@router.get("")
async def list_requests(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="commercial.read", resource=f"tenant:{tenant}")
        rows = (await conn.execute(text("SELECT * FROM commercial.service_request ORDER BY created_at DESC LIMIT 100"))).all()
    return {"items": [_view(r) for r in rows]}


@router.post("", status_code=201)
async def ask(body: RequestBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "An authenticated principal is required.", http_status=401)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="tenant.lifecycle.manage", resource=f"tenant:{tenant}")
        await _plan_allows(conn, body.service, getattr(request.app.state, "commercial_enforcement", "shadow"))
        try:
            async with conn.begin_nested():
                row = (
                    await conn.execute(
                        text(
                            "INSERT INTO commercial.service_request (request_id, tenant_id, service, details, contact, requested_by) "
                            "VALUES (:id, :t, :s, :d, :c, :u) RETURNING *"
                        ),
                        {"id": uuid.uuid4(), "t": uuid.UUID(tenant), "s": body.service, "d": body.details.strip(), "c": body.contact.strip(), "u": uuid.UUID(caller.ctx.principal_id)},
                    )
                ).one()
        except IntegrityError as e:
            raise ZidError("SERVICE_REQUEST_OPEN", "There is already an open request for this service.", http_status=409) from e
    return _view(row)


@router.post("/{request_id}:withdraw")
async def withdraw(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="tenant.lifecycle.manage", resource=f"tenant:{tenant}")
        res = await conn.execute(
            text("UPDATE commercial.service_request SET status = 'WITHDRAWN', updated_at = now() WHERE request_id = :id AND status IN ('REQUESTED', 'IN_PROGRESS')"),
            {"id": request_id},
        )
    if res.rowcount != 1:
        raise ZidError("SERVICE_REQUEST_NOT_FOUND", "No such open request.", http_status=404)
    return {"request_id": str(request_id), "status": "WITHDRAWN"}
