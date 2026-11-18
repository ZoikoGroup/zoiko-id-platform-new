"""Appeals, rechecks and the evidence export for a company's verification (ZID-DTV-WF-02 K06, K10; CR-109). Orchestration over the rules in `domain/appeal.py` and `domain/kyb_grade.py`.

- Appeals: opened by the applicant's side, decided by someone who made no part of the decision and is no party; an ALLOWED appeal reassesses the company and adds a new assessment.
- Rechecks: `recheck_pass` runs under the worker role (which sees every tenant) and reassesses each company whose next check is due; `apply_event` reassesses at once when something that invalidates a
  grade happens (a registry status change, a dissolution, an ownership change, a revoked mandate, a sanctions alert). A failing company never stops another.
- Export: a signed, watermarked summary of the grade history for one purpose, with no names, documents or personal data in it. Whoever receives it can check the signature; the watermark says who asked and why.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert
from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import kyb_company as company
from zid_platform.modules.organization.domain import appeal as ap
from zid_platform.modules.organization.domain import kyb_grade as g
from zid_platform.modules.organization.infrastructure import kyb_store

log = logging.getLogger("zid.kyb")
Signer = Callable[[dict[str, Any]], str]


# ------------------------------------------------------------------------------------------------------------------ appeals


async def _decision(conn: AsyncConnection, tenant_id: str, version: int) -> tuple[ap.Decision, str]:
    """The assessment an appeal is against, with who made it, and the applicant (whoever requested the company's verification)."""
    rows = await assessment.history(conn, tenant_id, limit=500)
    stored = next((r for r in rows if r.version == version), None)
    if stored is None:
        raise ap.AppealError("DECISION_NOT_FOUND", str(version))
    reviewers = set(stored.facts.documents.reviewers) if stored.facts.documents else set()
    case_id = await kyb_store.latest_case_id(conn, tenant_id)
    applicant = ""
    if case_id:
        case = await kyb_store.load_case(conn, tenant_id, case_id)
        applicant = str(case.requested_by or "")
        for e in await kyb_store.evidence_history(conn, tenant_id, case_id):
            if e.decided_by:
                reviewers.add(e.decided_by)
    return ap.Decision(
        stored.version, stored.grade.value, stored.assessed_at, tuple(sorted(reviewers))
    ), applicant


async def open_appeal(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    opened_by: str,
    version: int,
    reason: str,
    now: datetime | None = None,
    window=ap.DEFAULT_WINDOW,
) -> str:
    now = now or datetime.now(UTC)
    decision, _applicant = await _decision(conn, tenant_id, version)
    states = [
        ap.State(r.state)
        for r in (
            await conn.execute(
                text(
                    "SELECT state FROM org.kyb_appeal WHERE tenant_id = :t AND decision_version = :v"
                ),
                {"t": uuid.UUID(tenant_id), "v": version},
            )
        ).all()
    ]
    ap.may_open(decision=decision, existing_states=states, now=now, window=window, reason=reason)
    appeal_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO org.kyb_appeal (appeal_id, tenant_id, decision_version, opened_by, reason, opened_at) VALUES (:i, :t, :v, :o, :r, :n)"
        ),
        {
            "i": appeal_id,
            "t": uuid.UUID(tenant_id),
            "v": version,
            "o": uuid.UUID(opened_by),
            "r": reason.strip(),
            "n": now,
        },
    )
    return str(appeal_id)


