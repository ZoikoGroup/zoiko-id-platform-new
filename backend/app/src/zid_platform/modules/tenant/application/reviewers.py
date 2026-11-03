"""Appointed independent reviewers (ZID-ENG-DEC-012, slice L-10, CR-047).

A solo organization cannot reach independence from its own members, so it may appoint verified outside people (an external professional,
a co-director, a trustee, an authorized delegate) who vote on A1 and A2 requests without being employees, legal representatives, recovery
contacts or paid seats. An appointment is a proposal until it is made real by one of two independent routes: an A1 approval by someone
else (when the organization has one), or a platform verification of the reviewer's identity and authority (a person at Zoiko iD records it
with `python -m zid_platform.entrypoints.verify_reviewer`). The appointer can never be the reviewer, and a reviewer's declared conflicts bar
them from the requests they conflict with. "Zoiko iD Assured Review" is a switch that stays off until the owners record operational
sign-off; this module names it nowhere as a route.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.domain.approvals import ApprovalError
from zid_platform.modules.tenant.domain.lifecycle import RoleStatus
from zid_platform.modules.tenant.infrastructure import approvals as store
from zid_platform.modules.tenant.infrastructure import tenant_store

RELATIONSHIPS = ("EXTERNAL_PROFESSIONAL", "CO_DIRECTOR", "TRUSTEE", "AUTHORIZED_DELEGATE")


async def appoint(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    principal_id: str,
    relationship: str,
    authority_basis: str,
    independence_declaration: str,
    declared_conflict_principals: list[str],
    now: datetime | None = None,
) -> tuple[store.Reviewer, svc.CaseView]:
    """Propose a reviewer and open the A1 request that would activate them. Nothing is active yet."""
    now = now or datetime.now(UTC)
    if relationship not in RELATIONSHIPS:
        raise ApprovalError("VALIDATION_FAILED", "relationship is not one the record names")
    if not authority_basis.strip() or not independence_declaration.strip():
        raise ApprovalError(
            "REVIEWER_EVIDENCE_REQUIRED",
            "documented authority and an independence declaration are required",
        )
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=actor)
        if a.status is RoleStatus.ACTIVE
    }
    if not held & svc._REQUESTING_ROLES:
        raise ApprovalError(
            "APPROVAL_REQUEST_NOT_PERMITTED", "an Owner or Administrator appoints a reviewer"
        )
    human, active = await svc._principal_ok(conn, principal_id, tenant_id)
    if not (human and active):
        raise ApprovalError(
            "REVIEWER_NOT_ELIGIBLE", "a reviewer is an active person with an account here"
        )
    person = await tenant_store.person_of(conn, principal_id)
    if person == await tenant_store.person_of(conn, actor):
        raise ApprovalError(
            "REVIEWER_IS_REQUESTER", "you cannot appoint yourself as your own independent check"
        )
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise ApprovalError("TENANT_NOT_FOUND")
    conflicts = [await tenant_store.person_of(conn, p) for p in declared_conflict_principals]
    days = int(store.policy_settings().get("reviewer_validity_days", 365))
    reviewer_id = await store.insert_reviewer(
        conn, tenant_id=tenant_id, principal_id=principal_id, person_id=person, relationship=relationship,
        authority_basis=authority_basis.strip()[:500], independence_declaration=independence_declaration.strip()[:500],
        declared_conflicts=sorted(set(conflicts)), region=tenant.home_cell_id,
        expires_at=now + timedelta(days=days), proposed_by=actor,
    )  # fmt: skip
    case = await svc.create_case(
        conn, tenant_id, actor, action_id="appoint-independent-reviewer", target_id=f"reviewer:{reviewer_id}",
        payload={"reviewer_id": reviewer_id, "reviewer_principal_id": principal_id},
        reason=f"Appoint an independent reviewer ({relationship}).", now=now,
    )  # fmt: skip
    r = await store.load_reviewer(conn, reviewer_id)
    assert r is not None
    await svc._audit(
        conn, tenant_id, actor, "governance.reviewer.appointed", None, "SUCCESS", None,
        {"reviewer_id": reviewer_id, "relationship": relationship, "conflicts_declared": len(conflicts),
         "activation_case": case.case.case_id}, now, target=reviewer_id,
    )  # fmt: skip
    return r, case


async def verify_by_platform(
    conn: AsyncConnection, reviewer_id: str, *, evidence_ref: str, now: datetime | None = None
) -> store.Reviewer:
    """The second route: Zoiko iD trust operations verified the reviewer's identity and authority (the evidence is a reference, never
    the document) and activate the appointment. Run by an operator, never by the organization."""
    now = now or datetime.now(UTC)
    if not evidence_ref.strip():
        raise ApprovalError("REVIEWER_EVIDENCE_REQUIRED", "name the verification evidence")
    r = await store.load_reviewer(conn, reviewer_id, lock=True)
    if r is None or r.status != "PROPOSED":
        raise ApprovalError("REVIEWER_NOT_PROPOSED", "no proposed reviewer with that id")
    if r.person_id == await tenant_store.person_of(conn, r.proposed_by):
        raise ApprovalError("REVIEWER_IS_REQUESTER")
    await store.set_reviewer_status(
        conn,
        reviewer_id,
        "ACTIVE",
        via="PLATFORM_VERIFICATION",
        ref=evidence_ref.strip()[:128],
        now=now,
    )
    out = await store.load_reviewer(conn, reviewer_id)
    assert out is not None
    await svc._audit(
        conn, out.tenant_id, None, "governance.reviewer.platform_verified", None, "SUCCESS", None,
        {"reviewer_id": reviewer_id, "evidence_ref": evidence_ref.strip()[:128]}, now, target=reviewer_id, actor_type="OPERATOR",
    )  # fmt: skip
    return out


async def revoke(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    reviewer_id: str,
    *,
    now: datetime | None = None,
) -> store.Reviewer:
    """Ending an appointment is defensive and immediate: the reviewer's votes on open cases stop counting at once (the quorum is
    recomputed), and a later appointment is a new one."""
    now = now or datetime.now(UTC)
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=actor)
        if a.status is RoleStatus.ACTIVE
    }
    if not held & svc._REQUESTING_ROLES:
        raise ApprovalError(
            "APPROVAL_REQUEST_NOT_PERMITTED", "an Owner or Administrator ends an appointment"
        )
    r = await store.load_reviewer(conn, reviewer_id, lock=True)
    if r is None or r.status in ("REVOKED", "EXPIRED"):
        raise ApprovalError("REVIEWER_NOT_FOUND")
    await store.set_reviewer_status(conn, reviewer_id, "REVOKED", via=None, ref=None, now=now)
    out = await store.load_reviewer(conn, reviewer_id)
    assert out is not None
    await svc._audit(
        conn, tenant_id, actor, "governance.reviewer.revoked", None, "SUCCESS", None,
        {"reviewer_id": reviewer_id}, now, target=reviewer_id,
    )  # fmt: skip
    return out


def public(r: store.Reviewer) -> dict[str, Any]:
    """What the organization sees: no conflict list contents, only that one was declared."""
    return {
        "reviewer_id": r.reviewer_id,
        "principal_id": r.principal_id,
        "relationship": r.relationship,
        "status": r.status,
        "region": r.region,
        "expires_at": r.expires_at.isoformat(),
        "activated_via": r.activated_via,
        "conflicts_declared": len(r.declared_conflicts),
        "kind": "REVIEWER",  # not an employee, a legal representative, a recovery contact or a paid seat
    }
