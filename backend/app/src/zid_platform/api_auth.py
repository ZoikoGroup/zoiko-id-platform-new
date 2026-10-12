"""Shared API plumbing for module routers: the authenticated caller and domain-error mapping.

`authenticated_caller` is the single seam where Phase E authentication plugs in: its middleware sets
`request.state.caller`. Until then every protected endpoint answers 401 instead of running without a
trusted, server-derived tenant (INV-13). Domain error codes map to HTTP statuses per ZID-API-001 §3.3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from zid_context import RequestContext
from zid_errors import ZidError

from zid_platform.modules.decision.domain.pdp import AuthnFact


@dataclass(frozen=True, slots=True)
class Caller:
    ctx: RequestContext  # server-derived tenant and the authenticated principal
    authn: AuthnFact
    security_state: str = (
        "UNKNOWN"  # from the signals PIP (Phase E); UNKNOWN fails closed for R2/R3
    )
    client_id: str = "unknown-client"  # OAuth client, scopes idempotency keys


def authenticated_caller(request: Request) -> Caller:
    caller = getattr(request.state, "caller", None)
    if not isinstance(caller, Caller) or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "A trusted, authenticated caller is required.", http_status=401
        )
    return caller


_FORBIDDEN = (
    "AUTHORITY_",
    "GRANTOR_",
    "GRANT_",
    "ASSURANCE_INSUFFICIENT",
    "MANDATE_SOURCE_INVALID",
    "MANDATE_SCOPE_EXCEEDED",
    "MANDATE_TOOL_NOT_ALLOWED",
    "MANDATE_EXECUTOR_MISMATCH",
    "PURPOSE_NOT_ALLOWED",
    "APPROVER_",
)
_UNPROCESSABLE = (
    "MANDATE_VALUE_INVALID",
    "MANDATE_CURRENCY_MISMATCH",
    "VALIDATION_FAILED",
    "MANDATE_BUDGET_INVALID",
)


def http_error(code: str, detail: str = "") -> ZidError:
    """Domain code -> ZidError with the API §3.3 status (codes stay stable machine contracts)."""
    if code.endswith("NOT_FOUND"):
        status = 404
    elif code.startswith(_UNPROCESSABLE):
        status = 422
    elif code.startswith(_FORBIDDEN):
        status = 403
    elif code in ("STATE_UNAVAILABLE", "DEPENDENCY_UNAVAILABLE"):
        status = 503
    else:
        status = 409  # limits, conflicts, stale writer epoch, invalid transitions
    retryable = code in ("WRITER_EPOCH_STALE", "STATE_UNAVAILABLE", "CONCURRENT_MODIFICATION")
    return ZidError(
        code,
        detail or code.replace("_", " ").capitalize() + ".",
        http_status=status,
        retryable=retryable,
        retry_after_ms=250 if retryable else None,
    )


def request_engine(request: Request) -> AsyncEngine:
    engine: AsyncEngine | None = getattr(request.app.state, "engine", None)
    if engine is None:
        raise http_error("DEPENDENCY_UNAVAILABLE", "No database is configured for this service.")
    return engine


async def cell_writer_epoch(
    request: Request, conn: AsyncConnection, tenant_id: str
) -> tuple[uuid.UUID, int]:
    """This cell's identity and the tenant's current writer epoch (DEC-C5; MAND-21).

    Ledger writes are accepted only in the cell that holds the tenant's writer fence; a request routed
    to any other cell is refused (retryable after re-routing) instead of writing under a stale epoch.
    The ledger re-checks the fence under a row lock in the same transaction.
    """
    cell: str | None = getattr(request.app.state, "cell_uuid", None)
    if not cell:
        raise http_error("DEPENDENCY_UNAVAILABLE", "This cell has no ZID_CELL_UUID configured.")
    row = (
        await conn.execute(
            text("SELECT cell_id, epoch FROM agent.writer_fence WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None or str(row.cell_id) != cell:
        raise http_error("WRITER_EPOCH_STALE", "This cell is not the tenant's current writer.")
    return uuid.UUID(cell), int(row.epoch)
