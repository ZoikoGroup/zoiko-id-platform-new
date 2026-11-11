"""The /v2 webhook facade (ZID-INT-002 sections 03 and 13).

    POST /v2/webhook-endpoints                register a destination (Idempotency-Key required)
    GET  /v2/webhook-endpoints                the tenant's destinations
    GET  /v2/events/{event_id}                one delivery: subscription state and replay state, data-minimized
    POST /v2/events/{event_id}/replay         replay a FAILED or EXHAUSTED delivery

It adapts the /v1 webhook routes and changes none of their rules: HTTPS only, DNS checked against private ranges, a signed
challenge before ACTIVE, at-least-once delivery with backoff, dead-letter and controlled redrive. Event names may be given as the
document writes them (`credential.status_changed`) or as the CloudEvents type; both are the same subscription.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.webhook.api import webhooks as v1
from zid_platform.modules.webhook.application import service
from zid_platform.modules.webhook.application.runtime import system_resolver
from zid_platform.modules.webhook.domain.webhooks import WebhookError
from zid_platform.modules.webhook.infrastructure import endpoint_store as store
from zid_platform.protected import protected_action

router = APIRouter(tags=["webhooks-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def cloud_event_type(name: str) -> str:
    """`credential.status_changed` -> `com.zoiko.id.credential.status_changed.v1`; a full CloudEvents type is kept as it is."""
    return name if name.startswith("com.") else f"com.zoiko.id.{name}.v1"


def document_name(event_type: str) -> str:
    """The reverse, for answers: the name a consumer of Document 02 expects."""
    prefix, suffix = "com.zoiko.id.", ".v1"
    if event_type.startswith(prefix) and event_type.endswith(suffix):
        return event_type[len(prefix) : -len(suffix)]
    return event_type


class V2EndpointIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=2048)
    event_types: list[str] = Field(min_length=1, max_length=32)
    max_inflight: int = Field(default=4, ge=1, le=16)


def _endpoint_view(e: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in e.items() if k != "verification_token"}  # a token never leaves the cell
    if "event_types" in out:
        out["event_types"] = [document_name(t) for t in out["event_types"]]
    return out


@router.post("/v2/webhook-endpoints", status_code=201)
async def register(
    body: V2EndpointIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    signer = v1._signer(request)
    resolve = getattr(request.app.state, "webhook_resolver", None) or system_resolver
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/webhook-endpoints")
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            stored = await begin(
                conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
            )
            if stored is not None:
                return dict(stored.body)
            await protected_action(
                conn, caller, action="webhook.endpoint.manage", resource="webhook_endpoint:new"
            )
            made = await service.register(
                conn,
                caller.ctx.tenant_id or "",
                url=body.url,
                event_types=[cloud_event_type(t) for t in body.event_types],
                max_inflight=body.max_inflight,
                signer_kid=signer.kid,
                actor=caller.ctx.principal_id or "",
                resolve=resolve,
                now=datetime.now(UTC),
                allow_http=bool(getattr(request.app.state, "webhook_allow_http", False)),
            )
            out = {**_ids(), **_endpoint_view(made)}
            await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
            return out
    except WebhookError as e:
        raise v1._fail(e) from e


@router.get("/v2/webhook-endpoints")
async def list_endpoints(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
        )
        items = await store.list_endpoints(conn)
    return {**_ids(), "items": [_endpoint_view(e) for e in items]}


@router.get("/v2/events/{event_id}")
async def read_event(event_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(
                conn, caller, action="webhook.endpoint.read", resource="webhook_endpoint:*"
            )
            d = await store.load_delivery(conn, str(event_id))
    except WebhookError as e:
        raise v1._fail(e) from e
    return {**_ids(), "event_id": str(event_id), **d}


@router.post("/v2/events/{event_id}/replay", status_code=202)
async def replay_event(event_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    out = await v1.replay_delivery(event_id, request, caller)
    return {**_ids(), "event_id": str(event_id), **out}
