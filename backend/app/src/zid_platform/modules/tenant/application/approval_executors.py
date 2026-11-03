"""What an approved case applies (slice L-10, CR-047). One executor per kind of change, registered by name; the registry
(`config/approvals/action-tiers.yaml`) says which action uses which. An action whose executor is not here cannot be requested, so nothing
is approved that cannot run.

Every executor changes things only through the same domain functions the earlier routes call, with the approving people the case
counted, so the tenant records keep naming who asked and who checked. Containment executors can suspend or revoke; none can grant.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit, next_version

from zid_platform.modules.authentication.ports import (
    issue_enrollment_ticket,
    passkey_summary,
    revoke_for_security,
)
from zid_platform.modules.principal.ports import PrincipalStatus, PrincipalType, load_principal
from zid_platform.modules.screening.ports import request_rescreen
from zid_platform.modules.tenant.application import lifecycle
from zid_platform.modules.tenant.application.approvals import (
    ExecContext,
    Executor,
    Gate,
    register_executor,
)
from zid_platform.modules.tenant.domain.approvals import ApprovalError
from zid_platform.modules.tenant.domain.lifecycle import (
    Move,
    Role,
    RoleStatus,
    TenantError,
    TenantStatus,
)
from zid_platform.modules.tenant.domain.role_matrix import effective_rights
from zid_platform.modules.tenant.infrastructure import approvals as store
from zid_platform.modules.tenant.infrastructure import tenant_store
from zid_platform.modules.tenant.infrastructure.role_matrix import role_matrix

RECOVERY_TICKET_TTL = timedelta(hours=1)  # narrow and expiring (Decision 11 section 07)
_PRIVILEGED = (Role.OWNER, Role.ADMIN, Role.APPROVER)


def _text(payload: dict[str, Any], key: str, *, limit: int = 128) -> str:
    v = payload.get(key)
    if not isinstance(v, str) or not v.strip() or len(v) > limit:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", f"{key} is required")
    return v.strip()


async def _active_human(conn: AsyncConnection, tenant_id: str, principal_id: str) -> None:
    try:
        p, _ = await load_principal(conn, principal_id)
    except Exception as e:
        raise ApprovalError(
            "APPROVAL_PAYLOAD_INVALID", "no such account in this organization"
        ) from e
    if p.tenant_id != tenant_id or p.status is not PrincipalStatus.ACTIVE:
        raise ApprovalError(
            "APPROVAL_PAYLOAD_INVALID", "the account is not active in this organization"
        )
    if p.principal_type is not PrincipalType.HUMAN:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "only a person can hold this authority")


def _principal_of(payload: dict[str, Any]) -> str | None:
    v = payload.get("principal_id")
    return v if isinstance(v, str) else None


# ----------------------------------------------------------------------------------------------- A2: owners


async def _validate_owner_add(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    await _active_human(conn, tenant_id, _text(payload, "principal_id"))


async def _check_owner_add(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> list[Gate]:
    """Decision 11: reverify the incoming Owner and notify the affected parties. The platform can check that the incoming Owner holds a
    strong authenticator of their own (a passkey), so ownership never lands on an account that cannot meet step-up; the notice is the
    owner-change event the lifecycle emits."""
    who = _text(payload, "principal_id")
    held = await passkey_summary(conn, [who])
    return [
        Gate("INCOMING_OWNER_STRONG_AUTHENTICATOR", who in held, "The incoming Owner must have a passkey registered before ownership moves."),
        Gate("AFFECTED_PARTIES_NOTIFIED", True, "The owner-change event is emitted when it is applied.", blocking=False),
    ]  # fmt: skip


async def _owner_add(ctx: ExecContext) -> dict[str, Any]:
    p = _text(ctx.case.payload, "principal_id")
    a = await lifecycle.add_owner(
        ctx.conn, ctx.tenant_id, principal_id=p, granted_by=ctx.case.requester_principal_id,
        approved_by=ctx.approvers[0], now=ctx.now,
    )  # fmt: skip
    await request_rescreen(
        ctx.conn, ctx.tenant_id, reason="OWNER_ADDED", now=ctx.now
    )  # ED-04 section 07: privilege escalation
    return {"assignment_id": a.assignment_id, "role": "OWNER", "principal_id": p}


async def _validate_owner_remove(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    target = _text(payload, "principal_id")
    if (
        await tenant_store.active_owner_count(conn) <= 1
    ):  # prohibit orphaning: the successor is added first
        raise ApprovalError(
            "APPROVAL_WOULD_ORPHAN_TENANT",
            "the last Owner cannot be removed; add the successor first",
        )
    owners = [
        a for a in await tenant_store.roles(conn, principal_id=target) if a.role is Role.OWNER
    ]
    if not owners:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "that account is not an Owner")


async def _owner_remove(ctx: ExecContext) -> dict[str, Any]:
    p = _text(ctx.case.payload, "principal_id")
    a = await lifecycle.remove_owner(
        ctx.conn, ctx.tenant_id, principal_id=p, actor=ctx.case.requester_principal_id,
        approved_by=ctx.approvers[0], now=ctx.now,
    )  # fmt: skip
    return {
        "assignment_id": a.assignment_id,
        "role": "OWNER",
        "principal_id": p,
        "status": a.status.value,
    }


# ------------------------------------------------------------------------------------ A2: privileged roles


async def _validate_role_grant(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    if payload.get("role") not in ("ADMIN", "APPROVER"):
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "role is ADMIN or APPROVER")


async def _check_role_grant(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> list[Gate]:
    """Decision 11: evaluate the effective permissions the grant would carry. Shown to every approver, never blocking."""
    role = str(payload.get("role", ""))
    rights = effective_rights(role_matrix(), (role,)) if role else {}
    listed = ", ".join(sorted(rights)[:12]) + (" ..." if len(rights) > 12 else "")
    return [
        Gate(
            "EFFECTIVE_PERMISSIONS",
            True,
            f"{role} could perform {len(rights)} matrix actions: {listed}",
            blocking=False,
        )
    ]


async def _role_grant(ctx: ExecContext) -> dict[str, Any]:
    role = Role(_text(ctx.case.payload, "role"))
    inv = await lifecycle.invite(
        ctx.conn, ctx.tenant_id, role=role, invited_by=ctx.case.requester_principal_id,
        approved_by=ctx.approvers[0], now=ctx.now,
    )  # fmt: skip
    await request_rescreen(ctx.conn, ctx.tenant_id, reason="PRIVILEGED_ROLE_GRANTED", now=ctx.now)
    return {
        "assignment_id": inv.assignment.assignment_id,
        "role": role.value,
        "_once": {
            "enrollment_ticket": inv.enrollment_ticket,
            "expires_at": inv.ticket_expires_at.isoformat(),
        },
    }


# ------------------------------------------------------------------------------------------ A2: recovery


async def _validate_recovery(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    target = _text(payload, "principal_id")
    _text(
        payload, "identity_reproof_ref"
    )  # independent re-proofing is recorded, or the request cannot be made
    await _active_human(conn, tenant_id, target)
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=target)
        if a.status is RoleStatus.ACTIVE
    }
    if not held & set(_PRIVILEGED):
        raise ApprovalError(
            "APPROVAL_PAYLOAD_INVALID",
            "the account holds no privileged role; use ordinary recovery",
        )


async def _recovery(ctx: ExecContext) -> dict[str, Any]:
    """A narrow, expiring, single-use capability to enrol a new authenticator for exactly one account. It restores no role and no
    session: the person proves themselves with the new passkey, and every authority they held is checked as usual."""
    target = _text(ctx.case.payload, "principal_id")
    expires = ctx.now + RECOVERY_TICKET_TTL
    ticket = await issue_enrollment_ticket(
        ctx.conn, tenant_id=ctx.tenant_id, principal_id=target, purpose="BOOTSTRAP",
        issued_by=ctx.case.requester_principal_id, expires_at=expires,
    )  # fmt: skip
    return {
        "principal_id": target,
        "capability": "SINGLE_USE_ENROLLMENT_TICKET",
        "expires_at": expires.isoformat(),
        "_once": {"enrollment_ticket": ticket, "expires_at": expires.isoformat()},
    }


# ---------------------------------------------------------------------------------------- A2: reinstating


async def _validate_resume(conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]) -> None:
    _text(payload, "reason_code", limit=64)
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None or tenant.status is not TenantStatus.SUSPENDED:
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "the organization is not suspended")


async def _resume(ctx: ExecContext) -> dict[str, Any]:
    t = await lifecycle.transition(
        ctx.conn, ctx.tenant_id, Move.RESUME, reason_code=_text(ctx.case.payload, "reason_code", limit=64),
        actor=ctx.case.requester_principal_id, approval_ref=ctx.case.case_id, approvals=ctx.approvers, now=ctx.now,
    )  # fmt: skip
    return {"tenant_status": t.status.value}


# ---------------------------------------------------------------------------------------------- A1: reviewers


async def _validate_reviewer(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    r = await store.load_reviewer(conn, _text(payload, "reviewer_id"))
    if r is None or r.status != "PROPOSED":
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", "no proposed reviewer with that id")


def _reviewer_beneficiary(payload: dict[str, Any]) -> str | None:
    v = payload.get("reviewer_principal_id")
    return v if isinstance(v, str) else None


async def _prohibit_reviewer(ctx: ExecContext) -> str | None:
    r = await store.load_reviewer(ctx.conn, _text(ctx.case.payload, "reviewer_id"))
    if r is None or r.status != "PROPOSED":
        return "REVIEWER_NOT_PROPOSED"
    if (
        r.person_id == ctx.case.requester_person_id
    ):  # nobody appoints themselves as their own independent check
        return "REVIEWER_IS_REQUESTER"
    return None


async def _activate_reviewer(ctx: ExecContext) -> dict[str, Any]:
    rid = _text(ctx.case.payload, "reviewer_id")
    await store.set_reviewer_status(
        ctx.conn, rid, "ACTIVE", via="A1_APPROVAL", ref=ctx.case.case_id, now=ctx.now
    )
    return {"reviewer_id": rid, "status": "ACTIVE"}


# ----------------------------------------------------------------------------- A1: non-privileged invitation


async def _validate_member_invite(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    if payload.get("role") not in ("MEMBER", "OPERATOR", "VIEWER"):
        raise ApprovalError(
            "APPROVAL_PAYLOAD_INVALID",
            "role is MEMBER, OPERATOR or VIEWER; a privileged role is its own A2 action",
        )


async def _member_invite(ctx: ExecContext) -> dict[str, Any]:
    role = Role(_text(ctx.case.payload, "role"))
    inv = await lifecycle.invite(
        ctx.conn, ctx.tenant_id, role=role, invited_by=ctx.case.requester_principal_id,
        approved_by=ctx.approvers[0], now=ctx.now,
    )  # fmt: skip
    return {
        "assignment_id": inv.assignment.assignment_id,
        "role": role.value,
        "_once": {
            "enrollment_ticket": inv.enrollment_ticket,
            "expires_at": inv.ticket_expires_at.isoformat(),
        },
    }


# ---------------------------------------------------------------- A2: reversal of a high-assurance trust decision


async def _validate_adjudication(
    conn: AsyncConnection, tenant_id: str, payload: dict[str, Any]
) -> None:
    _text(payload, "decision_ref")
    _text(payload, "grounds", limit=500)


async def _adjudication_request(ctx: ExecContext) -> dict[str, Any]:
    """Tenant votes alone can never reverse a platform trust decision (Decision 11): approval only authorizes the organization to ask.
    This records and announces the request for Zoiko iD's own independent adjudication and changes no status."""
    ref = _text(ctx.case.payload, "decision_ref")
    await emit(
        ctx.conn, name="governance.adjudication.requested", tenant_id=ctx.tenant_id, aggregate_type="approval_case",
        aggregate_id=ctx.case.case_id, aggregate_version=await next_version(ctx.conn, "approval_case", ctx.case.case_id),
        data={"case_id": ctx.case.case_id, "decision_ref": ref, "request_hash": ctx.case.request_hash},
    )  # fmt: skip
    return {"adjudication": "REQUESTED", "status_changed": False, "decision_ref": ref}


