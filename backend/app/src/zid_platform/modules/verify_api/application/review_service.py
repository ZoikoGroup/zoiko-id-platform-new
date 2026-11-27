"""Case review and four-eyes control (CR-167; ZID-CP-WF04 v2.0).

Extends a partner's existing sessions and decisions; it makes no second session or decision store. A session waiting for review becomes a CASE. A reviewer CLAIMS it (a 15-minute lease), records FINDINGS on the required
checks, and PROPOSES an outcome; a DIFFERENT reviewer gives the second review; only then does the finality transaction write the decision (a new version that supersedes any earlier one), the event and the webhook outbox entry
together. Nothing here lets a proposal be final, a stale screen change anything, one person approve their own work, a held case move, or a finished case be edited: a correction is an APPEAL, a new case whose decision supersedes.

Every change names the case version it read and runs with the case row locked. Every step is an event whose digest includes the one before it.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.verify_api.application import observe
from zid_platform.modules.verify_api.application import sessions_service as ss
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.domain import review as r
from zid_platform.modules.verify_api.domain import sessions as s
from zid_platform.modules.verify_api.infrastructure import review_store as db
from zid_platform.modules.verify_api.infrastructure import session_store as sdb

CHALLENGES = ("evidence_reviewed", "reason_matches_evidence", "no_conflict_of_interest")
Adviser = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
ADVISER_MODEL = "adviser-v1"


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _new_id(prefix: str, n: int) -> str:
    return prefix + uuid.uuid4().hex[:n]


# ------------------------------------------------------------------------------- views


def case_view(
    c: dict[str, Any],
    *,
    lease: r.Lease | None = None,
    hold: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    live = lease is not None and not lease.released and lease.expires_at > now
    return {
        "case_id": c["case_id"],
        "session_id": c["session_id"],
        "case_type": c["case_type"],
        "parent_case_id": c["parent_case_id"],
        "status": c["status"],
        "case_version": c["case_version"],
        "queue": c["queue"],
        "four_eyes_required": c["four_eyes_required"],
        "required_checks": list(c["required_checks"]),
        "findings": dict(c["findings"] or {}),
        "policy_version": c["policy_version"],
        "final_outcome": c["final_outcome"],
        "created_at": _iso(c["created_at"]),
        "sla_due_at": _iso(c["sla_due_at"]),
        "overdue": c["status"] != "APPROVED_FINAL" and c["sla_due_at"] <= now,
        "lease": {"reviewer": lease.reviewer, "expires_at": _iso(lease.expires_at)}
        if live and lease
        else None,
        "on_hold": hold is not None,
    }


async def _detail(conn: AsyncConnection, c: dict[str, Any], now: datetime) -> dict[str, Any]:
    await db.expire_leases(conn, c["case_id"], now)
    out = case_view(
        c,
        lease=await db.live_lease(conn, c["case_id"], now),
        hold=await db.active_hold(conn, c["case_id"]),
        now=now,
    )
    out["evidence"] = [r.redact(e) for e in await db.evidence(conn, c["case_id"])]
    out["evidence_manifest_hash"] = await _manifest(
        conn, c["case_id"]
    )  # a proposal must name exactly this
    out["challenges"] = list(CHALLENGES)  # the second reviewer's checklist
    out["info_templates"] = dict(
        r.INFO_TEMPLATES
    )  # the only requests a reviewer may send the applicant
    proposal = await db.open_proposal(conn, c["case_id"])
    out["proposal"] = (
        {
            "proposal_id": str(proposal["proposal_id"]),
            "outcome": proposal["outcome"],
            "reason_code": proposal["reason_code"],
            "reviewer": str(proposal["reviewer"]),
            "created_at": _iso(proposal["created_at"]),
        }
        if proposal
        else None
    )
    appeal = await db.appeal_of(conn, c["case_id"])
    out["appeal_case_id"] = appeal["case_id"] if appeal else None
    return out


async def list_cases(
    conn: AsyncConnection,
    *,
    status: str | None,
    queue: str | None,
    limit: int = 50,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    if status is not None and status not in {x.value for x in r.CaseState}:
        raise m.PartnerError("VALIDATION_FAILED", "Unknown case state.")
    if queue is not None and queue not in ("STANDARD", "SPECIALIST"):
        raise m.PartnerError("VALIDATION_FAILED", "The queue is STANDARD or SPECIALIST.")
    items = []
    for c in await db.list_cases(conn, status=status, queue=queue, limit=limit):
        items.append(
            case_view(
                c,
                lease=await db.live_lease(conn, c["case_id"], now),
                hold=await db.active_hold(conn, c["case_id"]),
                now=now,
            )
        )
    return {"items": items}


async def _load(conn: AsyncConnection, case_id: str, *, lock: bool = False) -> dict[str, Any]:
    c = await db.get_case(conn, case_id, lock=lock)
    if c is None:
        raise m.PartnerError(
            "CASE_NOT_FOUND", "No such case."
        )  # the same answer for a case that is not this company's
    return c


async def get(conn: AsyncConnection, case_id: str, now: datetime | None = None) -> dict[str, Any]:
    return await _detail(conn, await _load(conn, case_id), now or datetime.now(UTC))


async def _held(conn: AsyncConnection, case_id: str) -> None:
    if await db.active_hold(conn, case_id) is not None:
        raise m.PartnerError(
            "REGULATORY_HOLD",
            "A regulatory hold is in force on this case. It cannot be changed or finalized until the hold is released.",
        )


def _open(c: dict[str, Any]) -> r.CaseState:
    st = r.CaseState(c["status"])
    if st is r.CaseState.APPROVED_FINAL:
        raise m.PartnerError(
            "CASE_FINAL", "This case is final. A correction is an appeal, which makes a new case."
        )
    return st


async def _lease_for(
    conn: AsyncConnection, c: dict[str, Any], reviewer: str, now: datetime
) -> None:
    await db.expire_leases(conn, c["case_id"], now)
    r.require_lease(await db.live_lease(conn, c["case_id"], now), reviewer, now)


async def _event(
    conn: AsyncConnection,
    c: dict[str, Any],
    kind: str,
    actor: str,
    now: datetime,
    *,
    reason: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    await db.append_event(
        conn,
        case_id=c["case_id"],
        tenant_id=str(c["tenant_id"]),
        event_type=kind,
        actor=actor,
        reason=reason,
        data=data or {},
        now=now,
    )


async def _move(
    conn: AsyncConnection, c: dict[str, Any], target: r.CaseState, now: datetime, **kw: Any
) -> None:
    r.check_transition(r.CaseState(c["status"]), target)
    await db.bump(conn, c["case_id"], status=target.value, now=now, **kw)


# ------------------------------------------------------------------------------- opening a case


async def _snapshot(
    conn: AsyncConnection, case_id: str, tenant_id: str, row: dict[str, Any]
) -> None:
    """The case's evidence manifest: each check's result and the reasons are STANDARD; what the partner called the customer and where they are is SENSITIVE and shown only through a reveal. No photograph or document data exists here."""
    latest = await sdb.latest_decision(conn, row["session_id"])
    items: list[tuple[str, str, str, dict[str, Any], dict[str, Any]]] = []
    if latest is not None:
        for chk in latest["checks"]:
            items.append(
                (
                    "check",
                    f"Check: {chk['method']}",
                    r.STANDARD,
                    {"method": chk["method"], "result": chk["result"]},
                    {},
                )
            )
        items.append(
            (
                "reason_codes",
                "Reasons recorded",
                r.STANDARD,
                {"codes": list(latest["reason_codes"])},
                {},
            )
        )
        items.append(
            (
                "decision",
                "Latest decision",
                r.STANDARD,
                {"outcome": latest["outcome"], "version": latest["decision_version"]},
                {},
            )
        )
    items.append(
        (
            "session_context",
            "Customer and request",
            r.SENSITIVE,
            {},
            {
                "customer_reference": row["customer_reference"],
                "country": row["country"],
                "purpose_code": row["purpose_code"],
                "workflow_id": row["workflow_id"],
            },
        )
    )
    for kind, label, klass, summary, detail in items:
        await db.insert_evidence(
            conn,
            evidence_id=_new_id("ev_", 24),
            case_id=case_id,
            tenant_id=tenant_id,
            kind=kind,
            label=label,
            classification=klass,
            content_hash=r.content_hash([kind, summary, detail]),
            summary=summary,
            detail=detail,
        )


async def open_case(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    session_id: str,
    *,
    appeal_of: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = await sdb.get_session_any(conn, session_id)
    if row is None:
        raise m.PartnerError("CASE_NOT_FOUND", "No such session.")
    kind = "APPEAL" if appeal_of else "REVIEW"
    if kind == "REVIEW" and s.Status(row["status"]) is not s.Status.MANUAL_REVIEW:
        raise m.PartnerError(
            "CASE_NOT_ALLOWED", "Only a session waiting for review can be opened as a case."
        )
    if kind == "APPEAL" and s.Status(row["status"]) not in (
        s.Status.COMPLETED,
        s.Status.INCONCLUSIVE,
    ):
        raise m.PartnerError(
            "CASE_NOT_ALLOWED", "Only a session with a final result can be appealed."
        )
    required = list(s.WORKFLOWS[row["workflow_id"]]["methods"])
    case_id = _new_id("cas_", 32)
    try:
        async with conn.begin_nested():
            await db.insert_case(
                conn,
                case_id=case_id,
                tenant_id=tenant_id,
                session_id=session_id,
                case_type=kind,
                parent=appeal_of,
                queue="STANDARD",
                four_eyes=True,
                required=required,
                policy_version=(await sdb.latest_decision(conn, session_id) or {}).get(
                    "policy_version", "unset"
                ),
                region=row["region"],
                created_by=principal,
                now=now,
            )
    except Exception as e:  # the partial unique index: one open case of a kind per session
        if "api_review_case_one_open" in str(e):
            raise m.PartnerError(
                "CASE_OPEN", "There is already an open case for that session."
            ) from e
        raise
    await _snapshot(conn, case_id, tenant_id, row)
    c = await _load(conn, case_id)
    await _event(
        conn,
        c,
        "appeal_opened" if appeal_of else "opened",
        principal,
        now,
        data={"required_checks": required, "queue": "STANDARD"},
    )
    observe.REVIEWS.inc(event="opened")
    return await _detail(conn, c, now)


async def open_from_session(conn: AsyncConnection, row: dict[str, Any], now: datetime) -> None:
    """A session that has just moved to manual review is queued for a reviewer without anyone having to remember to. Idempotent and never raises."""
    try:
        if await db.case_for_session(conn, row["session_id"]) is None:
            await open_case(
                conn, str(row["tenant_id"]), str(row["application_id"]), row["session_id"], now=now
            )
    except m.PartnerError:
        return


async def attach_evidence(
    conn: AsyncConnection,
    case_id: str,
    actor: str,
    *,
    kind: str,
    label: str,
    summary: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Add one STANDARD evidence item from another part of the product (the fraud graph's lead), without touching the reviewer's work. It is refused once the case waits for its second review or is final, because the
    proposal there names the evidence the reviewers saw. The evidence manifest changes, so a proposal made earlier must be made again: that is the rule, not a side effect."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    await _held(conn, case_id)
    if st is r.CaseState.AWAITING_SECOND_REVIEW:
        raise m.PartnerError(
            "CASE_STATE_INVALID",
            "This case is waiting for its second review, so its evidence cannot change now.",
        )
    content = r.content_hash(summary)
    await db.insert_evidence(
        conn,
        evidence_id=_new_id("ev_", 24),
        case_id=case_id,
        tenant_id=str(c["tenant_id"]),
        kind=kind,
        label=label,
        classification=r.STANDARD,
        content_hash=content,
        summary=summary,
        detail={},
    )
    await db.bump(conn, case_id, status=st.value, now=now)
    await _event(conn, c, "evidence_added", actor, now, data={"kind": kind, "content": content})
    return await _detail(conn, await _load(conn, case_id), now)


# ------------------------------------------------------------------------------- claiming, leases and investigating


async def _appeal_independence(conn: AsyncConnection, c: dict[str, Any], reviewer: str) -> None:
    """An appeal is looked at, start to finish, by people who did not take part in the decision under appeal."""
    if c["case_type"] == "APPEAL" and reviewer in await db.reviewers_of(conn, c["parent_case_id"]):
        raise m.PartnerError(
            "REVIEWER_NOT_INDEPENDENT",
            "You took part in the decision under appeal, so you cannot review its appeal.",
        )


async def claim(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st not in r.CLAIMABLE:
        raise m.PartnerError(
            "ASSIGNMENT_CONFLICT",
            "This case is already being worked or is not waiting for a reviewer.",
        )
    await db.expire_leases(conn, case_id, now)
    if await db.live_lease(conn, case_id, now) is not None:
        raise m.PartnerError("ASSIGNMENT_CONFLICT", "Another reviewer has just claimed this case.")
    await _appeal_independence(conn, c, reviewer)
    target = (
        r.CaseState.SPECIALIST_REVIEW
        if st is r.CaseState.ESCALATED
        else (r.CaseState.INVESTIGATING if st is r.CaseState.RETURNED else r.CaseState.CLAIMED)
    )
    await db.insert_lease(
        conn,
        lease_id=str(uuid.uuid4()),
        case_id=case_id,
        tenant_id=str(c["tenant_id"]),
        reviewer=reviewer,
        expires_at=now + r.LEASE_TTL,
    )
    await _move(conn, c, target, now)
    await _event(
        conn,
        c,
        "claimed",
        reviewer,
        now,
        data={"lease_minutes": int(r.LEASE_TTL.total_seconds() // 60), "from": st.value},
    )
    observe.REVIEWS.inc(event="claimed")
    return await _detail(conn, await _load(conn, case_id), now)


async def heartbeat(
    conn: AsyncConnection, case_id: str, reviewer: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    _open(c)
    await _lease_for(conn, c, reviewer, now)
    await db.extend_lease(conn, case_id, now + r.LEASE_TTL)
    return await _detail(conn, c, now)


async def release(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Give a case back to the queue. Allowed to the lease holder only."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _lease_for(conn, c, reviewer, now)
    reason = r.check_note(note)
    await db.release_lease(conn, case_id, now)
    if st in r.WORKING:
        await _move(conn, c, r.CaseState.QUEUED, now)
    await _event(conn, c, "released", reviewer, now, reason=reason)
    return await _detail(conn, await _load(conn, case_id), now)


async def record_findings(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    findings: dict[str, str],
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The reviewer's finding on each required check. The first one moves the case from claimed to investigating."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st not in r.WORKING:
        raise m.PartnerError(
            "CASE_STATE_INVALID", "Findings are recorded while a case is being worked."
        )
    await _lease_for(conn, c, reviewer, now)
    clean = r.check_findings(findings, list(c["required_checks"]))
    merged = {**dict(c["findings"] or {}), **clean}
    target = r.CaseState.INVESTIGATING if st is r.CaseState.CLAIMED else st
    await db.bump(conn, case_id, status=target.value, now=now, findings=merged)
    await _event(
        conn,
        c,
        "findings_recorded",
        reviewer,
        now,
        reason=r.check_note(note, required=False) or None,
        data={"findings": clean},
    )
    return await _detail(conn, await _load(conn, case_id), now)


# ------------------------------------------------------------------------------- evidence reveal


async def reveal(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    evidence_id: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Show one sensitive piece of evidence to the reviewer working the case (or to the independent second reviewer): with a reason, for minutes, counted. The event log records THAT it was shown and why, never what it contained."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    await _held(conn, case_id)
    await db.expire_leases(conn, case_id, now)
    lease = await db.live_lease(conn, case_id, now)
    holder = r.lease_problem(lease, reviewer, now) is None and st in r.WORKING
    second = st is r.CaseState.AWAITING_SECOND_REVIEW and reviewer != (
        str(c["first_reviewer"]) if c["first_reviewer"] else None
    )
    if not (holder or second):
        raise m.PartnerError(
            "ASSIGNMENT_CONFLICT",
            "Only the reviewer working this case, or its independent second reviewer, can reveal evidence.",
        )
    ref = await db.evidence_detail(conn, case_id, evidence_id)
    why = r.check_reveal(ref, reason=reason, reveals_so_far=await db.count_reveals(conn, case_id))
    assert ref is not None
    expires = now + r.REVEAL_TTL
    await db.insert_reveal(
        conn,
        case_id=case_id,
        tenant_id=str(c["tenant_id"]),
        evidence_id=evidence_id,
        reviewer=reviewer,
        reason=why,
        expires_at=expires,
    )
    await _event(
        conn,
        c,
        "evidence_revealed",
        reviewer,
        now,
        reason=why,
        data={"evidence_id": evidence_id, "classification": ref["classification"]},
    )
    observe.REVIEWS.inc(event="revealed")
    return {
        "evidence_id": evidence_id,
        "label": ref["label"],
        "detail": dict(ref["detail"] or {}),
        "visible_until": _iso(expires),
    }


# ------------------------------------------------------------------------------- the first review, the second review and finality


async def _manifest(conn: AsyncConnection, case_id: str) -> str:
    return r.manifest_hash(await db.evidence(conn, case_id))


async def propose(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    outcome_value: str,
    reason_code: str,
    evidence_manifest_hash: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The first review's outcome. It is a proposal: it never finalizes, except where an approved workflow exempts the case from four-eyes (none does today)."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st not in r.WORKING:
        raise m.PartnerError("CASE_STATE_INVALID", "Claim the case before proposing an outcome.")
    await _lease_for(conn, c, reviewer, now)
    outcome = r.check_outcome(outcome_value)
    if outcome is r.Outcome.REQUEST_INFORMATION:
        raise m.PartnerError(
            "VALIDATION_FAILED", "To ask the applicant for something, use request information."
        )
    code = r.check_reason_code(reason_code)
    current = await _manifest(conn, case_id)
    if evidence_manifest_hash != current:
        raise m.PartnerError(
            "EVIDENCE_CHANGED",
            "The evidence this proposal names is not the evidence the case holds now.",
        )
    problems = r.proposal_problems(
        outcome, required=list(c["required_checks"]), findings=dict(c["findings"] or {})
    )
    if problems:
        raise m.PartnerError(
            "MANDATORY_CHECK_MISSING" if outcome is r.Outcome.VERIFIED else "VALIDATION_FAILED",
            "Not allowed yet: " + ", ".join(problems) + ".",
        )
    pid = str(uuid.uuid4())
    await db.insert_proposal(
        conn,
        proposal_id=pid,
        case_id=case_id,
        tenant_id=str(c["tenant_id"]),
        case_version=c["case_version"],
        reviewer=reviewer,
        outcome=outcome.value,
        reason_code=code,
        manifest=current,
        policy_version=c["policy_version"],
    )
    await db.release_lease(conn, case_id, now)
    if c["four_eyes_required"]:
        await _move(conn, c, r.CaseState.AWAITING_SECOND_REVIEW, now, first_reviewer=reviewer)
        await _event(
            conn,
            c,
            "proposed",
            reviewer,
            now,
            reason=code,
            data={"proposal_id": pid, "outcome": outcome.value, "manifest": current},
        )
        observe.REVIEWS.inc(event="proposed")
        return await _detail(conn, await _load(conn, case_id), now)
    await db.bump(
        conn, case_id, status=c["status"], now=now, first_reviewer=reviewer
    )  # the exempt path: the proposer is the only reviewer, on the record
    await _event(
        conn,
        c,
        "proposed",
        reviewer,
        now,
        reason=code,
        data={
            "proposal_id": pid,
            "outcome": outcome.value,
            "manifest": current,
            "four_eyes": "exempt",
        },
    )
    return await _finalize(
        conn,
        await _load(conn, case_id, lock=True),
        await _open_proposal(conn, case_id),
        approver=None,
        now=now,
    )


async def _open_proposal(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    p = await db.open_proposal(conn, case_id)
    if p is None:
        raise m.PartnerError("CASE_STATE_INVALID", "There is no proposal to review.")
    return p


async def second_review(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    decision: str,
    challenge_answers: dict[str, Any],
    reason_code: str | None,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """An independent reviewer approves (and finality runs in the same transaction), returns the case with a reason, or escalates it. The first reviewer can never do this."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st is not r.CaseState.AWAITING_SECOND_REVIEW:
        raise m.PartnerError("CASE_STATE_INVALID", "This case is not waiting for a second review.")
    if decision not in ("approve", "return", "escalate"):
        raise m.PartnerError("VALIDATION_FAILED", "Choose approve, return or escalate.")
    p = await _open_proposal(conn, case_id)
    r.check_independent(str(p["reviewer"]), reviewer)
    await _appeal_independence(conn, c, reviewer)
    if c["first_reviewer"] is not None:
        r.check_independent(str(c["first_reviewer"]), reviewer)
    if decision == "approve" and not all(challenge_answers.get(k) is True for k in CHALLENGES):
        raise m.PartnerError(
            "CHALLENGE_INCOMPLETE",
            "Confirm every point of the checklist before approving: " + ", ".join(CHALLENGES) + ".",
        )
    clean = r.check_note(note, required=decision != "approve")
    await db.insert_second(
        conn,
        proposal_id=str(p["proposal_id"]),
        case_id=case_id,
        tenant_id=str(c["tenant_id"]),
        reviewer=reviewer,
        first_reviewer=str(p["reviewer"]),
        decision=decision,
        answers={k: challenge_answers.get(k) is True for k in CHALLENGES},
        reason_code=reason_code,
        note=clean or None,
    )
    if decision == "return":
        await db.set_proposal_status(conn, str(p["proposal_id"]), "RETURNED")
        await _move(conn, c, r.CaseState.RETURNED, now)
        await _event(
            conn,
            c,
            "returned",
            reviewer,
            now,
            reason=clean,
            data={"proposal_id": str(p["proposal_id"])},
        )
        observe.REVIEWS.inc(event="returned")
        return await _detail(conn, await _load(conn, case_id), now)
    if decision == "escalate":
        await db.set_proposal_status(conn, str(p["proposal_id"]), "RETURNED")
        await _move(conn, c, r.CaseState.ESCALATED, now, queue="SPECIALIST")
        await _event(conn, c, "escalated", reviewer, now, reason=clean, data={"from": st.value})
        observe.REVIEWS.inc(event="escalated")
        return await _detail(conn, await _load(conn, case_id), now)
    return await _finalize(conn, c, p, approver=reviewer, now=now)


