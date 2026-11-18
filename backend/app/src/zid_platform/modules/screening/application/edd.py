"""EDD information requests, stored (ZID-DTV-WF-06 section 10; CR-121). The rules are `domain.edd_request`; this keeps the facts.

A request is opened by a reviewer for a case, answered by the customer, reviewed by someone other than the requester, and ticked by the worker: reminders are recorded (for a notifier to send) and an unanswered
request expires. Expiry never clears the case. Run under the case's own tenant, except `tick_all`, which finds due requests as a screening reviewer and then works under each request's tenant.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain import edd_request as e
from zid_platform.modules.screening.domain.decision import ScreeningError

_COLS = "edd_id, tenant_id, request_id, items, requested_by, opened_at, due_at, state, provided, reminders, reviewed_by, review_code"


def _request(r: Any) -> e.Request:
    return e.Request(
        request_id=str(r.edd_id),
        case_id=str(r.request_id),
        items=tuple(r.items),
        requested_by=str(r.requested_by),
        opened_at=r.opened_at,
        due_at=r.due_at,
        state=e.State(r.state),
        provided=frozenset(r.provided),
        reminders=tuple(datetime.fromisoformat(x) for x in r.reminders),
        reviewed_by=str(r.reviewed_by) if r.reviewed_by else None,
        review_code=r.review_code,
    )


def _fail(err: e.EddError) -> ScreeningError:
    return ScreeningError(err.code, err.args[0])


async def _load(conn: AsyncConnection, edd_id: str, *, lock: bool = True) -> Any:
    try:
        key = uuid.UUID(edd_id)
    except ValueError as err:
        raise ScreeningError("REQUEST_NOT_FOUND", edd_id) from err
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM verify.screening_edd_request WHERE edd_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": key},
        )
    ).first()
    if row is None:
        raise ScreeningError("REQUEST_NOT_FOUND", edd_id)
    return row


async def _save(conn: AsyncConnection, r: e.Request, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.screening_edd_request SET state = :s, provided = CAST(:p AS jsonb), reminders = CAST(:rm AS jsonb), reviewed_by = :by, review_code = :c, updated_at = :now WHERE edd_id = :i"
        ),
        {
            "s": r.state.value,
            "p": json.dumps(sorted(r.provided)),
            "rm": json.dumps([x.isoformat() for x in r.reminders]),
            "by": uuid.UUID(r.reviewed_by) if r.reviewed_by else None,
            "c": r.review_code,
            "now": now,
            "i": uuid.UUID(r.request_id),
        },
    )


def view(r: e.Request) -> dict[str, Any]:
    """What the customer sees: the state in plain words, what is still wanted, the due date, and wording that never says why."""
    return {
        "edd_id": r.request_id,
        "state": r.state.value,
        "still_needed": list(r.missing),
        "due_at": r.due_at.isoformat(),
        "message": e.customer_text(r),
    }


async def open_request(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    items: list[str],
    *,
    requested_by: str,
    due_in_days: int,
    now: datetime,
) -> dict[str, Any]:
    edd_id = str(uuid.uuid4())
    try:
        r = e.open_request(
            edd_id, case_id, items, requested_by=requested_by, now=now, due_in_days=due_in_days
        )
    except e.EddError as err:
        raise _fail(err) from err
    try:
        async with (
            conn.begin_nested()
        ):  # a refused duplicate must not abort the caller's transaction
            await conn.execute(
                text(
                    "INSERT INTO verify.screening_edd_request (edd_id, tenant_id, request_id, items, requested_by, opened_at, due_at, state, updated_at) "
                    "VALUES (:i, :t, :c, CAST(:it AS jsonb), :by, :at, :due, 'REQUESTED', :at)"
                ),
                {
                    "i": uuid.UUID(edd_id),
                    "t": uuid.UUID(tenant_id),
                    "c": uuid.UUID(case_id),
                    "it": json.dumps(list(r.items)),
                    "by": uuid.UUID(requested_by),
                    "at": now,
                    "due": r.due_at,
                },
            )
    except IntegrityError as err:
        raise ScreeningError(
            "REQUEST_ALREADY_OPEN", "this case already has an open request for information"
        ) from err
    return view(r)


async def receive(
    conn: AsyncConnection, edd_id: str, provided: list[str], *, now: datetime
) -> dict[str, Any]:
    r = _request(await _load(conn, edd_id))
    try:
        r = e.receive(r, provided, now=now)
    except e.EddError as err:
        raise _fail(err) from err
    await _save(conn, r, now)
    return view(r)


async def review(
    conn: AsyncConnection,
    edd_id: str,
    *,
    reviewer: str,
    accepted: bool,
    code: str | None,
    now: datetime,
) -> dict[str, Any]:
    r = _request(await _load(conn, edd_id))
    try:
        r = e.review(r, reviewer=reviewer, accepted=accepted, code=code)
    except e.EddError as err:
        raise _fail(err) from err
    await _save(conn, r, now)
    return view(r)


async def tick_all(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """The worker pass: record reminders that fell due and expire what ran out. Finds open requests as a screening reviewer, then works under each request's own tenant. Expiry clears nothing."""
    from zid_db import in_tenant

    counts = {"reminders": 0, "expired": 0}
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        rows = (
            await conn.execute(
                text(
                    "SELECT edd_id, tenant_id FROM verify.screening_edd_request WHERE state = 'REQUESTED'"
                )
            )
        ).all()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    for row in rows:
        async with in_tenant(conn, str(row.tenant_id)):
            r = _request(await _load(conn, str(row.edd_id)))
            after, events = e.tick(r, now=now)
            if not events:
                continue
            await _save(conn, after, now)
            counts["reminders"] += events.count("REMINDER")
            counts["expired"] += events.count("EXPIRED")
    return counts


