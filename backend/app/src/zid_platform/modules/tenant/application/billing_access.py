"""Who may do what in billing, decided afresh on every request (ZID-COM-CL-002; slice L-14).

The roles are read from the tenant's own records and the grants from the grant table at the moment of the call, so revoking a grant or
a role takes effect on the next request: there is no cache to go stale. Every refusal, every grant and every revoke writes a redacted
audit record (ids, codes and the permission key; never content).
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_db import in_tenant

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.tenant.domain.billing_access import (
    BUNDLES,
    BillingAccessError,
    Decision,
    check_grant,
    check_revoke,
    decide,
    effective_permissions,
)
from zid_platform.modules.tenant.domain.lifecycle import RoleStatus
from zid_platform.modules.tenant.infrastructure import billing_grants as grants_store
from zid_platform.modules.tenant.infrastructure import tenant_store as store


async def _roles(conn: AsyncConnection, principal_id: str) -> tuple[str, ...]:
    held = await store.roles(conn, principal_id=principal_id)
    return tuple(sorted({a.role.value for a in held if a.status is RoleStatus.ACTIVE}))


async def is_owner(conn: AsyncConnection, tenant_id: str, principal_id: str) -> bool:
    """Is this person an active Owner of this organization right now. Handed to the commercial module, which approves customer commitments."""
    async with in_tenant(conn, tenant_id):
        held = await store.roles(conn, principal_id=principal_id)
    return any(a.role.value == "OWNER" and a.status is RoleStatus.ACTIVE for a in held)


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    actor_id: str,
    *,
    event: str,
    action: str,
    outcome: str,
    target: str,
    reason: str | None,
    details: dict[str, str],
    now: datetime,
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=action,
            target_type="billing_access",
            target_ref=target[:128],
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor_id,
            reason_code=reason,
            source_service="tenant",
            source_event_id=str(uuid7()),
            details=details,
        ),
    )


async def decide_for(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    permission: str,
    *,
    now: datetime | None = None,
    record_refusal: bool = True,
) -> Decision:
    now = now or datetime.now(UTC)
    roles = await _roles(conn, principal_id)
    grants = await grants_store.grants_of(conn, principal_id)
    decision = decide(roles, grants, permission, now)
    if not decision.allowed and record_refusal:
        await _audit(
            conn,
            tenant_id,
            principal_id,
            event="tenant.billing.access.decision",
            action="decision",
            outcome="DENIED",
            target=permission,
            reason=decision.via,
            details={"permission": permission, "reasons": decision.via},
            now=now,
        )
    return decision


async def effective(
    conn: AsyncConnection, principal_id: str, *, now: datetime | None = None
) -> list[str]:
    now = now or datetime.now(UTC)
    roles = await _roles(conn, principal_id)
    grants = await grants_store.grants_of(conn, principal_id)
    return sorted(effective_permissions(roles, grants, now))


async def give(
    conn: AsyncConnection,
    tenant_id: str,
    actor_id: str,
    *,
    grantee_principal_id: str,
    bundle: str,
    reason: str,
    expires_at: datetime | None,
) -> str:
    now = datetime.now(UTC)
    actor_roles = await _roles(conn, actor_id)
    members = {a.principal_id for a in await store.roles(conn) if a.status is RoleStatus.ACTIVE}
    try:
        chosen = check_grant(
            actor_roles=actor_roles,
            actor_person=await store.person_of(conn, actor_id),
            grantee_person=await store.person_of(conn, grantee_principal_id),
            grantee_is_member=grantee_principal_id in members,
            bundle=bundle,
            now=now,
            expires_at=expires_at,
        )
    except BillingAccessError as e:
        await _audit(
            conn,
            tenant_id,
            actor_id,
            event="tenant.billing.access.granted",
            action="grant",
            outcome="DENIED",
            target=grantee_principal_id,
            reason=e.code,
            details={"bundle": bundle},
            now=now,
        )
        raise
    # A repeat is not a second grant (a double click, a retry, two administrators at once): the same bundle for the same person that is still in force is
    # the same grant, and one that differs only in its end date is a conflict the Owner resolves by taking the first away.
    for held in await grants_store.grants_of(conn, grantee_principal_id):
        if held.bundle is chosen and held.live(now):
            if held.expires_at == expires_at:
                return held.grant_id
            raise BillingAccessError(
                "BILLING_GRANT_EXISTS",
                "That person already holds this billing access with a different end date. Take it away first to change it.",
            )
    grant_id = await grants_store.insert_grant(
        conn,
        tenant_id=tenant_id,
        grantee_principal_id=grantee_principal_id,
        grantee_person_id=await store.person_of(conn, grantee_principal_id),
        bundle=chosen,
        permissions=BUNDLES[chosen],
        reason=reason,
        granted_by=actor_id,
        expires_at=expires_at,
    )
    await _audit(
        conn,
        tenant_id,
        actor_id,
        event="tenant.billing.access.granted",
        action="grant",
        outcome="SUCCESS",
        target=grant_id,
        reason=None,
        details={"bundle": chosen.value, "grantee": grantee_principal_id},
        now=now,
    )
    return grant_id


async def take_away(conn: AsyncConnection, tenant_id: str, actor_id: str, grant_id: str) -> None:
    now = datetime.now(UTC)
    try:
        check_revoke(actor_roles=await _roles(conn, actor_id))
    except BillingAccessError as e:
        await _audit(
            conn,
            tenant_id,
            actor_id,
            event="tenant.billing.access.revoked",
            action="revoke",
            outcome="DENIED",
            target=grant_id,
            reason=e.code,
            details={},
            now=now,
        )
        raise
    grantee = await grants_store.revoke_grant(conn, grant_id, revoked_by=actor_id, now=now)
    if grantee is None:
        raise BillingAccessError("BILLING_GRANT_NOT_FOUND", "No live billing grant by that id.")
    await _audit(
        conn,
        tenant_id,
        actor_id,
        event="tenant.billing.access.revoked",
        action="revoke",
        outcome="SUCCESS",
        target=grant_id,
        reason=None,
        details={"grantee": grantee},
        now=now,
    )
