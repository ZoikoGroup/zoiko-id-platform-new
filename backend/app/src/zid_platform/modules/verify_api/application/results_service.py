"""The company's view of its verification results across all its applications (CR-155; ZID-EXT-IDV-001 epic E16, WF-12).

A signed-in administrator searches the company's sessions by fixed filters (application, environment, state, an exact customer reference), opens one to see its decision history and the rechecks that follow
it, and may start a recheck. Everything is the minimized view the partner's server already gets: an outcome, which checks passed, reason codes and provenance. Never a photograph, a document image, a score or a
reviewer's note. Rows are confined to the company by row-level security; the application name is the only addition.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.application import sessions_service as svc
from zid_platform.modules.verify_api.application.service import VerifyApiCaller
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import sessions as s
from zid_platform.modules.verify_api.infrastructure import session_store as db
from zid_platform.modules.verify_api.infrastructure import store

ENVIRONMENTS = ("SANDBOX", "PRODUCTION")


async def _names(conn: AsyncConnection) -> dict[str, str]:
    return {str(a["application_id"]): str(a["name"]) for a in await store.list_applications(conn)}


async def search(
    conn: AsyncConnection,
    *,
    application_id: str | None = None,
    environment: str | None = None,
    status: str | None = None,
    customer_reference: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    if status is not None and status not in {x.value for x in s.Status}:
        raise m.PartnerError(
            "VALIDATION_FAILED", f"status is one of: {', '.join(x.value for x in s.Status)}."
        )
    if environment is not None and environment.upper() not in ENVIRONMENTS:
        raise m.PartnerError("VALIDATION_FAILED", "environment is SANDBOX or PRODUCTION.")
    if not 1 <= limit <= s.MAX_LIST:
        raise m.PartnerError("VALIDATION_FAILED", f"limit is between 1 and {s.MAX_LIST}.")
    rows = await db.search_sessions(
        conn,
        application_id=application_id,
        environment=environment.upper() if environment else None,
        status=status,
        reference=customer_reference,
        after=svc._decode_cursor(cursor) if cursor else None,
        limit=limit + 1,
    )
    page = rows[:limit]
    names = await _names(conn)
    return {
        "items": [
            {
                **svc.session_view(r),
                "application_id": str(r["application_id"]),
                "application_name": names.get(str(r["application_id"]), ""),
            }
            for r in page
        ],
        "next_cursor": svc._cursor(page[-1]) if len(rows) > limit and page else None,
    }


async def detail(
    conn: AsyncConnection, session_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """One session with every decision made about it (oldest first) and the rechecks that follow it. A session nobody finished in time is shown as expired."""
    now = now or datetime.now(UTC)
    row = await db.get_session_any(conn, session_id, lock=True)
    if row is None:
        raise m.PartnerError("NOT_FOUND", "No such session.")
    row = await svc._expire_if_due(conn, row, now)
    names = await _names(conn)
    return {
        **svc.session_view(row),
        "application_id": str(row["application_id"]),
        "application_name": names.get(str(row["application_id"]), ""),
        "decisions": [svc.decision_view(d, row) for d in await db.list_decisions(conn, session_id)],
        "rechecks": [
            {
                "session_id": r["session_id"],
                "status": r["status"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in await db.rechecks_of(conn, session_id)
        ],
        "can_recheck": s.Status(row["status"]) in svc.RECHECKABLE,
    }


async def recheck(
    conn: AsyncConnection,
    caller: VerifyApiCaller,
    session_id: str,
    *,
    key: str,
    origin: str,
) -> dict[str, Any]:
    """A signed-in administrator starts a recheck, acting as the application that made the session."""
    view, _ = await svc.recheck_session(conn, caller, session_id, key=key, origin=origin)
    return view