async def _finalize(
    conn: AsyncConnection,
    c: dict[str, Any],
    p: dict[str, Any],
    *,
    approver: str | None,
    now: datetime,
) -> dict[str, Any]:
    """The finality transaction. Every gate is checked again here, from the database, never from what a screen showed. On success, the decision (a new version superseding any earlier one), the case event and the
    webhook outbox entry are written together, or none is."""
    case_id = c["case_id"]
    st = r.CaseState(c["status"])
    hold = await db.active_hold(conn, case_id) is not None
    problems = r.finality_problems(
        case_state=st,
        case_version=c["case_version"],
        proposal_case_version=p["case_version"],
        proposal_manifest=p["evidence_manifest_hash"],
        current_manifest=await _manifest(conn, case_id),
        on_hold=hold,
        four_eyes_required=c["four_eyes_required"],
        first_reviewer=str(p["reviewer"]),
        approver=approver,
        proposal_outcome=r.Outcome(p["outcome"]),
        required=list(c["required_checks"]),
        findings=dict(c["findings"] or {}),
    )
    if problems:
        code = (
            "REGULATORY_HOLD"
            if "REGULATORY_HOLD" in problems
            else ("EVIDENCE_CHANGED" if "EVIDENCE_CHANGED" in problems else "FINALITY_REFUSED")
        )
        raise m.PartnerError(
            code, "This decision cannot be finalized: " + ", ".join(problems) + "."
        )
    row = await sdb.get_session_any(conn, c["session_id"], lock=True)
    if row is None:
        raise m.PartnerError("CASE_NOT_FOUND", "The session of this case no longer exists.")
    outcome = s.Outcome(p["outcome"])
    findings = dict(c["findings"] or {})
    checks = [
        {"method": mth, "result": findings.get(mth, "inconclusive")} for mth in c["required_checks"]
    ]
    final_status = (
        s.Status.COMPLETED
        if outcome in (s.Outcome.VERIFIED, s.Outcome.NOT_VERIFIED)
        else s.Status.INCONCLUSIVE
    )
    sandbox = row["environment"] == "SANDBOX"
    if c["case_type"] == "REVIEW":
        s.check_transition(s.Status(row["status"]), final_status)
        made = await ss.record_decision(
            conn,
            row,
            target=final_status,
            outcome=outcome,
            checks=checks,
            reasons=[p["reason_code"]],
            sandbox=sandbox,
            policy_version=p["policy_version"],
            now=now,
        )
    else:
        # an appeal's decision supersedes the earlier one; the session already ended, so its status follows the new result and no earlier decision or case is edited
        made = await ss.record_decision(
            conn,
            row,
            target=final_status,
            outcome=outcome,
            checks=checks,
            reasons=[p["reason_code"]],
            sandbox=sandbox,
            policy_version=p["policy_version"],
            now=now,
        )
    await db.set_proposal_status(conn, str(p["proposal_id"]), "APPROVED")
    await db.bump(
        conn,
        case_id,
        status=r.CaseState.APPROVED_FINAL.value,
        now=now,
        final=(outcome.value, made["decision_id"]),
    )
    await _event(
        conn,
        c,
        "finalized",
        approver or str(p["reviewer"]),
        now,
        reason=p["reason_code"],
        data={
            "decision_id": made["decision_id"],
            "decision_version": made["decision_version"],
            "outcome": outcome.value,
            "supersedes": made["supersedes_decision_id"],
        },
    )
    observe.REVIEWS.inc(event="finalized")
    return await _detail(conn, await _load(conn, case_id), now)


