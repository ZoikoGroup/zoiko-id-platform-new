"""Biometric method lifecycle service (ZID-DTV-WF-04 BIO-03; CR-107): propose a step up, approve or reject it, step down (the kill switch), list.

    propose        a step up for an exact scope; changes nothing until someone else approves it
    approve        the second person: the proposal becomes the scope's next version, if the state has not moved since
    reject         refuse (or withdraw) an open proposal; nothing changes state
    step_down      PAUSED, a lower state, or OFF: one person, at once
    list_scopes    each scope's versions, oldest first, and the open proposals

All of it runs inside the caller's transaction, under a row lock on the scope's history. Who may call it, and the audit record, is the API's job.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.infrastructure import biometric_method_store as store


async def propose(
    conn: AsyncConnection,
    *,
    scope: bm.Scope,
    target: bm.State,
    dpia_ref: str | None,
    evaluation_ref: str | None,
    reason: str,
    evidence_ref: str,
    by: str,
    now: datetime,
    scope_id: str | None = None,
    exposure_percent: int = 100,
) -> bm.Proposal:
    """`scope_id` names a new scope; an existing scope is found by its fields, and a different id for it is refused."""
    bm.check_scope(scope)
    known = await store.scope_id_of(conn, scope)
    if known is not None and scope_id is not None and scope_id != known:
        raise bm.MethodError("METHOD_SCOPE_ID_TAKEN", f"this scope is {known}")
    sid = known or scope_id
    if sid is None:
        raise bm.MethodError("METHOD_SCOPE_ID_REQUIRED", "name the new scope")
    history = await store.history(conn, sid, lock=True)
    if any(p.scope_id == sid for p in await store.proposals(conn, open_only=True)):
        raise bm.MethodError("METHOD_PROPOSAL_OPEN", "this scope already has an open proposal")
    last = bm.current(history)
    p = bm.Proposal(
        proposal_id=str(uuid.uuid4()),
        scope_id=sid,
        scope=scope,
        from_version=last.version if last else 0,
        target=target,
        dpia_ref=dpia_ref,
        evaluation_ref=evaluation_ref,
        reason=reason,
        evidence_ref=evidence_ref,
        proposed_by=by,
        proposed_at=now,
        exposure_percent=exposure_percent,
    )
    bm.check_proposal(p, history=history, now=now)
    await store.insert_proposal(conn, p)
    return p


async def _open(conn: AsyncConnection, proposal_id: str) -> bm.Proposal:
    p = await store.proposal(conn, proposal_id, lock=True)
    if p is None:
        raise bm.MethodError("METHOD_PROPOSAL_NOT_FOUND", proposal_id)
    return p


async def approve(
    conn: AsyncConnection, proposal_id: str, *, by: str, reason: str, now: datetime
) -> tuple[bm.Proposal, bm.Record]:
    p = await _open(conn, proposal_id)
    history = await store.history(conn, p.scope_id, lock=True)
    decided, record = bm.approve(p, history=history, by=by, reason=reason, now=now)
    await store.decide_proposal(conn, decided)
    await store.insert_state(conn, record)
    return decided, record


async def reject(
    conn: AsyncConnection, proposal_id: str, *, by: str, reason: str, now: datetime
) -> bm.Proposal:
    decided = bm.reject(await _open(conn, proposal_id), by=by, reason=reason, now=now)
    await store.decide_proposal(conn, decided)
    return decided


async def step_down(
    conn: AsyncConnection,
    scope_id: str,
    *,
    target: bm.State,
    by: str,
    reason: str,
    evidence_ref: str,
    now: datetime,
    exposure_percent: int | None = None,
) -> bm.Record:
    history = await store.history(conn, scope_id, lock=True)
    record = bm.step_down(
        history,
        target=target,
        by=by,
        reason=reason,
        evidence_ref=evidence_ref,
        now=now,
        exposure_percent=exposure_percent,
    )
    await store.insert_state(conn, record)
    return record


async def list_scopes(conn: AsyncConnection) -> dict[str, Any]:
    by_id: dict[str, list[bm.Record]] = {}
    for r in await store.all_states(conn):
        by_id.setdefault(r.scope_id, []).append(r)
    return {
        "scopes": [by_id[k] for k in sorted(by_id)],
        "open_proposals": await store.proposals(conn, open_only=True),
    }


async def availability_for(
    conn: AsyncConnection, call: bm.Call, *, at: datetime
) -> bm.Availability:
    return bm.availability(bm.in_force(await store.for_call(conn, call), at), call)
