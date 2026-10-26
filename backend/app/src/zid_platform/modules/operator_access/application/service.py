"""Operator access use cases: JIT request -> approval (+ lockbox) -> activation -> expiry/revoke,
break-glass with mandatory post-review, and the ephemeral capability check (DEC-F4; SEC-001 §11-13).

Evidence: every state change emits an outbox event in the same transaction, naming its evidence
class (E2/E3) and carrying ids and SHA-256 commitments only, never case text or customer content,
and writes the matching protected AuditRecord (EVIDENCE-001 §16) in that same transaction:
operator-plane changes go to the platform-security partition (DEC-F2 §2), customer-side lockbox
and policy changes to the customer tenant's own partition, so the customer can verify them.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.operator_access.domain.model import (
    Capability,
    GrantState,
    LockboxState,
    OperatorAccessError,
    OperatorRole,
    ReviewOutcome,
    bounded_duration,
    capability,
    check_approver,
    check_jurisdiction,
    check_reviewer,
    check_scope,
    check_sod,
    commitment,
    max_duration,
    resource_matches,
    transition,
)
from zid_platform.modules.operator_access.infrastructure import store
from zid_platform.modules.operator_access.infrastructure.store import (
    Grant,
    Lockbox,
    OperatorProfile,
    Review,
    in_tenant,
)
from zid_platform.modules.signals.ports import security_view
from zid_platform.modules.tenant.ports import (
    ResidencyError,
    authorize_remote_access,
    effective_operator_jurisdictions,
)

OPERATOR_RISK_CLASS = 3  # operator-plane actions never act on a cached security state


@dataclass(frozen=True, slots=True)
class OperatorSettings:
    """Operator plane configuration (settings.py: ZID_OPERATOR_*)."""

    tenant_id: str | None  # the dedicated operator tenant; None: the operator plane is disabled
    client_ids: tuple[str, ...] = ("zoiko-operator",)
    max_minutes: dict[str, int] = field(default_factory=dict)  # may only lower catalog ceilings
    break_glass_max: timedelta = timedelta(minutes=60)
    request_ttl: timedelta = timedelta(hours=24)  # REQUESTED/APPROVED must complete within this
    lockbox_ttl: timedelta = timedelta(hours=72)  # the customer's approval window and validity
    environment: str = "local"  # this cell's environment
    cell_id: str = "local-1"  # this cell: requests are region-bound to the cell they're made in

    def ceiling(self, cap: Capability) -> timedelta:
        return max_duration(cap, self.max_minutes)


@dataclass(frozen=True, slots=True)
class Operator:
    tenant_id: str  # the operator tenant (from the token, never the body)
    principal_id: str
    auth_time: datetime | None


@dataclass(frozen=True, slots=True)
class AccessInput:
    capability: str
    case_ref: str
    reason: str
    target_tenant_id: str | None
    environment: str
    cell_id: str
    resource_scope: str
    duration: timedelta


@dataclass(frozen=True, slots=True)
class CapabilityCheck:
    allowed: bool
    reason: str  # ALLOWED or the first refusal reason
    jit_grant_id: str | None = None


# ----------------------------------------------------------------------------------- helpers


def _ev(cls: int) -> str:
    return f"E{cls}"


def grant_data(g: Grant, **extra: Any) -> dict[str, Any]:
    """Event payload: ids, classes and commitments only (no case text, no customer content)."""
    return {
        "jit_grant_id": g.jit_grant_id,
        "requester_principal_id": g.requester_principal_id,
        "capability": g.capability,
        "target_tenant_id": g.target_tenant_id,
        "environment": g.environment,
        "cell_id": g.cell_id,
        "resource_scope_commitment": commitment(g.resource_scope),
        "case_ref_commitment": commitment(g.case_ref),
        "reason_commitment": commitment(g.reason),
        "state": g.state.value,
        "risk_class": f"R{g.risk_class}",
        "evidence_class": _ev(g.evidence_class),
        "break_glass": g.break_glass,
        "lockbox_request_id": g.lockbox_request_id,
        "expires_at": g.expires_at.isoformat() if g.expires_at else None,
        **extra,
    }


_ACTOR_KEYS = ("decided_by", "revoked_by", "changed_by")


async def _audit(
    conn: AsyncConnection,
    event_id: str,
    name: str,
    tenant_id: str | None,
    *,
    actor: str | None,
    target_type: str,
    target_ref: str,
    data: dict[str, Any],
    approvals: tuple[str, ...] = (),
) -> None:
    """The protected AuditRecord of an operator-access change (EVIDENCE-001 §7.1, §16)."""
    details = {
        k: v
        for k, v in data.items()
        if isinstance(v, str | int | bool | type(None))
        and not k.endswith("principal_id")
        and k not in _ACTOR_KEYS
    }
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="OPERATOR" if tenant_id is None else "HUMAN",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome="DENIED" if name.endswith((".denied", ".revoked")) else "SUCCESS",
            evidence_class=EvidenceClass(str(data.get("evidence_class", "E2"))),
            occurred_at=datetime.now(UTC),
            actor_principal_id=actor,
            source_service="operator_access",
            approval_refs=approvals,
            source_event_id=event_id,
            details=details,
        ),
    )


async def _emit_grant(
    conn: AsyncConnection,
    name: str,
    g: Grant,
    *,
    evidence_class: int | None = None,
    aggregate: tuple[str, str] | None = None,
    **extra: Any,
) -> None:
    """`aggregate`: (type, id) for events that are not a grant state change (one per approval)."""
    data = grant_data(g, **extra)
    if evidence_class is not None:
        data["evidence_class"] = _ev(evidence_class)
    event_id = await emit(
        conn,
        name=name,
        tenant_id=g.tenant_id,
        aggregate_type=aggregate[0] if aggregate else "operator_access_grant",
        aggregate_id=aggregate[1] if aggregate else g.jit_grant_id,
        aggregate_version=1 if aggregate else g.version,
        data=data,
        classification="RESTRICTED",
    )
    actor = next(
        (str(extra[k]) for k in ("approver_principal_id", *_ACTOR_KEYS) if extra.get(k)),
        g.requester_principal_id,
    )
    approvals = tuple(str(a) for a in extra.get("approver_principal_ids", ()) or ())
    await _audit(
        conn,
        event_id,
        name,
        None,
        actor=actor,
        target_type="jit_grant",
        target_ref=g.jit_grant_id,
        data=data,
        approvals=approvals,
    )


async def _emit_lockbox(conn: AsyncConnection, name: str, lb: Lockbox) -> None:
    event_id = await emit(
        conn,
        name=name,
        tenant_id=lb.tenant_id,
        aggregate_type="lockbox_request",
        aggregate_id=lb.lockbox_request_id,
        aggregate_version=lb.version,
        data={
            "lockbox_request_id": lb.lockbox_request_id,
            "jit_grant_id": lb.jit_grant_id,
            "operator_tenant_id": lb.operator_tenant_id,
            "requester_principal_id": lb.requester_principal_id,
            "capability": lb.capability,
            "state": lb.state,
            "decided_by": lb.decided_by,
            "case_ref_commitment": commitment(lb.case_ref),
            "evidence_class": "E3",
        },
        classification="RESTRICTED",
    )
    await _audit(
        conn,
        event_id,
        name,
        lb.tenant_id,  # the customer tenant's own evidence of operator access to its data
        actor=lb.decided_by,
        target_type="lockbox_request",
        target_ref=lb.lockbox_request_id,
        data={
            "lockbox_request_id": lb.lockbox_request_id,
            "jit_grant_id": lb.jit_grant_id,
            "capability": lb.capability,
            "state": lb.state,
            "case_ref_commitment": commitment(lb.case_ref),
            "evidence_class": "E3",
        },
    )


async def _security_ok(conn: AsyncConnection, tenant_id: str, principal_id: str) -> bool:
    view = await security_view(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        auth_time=None,
        risk_class=OPERATOR_RISK_CLASS,
        now=datetime.now(UTC),
    )
    return bool(view.state == "NORMAL")


async def operator_profile(
    conn: AsyncConnection, op: Operator, role: OperatorRole
) -> OperatorProfile:
    """A registered, ACTIVE operator with `role`, in a NORMAL security state (fresh read, R3)."""
    profile = await store.load_profile(conn, op.principal_id)
    if profile is None or not profile.active:
        raise OperatorAccessError("OPERATOR_PROFILE_MISSING", "Not a registered, active operator.")
    if role.value not in profile.roles:
        code = {
            OperatorRole.APPROVER: "APPROVER_ROLE_MISSING",
            OperatorRole.REVIEWER: "REVIEWER_ROLE_MISSING",
        }.get(role, "OPERATOR_ROLE_MISSING")
        raise OperatorAccessError(code, f"The operator lacks the {role.value} role.")
    if not await _security_ok(conn, op.tenant_id, op.principal_id):
        raise OperatorAccessError(
            "OPERATOR_SECURITY_STATE", "The operator's security state does not allow this."
        )
    return profile


async def _target_policy(conn: AsyncConnection, target: str | None) -> store.AccessPolicy | None:
    if not target:
        return None
    async with in_tenant(conn, target):
        policy = await store.load_policy(conn, target)
        # SRP-2/3 residency restricts operators even when the tenant's own policy is empty (F-W5)
        effective = await effective_operator_jurisdictions(
            conn, target, policy.allowed_operator_jurisdictions
        )
    return replace(policy, allowed_operator_jurisdictions=effective)


def _region(s: OperatorSettings, environment: str, cell_id: str) -> None:
    """Region/profile constraints: a cell grants access only to its own environment and cell."""
    if environment != s.environment or cell_id != s.cell_id:
        raise OperatorAccessError(
            "OPERATOR_SCOPE_INVALID", "Request access in the cell and environment it applies to."
        )


def _live(g: Grant, now: datetime, *expected: GrantState) -> None:
    state = g.effective_state(now)
    if state is GrantState.EXPIRED and g.state is not GrantState.EXPIRED:
        raise OperatorAccessError("OPERATOR_ACCESS_EXPIRED", "The request or grant has expired.")
    if state not in expected:
        raise OperatorAccessError("OPERATOR_ACCESS_INVALID_STATE", f"The request is {state.value}.")


# ------------------------------------------------------------------------------- JIT request


async def request_access(
    conn: AsyncConnection, s: OperatorSettings, op: Operator, inp: AccessInput, now: datetime
) -> Grant:
    cap = capability(inp.capability)
    check_scope(cap, inp.target_tenant_id)
    _region(s, inp.environment, inp.cell_id)
    duration = bounded_duration(inp.duration, s.ceiling(cap))
    profile = await operator_profile(conn, op, OperatorRole.REQUESTER)
    check_sod(
        cap.name, await store.held_capabilities(conn, op.principal_id, now, include_pending=True)
    )
    policy = await _target_policy(conn, inp.target_tenant_id)
    if policy is not None:
        check_jurisdiction(profile.jurisdiction, policy.allowed_operator_jurisdictions)
    if cap.customer_data and inp.target_tenant_id:
        # PRIV-25: reading a company's data from outside its region is a transfer; it needs an approved rule
        try:
            async with in_tenant(conn, inp.target_tenant_id):
                await authorize_remote_access(
                    conn, inp.target_tenant_id, profile.jurisdiction, actor=op.principal_id
                )
        except ResidencyError as e:
            raise OperatorAccessError(e.code, e.detail or e.code) from e
    lockbox_required = bool(cap.customer_data and policy is not None and policy.lockbox_required)
    grant_id = str(uuid7())
    lockbox_id = str(uuid7()) if lockbox_required else None
    g = Grant(
        jit_grant_id=grant_id,
        tenant_id=op.tenant_id,
        requester_principal_id=op.principal_id,
        capability=cap.name,
        target_tenant_id=inp.target_tenant_id,
        environment=inp.environment,
        cell_id=inp.cell_id,
        resource_scope=inp.resource_scope,
        case_ref=inp.case_ref,
        reason=inp.reason,
        risk_class=cap.risk_class,
        evidence_class=cap.evidence_class,
        duration=duration,
        required_approvals=cap.required_approvals,
        lockbox_required=lockbox_required,
        lockbox_request_id=lockbox_id,
        break_glass=False,
        requester_jurisdiction=profile.jurisdiction,
        state=GrantState.REQUESTED,
        version=1,
        created_at=now,
        request_expires_at=now + s.request_ttl,
    )
    await store.insert_grant(conn, g)
    await _emit_grant(conn, "operator_access.requested", g, evidence_class=2)
    if lockbox_id and inp.target_tenant_id:
        lb = Lockbox(
            lockbox_id,
            inp.target_tenant_id,
            op.tenant_id,
            grant_id,
            op.principal_id,
            cap.name,
            inp.resource_scope,
            inp.case_ref,
            inp.reason,
            duration,
            LockboxState.PENDING.value,
            1,
            now,
            now + s.lockbox_ttl,
            None,
            None,
        )
        async with in_tenant(conn, inp.target_tenant_id):  # the customer sees it in their tenant
            await store.insert_lockbox(conn, lb)
            await _emit_lockbox(conn, "operator_access.lockbox.requested", lb)
    return g


async def approve(
    conn: AsyncConnection, s: OperatorSettings, op: Operator, grant_id: str, now: datetime
) -> Grant:
    g = await store.load_grant(conn, grant_id, lock=True)
    _live(g, now, GrantState.REQUESTED)
    profile = await operator_profile(conn, op, OperatorRole.APPROVER)
    prior = await store.decisions(conn, g.jit_grant_id)
    check_approver(
        approver=op.principal_id,
        requester=g.requester_principal_id,
        approver_roles=profile.roles,
        already_approved={a for a, _ in prior},
        approver_active_capabilities=await store.held_capabilities(conn, op.principal_id, now),
        requested_capability=g.capability,
    )
    # SRP-2/3: the tenant's operator jurisdiction rule is re-checked at approval time
    policy = await _target_policy(conn, g.target_tenant_id)
    if policy is not None:
        check_jurisdiction(g.requester_jurisdiction, policy.allowed_operator_jurisdictions)
    await store.insert_approval(conn, g, approver=op.principal_id, verdict="APPROVE", now=now)
    approvers = [a for a, v in prior if v == "APPROVE"] + [op.principal_id]
    await _emit_grant(
        conn,
        "operator_access.approval.recorded",
        g,
        aggregate=("operator_access_approval", f"{g.jit_grant_id}:{op.principal_id}"),
        approver_principal_id=op.principal_id,
        approvals_received=len(approvers),
        required_approvals=g.required_approvals,
    )
    if len(approvers) >= g.required_approvals:
        g = await store.update_grant(
            conn, g, state=transition(g.state, GrantState.APPROVED), approved_at=now
        )
        await _emit_grant(conn, "operator_access.approved", g, approver_principal_ids=approvers)
    return g


async def deny(
    conn: AsyncConnection, op: Operator, grant_id: str, reason: str, now: datetime
) -> Grant:
    g = await store.load_grant(conn, grant_id, lock=True)
    _live(g, now, GrantState.REQUESTED)
    if op.principal_id == g.requester_principal_id:
        raise OperatorAccessError("APPROVER_SELF_APPROVAL", "Requesters cancel; they don't deny.")
    await operator_profile(conn, op, OperatorRole.APPROVER)
    await store.insert_approval(conn, g, approver=op.principal_id, verdict="DENY", now=now)
    g = await store.update_grant(
        conn,
        g,
        state=transition(g.state, GrantState.DENIED),
        ended_at=now,
        ended_by=op.principal_id,
        end_reason=reason,
    )
    await _emit_grant(
        conn,
        "operator_access.denied",
        g,
        decided_by=op.principal_id,
        decision_reason_commitment=commitment(reason),
    )
    return g


async def cancel(conn: AsyncConnection, op: Operator, grant_id: str, now: datetime) -> Grant:
    g = await store.load_grant(conn, grant_id, lock=True)
    if op.principal_id != g.requester_principal_id:
        raise OperatorAccessError("OPERATOR_NOT_REQUESTER", "Only the requester cancels.")
    _live(g, now, GrantState.REQUESTED, GrantState.APPROVED)
    g = await store.update_grant(
        conn,
        g,
        state=transition(g.state, GrantState.CANCELLED),
        ended_at=now,
        ended_by=op.principal_id,
        end_reason="cancelled",
    )
    await _emit_grant(conn, "operator_access.cancelled", g, evidence_class=2)
    return g


async def activate(
    conn: AsyncConnection, s: OperatorSettings, op: Operator, grant_id: str, now: datetime
) -> Grant:
    """APPROVED -> ACTIVE, by the requester, when they start work: the clock starts now."""
    g = await store.load_grant(conn, grant_id, lock=True)
    if op.principal_id != g.requester_principal_id:
        raise OperatorAccessError("OPERATOR_NOT_REQUESTER", "Only the requester activates.")
    _live(g, now, GrantState.APPROVED)
    profile = await operator_profile(conn, op, OperatorRole.REQUESTER)
    check_sod(g.capability, await store.held_capabilities(conn, op.principal_id, now))
    policy = await _target_policy(conn, g.target_tenant_id)
    if policy is not None:
        check_jurisdiction(profile.jurisdiction, policy.allowed_operator_jurisdictions)
    expires_at = now + g.duration
    if g.lockbox_required:
        lb = await _lockbox_of(conn, g)
        if lb is None or lb.effective_state(now) is not LockboxState.APPROVED:
            raise OperatorAccessError(
                "LOCKBOX_APPROVAL_REQUIRED", "The customer has not approved this access."
            )
        expires_at = min(expires_at, lb.expires_at)  # never outlives the customer's approval
    g = await store.update_grant(
        conn,
        g,
        state=transition(g.state, GrantState.ACTIVE),
        activated_at=now,
        expires_at=expires_at,
    )
    await _emit_grant(conn, "operator_access.activated", g)
    return g


async def revoke(
    conn: AsyncConnection, op: Operator, grant_id: str, reason: str, now: datetime
) -> Grant:
    """The requester gives it up, or any approver takes it away; effective immediately."""
    g = await store.load_grant(conn, grant_id, lock=True)
    if op.principal_id != g.requester_principal_id:
        await operator_profile(conn, op, OperatorRole.APPROVER)
    _live(g, now, GrantState.APPROVED, GrantState.ACTIVE)
    g = await store.update_grant(
        conn,
        g,
        state=transition(g.state, GrantState.REVOKED),
        ended_at=now,
        ended_by=op.principal_id,
        end_reason=reason,
    )
    await _emit_grant(
        conn,
        "operator_access.revoked",
        g,
        revoked_by=op.principal_id,
        revocation_reason_commitment=commitment(reason),
    )
    return g


async def _lockbox_of(conn: AsyncConnection, g: Grant) -> Lockbox | None:
    if not (g.lockbox_request_id and g.target_tenant_id):
        return None
    async with in_tenant(conn, g.target_tenant_id):
        return await store.load_lockbox(conn, g.lockbox_request_id)


# -------------------------------------------------------------------------------- break-glass


async def break_glass(
    conn: AsyncConnection, s: OperatorSettings, op: Operator, inp: AccessInput, now: datetime
) -> tuple[Grant, str]:
    """Emergency path: ACTIVE at once, Security alerted, independent post-review mandatory.

    Approvals and the lockbox are skipped (that's the emergency); scope, short duration,
    SoD, the operator's security state, region and the tenant's operator jurisdiction rule are not.
    """
    cap = capability(inp.capability)
    if not cap.break_glass:
        raise OperatorAccessError(
            "BREAK_GLASS_NOT_ALLOWED", f"{cap.name} is not available through break-glass."
        )
    check_scope(cap, inp.target_tenant_id)
    _region(s, inp.environment, inp.cell_id)
    duration = bounded_duration(inp.duration, min(s.ceiling(cap), s.break_glass_max))
    profile = await operator_profile(conn, op, OperatorRole.REQUESTER)
    check_sod(cap.name, await store.held_capabilities(conn, op.principal_id, now))
    policy = await _target_policy(conn, inp.target_tenant_id)
    if policy is not None:
        check_jurisdiction(profile.jurisdiction, policy.allowed_operator_jurisdictions)
    g = Grant(
        jit_grant_id=str(uuid7()),
        tenant_id=op.tenant_id,
        requester_principal_id=op.principal_id,
        capability=cap.name,
        target_tenant_id=inp.target_tenant_id,
        environment=inp.environment,
        cell_id=inp.cell_id,
        resource_scope=inp.resource_scope,
        case_ref=inp.case_ref,
        reason=inp.reason,
        risk_class=3,
        evidence_class=3,  # break-glass is always E3 (TENANCY-001 §19)
        duration=duration,
        required_approvals=0,
        lockbox_required=False,
        lockbox_request_id=None,
        break_glass=True,
        requester_jurisdiction=profile.jurisdiction,
        state=GrantState.ACTIVE,
        version=1,
        created_at=now,
        request_expires_at=now + duration,
        activated_at=now,
        expires_at=now + duration,
    )
    await store.insert_grant(conn, g)
    review_id = str(uuid7())
    await store.insert_review(conn, g, review_id, now)
    # The high-severity Security notification: the notification module routes this event.
    await _emit_grant(
        conn,
        "operator_access.break_glass.activated",
        g,
        severity="HIGH",
        notify=["security"],
        review_id=review_id,
        lockbox_bypassed=bool(cap.customer_data and policy is not None and policy.lockbox_required),
    )
    return g, review_id


async def complete_review(
    conn: AsyncConnection,
    op: Operator,
    review_id: str,
    outcome: ReviewOutcome,
    notes: str,
    now: datetime,
) -> Review:
    r = await store.load_review(conn, review_id)
    profile = await operator_profile(conn, op, OperatorRole.REVIEWER)
    check_reviewer(
        reviewer=op.principal_id, requester=r.requester_principal_id, reviewer_roles=profile.roles
    )
    r = await store.complete_review(
        conn, r, reviewer=op.principal_id, outcome=outcome, notes=notes, now=now
    )
    review_event = await emit(
        conn,
        name="operator_access.review.completed",
        tenant_id=r.tenant_id,
        aggregate_type="break_glass_review",
        aggregate_id=r.review_id,
        aggregate_version=2,
        data={
            "review_id": r.review_id,
            "jit_grant_id": r.jit_grant_id,
            "requester_principal_id": r.requester_principal_id,
            "reviewer_principal_id": op.principal_id,
            "outcome": outcome.value,
            "notes_commitment": commitment(notes),
            "evidence_class": "E3",
        },
        classification="RESTRICTED",
    )
    await _audit(
        conn,
        review_event,
        "operator_access.review.completed",
        None,
        actor=op.principal_id,
        target_type="break_glass_review",
        target_ref=r.review_id,
        data={
            "review_id": r.review_id,
            "jit_grant_id": r.jit_grant_id,
            "outcome": outcome.value,
            "notes_commitment": commitment(notes),
            "evidence_class": "E3",
        },
    )
    return r


# ------------------------------------------------------------------------ capability check


async def check_capability(
    conn: AsyncConnection,
    *,
    operator_principal_id: str,
    capability_name: str,
    tenant_id: str | None,
    resource: str,
    now: datetime,
) -> CapabilityCheck:
    """The ephemeral capability, re-checked on every use: an ACTIVE, unexpired grant for exactly
    this capability, tenant and resource; the operator still registered and in a NORMAL security
    state; the customer's lockbox approval (where one was needed) still standing.

    `conn` is scoped to the operator tenant."""
    profile = await store.load_profile(conn, operator_principal_id)
    if profile is None or not profile.active:
        return CapabilityCheck(False, "OPERATOR_PROFILE_MISSING")
    if not await _security_ok(conn, profile.tenant_id, operator_principal_id):
        return CapabilityCheck(False, "OPERATOR_SECURITY_STATE")
    reason = "NO_ACTIVE_GRANT"
    for g in await store.active_grants_for(conn, operator_principal_id, capability_name, now):
        if g.target_tenant_id != tenant_id or not resource_matches(g.resource_scope, resource):
            continue
        if g.lockbox_required:
            lb = await _lockbox_of(conn, g)
            if lb is None or lb.effective_state(now) is not LockboxState.APPROVED:
                reason = "LOCKBOX_APPROVAL_WITHDRAWN"
                continue
        return CapabilityCheck(True, "ALLOWED", g.jit_grant_id)
    return CapabilityCheck(False, reason)


async def expire_due(conn: AsyncConnection, now: datetime) -> list[str]:
    """Sweeper: mark grants past their end (and requests never completed) EXPIRED and emit
    `operator_access.expired` for each. Expiry is already enforced on use; this makes it visible.

    Run on a connection scoped to the operator tenant, or as the maintenance role (BYPASSRLS)."""
    expired = await store.expire_due(conn, now)
    for g in expired:
        await _emit_grant(conn, "operator_access.expired", g, end_reason=g.end_reason)
    return [g.jit_grant_id for g in expired]


# ------------------------------------------------------------------- tenant side (customer)


async def decide_lockbox(
    conn: AsyncConnection, *, lockbox_id: str, admin: str, approve: bool, reason: str, now: datetime
) -> Lockbox:
    """A customer administrator approves, denies, or withdraws an approval. `conn` is scoped to the
    customer tenant, so another tenant's request is simply not found (RLS)."""
    lb = await store.load_lockbox(conn, lockbox_id, lock=True)
    if lb is None:
        raise OperatorAccessError("LOCKBOX_REQUEST_NOT_FOUND")
    state = lb.effective_state(now)
    if state is LockboxState.EXPIRED:
        raise OperatorAccessError("LOCKBOX_EXPIRED", "The lockbox request has expired.")
    if approve:
        if state is not LockboxState.PENDING:
            raise OperatorAccessError("LOCKBOX_INVALID_STATE", f"The request is {state.value}.")
        target, name = LockboxState.APPROVED, "operator_access.lockbox.approved"
    elif state is LockboxState.PENDING:
        target, name = LockboxState.DENIED, "operator_access.lockbox.denied"
    elif state is LockboxState.APPROVED:  # withdrawing consent stops the operator at once
        target, name = LockboxState.REVOKED, "operator_access.lockbox.revoked"
    else:
        raise OperatorAccessError("LOCKBOX_INVALID_STATE", f"The request is {state.value}.")
    if admin == lb.requester_principal_id:
        raise OperatorAccessError("APPROVER_SELF_APPROVAL")
    lb = await store.decide_lockbox(conn, lb, state=target, by=admin, reason=reason, now=now)
    await _emit_lockbox(conn, name, lb)
    return lb


async def set_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    lockbox_required: bool,
    jurisdictions: tuple[str, ...],
    admin: str,
    now: datetime,
) -> store.AccessPolicy:
    p = await store.save_policy(
        conn,
        tenant_id=tenant_id,
        lockbox_required=lockbox_required,
        jurisdictions=jurisdictions,
        by=admin,
        now=now,
    )
    policy_event = await emit(
        conn,
        name="operator_access.policy.changed",
        tenant_id=tenant_id,
        aggregate_type="operator_access_policy",
        aggregate_id=tenant_id,
        aggregate_version=p.revision,
        data={
            "lockbox_required": p.lockbox_required,
            "allowed_operator_jurisdictions": list(p.allowed_operator_jurisdictions),
            "changed_by": admin,
            "evidence_class": "E3",
        },
    )
    await _audit(
        conn,
        policy_event,
        "operator_access.policy.changed",
        tenant_id,
        actor=admin,
        target_type="operator_access_policy",
        target_ref=tenant_id,
        data={
            "lockbox_required": p.lockbox_required,
            "revision": p.revision,
            "evidence_class": "E3",
        },
    )
    return p


async def register_operator(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    jurisdiction: str,
    roles: frozenset[OperatorRole],
    active: bool = True,
    now: datetime,
) -> None:
    """Workforce provisioning of an operator profile (no API: done by the operator-tenant
    provisioning path, e.g. SCIM from the workforce IdP or a governed bootstrap)."""
    await store.upsert_profile(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        jurisdiction=jurisdiction,
        roles=frozenset(r.value for r in roles),
        status="ACTIVE" if active else "DISABLED",
        now=now,
    )
    registered = await emit(
        conn,
        name="operator_access.operator.registered",
        tenant_id=tenant_id,
        aggregate_type="operator_profile",
        aggregate_id=principal_id,
        aggregate_version=1,
        data={
            "principal_id": principal_id,
            "jurisdiction": jurisdiction.upper(),
            "roles": sorted(r.value for r in roles),
            "active": active,
            "evidence_class": "E3",
        },
    )
    await _audit(
        conn,
        registered,
        "operator_access.operator.registered",
        None,
        actor=None,
        target_type="operator_profile",
        target_ref=principal_id,
        data={"jurisdiction": jurisdiction.upper(), "active": active, "evidence_class": "E3"},
    )
