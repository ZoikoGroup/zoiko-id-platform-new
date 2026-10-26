"""Webhook management API (ZID-EVENTS-001 §14-20; ZID-API-001 §17; ZID-UX-001 §18).

GET    /v1/webhooks/event-types                  subscribable event types         (webhook.endpoint.read)
GET    /v1/webhooks/signing-keys                 public key receivers verify with (no authentication)
GET    /v1/webhooks/endpoints                    list endpoints                   (webhook.endpoint.read)
POST   /v1/webhooks/endpoints                    register -> PENDING_VERIFICATION (webhook.endpoint.manage)
GET    /v1/webhooks/endpoints/{id}               read one                         (webhook.endpoint.read)
POST   /v1/webhooks/endpoints/{id}:verify        signed challenge round trip      (webhook.endpoint.manage)
POST   /v1/webhooks/endpoints/{id}:pause | :resume | :retire                      (webhook.endpoint.manage)
GET    /v1/webhooks/deliveries                   delivery log, filterable         (webhook.endpoint.read)
GET    /v1/webhooks/deliveries/{id}              one delivery                     (webhook.endpoint.read)
POST   /v1/webhooks/deliveries/{id}:replay       replay a FAILED/EXHAUSTED one    (webhook.endpoint.manage)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, http_error, request_engine
from zid_platform.modules.webhook.application import service
from zid_platform.modules.webhook.application.runtime import http_client, system_resolver
from zid_platform.modules.webhook.domain.webhooks import SUBSCRIBABLE, WebhookError
from zid_platform.modules.webhook.infrastructure import endpoint_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["webhooks"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


def _signer(request: Request) -> Any:
    signer = getattr(request.app.state, "webhook_signer", None)
    if signer is None:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "No webhook signing key is configured.", http_status=503
        )
    return signer


def _fail(e: WebhookError) -> ZidError:
    return http_error(e.code, str(e))


def _actor(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


class EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    event_types: list[str] = Field(min_length=1, max_length=32)
    max_inflight: int = Field(default=4, ge=1, le=16)


@router.get("/v1/webhooks/event-types")
async def event_types(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
        )
    return {"items": [{"type": t, "description": d} for t, d in sorted(SUBSCRIBABLE.items())]}


@router.get("/v1/webhooks/signing-keys")
async def signing_keys(request: Request) -> dict[str, Any]:
    """The public key receivers verify RFC 9421 signatures with (`keyid` in the signature names it)."""
    keys = getattr(request.app.state, "webhook_keys", None)
    return {"keys": keys.published(datetime.now(UTC)) if keys else []}


@router.get("/v1/webhooks/endpoints")
async def list_endpoints(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
        )
        return {"items": await store.list_endpoints(conn)}


@router.post("/v1/webhooks/endpoints", status_code=201)
async def register_endpoint(
    body: EndpointIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    signer = _signer(request)
    resolve = getattr(request.app.state, "webhook_resolver", None) or system_resolver
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="webhook.endpoint.manage", resource="webhook_endpoint:new"
            )
            return await service.register(
                conn,
                caller.ctx.tenant_id or "",
                url=body.url,
                event_types=body.event_types,
                max_inflight=body.max_inflight,
                signer_kid=signer.kid,
                actor=_actor(caller),
                resolve=resolve,
                now=datetime.now(UTC),
                allow_http=bool(getattr(request.app.state, "webhook_allow_http", False)),
            )
    except WebhookError as e:
        raise _fail(e) from e


@router.get("/v1/webhooks/endpoints/{endpoint_id}")
async def read_endpoint(
    endpoint_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action="webhook.endpoint.read",
                resource=f"webhook_endpoint:{endpoint_id}",
            )
            return await store.load_endpoint(conn, str(endpoint_id))
    except WebhookError as e:
        raise _fail(e) from e


@router.post("/v1/webhooks/endpoints/{endpoint_id}:verify")
async def verify_endpoint(
    endpoint_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    signer = _signer(request)
    resolve = getattr(request.app.state, "webhook_resolver", None) or system_resolver
    injected = getattr(request.app.state, "webhook_client", None)
    client = injected or http_client()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action="webhook.endpoint.manage",
                resource=f"webhook_endpoint:{endpoint_id}",
            )
            return await service.verify(
                conn,
                caller.ctx.tenant_id or "",
                str(endpoint_id),
                client=client,
                resolve=resolve,
                sign=signer.sign,
                actor=_actor(caller),
                now=datetime.now(UTC),
            )
    except WebhookError as e:
        raise _fail(e) from e
    finally:
        if injected is None:
            await client.aclose()


async def _change(
    request: Request, caller: Caller, endpoint_id: uuid.UUID, change: str
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action="webhook.endpoint.manage",
                resource=f"webhook_endpoint:{endpoint_id}",
            )
            return await service.change_status(
                conn,
                caller.ctx.tenant_id or "",
                str(endpoint_id),
                change=change,
                actor=_actor(caller),
                now=datetime.now(UTC),
            )
    except WebhookError as e:
        raise _fail(e) from e


@router.post("/v1/webhooks/endpoints/{endpoint_id}:pause")
async def pause(endpoint_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, endpoint_id, "pause")


@router.post("/v1/webhooks/endpoints/{endpoint_id}:resume")
async def resume(endpoint_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, endpoint_id, "resume")


@router.post("/v1/webhooks/endpoints/{endpoint_id}:retire")
async def retire(endpoint_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, endpoint_id, "retire")


@router.get("/v1/webhooks/deliveries")
async def list_deliveries(
    request: Request,
    caller: CallerDep,
    endpoint_id: uuid.UUID | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
        )
        items = await store.list_deliveries(
            conn, endpoint_id=str(endpoint_id) if endpoint_id else None, status=status
        )
    return {"items": items}


@router.get("/v1/webhooks/deliveries/{delivery_id}")
async def read_delivery(
    delivery_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
            )
            return await store.load_delivery(conn, str(delivery_id))
    except WebhookError as e:
        raise _fail(e) from e


@router.post("/v1/webhooks/deliveries/{delivery_id}:replay", status_code=202)
async def replay_delivery(
    delivery_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn,
                caller,
                action="webhook.endpoint.manage",
                resource=f"webhook_delivery:{delivery_id}",
            )
            return await service.replay(
                conn,
                caller.ctx.tenant_id or "",
                str(delivery_id),
                actor=_actor(caller),
                now=datetime.now(UTC),
            )
    except WebhookError as e:
        raise _fail(e) from e