# ------------------------------------------------------------------------------- asking the applicant, escalating, appealing


async def request_information(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    template: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st not in r.WORKING:
        raise m.PartnerError(
            "CASE_STATE_INVALID", "Claim the case before asking the applicant for anything."
        )
    await _lease_for(conn, c, reviewer, now)
    if template not in r.INFO_TEMPLATES:
        raise m.PartnerError(
            "VALIDATION_FAILED",
            "Choose one of the approved requests: " + ", ".join(r.INFO_TEMPLATES) + ".",
        )
    await db.release_lease(conn, case_id, now)
    await _move(conn, c, r.CaseState.AWAITING_APPLICANT, now)
    await _event(conn, c, "information_requested", reviewer, now, data={"template": template})
    return await _detail(conn, await _load(conn, case_id), now)


async def information_received(
    conn: AsyncConnection,
    case_id: str,
    actor: str,
    expected_version: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The applicant has answered: the case goes back to the queue. The company's administrator records it after a resume token has been used."""
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    await _move(conn, c, r.CaseState.QUEUED, now)
    await _event(conn, c, "information_received", actor, now)
    return await _detail(conn, await _load(conn, case_id), now)


async def escalate(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    expected_version: int | None,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    c = await _load(conn, case_id, lock=True)
    st = _open(c)
    r.check_version(c["case_version"], expected_version)
    await _held(conn, case_id)
    if st not in r.WORKING or st is r.CaseState.SPECIALIST_REVIEW:
        raise m.PartnerError(
            "CASE_STATE_INVALID",
            "A case being worked by a general reviewer can be escalated to a specialist.",
        )
    await _lease_for(conn, c, reviewer, now)
    reason = r.check_note(note)
    await db.release_lease(conn, case_id, now)
    await _move(conn, c, r.CaseState.ESCALATED, now, queue="SPECIALIST")
    await _event(conn, c, "escalated", reviewer, now, reason=reason, data={"from": st.value})
    observe.REVIEWS.inc(event="escalated")
    return await _detail(conn, await _load(conn, case_id), now)


async def appeal(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    case_id: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A final case is never reopened. An appeal is a separate case, reviewed by people who did not take part, that may SUPERSEDE the decision with a new version."""
    now = now or datetime.now(UTC)
    parent = await _load(conn, case_id, lock=True)
    if r.CaseState(parent["status"]) is not r.CaseState.APPROVED_FINAL:
        raise m.PartnerError("CASE_NOT_ALLOWED", "Only a final case can be appealed.")
    why = r.check_note(note)
    out = await open_case(
        conn, tenant_id, principal, parent["session_id"], appeal_of=case_id, now=now
    )
    created = await _load(conn, out["case_id"])
    await _event(conn, created, "appeal_reason", principal, now, reason=why)
    await _event(conn, parent, "appealed", principal, now, data={"appeal_case_id": out["case_id"]})
    observe.REVIEWS.inc(event="appealed")
    return await get(conn, out["case_id"], now)


# ------------------------------------------------------------------------------- the audit trail


async def audit(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    await _load(conn, case_id)
    evs = await db.events(conn, case_id)
    return {
        "case_id": case_id,
        "chain_intact": not r.chain_problems(evs),
        "problems": r.chain_problems(evs),
        "events": [
            {
                "seq": e["seq"],
                "type": e["event_type"],
                "actor": e["actor"],
                "reason": e["reason"],
                "data": e["data"],
                "digest": e["digest"],
                "at": _iso(e["created_at"]),
            }
            for e in evs
        ],
    }


# ------------------------------------------------------------------------------- the operator plane: regulatory holds


async def operator_open(conn: AsyncConnection) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    return [
        {
            **case_view(c, now=now),
            "tenant_id": str(c["tenant_id"]),
            "on_hold": await db.active_hold(conn, c["case_id"]) is not None,
        }
        for c in await db.cases_for_operator(conn)
    ]


async def place_hold(
    conn: AsyncConnection,
    case_id: str,
    operator: str,
    reason_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Only the operator plane places a hold. It stops every change and finality; it does not end the case or tell the applicant anything."""
    now = now or datetime.now(UTC)
    code = " ".join((reason_code or "").split())
    if not 3 <= len(code) <= 60:
        raise m.PartnerError("VALIDATION_FAILED", "Give a short reason code for the hold.")
    seen = await _load(
        conn, case_id
    )  # the operator may read any company's case; a change is made in that company's own scope, locked like any other
    async with in_tenant(conn, str(seen["tenant_id"])):
        c = await _load(conn, case_id, lock=True)
        _open(c)
        try:
            async with conn.begin_nested():
                await db.place_hold(
                    conn,
                    case_id=case_id,
                    tenant_id=str(c["tenant_id"]),
                    reason_code=code,
                    operator=operator,
                )
        except Exception as e:
            if "api_review_hold_one_active" in str(e):
                raise m.PartnerError("CASE_OPEN", "A hold is already in force on this case.") from e
            raise
        await db.release_lease(conn, case_id, now)
        await db.bump(conn, case_id, status=c["status"], now=now)
        await _event(conn, c, "hold_placed", f"operator:{operator}", now, reason=code)
    observe.REVIEWS.inc(event="held")
    return {"case_id": case_id, "on_hold": True}


async def release_hold(
    conn: AsyncConnection,
    case_id: str,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A different operator from the one who placed it releases it, with a reason. A company reviewer never can."""
    now = now or datetime.now(UTC)
    why = r.check_note(note)
    seen = await _load(conn, case_id)
    async with in_tenant(conn, str(seen["tenant_id"])):
        c = await _load(conn, case_id, lock=True)
        if not await db.release_hold(conn, case_id, operator, why, now):
            raise m.PartnerError(
                "HOLD_NOT_RELEASABLE",
                "There is no hold to release, or you placed it yourself: a different operator releases it.",
            )
        await db.bump(conn, case_id, status=c["status"], now=now)
        await _event(conn, c, "hold_released", f"operator:{operator}", now, reason=why)
    return {"case_id": case_id, "on_hold": False}


# ------------------------------------------------------------------------------- the AI adviser (advisory only, off by default)


def _snapshot_for_adviser(c: dict[str, Any], evidence: list[dict[str, Any]]) -> dict[str, Any]:
    """All the adviser ever sees: case facts and STANDARD evidence summaries. No sensitive detail, no free text a person typed, no reviewer notes: nothing in it can carry an instruction."""
    return {
        "case_id": c["case_id"],
        "case_version": c["case_version"],
        "required_checks": list(c["required_checks"]),
        "evidence": [
            {"evidence_id": e["evidence_id"], "kind": e["kind"], "summary": e["summary"]}
            for e in evidence
            if e["classification"] == r.STANDARD
        ],
    }


async def ask_adviser(
    conn: AsyncConnection,
    case_id: str,
    reviewer: str,
    adviser: Adviser | None,
    enabled: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Advice for the reviewer, never an action. Off unless the cell enables it AND an adviser is installed. A bad, unsupported or over-confident answer is rejected whole and stored as rejected."""
    now = now or datetime.now(UTC)
    if not enabled or adviser is None:
        raise m.PartnerError(
            "ADVISER_NOT_ENABLED", "The review adviser is not enabled for this deployment."
        )
    c = await _load(conn, case_id, lock=True)
    _open(c)
    await _held(conn, case_id)
    await _lease_for(conn, c, reviewer, now)
    if await db.count_advice(conn, case_id) >= r.ADVICE_LIMIT_PER_CASE:
        raise m.PartnerError("RATE_LIMITED", "This case has reached its limit of adviser requests.")
    evidence = await db.evidence(conn, case_id)
    cites = [e["evidence_id"] for e in evidence if e["classification"] == r.STANDARD]
    stop = r.adviser_must_abstain(evidence=evidence, on_hold=False)
    tenant = str(c["tenant_id"])
    if stop is not None:
        aid = await db.insert_advice(
            conn,
            case_id=case_id,
            tenant_id=tenant,
            case_version=c["case_version"],
            model_version=ADVISER_MODEL,
            requested_by=reviewer,
            status="ABSTAINED",
            body={"abstain": True, "abstain_reason": stop},
            flags=[],
        )
        return {"advice_id": aid, "status": "ABSTAINED", "abstain_reason": stop}
    try:
        answer = await adviser(_snapshot_for_adviser(c, evidence))
    except Exception:  # noqa: BLE001 - an adviser that fails is no advice, and never a reason to block the reviewer
        aid = await db.insert_advice(
            conn,
            case_id=case_id,
            tenant_id=tenant,
            case_version=c["case_version"],
            model_version=ADVISER_MODEL,
            requested_by=reviewer,
            status="FAILED",
            body={},
            flags=["ADVISER_ERROR"],
        )
        return {"advice_id": aid, "status": "FAILED", "abstain_reason": None}
    check = r.validate_advice(answer, citable=cites)
    if not check.ok:
        aid = await db.insert_advice(
            conn,
            case_id=case_id,
            tenant_id=tenant,
            case_version=c["case_version"],
            model_version=ADVISER_MODEL,
            requested_by=reviewer,
            status="REJECTED",
            body={},
            flags=list(check.flags),
        )
        await _event(conn, c, "advice_rejected", reviewer, now, data={"flags": list(check.flags)})
        return {"advice_id": aid, "status": "REJECTED", "flags": list(check.flags)}
    status = "ABSTAINED" if answer.get("abstain") is True else "ADVICE"
    body = {
        k: answer.get(k)
        for k in ("summary", "suggested_outcome", "citations", "abstain", "abstain_reason")
        if k in answer
    }
    aid = await db.insert_advice(
        conn,
        case_id=case_id,
        tenant_id=tenant,
        case_version=c["case_version"],
        model_version=ADVISER_MODEL,
        requested_by=reviewer,
        status=status,
        body=body,  # type: ignore[arg-type]
        flags=[],
    )
    await _event(conn, c, "advice_given", reviewer, now, data={"advice_id": aid, "status": status})
    return {"advice_id": aid, "status": status, "advice": body, "advisory_only": True}


async def advice_feedback(
    conn: AsyncConnection, case_id: str, advice_id: str, feedback: str
) -> dict[str, Any]:
    await _load(conn, case_id)
    if feedback not in ("helpful", "incorrect", "ignored"):
        raise m.PartnerError("VALIDATION_FAILED", "Feedback is helpful, incorrect or ignored.")
    if not await db.set_feedback(conn, advice_id, case_id, feedback):
        raise m.PartnerError("NOT_FOUND", "No such advice.")
    return {"advice_id": advice_id, "feedback": feedback}


async def list_advice(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    await _load(conn, case_id)
    return {
        "items": [
            {
                "advice_id": str(a["advice_id"]),
                "case_version": a["case_version"],
                "status": a["status"],
                "body": a["body"],
                "flags": list(a["flags"]),
                "feedback": a["human_feedback"],
                "at": _iso(a["created_at"]),
            }
            for a in await db.advice_for(conn, case_id)
        ]
    }
