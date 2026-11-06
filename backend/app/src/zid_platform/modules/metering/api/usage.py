"""Usage and rate-limit API (ZID-UX-001 §18; CR-023).

GET /v1/usage?days=7         daily calls per API surface and status class, and per OAuth client (metering.read)
GET /v1/usage/limits         the tenant's request limit and where it stands this minute       (metering.read)

Counts only: no paths with ids, bodies or people.
"""

from __future__ import annotations

import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.metering.infrastructure import usage_store
from zid_platform.protected import protected_action

router = APIRouter(tags=["usage"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/v1/usage")
async def usage(
    request: Request, caller: CallerDep, days: Annotated[int, Query(ge=1, le=31)] = 7
) -> dict[str, Any]:
    recorder = getattr(request.app.state, "usage_recorder", None)
    if recorder is not None:
        await recorder.flush()  # include the last few seconds in what the caller sees
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="metering.read", resource="usage:*")
        rows = await usage_store.summary(conn, days=days)
        clients = await usage_store.by_client(conn, days=days)
    return {"days": days, "items": rows, "clients": clients}


@router.get("/v1/usage/limits")
async def limits(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="metering.read", resource="usage:*")
    limiter = getattr(request.app.state, "rate_limiter", None)
    if limiter is None or limiter.limit <= 0:
        return {
            "enforced": False,
            "scope": "tenant",
            "limit_per_minute": 0,
            "used_this_minute": 0,
            "resets_in_s": 0,
        }
    resolve_limit = getattr(request.app.state, "plan_request_limit", None)
    plan_limit = await resolve_limit(request_engine(request), caller.ctx) if resolve_limit is not None else None
    v = limiter.peek(caller.ctx.tenant_id or "", time.monotonic(), plan_limit)
    if plan_limit == 0:  # the plan has no request limit (contracted)
        return {"enforced": False, "scope": "tenant", "limit_per_minute": 0, "used_this_minute": 0, "resets_in_s": 0}
    return {
        "enforced": True,
        "scope": "tenant",
        "limit_per_minute": v.limit,
        "used_this_minute": v.used,
        "resets_in_s": v.retry_after_s,
        "on_exceed": "HTTP 429 RATE_LIMITED with a Retry-After header; wait that long, then retry",
    }