# ------------------------------------------------------------------------- platform operations authorization


async def _platform_authorize(ctx: ExecContext) -> dict[str, Any]:
    """A platform A2 action is not applied here: the operation lives in an operator tool. Executing the case records that two independent
    approvers authorized exactly this payload, and the tool must present it to `consume_authorization` before it acts."""
    return {"authorization": "GRANTED", "single_use": True}


# ------------------------------------------------------------------------------------------- containment


async def _suspend(ctx: ExecContext) -> dict[str, Any]:
    reason = str(ctx.case.payload.get("reason_code") or "INCIDENT_CONTAINMENT")[:64]
    t = await lifecycle.transition(
        ctx.conn,
        ctx.tenant_id,
        Move.SUSPEND,
        reason_code=reason,
        actor=ctx.executor_principal,
        now=ctx.now,
    )
    return {"tenant_status": t.status.value}


async def _revoke_authenticator(ctx: ExecContext) -> dict[str, Any]:
    aid = _text(ctx.case.payload, "authenticator_id")
    try:
        done = await revoke_for_security(
            ctx.conn, authenticator_id=aid, reason="INCIDENT_CONTAINMENT", now=ctx.now
        )
    except ValueError as e:
        raise TenantError("CONTAINMENT_TARGET_NOT_FOUND") from e
    if not done:
        raise TenantError("CONTAINMENT_TARGET_NOT_FOUND", "no live authenticator with that id")
    return {"authenticator_id": aid, "status": "REVOKED"}


for _e in (
    Executor(
        "owner.add",
        _owner_add,
        _validate_owner_add,
        lambda p: _principal_of(p),
        check=_check_owner_add,
    ),
    Executor("owner.remove", _owner_remove, _validate_owner_remove, lambda p: _principal_of(p)),
    Executor("role.grant_privileged", _role_grant, _validate_role_grant, check=_check_role_grant),
    Executor("member.invite", _member_invite, _validate_member_invite),
    Executor("trust.adjudication_request", _adjudication_request, _validate_adjudication),
    Executor("platform.authorize", _platform_authorize),
    Executor(
        "lifecycle.recovery_reissue", _recovery, _validate_recovery, lambda p: _principal_of(p)
    ),
    Executor("lifecycle.resume", _resume, _validate_resume),
    Executor(
        "reviewer.activate",
        _activate_reviewer,
        _validate_reviewer,
        _reviewer_beneficiary,
        _prohibit_reviewer,
    ),
    Executor("lifecycle.suspend", _suspend),
    Executor("containment.revoke_authenticator", _revoke_authenticator),
):
    register_executor(_e)