async def decide_appeal(
    conn: AsyncConnection,
    tenant_id: str,
    appeal_id: str,
    *,
    decider: str,
    outcome: ap.State,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    row = (
        await conn.execute(
            text(
                "SELECT decision_version, opened_by, state FROM org.kyb_appeal WHERE tenant_id = :t AND appeal_id = :a FOR UPDATE"
            ),
            {"t": uuid.UUID(tenant_id), "a": uuid.UUID(appeal_id)},
        )
    ).first()
    if row is None:
        raise ap.AppealError("APPEAL_NOT_FOUND", appeal_id)
    if row.state != "OPEN":
        raise ap.AppealError("APPEAL_ALREADY_DECIDED", appeal_id)
    decision, applicant = await _decision(conn, tenant_id, int(row.decision_version))
    ap.may_decide(
        decision=decision,
        opened_by=str(row.opened_by),
        applicant=applicant,
        decider=decider,
        outcome=outcome,
        reason=reason,
    )
    await conn.execute(
        text(
            "UPDATE org.kyb_appeal SET state = :s, decided_by = :d, decided_at = :n, decision_reason = :r WHERE tenant_id = :t AND appeal_id = :a"
        ),
        {
            "s": outcome.value,
            "d": uuid.UUID(decider),
            "n": now,
            "r": reason.strip(),
            "t": uuid.UUID(tenant_id),
            "a": uuid.UUID(appeal_id),
        },
    )
    out: dict[str, Any] = {
        "appeal_id": appeal_id,
        "state": outcome.value,
        "message": ap.applicant_message(outcome),
    }
    # The applicant is told in their own alert inbox, once (the correlation key makes a repeat the same alert). The words are the safe message: no threshold, rule or detector.
    await raise_alert(
        conn,
        tenant_id,
        AlertDraft(
            severity=Severity.MEDIUM if outcome is ap.State.ALLOWED else Severity.INFO,
            kind="kyb.appeal.decided",
            title="Your company appeal was decided",
            body=ap.applicant_message(outcome),
            object_type="kyb_appeal",
            object_ref=appeal_id,
            href="/admin/company-verification",
            recommended_action="Open the company verification page to see where the company stands.",
            is_task=False,
            mandatory=False,
            correlation_key=f"kyb.appeal.decided:{appeal_id}",
        ),
        now=now,
    )
    if outcome is ap.State.ALLOWED:
        # the earlier decision is set aside by adding a new assessment from what is recorded now; nothing earlier is edited
        stored = await company.reassess(
            conn,
            tenant_id,
            trigger=f"APPEAL_ALLOWED:{row.decision_version}",
            actor=decider,
            now=now,
        )
        out["grade"] = stored.grade.value
    return out


async def appeals(conn: AsyncConnection, tenant_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT appeal_id, decision_version, state, opened_at, decided_at FROM org.kyb_appeal WHERE tenant_id = :t ORDER BY opened_at DESC LIMIT 100"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).all()
    return [
        {"appeal_id": str(r.appeal_id), "decision_version": r.decision_version, "state": r.state, "message": ap.applicant_message(ap.State(r.state)), "opened_at": r.opened_at.isoformat(),
         "decided_at": r.decided_at.isoformat() if r.decided_at else None}
        for r in rows
    ]  # fmt: skip


# ------------------------------------------------------------------------------------------------------------------ rechecks


async def due_tenants(conn: AsyncConnection, now: datetime) -> list[str]:
    """Companies whose latest assessment names a recheck date that has passed. Reads every tenant: for the worker role only."""
    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id FROM (SELECT DISTINCT ON (tenant_id) tenant_id, recheck_due FROM org.kyb_assessment ORDER BY tenant_id, version DESC) latest "
                "WHERE recheck_due IS NOT NULL AND recheck_due <= :n"
            ),
            {"n": now},
        )
    ).all()
    return [str(r.tenant_id) for r in rows]


async def recheck_pass(conn: AsyncConnection, now: datetime | None = None) -> dict[str, str]:
    """Reassess each company that is due. One failing company never stops the others; the result says what happened to each (grade, or the error code)."""
    now = now or datetime.now(UTC)
    done: dict[str, str] = {}
    for tenant in await due_tenants(conn, now):
        try:
            async with in_tenant(conn, tenant):
                stored = await company.reassess(
                    conn, tenant, trigger="RECHECK_DUE", actor=None, now=now
                )
            done[tenant] = stored.grade.value
        except Exception as e:  # noqa: BLE001 - a company that cannot be reassessed today is tried again next pass; the others go on
            log.warning("kyb recheck failed for a company: %s", type(e).__name__)
            done[tenant] = f"ERROR:{type(e).__name__}"
    return done


async def apply_event(
    conn: AsyncConnection,
    tenant_id: str,
    event: str,
    *,
    actor: str | None = None,
    now: datetime | None = None,
) -> assessment.Stored | None:
    """Something happened that may change what is proven. Events that cannot invalidate a grade do nothing; the others reassess at once, whatever the recheck date says."""
    if not g.event_invalidates(event):
        return None
    return await company.reassess(
        conn, tenant_id, trigger=f"EVENT:{event}", actor=actor, now=now or datetime.now(UTC)
    )


# ------------------------------------------------------------------------------------------------------------------ export


async def evidence_export(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    requester: str,
    purpose: str,
    sign: Signer,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A signed summary of the company's grade history for one stated purpose. Versions, grades, claims, reasons, triggers and times only: no person, no document, no source detail."""
    now = now or datetime.now(UTC)
    if not 3 <= len(purpose.strip()) <= 200:
        raise ap.AppealError("PURPOSE_REQUIRED", "say what the export is for")
    rows = await assessment.history(conn, tenant_id, limit=500)
    current = rows[0] if rows else None
    live = await assessment.allowed(conn, tenant_id, now=now)
    body: dict[str, Any] = {
        "export_id": str(uuid.uuid4()),
        "watermark": {"requested_by": requester, "purpose": purpose.strip(), "generated_at": now.isoformat(), "scope": "company verification grade; no personal data"},
        "grade_now": {"grade": live.grade.value, "label": live.label, "reasons": list(live.reasons)},
        "recorded_grade": None if current is None else {"version": current.version, "grade": current.grade.value, "label": current.label, "assessed_at": current.assessed_at.isoformat()},
        "history": [
            {"version": r.version, "grade": r.grade.value, "claims": list(r.claims), "reasons": list(r.reasons), "trigger": r.trigger.split(":")[0], "assessed_at": r.assessed_at.isoformat()}
            for r in reversed(rows)
        ],
    }  # fmt: skip
    return {"summary": body, "jws": sign(body)}
