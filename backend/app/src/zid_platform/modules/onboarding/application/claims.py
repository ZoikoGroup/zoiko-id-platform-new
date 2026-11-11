"""A company claim as the applicant sees it: four checks, one outcome, one state (ZID-UX-WF-013B screen 3, states and events; slice L-12).

The four checks (the company, the applicant's own identity, the authority to act for the company, the risk screening) come from three
services and change in different places: a reviewer, the worker, the applicant, the passing of time. This module reads them together and
derives the one label. It decides nothing itself, and it keeps the last label it saw so that when something changes the platform says so
exactly once, whoever caused it (`record_outcome`). It also closes a draft nobody submitted (`abandon_drafts`).

Check states: COMPLETED, PENDING, NEEDS_ACTION (the applicant has something to do), HELD (a person is looking), STALE (it was complete and
has lapsed), RESTRICTED (a review suspended it), DECLINED. Outcome, strongest first: DECLINED, RESTRICTED, IN_REVIEW, STALE,
NEEDS_EVIDENCE, VERIFIED (all four complete), otherwise PROVISIONAL; a closed claim reads WITHDRAWN or ABANDONED.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_outbox import emit, next_version

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.organization.ports import (
    KybStatus,
    kyb_close,
    kyb_drafts_before,
    kyb_get_case,
    kyb_open_claims,
    kyb_routing,
    latest_kyb_case_id,
)
from zid_platform.modules.screening.ports import ScreeningStatus, case_for_kyb, close_for_kyb
from zid_platform.modules.verification.ports import person_assurance_current

log = logging.getLogger("zid.claims")

_COMPANY = {
    KybStatus.VERIFIED: "COMPLETED",
    KybStatus.NEEDS_REVIEW: "HELD",
    KybStatus.UNAVAILABLE: "PENDING",
    KybStatus.IN_PROGRESS: "PENDING",
    KybStatus.NOT_STARTED: "PENDING",
    KybStatus.REJECTED: "DECLINED",
    KybStatus.EXPIRED: "STALE",
}
_SCREENING = {
    ScreeningStatus.VERIFIED: "COMPLETED",
    ScreeningStatus.REVIEW_REQUIRED: "HELD",
    ScreeningStatus.UNAVAILABLE: "PENDING",
    ScreeningStatus.GATHERING: "NEEDS_ACTION",
    ScreeningStatus.SCREENING: "PENDING",
    ScreeningStatus.NOT_STARTED: "PENDING",
    ScreeningStatus.REJECTED: "DECLINED",
    ScreeningStatus.EXPIRED: "STALE",
    ScreeningStatus.SUSPENDED: "RESTRICTED",
}
# the check that says each of the design's events: what a check is, when it changes
_CHECK_EVENT = {
    "company": "organization.claim.source_verified",
    "authority": "organization.claim.authority_evaluated",
    "risk": "organization.claim.risk_assessed",
}
_DECISIONS = frozenset({"VERIFIED", "DECLINED", "RESTRICTED"})
_CLOSED = frozenset({"WITHDRAWN", "ABANDONED"})


_DEV_SHORTCUT = False


def configure_dev_shortcut(enabled: bool, environment: str) -> None:
    """LOCAL/TEST ONLY. With it on, a claim reads as VERIFIED once the claimant's own identity check is complete (see `status`). Refused in any other
    environment, so a deployed cell can never run with it."""
    global _DEV_SHORTCUT
    if enabled and environment not in ("local", "test"):
        raise RuntimeError("the development claim shortcut is for local/test only")
    _DEV_SHORTCUT = enabled


def dev_shortcut_enabled() -> bool:
    return _DEV_SHORTCUT


def final_outcome(checks: dict[str, str]) -> str:
    """The one label. VERIFIED only when every one of the four checks is complete: a registry match, a declaration or a payment completes
    none of them. Anything that is not complete never reads as VERIFIED, and the strongest problem is the one shown."""
    states = set(checks.values())
    for state, label in (
        ("DECLINED", "DECLINED"),
        ("RESTRICTED", "RESTRICTED"),
        ("HELD", "IN_REVIEW"),
        ("STALE", "STALE"),
        ("NEEDS_ACTION", "NEEDS_EVIDENCE"),
    ):
        if state in states:
            return label
    if states == {"COMPLETED"}:
        return "VERIFIED"
    return "PROVISIONAL"


async def status(
    conn: AsyncConnection, tenant_id: str, case_id: str, now: datetime
) -> dict[str, Any]:
    kyb = await kyb_get_case(conn, tenant_id, case_id, now=now)
    screening = await case_for_kyb(conn, tenant_id, case_id, now=now)
    checks = {
        "company": _COMPANY[kyb.status],
        # the applicant's own identity check, read from the verification service: a valid, unexpired assertion from an identity check, nothing else
        "person_assurance": "COMPLETED"
        if await person_assurance_current(conn, kyb.requested_by, now)
        else "PENDING",
        "authority": screening[0].authority_state if screening else "PENDING",
        "risk": _SCREENING[screening[0].status] if screening else "PENDING",
    }
    closed = kyb.closed_reason
    # LOCAL/TEST ONLY: the company, authority and risk checks are not run; the claimant's identity check is the only one that counts. The result says so
    # (`development_shortcut`) and is never announced as if those checks had been done (see `record_outcome`).
    shortcut = _DEV_SHORTCUT and not closed and checks["person_assurance"] == "COMPLETED"
    if shortcut:
        checks = {**checks, "company": "COMPLETED", "authority": "COMPLETED", "risk": "COMPLETED"}
    return {
        "development_shortcut": shortcut,
        "claim_id": case_id,
        "jurisdiction": kyb.jurisdiction,
        "registration_number": kyb.registration_number,
        "state": closed or ("SUBMITTED" if kyb.submitted_at else "DRAFT"),
        "outcome": closed or final_outcome(checks),
        "checks": checks,
        "relationship": screening[0].relationship if screening else None,
        "submitted_at": kyb.submitted_at.isoformat() if kyb.submitted_at else None,
        "closed_at": kyb.closed_at.isoformat() if kyb.closed_at else None,
        "company": {
            "status": kyb.status.value,
            "review_reason": kyb.review_reason,
            "reasons": list(kyb.reasons),
            "expires_at": kyb.expiry_at.isoformat() if kyb.expiry_at else None,
        },
        "screening": (
            {
                "status": screening[0].status.value,
                "next_action": screening[0].next_action,
                "tasks": list(screening[0].tasks),
                "expires_at": screening[0].expiry_at.isoformat()
                if screening[0].expiry_at
                else None,
            }
            if screening
            else None
        ),
    }


async def _announce(
    conn: AsyncConnection, tenant_id: str, name: str, claim_id: str, data: dict[str, Any]
) -> None:
    """Ids and coarse states only: never a name, a document or a score."""
    await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type="org_claim",
        aggregate_id=claim_id,
        aggregate_version=await next_version(conn, "org_claim", claim_id),
        data={"claim_id": claim_id, **data},
    )


async def record_outcome(
    conn: AsyncConnection, tenant_id: str, claim_id: str, seen: dict[str, Any], now: datetime
) -> list[str]:
    """Remember the checks and the outcome that were just derived and announce what changed since the last time, once. Returns the
    events announced. Safe to call from anywhere a claim can change, and again and again: a call that sees nothing new announces nothing."""
    if seen.get(
        "development_shortcut"
    ):  # LOCAL/TEST ONLY shortcut: checks that never ran are never announced as done
        return []
    checks = {k: str(v) for k, v in seen["checks"].items()}
    outcome = str(seen["outcome"])
    tenant, claim = uuid.UUID(tenant_id), uuid.UUID(claim_id)
    await conn.execute(
        text(
            "INSERT INTO platform.org_claim_outcome (tenant_id, claim_id, outcome, checks, since) "
            "VALUES (:t, :c, 'NEW', '{}'::jsonb, :now) ON CONFLICT DO NOTHING"
        ),
        {"t": tenant, "c": claim, "now": now},
    )
    row = (
        await conn.execute(
            text(
                "SELECT outcome, checks FROM platform.org_claim_outcome WHERE tenant_id = :t AND claim_id = :c FOR UPDATE"
            ),
            {"t": tenant, "c": claim},
        )
    ).one()
    before_checks: dict[str, str] = dict(row.checks or {})
    before_outcome = str(row.outcome)
    announced: list[str] = []
    for check, event in _CHECK_EVENT.items():
        was = before_checks.get(check, "PENDING")
        if checks[check] != was:
            await _announce(
                conn,
                tenant_id,
                event,
                claim_id,
                {"check": check, "state": checks[check], "previous": was},
            )
            announced.append(event)
    changed = outcome != before_outcome
    if changed and outcome in _DECISIONS:
        await _announce(
            conn,
            tenant_id,
            "organization.claim.decision_issued",
            claim_id,
            {"outcome": outcome, "previous": before_outcome},
        )
        announced.append("organization.claim.decision_issued")
    if changed and outcome in _CLOSED:
        await _announce(conn, tenant_id, "organization.claim.closed", claim_id, {"reason": outcome})
        announced.append("organization.claim.closed")
    if outcome != before_outcome or checks != before_checks:
        await conn.execute(
            text(
                "UPDATE platform.org_claim_outcome SET outcome = :o, checks = CAST(:k AS jsonb), "
                "since = CASE WHEN outcome <> :o THEN :now ELSE since END WHERE tenant_id = :t AND claim_id = :c"
            ),
            {"t": tenant, "c": claim, "o": outcome, "k": json.dumps(checks), "now": now},
        )
    return announced


async def organization_state(conn: AsyncConnection, tenant_id: str, now: datetime) -> str:
    """The organization's readiness state (DRAFT ... ACTIVE, ACTIVE_LIMITED, RESTRICTED), from the same facts the setup screen shows, read for
    the person who made the claim: the state belongs to the organization, not to whoever happens to be asking. An organization that has
    started no claim is DRAFT. This is what the readiness policy is asked about."""
    from zid_platform.modules.onboarding.application.readiness import gather

    case_id = await latest_kyb_case_id(conn, tenant_id)
    if case_id is None:
        return "DRAFT"
    applicant = (await kyb_get_case(conn, tenant_id, case_id, now=now)).requested_by
    seen = await status(conn, tenant_id, case_id, now)
    return (await gather(conn, tenant_id, applicant, seen, now=now)).projection.state.value


async def close(
    conn: AsyncConnection,
    tenant_id: str,
    claim_id: str,
    reason: str,
    *,
    actor: str | None,
    now: datetime,
) -> bool:
    """Close a claim (WITHDRAWN or ABANDONED), stop watching the people screened for it, say so in the audit record and announce it.
    Returns whether this call closed it. A verified claim is not closed here: the caller refuses that first."""
    _, done = await kyb_close(conn, tenant_id, claim_id, reason, now=now)
    if not done:
        return False
    await close_for_kyb(conn, tenant_id, claim_id, now)
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.claim.closed",
            actor_type="HUMAN" if actor else "SERVICE",
            action="closed",
            target_type="kyb_case",
            target_ref=claim_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service="onboarding",
            source_event_id=str(uuid.uuid4()),
            details={"reason": reason},
        ),
    )
    await record_outcome(
        conn, tenant_id, claim_id, await status(conn, tenant_id, claim_id, now), now
    )
    return True


async def announce_submitted(
    conn: AsyncConnection, tenant_id: str, claim_id: str, *, actor: str, now: datetime
) -> None:
    """The applicant said the claim is complete: one event and one audit record (ids only)."""
    await _announce(conn, tenant_id, "organization.claim.submitted", claim_id, {})
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="organization.claim.submitted",
            actor_type="HUMAN",
            action="submitted",
            target_type="kyb_case",
            target_ref=claim_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=None,
            source_service="onboarding",
            source_event_id=str(uuid.uuid4()),
            details={},
        ),
    )


async def abandon_drafts(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """A claim nobody submitted within the policy's draft period is closed as abandoned, so a half-made claim does not sit there watching
    people for ever. tenant id -> claims closed. One claim failing never stops another."""
    cutoff = now - kyb_routing().policy.draft_period
    out: dict[str, int] = {}
    for tenant, claim in await kyb_drafts_before(conn, cutoff):
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                if await close(conn, tenant, claim, "ABANDONED", actor=None, now=now):
                    out[tenant] = out.get(tenant, 0) + 1
        except Exception:
            log.exception("could not close the abandoned draft %s", claim)
    return out


async def sync_outcomes(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """Follow every open claim: whatever changed since the last pass (a reviewer's ruling, the worker's refresh, a lapse) is announced
    once. tenant id -> events announced."""
    out: dict[str, int] = {}
    for tenant, claim in await kyb_open_claims(conn):
        try:
            async with conn.begin_nested(), in_tenant(conn, tenant):
                events = await record_outcome(
                    conn, tenant, claim, await status(conn, tenant, claim, now), now
                )
            if events:
                out[tenant] = out.get(tenant, 0) + len(events)
        except Exception:
            log.exception("could not follow the claim %s", claim)
    return out
