"""Operator plane: move an enterprise service request forward (commercial.service_request). Run inside `operator_scope`.

REQUESTED -> IN_PROGRESS or DECLINED; IN_PROGRESS -> ACTIVE or DECLINED. A closed request (ACTIVE, DECLINED, WITHDRAWN) does not move again; a company
that needs the service again asks again. Every move is recorded in the commercial change log, and the company is told in its own Alerts by the caller.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.application.change_log import log_change

MOVES = {
    "REQUESTED": frozenset({"IN_PROGRESS", "DECLINED"}),
    "IN_PROGRESS": frozenset({"ACTIVE", "DECLINED"}),
}
MAX_NOTE = 1000


class ServiceRequestError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail or code.replace("_", " ").capitalize() + "."


def check_move(current: str, target: str, note: str | None) -> str:
    """The cleaned note for a move that is allowed, or the reason it is not."""
    if target not in MOVES.get(current, frozenset()):
        raise ServiceRequestError(
            "SERVICE_REQUEST_MOVE_REFUSED",
            f"A {current.lower().replace('_', ' ')} request cannot become {target.lower().replace('_', ' ')}.",
        )
    clean = (note or "").strip()
    if len(clean) > MAX_NOTE:
        raise ServiceRequestError(
            "VALIDATION_FAILED", f"The note is longer than {MAX_NOTE} characters."
        )
    if target == "DECLINED" and len(clean) < 3:
        raise ServiceRequestError(
            "SERVICE_REQUEST_NOTE_REQUIRED", "Say why it is declined; the company reads it."
        )
    return clean


async def open_requests(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT request_id, tenant_id, service, status, details, contact, note, created_at, updated_at FROM commercial.service_request "
                "WHERE status IN ('REQUESTED', 'IN_PROGRESS') ORDER BY created_at"
            )
        )
    ).all()
    return [
        {
            "request_id": str(r.request_id),
            "tenant_id": str(r.tenant_id),
            "service": r.service,
            "status": r.status,
            "details": r.details,
            "contact": r.contact,
            "note": r.note,
            "created_at": r.created_at.isoformat(),
            "updated_at": r.updated_at.isoformat(),
        }
        for r in rows
    ]


async def move(
    conn: AsyncConnection, request_id: str, *, target: str, operator_id: str, note: str | None
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT tenant_id, service, status FROM commercial.service_request WHERE request_id = :i"
            ),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    if row is None:
        raise ServiceRequestError("SERVICE_REQUEST_NOT_FOUND", "No such service request.")
    clean = check_move(row.status, target, note)
    now = datetime.now(UTC)
    res = await conn.execute(
        text(
            "UPDATE commercial.service_request SET status = :s, note = :n, moved_by = :by, moved_at = :at, updated_at = :at "
            "WHERE request_id = :i AND status = :cur"
        ),
        {
            "s": target,
            "n": clean,
            "by": uuid.UUID(operator_id),
            "at": now,
            "i": uuid.UUID(request_id),
            "cur": row.status,
        },
    )
    if res.rowcount != 1:
        raise ServiceRequestError(
            "SERVICE_REQUEST_MOVE_REFUSED",
            "The request changed while you were moving it; reload and try again.",
        )
    await log_change(
        conn,
        kind=f"SERVICE_REQUEST_{target}",
        ref=request_id,
        tenant_id=str(row.tenant_id),
        actor=operator_id,
        reason=clean or None,
        detail={"service": row.service},
    )
    return {
        "request_id": request_id,
        "status": target,
        "tenant_id": str(row.tenant_id),
        "service": row.service,
        "note": clean,
    }
