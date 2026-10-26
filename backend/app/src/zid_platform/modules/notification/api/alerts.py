"""Alerts and tasks API (ZID-UX-001 §20; CR-019).

GET   /v1/alerts                      open alerts and tasks, most severe first (notification.alert.read)
GET   /v1/alerts/summary              counts for the shell                     (notification.alert.read)
GET   /v1/alerts/{id}                 one alert                                (notification.alert.read)
POST  /v1/alerts/{id}:acknowledge     seen, not resolved                       (notification.alert.manage)
POST  /v1/alerts/{id}:resolve         closed                                   (notification.alert.manage)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.notification.application import service
from zid_platform.modules.notification.domain.alerts import AlertError, AlertState
from zid_platform.modules.notification.infrastructure import alert_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["alerts"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Severity = Literal["CRITICAL", "HIGH", "MEDIUM", "INFO"]
State = Literal["OPEN", "ACKNOWLEDGED", "RESOLVED"]


def _fail(e: AlertError) -> Exception:
    return http_error(e.code, str(e))


@router.get("/v1/alerts")
async def list_alerts(
    request: Request,
    caller: CallerDep,
    state: State | None = None,
    severity: Severity | None = None,
    tasks_only: bool = False,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="notification.alert.read", resource="alert:*")
        items = await store.list_alerts(conn, state=state, severity=severity, tasks_only=tasks_only)
    return {"items": items}


@router.get("/v1/alerts/summary")
async def alert_summary(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="notification.alert.read", resource="alert:*")
        return await store.summary(conn)


@router.get("/v1/alerts/{alert_id}")
async def read_alert(alert_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="notification.alert.read", resource=f"alert:{alert_id}"
            )
            return await store.load_alert(conn, str(alert_id))
    except AlertError as e:
        raise _fail(e) from e


async def _change(
    request: Request, caller: Caller, alert_id: uuid.UUID, to: AlertState
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="notification.alert.manage", resource=f"alert:{alert_id}"
            )
            return await service.change_state(
                conn,
                caller.ctx.tenant_id or "",
                str(alert_id),
                to=to,
                actor=caller.ctx.principal_id or "",
                now=datetime.now(UTC),
            )
    except AlertError as e:
        raise _fail(e) from e


@router.post("/v1/alerts/{alert_id}:acknowledge")
async def acknowledge(alert_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, alert_id, AlertState.ACKNOWLEDGED)


@router.post("/v1/alerts/{alert_id}:resolve")
async def resolve(alert_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, alert_id, AlertState.RESOLVED)
