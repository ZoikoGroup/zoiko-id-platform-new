"""Disputes, appeals and erasure requests: the company's side and the operator's side (CR-159; ZID-EXT-IDV-001 WF-14, WF-16, epic E18).

The company raises a case about one session or one of its customer references. An operator takes it into review and a DIFFERENT operator resolves or rejects it, with a reason the company can read. A dispute or an
appeal changes no decision (a new look is a recheck, which makes a new decision version). An erasure removes the customer reference from the sessions and leaves the decisions, so the audit trail stays whole.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.verify_api.application import observe
from zid_platform.modules.verify_api.domain import cases as c
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import case_store as db
from zid_platform.modules.verify_api.infrastructure import session_store as sessions


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def case_view(r: dict[str, Any], *, operator: bool = False) -> dict[str, Any]:
    """Never the customer reference, never its hash: a case about a reference says only that it is one."""
    out: dict[str, Any] = {
        "case_id": str(r["case_id"]),
        "kind": r["kind"],
        "status": r["status"],
        "session_id": r["session_id"],
        "reason": r["reason"],
        "note": r["note"],
        "sessions_erased": r["sessions_erased"],
        "created_at": _iso(r["created_at"]),
        "decided_at": _iso(r["decided_at"]),
    }
    if operator:
        out["tenant_id"] = str(r["tenant_id"])
        out["reviewer_id"] = str(r["reviewer_id"]) if r["reviewer_id"] else None
    return out


# ------------------------------------------------------------------------------- the company


async def list_cases(conn: AsyncConnection) -> dict[str, Any]:
    return {"items": [case_view(r) for r in await db.list_cases(conn)]}


async def raise_case(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    body: dict[str, Any],
) -> dict[str, Any]:
    kind = c.check_kind(body.get("kind", ""))
    reason = c.check_reason(body.get("reason"))
    session_id = body.get("session_id") or None
    reference = body.get("customer_reference") or None
    c.check_target(
        kind, session_id=session_id, reference=reference
    )  # a session for a dispute or appeal, a reference for an erasure, never both
    ref_hash: bytes | None = None
    if kind is c.CaseKind.ERASURE:
        ref_hash = c.reference_hash(tenant_id, c.check_reference(reference))
        if await db.count_sessions_for(conn, tenant_id, ref_hash) == 0:
            raise m.PartnerError(
                "NOT_FOUND", "No session of yours carries that customer reference."
            )
    else:
        found = await sessions.get_session_any(conn, session_id or "")
        if found is None:
            raise m.PartnerError("NOT_FOUND", "No such session.")
        latest = await sessions.latest_decision(conn, found["session_id"])
        c.check_session_state(kind, found["status"], latest["outcome"] if latest else None)
    case_id = str(uuid.uuid4())
    try:
        await db.insert_case(
            conn,
            case_id=case_id,
            tenant_id=tenant_id,
            kind=kind.value,
            session_id=session_id,
            reference_hash=ref_hash,
            reason=reason,
            raised_by=principal,
        )
    except Exception as e:  # the unique indexes: one open case of a kind per session or reference
        if "api_case_one_open" in str(e):
            raise m.PartnerError(
                "CASE_OPEN",
                "There is already an open case for that. Withdraw it to send a new one.",
            ) from e
        raise
    made = await db.get_case(conn, case_id)
    assert made is not None
    observe.CASES.inc(kind=kind.value, event="raised")
    return case_view(made)


async def withdraw(
    conn: AsyncConnection, case_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await db.get_case(conn, case_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such case.")
    c.check_transition(c.CaseStatus(r["status"]), c.CaseStatus.WITHDRAWN)
    await db.move_case(conn, case_id, c.CaseStatus.WITHDRAWN.value, now)
    observe.CASES.inc(kind=r["kind"], event="withdrawn")
    fresh = await db.get_case(conn, case_id)
    assert fresh is not None
    return case_view(fresh)


# ------------------------------------------------------------------------------- the operator (inside `go_live_store.operator_scope`)


async def operator_open(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [case_view(r, operator=True) for r in await db.open_cases(conn)]


async def _load(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    r = await db.get_case(conn, case_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such case.")
    return r


async def start_review(
    conn: AsyncConnection, case_id: str, operator: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _load(conn, case_id)
    c.check_transition(c.CaseStatus(r["status"]), c.CaseStatus.IN_REVIEW)
    await db.move_case(conn, case_id, c.CaseStatus.IN_REVIEW.value, now, reviewer=operator)
    observe.CASES.inc(kind=r["kind"], event="reviewed")
    return case_view((await db.get_case(conn, case_id)) or r, operator=True)


async def reject(
    conn: AsyncConnection,
    case_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _load(conn, case_id)
    c.check_transition(c.CaseStatus(r["status"]), c.CaseStatus.REJECTED)
    clean = c.check_note(note, required=True)
    await db.move_case(
        conn, case_id, c.CaseStatus.REJECTED.value, now, decider=operator, note=clean
    )
    observe.CASES.inc(kind=r["kind"], event="rejected")
    return case_view((await db.get_case(conn, case_id)) or r, operator=True)


async def resolve(
    conn: AsyncConnection,
    case_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Resolve an in-review case, by a different operator from the reviewer. An erasure case also carries out the erasure, in the company's own scope, and records how many sessions it changed."""
    now = now or datetime.now(UTC)
    r = await _load(conn, case_id)
    c.check_transition(c.CaseStatus(r["status"]), c.CaseStatus.RESOLVED)
    if r["reviewer_id"] is not None and str(r["reviewer_id"]) == operator:
        raise m.PartnerError(
            "CASE_SELF_RESOLVE", "The person who took the case into review cannot also resolve it."
        )
    clean = c.check_note(note, required=True)
    erased: int | None = None
    if r["kind"] == c.CaseKind.ERASURE.value:
        tenant_id = str(r["tenant_id"])
        async with in_tenant(conn, tenant_id):
            erased = await db.erase_reference(conn, tenant_id, bytes(r["reference_hash"]))
            # the fraud graph holds a token made from the same reference: it goes too, with the links made from it
            from zid_platform.modules.verify_api.application import fraud_service

            await fraud_service.purge_reference(conn, tenant_id, bytes(r["reference_hash"]), now)
    await db.move_case(
        conn,
        case_id,
        c.CaseStatus.RESOLVED.value,
        now,
        decider=operator,
        note=clean,
        sessions_erased=erased,
    )
    observe.CASES.inc(kind=r["kind"], event="resolved")
    return case_view((await db.get_case(conn, case_id)) or r, operator=True)
