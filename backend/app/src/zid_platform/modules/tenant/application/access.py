"""Evaluate a role-matrix decision for a real caller and keep the evidence (ZID-ENG-DEC-007, slice L-7).

The server builds the facts: the roles come from the tenant's own role records, the membership from their status, the trust state
from the tenant's lifecycle. A caller that cannot establish a fact says so with `None`, and that is a HOLD, never an ALLOW. Every
decision that changes something, and every DENY or HOLD, writes a redacted E3 audit record naming the policy hash and the role
that allowed it: ids, codes and the matrix hash only, never content.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.authority.ports import GrantStatus, load_grants_for_grantee
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.tenant.domain.lifecycle import Role, RoleStatus, serves_runtime
from zid_platform.modules.tenant.domain.role_matrix import (
    BOOTSTRAP_ACTIONS,
    Decision,
    Facts,
    Mode,
    Outcome,
    decide,
)
from zid_platform.modules.tenant.infrastructure import tenant_store as store
from zid_platform.modules.tenant.infrastructure.role_matrix import role_matrix

_LIVE = (RoleStatus.ACTIVE,)


async def evaluate(
    conn: AsyncConnection,
    tenant_id: str,
    actor_id: str,
    action_id: str,
    mode: Mode,
    *,
    object_id: str,
    session_ok: bool | None,
    resource_in_scope: bool | None = True,
    region_ok: bool | None = True,
    delegation_ok: bool | None = True,
    entitlement_ok: bool | None = True,
    workflow_ok: bool | None = True,
    own_account: bool = False,
    initiator_id: str | None = None,
    acting_for: str | None = None,
    deny_with: str | None = None,
    now: datetime | None = None,
) -> Decision:
    """Decide and record. The defaults for region, delegation, entitlement and workflow say "this action does not use one"; an
    action that does must pass what it established, `None` when it could not."""
    matrix = role_matrix()
    now = now or datetime.now(UTC)
    held = await store.roles(conn, principal_id=actor_id)
    assignments = [a for a in held if a.status in _LIVE]
    if not assignments and action_id in BOOTSTRAP_ACTIONS:
        assignments = [  # the pending bootstrap Owner, for company verification only
            a for a in held if a.role is Role.OWNER and a.status is RoleStatus.PENDING_VERIFICATION
        ]
    if acting_for and acting_for != actor_id:
        delegation_ok = await delegation_valid(conn, actor_id, acting_for, now)
    tenant = await store.load_tenant(conn, tenant_id)
    facts = Facts(
        authenticated=bool(actor_id),
        membership_active=bool(assignments),
        roles=tuple(sorted({a.role.value for a in assignments})),
        resource_in_scope=resource_in_scope,
        policy_current=True,  # the matrix loaded and validated; a stale grant is removed by the role record itself
        region_ok=region_ok,
        trust_ok=None if tenant is None else serves_runtime(tenant.status),
        delegation_ok=delegation_ok,
        session_ok=session_ok,
        entitlement_ok=entitlement_ok,
        workflow_ok=workflow_ok,
        own_account=own_account,
        actor_person=await store.person_of(conn, acting_for or actor_id),
        initiator_person=await store.person_of(conn, initiator_id) if initiator_id else None,
    )
    decision = decide(matrix, action_id, mode, facts)
    if (
        deny_with and decision.allowed
    ):  # a rule about the object (a role that is never assigned directly) refuses what the role allowed
        decision = replace(
            decision, outcome=Outcome.DENY, reasons=(deny_with,), granted_by_role=None
        )
    if mode not in (Mode.READ, Mode.OWN) or decision.outcome is not Outcome.ALLOW:
        await record(conn, tenant_id, actor_id, decision, object_id=object_id, now=now)
    return decision


async def record(
    conn: AsyncConnection,
    tenant_id: str,
    actor_id: str,
    decision: Decision,
    *,
    object_id: str,
    now: datetime | None = None,
) -> str:
    """The AuthorizationDecision evidence: one append-only audit record with the decision id as its idempotency key."""
    decision_id = str(uuid7())
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="tenant.access.decision",
            actor_type="HUMAN",
            action="decision",
            target_type="authorization",
            target_ref=object_id[:128],
            outcome="SUCCESS" if decision.allowed else "DENIED",
            evidence_class=EvidenceClass.E3,
            occurred_at=now or datetime.now(UTC),
            actor_principal_id=actor_id,
            reason_code=decision.reasons[0] if decision.reasons else None,
            source_service="tenant",
            source_event_id=decision_id,
            details={
                "decision_id": decision_id,
                "decision": decision.outcome.value,
                "action": decision.action_id,
                "mode": decision.mode,
                "policy_hash": decision.policy_hash,
                "granted_by_role": decision.granted_by_role,
                "reasons": ",".join(decision.reasons),
            },
        ),
    )
    return decision_id


async def delegation_valid(
    conn: AsyncConnection, actor_id: str, acting_for: str, now: datetime
) -> bool:
    """Is the actor acting for `acting_for` under a live delegation? A grant from that person to the actor, ACTIVE and inside its
    window at this moment. A revoked, suspended or expired grant is not one, and the grants are read afresh on every decision, so
    ending a delegation is effective at once."""
    for g in await load_grants_for_grantee(conn, actor_id):
        w = g.authority.window
        if (
            g.grantor_principal_id == acting_for
            and g.status is GrantStatus.ACTIVE
            and (w.valid_from is None or w.valid_from <= now)
            and (w.valid_to is None or now < w.valid_to)
        ):
            return True
    return False


async def link_person(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    person_id: str,
    *,
    source: str,
    evidence_ref: str | None,
    linked_by: str | None,
    now: datetime | None = None,
) -> bool:
    """Record that two accounts are one person, with audit evidence. The writer is a platform step (the entrypoint
    `link_person`), not a tenant route: who else may write a link is an owner ruling."""
    created = await store.link_person(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        person_id=person_id,
        source=source,
        evidence_ref=evidence_ref,
        linked_by=linked_by,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type="tenant.person.linked",
            actor_type="OPERATOR" if linked_by is None else "HUMAN",
            action="linked",
            target_type="principal",
            target_ref=principal_id,
            outcome="SUCCESS" if created else "FAILURE",
            evidence_class=EvidenceClass.E3,
            occurred_at=now or datetime.now(UTC),
            actor_principal_id=linked_by,
            source_service="tenant",
            details={"source": source, "linked": created, "evidence_ref": evidence_ref},
        ),
    )
    return created