async def current(conn: AsyncConnection, case_id: str) -> dict[str, Any] | None:
    """The case's open request as the customer sees it, or None."""
    try:
        key = uuid.UUID(case_id)
    except ValueError:
        return None
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM verify.screening_edd_request WHERE request_id = :c AND state IN ('REQUESTED', 'RECEIVED')"
            ),
            {"c": key},
        )
    ).first()
    return view(_request(row)) if row else None


async def receive_for_case(
    conn: AsyncConnection, case_id: str, provided: list[str], *, now: datetime
) -> dict[str, Any]:
    open_one = await current(conn, case_id)
    if open_one is None:
        raise ScreeningError(
            "REQUEST_NOT_FOUND", "there is no open request for information on this case"
        )
    return await receive(conn, open_one["edd_id"], provided, now=now)


async def list_for_case(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    """For a reviewer: every request on the case, with who asked, who reviewed and why it was not enough."""
    try:
        key = uuid.UUID(case_id)
    except ValueError:
        return []
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM verify.screening_edd_request WHERE request_id = :c ORDER BY opened_at"
            ),
            {"c": key},
        )
    ).all()
    return [
        {
            **view(_request(r)),
            "requested_by": str(r.requested_by),
            "reviewed_by": str(r.reviewed_by) if r.reviewed_by else None,
            "review_code": r.review_code,
            "reminders": len(r.reminders),
        }
        for r in rows
    ]


async def locate(conn: AsyncConnection, edd_id: str) -> str | None:
    """The tenant a request belongs to, for a screening reviewer. The caller then works under that tenant's own scope."""
    try:
        key = uuid.UUID(edd_id)
    except ValueError:
        return None
    await conn.execute(text("SELECT set_config('app.screening_reviewer', 'on', true)"))
    try:
        row = (
            await conn.execute(
                text("SELECT tenant_id FROM verify.screening_edd_request WHERE edd_id = :i"),
                {"i": key},
            )
        ).first()
    finally:
        await conn.execute(text("SELECT set_config('app.screening_reviewer', '', true)"))
    return str(row.tenant_id) if row else None
