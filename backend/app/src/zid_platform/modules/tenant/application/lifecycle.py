"""Tenant lifecycle use cases: bootstrap, governed transitions, invitations and OWNER changes
(ZID-OVERLAY-001 §2-4; TENANCY-001 §14 steps 1, 7, 11; G-W0).

Bootstrap (§4): a principal of the configured signup tenant creates a DRAFT tenant. The platform
creates the new tenant's first principal with a *pending* OWNER role and a single-use BOOTSTRAP
passkey enrollment ticket (and, where configured, the tenant's first-party console client). The
OWNER only becomes ACTIVE at the organization gate (G-W1); support staff never become owners and
nothing here edits ownership rows by hand.

Every change emits its event in the same transaction and writes a protected AuditRecord into the
tenant's own evidence partition: E3 for owner changes and suspend/resume/offboard/close, E2 for the
rest (OVERLAY §4 "DecisionReceipt"; EVIDENCE-001 §16). Event payloads carry ids, states and
commitments only.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_db import in_tenant
from zid_outbox import emit

from zid_platform.modules.authentication.ports import issue_enrollment_ticket
from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, commitment, record_audit
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
    load_principal,
    set_status,
)
from zid_platform.modules.resilience.ports import cell_accepts_placement
from zid_platform.modules.session.ports import OAuthClient, register_client
from zid_platform.modules.tenant.application.routing import forget
from zid_platform.modules.tenant.domain.lifecycle import (
    MAX_OPEN_BOOTSTRAPS,
    Move,
    Role,
    RoleOrigin,
    RoleStatus,
    TenantError,
    TenantStatus,
    check_invitation,
    check_mode,
    check_owner_removal,
    check_slug,
    plan_transition,
)
from zid_platform.modules.tenant.infrastructure import registration_store
from zid_platform.modules.tenant.infrastructure import tenant_store as store
from zid_platform.modules.tenant.infrastructure.tenant_store import RoleAssignment, Tenant

ENROLLMENT_TTL = timedelta(hours=23)  # authn.enrollment_ticket caps tickets at 24 h from DB time
# Moves that change tenant-wide security posture: security_revision++ (DATA §2.1 invariant)
_SECURITY_MOVES = frozenset({Move.SUSPEND, Move.RESUME, Move.OFFBOARD, Move.CLOSE})
_E3_MOVES = frozenset({*_SECURITY_MOVES, Move.ROLLBACK})
# Event versions of a role-assignment aggregate: one per status it can reach, in order.
ROLE_VERSION = {
    RoleStatus.PENDING_VERIFICATION: 1,
    RoleStatus.INVITED: 1,
    RoleStatus.ACTIVE: 2,
    RoleStatus.REVOKED: 3,
}
_NO_NEW_MEMBERS = frozenset({TenantStatus.SUSPENDED, TenantStatus.OFFBOARDING, TenantStatus.CLOSED})


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class ConsoleClient:
    """The new tenant's first-party console client (ZID_TENANT_CONSOLE_REDIRECT_URIS)."""

    redirect_uris: tuple[str, ...]
    audiences: tuple[str, ...]
    scopes: tuple[str, ...] = ("openid",)


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    tenant: Tenant
    owner_principal_id: str
    enrollment_ticket: str  # shown once
    ticket_expires_at: datetime
    console_client_id: str | None


@dataclass(frozen=True, slots=True)
class Invitation:
    assignment: RoleAssignment
    enrollment_ticket: str  # shown once
    ticket_expires_at: datetime


def tenant_data(t: Tenant, **extra: Any) -> dict[str, Any]:
    return {
        "tenant_id": t.tenant_id,
        "status": t.status.value,
        "deployment_mode": t.deployment_mode,
        "home_cell_id": t.home_cell_id,
        "configuration_revision": t.configuration_revision,
        "security_revision": t.security_revision,
        **extra,
    }


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event_id: str | None,
    name: str,
    *,
    actor: str | None,
    actor_type: str = "HUMAN",
    target_type: str,
    target_ref: str,
    evidence_class: EvidenceClass,
    details: Mapping[str, Any],
    approvals: Sequence[str] = (),
    outcome: str = "SUCCESS",
) -> None:
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type=actor_type if actor else "SERVICE",
            action=name.rsplit(".", 1)[-1],
            target_type=target_type,
            target_ref=target_ref,
            outcome=outcome,
            evidence_class=evidence_class,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="tenant",
            approval_refs=tuple(approvals),
            source_event_id=event_id,
            details={
                k: v for k, v in details.items() if isinstance(v, str | int | bool | type(None))
            },
        ),
    )


async def _event(
    conn: AsyncConnection,
    name: str,
    t: Tenant,
    *,
    actor: str | None,
    evidence_class: EvidenceClass,
    actor_type: str = "HUMAN",
    target: tuple[str, str] | None = None,
    version: int | None = None,
    approvals: Sequence[str] = (),
    **extra: Any,
) -> str:
    data = tenant_data(t, evidence_class=evidence_class.value, **extra)
    event_id = await emit(
        conn,
        name=name,
        tenant_id=t.tenant_id,
        aggregate_type=target[0] if target else "tenant",
        aggregate_id=target[1] if target else t.tenant_id,
        aggregate_version=version if version is not None else t.row_version,
        data=data,
        classification="RESTRICTED",
    )
    await _audit(
        conn,
        t.tenant_id,
        event_id,
        name,
        actor=actor,
        actor_type=actor_type,
        target_type=target[0] if target else "tenant",
        target_ref=target[1] if target else t.tenant_id,
        evidence_class=evidence_class,
        details=data,
        approvals=approvals,
    )
    return event_id


# ---------------------------------------------------------------------------------- bootstrap


def _check_names(display_name: str, tenant_slug: str, deployment_mode: str) -> tuple[str, str]:
    check_slug(tenant_slug)
    mode = check_mode(deployment_mode)
    name = display_name.strip()
    if not 1 <= len(name) <= 200:
        raise TenantError("VALIDATION_FAILED", "display_name is 1-200 characters.")
    return name, mode.value


async def _provision(
    conn: AsyncConnection,
    *,
    new_tenant: str,
    owner: str,
    name: str,
    tenant_slug: str,
    mode: str,
    home_cell_id: str,
    creating_tenant_id: str,
    creator_principal_id: str,
    console: ConsoleClient | None,
    now: datetime,
) -> BootstrapResult:
    """The new tenant's rows, under its own RLS scope: record, first principal with a pending
    OWNER role, BOOTSTRAP enrollment ticket, first-party console client and workspace entry."""
    if not await cell_accepts_placement(conn, home_cell_id):
        raise TenantError(
            "CELL_UNAVAILABLE", "This cell is not accepting new organizations right now."
        )
    expires = now + ENROLLMENT_TTL
    async with in_tenant(conn, new_tenant):
        tenant = await store.insert_tenant(
            conn,
            tenant_id=new_tenant,
            display_name=name,
            tenant_slug=tenant_slug,
            deployment_mode=mode,
            home_cell_id=home_cell_id,
            creator_tenant_id=creating_tenant_id,
            creator_principal_id=creator_principal_id,
            now=now,
        )
        await store.record_creation(conn, tenant, actor_principal_id=creator_principal_id, now=now)
        await create_principal(
            conn,
            Principal(owner, new_tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, home_cell_id),
        )
        await store.insert_role(
            conn,
            tenant_id=new_tenant,
            principal_id=owner,
            role=Role.OWNER,
            status=RoleStatus.PENDING_VERIFICATION,
            origin=RoleOrigin.BOOTSTRAP,
            granted_by=None,
            approved_by=None,
            now=now,
        )
        ticket = await issue_enrollment_ticket(
            conn,
            tenant_id=new_tenant,
            principal_id=owner,
            purpose="BOOTSTRAP",
            issued_by="tenant-bootstrap",
            expires_at=expires,
        )
        client_id = None
        if console is not None and console.redirect_uris:
            client_id = f"zid-console-{uuid7().hex}"
            await register_client(
                conn,
                OAuthClient(
                    client_id=client_id,
                    tenant_id=new_tenant,
                    client_type="PUBLIC",
                    profile="STANDARD",
                    redirect_uris=console.redirect_uris,
                    grant_types=frozenset({"authorization_code", "refresh_token"}),
                    scopes=frozenset(console.scopes),
                    audiences=console.audiences,
                    token_auth_method="none",
                    dpop_bound=False,
                    status="ACTIVE",
                    first_party=True,
                ),
            )
            await store.insert_workspace(
                conn, workspace=tenant_slug, tenant_id=new_tenant, client_id=client_id, name=name
            )
        creator = commitment(creator_principal_id, tenant_id=new_tenant, kind="tenant-creator")
        await _event(
            conn,
            "tenant.created",
            tenant,
            actor=None,
            evidence_class=EvidenceClass.E2,
            creator_commitment=creator,
            owner_principal_id=owner,
            console_client_id=client_id,
        )
        await emit(
            conn,
            name="tenant.provisioning.started",
            tenant_id=new_tenant,
            aggregate_type="tenant_milestone",
            aggregate_id=new_tenant,
            aggregate_version=tenant.row_version,
            data=tenant_data(tenant),
        )
    return BootstrapResult(tenant, owner, ticket, expires, client_id)


async def bootstrap_tenant(
    conn: AsyncConnection,
    *,
    creating_tenant_id: str,
    creator_principal_id: str,
    display_name: str,
    tenant_slug: str,
    deployment_mode: str,
    home_cell_id: str,
    console: ConsoleClient | None,
    now: datetime | None = None,
) -> BootstrapResult:
    """TENANCY §14 step 1 + OVERLAY §4: an immutable server-generated tenant id in DRAFT, its
    first principal with a pending OWNER role, a BOOTSTRAP enrollment ticket. `conn` is scoped to
    the creating (signup) tenant; the new tenant's rows are written under its own RLS scope."""
    now = now or _now()
    name, mode = _check_names(display_name, tenant_slug, deployment_mode)
    if await store.open_bootstraps(conn, creator_principal_id) >= MAX_OPEN_BOOTSTRAPS:
        raise TenantError(
            "TENANT_BOOTSTRAP_LIMIT",
            f"At most {MAX_OPEN_BOOTSTRAPS} tenants may be in onboarding for one creator.",
        )
    new_tenant, owner = str(uuid7()), str(uuid7())
    await store.insert_bootstrap(
        conn,
        creating_tenant_id=creating_tenant_id,
        creator_principal_id=creator_principal_id,
        new_tenant_id=new_tenant,
        now=now,
    )
    return await _provision(
        conn,
        new_tenant=new_tenant,
        owner=owner,
        name=name,
        tenant_slug=tenant_slug,
        mode=mode,
        home_cell_id=home_cell_id,
        creating_tenant_id=creating_tenant_id,
        creator_principal_id=creator_principal_id,
        console=console,
        now=now,
    )


async def self_signup(
    conn: AsyncConnection,
    *,
    display_name: str,
    tenant_slug: str,
    deployment_mode: str,
    home_cell_id: str,
    console: ConsoleClient | None,
    registration: Declaration | None = None,
    now: datetime | None = None,
) -> BootstrapResult:
    """Public company sign-up: the new company's first principal is its own creator. `conn` is a
    plain transaction; every row is written under the new tenant's own RLS scope. Abuse limits
    (rate, CAPTCHA, email/domain proof) are the caller's: this only provisions.

    `registration`: what the registrant declared (register identifier, acknowledged privacy-notice
    version) - stored in the same transaction, verified later against the register."""
    now = now or _now()
    name, mode = _check_names(display_name, tenant_slug, deployment_mode)
    if await store.workspace(conn, tenant_slug) is not None:
        raise TenantError("TENANT_SLUG_TAKEN", "That workspace name is already in use.")
    new_tenant, owner = str(uuid7()), str(uuid7())
    async with in_tenant(conn, new_tenant):
        await store.insert_bootstrap(
            conn,
            creating_tenant_id=new_tenant,
            creator_principal_id=owner,
            new_tenant_id=new_tenant,
            now=now,
        )
    result = await _provision(
        conn,
        new_tenant=new_tenant,
        owner=owner,
        name=name,
        tenant_slug=tenant_slug,
        mode=mode,
        home_cell_id=home_cell_id,
        creating_tenant_id=new_tenant,
        creator_principal_id=owner,
        console=console,
        now=now,
    )
    if registration is not None:
        async with in_tenant(conn, new_tenant):
            await registration_store.insert(
                conn,
                tenant_id=new_tenant,
                namespace=registration.namespace,
                identifier=registration.identifier,
                lei=registration.lei,
                notice_version=registration.notice_version,
                accepted_at=now,
                home_cell_id=home_cell_id,
            )
    return result


@dataclass(frozen=True, slots=True)
class Declaration:
    """What a registrant declares at sign-up (OVERLAY-001 §4; ORG-001 §19 step 1)."""

    namespace: str
    identifier: str
    lei: str | None
    notice_version: str


async def declared_registration(
    conn: AsyncConnection, tenant_id: str
) -> registration_store.Registration | None:
    """`conn` is scoped to the tenant."""
    return await registration_store.load(conn, tenant_id)


# -------------------------------------------------------------------------------- transitions


async def transition(
    conn: AsyncConnection,
    tenant_id: str,
    move: Move,
    *,
    reason_code: str,
    actor: str | None,
    actor_type: str = "HUMAN",
    gate_report: Sequence[Mapping[str, Any]] = (),
    approval_ref: str | None = None,
    approvals: Sequence[str] = (),
    now: datetime | None = None,
) -> Tenant:
    """One legal lifecycle move, recorded with who, why and the machine-readable gate report."""
    now = now or _now()
    tenant = await store.load_tenant(conn, tenant_id, lock=True)
    if tenant is None:
        raise TenantError("TENANT_NOT_FOUND")
    t = plan_transition(tenant.status, move, status_before=tenant.status_before)
    if move in _SECURITY_MOVES:  # before the move: a CLOSED tenant can never change again
        await store.bump_configuration(
            conn,
            tenant_id,
            change_kind=f"lifecycle.{move.value.lower()}",
            change_commitment=commitment(reason_code, tenant_id=tenant_id, kind="lifecycle"),
            actor_principal_id=actor,
            approval_ref=approval_ref,
            now=now,
            security=True,
        )
        tenant = await store.load_tenant(conn, tenant_id) or tenant
    updated = await store.apply_transition(
        conn,
        tenant,
        t,
        reason_code=reason_code,
        actor_type=actor_type,
        actor_principal_id=actor,
        gate_report=gate_report,
        approval_ref=approval_ref,
        now=now,
    )
    forget(tenant_id)
    ev = (
        EvidenceClass.E3
        if move in _E3_MOVES or t.to_status is TenantStatus.ACTIVE
        else (EvidenceClass.E2)
    )
    extra: dict[str, Any] = {
        "from_status": t.from_status.value,
        "move": move.value,
        "reason_code": reason_code,
        "gates_passed": sum(1 for g in gate_report if g.get("status") == "PASS"),
        "gates_total": len(gate_report),
    }
    await _event(
        conn,
        "tenant.lifecycle.changed",
        updated,
        actor=actor,
        actor_type=actor_type,
        evidence_class=ev,
        approvals=approvals,
        **extra,
    )
    milestone = _milestone(t.from_status, t.to_status, move)
    if milestone is not None:
        await emit(
            conn,
            name=milestone,
            tenant_id=tenant_id,
            aggregate_type="tenant_milestone",
            aggregate_id=tenant_id,
            aggregate_version=updated.row_version,
            data=tenant_data(updated, **extra),
            classification="RESTRICTED",
        )
    if t.to_status is TenantStatus.CLOSED:
        async with in_tenant(conn, tenant.creator_tenant_id):
            await store.close_bootstrap(conn, tenant_id, "ABANDONED")
    return updated


def _milestone(src: TenantStatus, dst: TenantStatus, move: Move) -> str | None:
    """The canonical OVERLAY §28 / TENANCY §27 event a transition also stands for."""
    if src is TenantStatus.ORGANIZATION_VERIFYING and dst is TenantStatus.CONFIGURING:
        return "tenant.organization_verified"
    if dst is TenantStatus.ACTIVE and move is Move.ADVANCE:
        return "tenant.activated"
    if move is Move.SUSPEND:
        return "tenant.suspended"
    if move is Move.RESUME:
        return "tenant.resumed"
    if move is Move.OFFBOARD:
        return "tenant.offboarding_started"
    if dst is TenantStatus.CLOSED:
        return "tenant.closed"
    return None


# ---------------------------------------------------------------------------------- members


async def invite(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    role: Role,
    invited_by: str,
    approved_by: str | None = None,
    invitee_email: str | None = None,
    now: datetime | None = None,
) -> Invitation:
    """A new principal with an INVITED role and a single-use enrollment ticket (MEMBER default). `invitee_email`, when given, is handed to
    the registered invitation hooks (the sign-up route that recognises the invitee after they prove the inbox); it is never stored here."""
    now = now or _now()
    check_invitation(role)
    tenant = await store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise TenantError("TENANT_NOT_FOUND")
    if tenant.status in _NO_NEW_MEMBERS:
        raise TenantError("TENANT_STATE_INVALID", f"No invitations while {tenant.status}.")
    principal = str(uuid7())
    await create_principal(
        conn,
        Principal(
            principal, tenant_id, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, tenant.home_cell_id
        ),
    )
    assignment = await store.insert_role(
        conn,
        tenant_id=tenant_id,
        principal_id=principal,
        role=role,
        status=RoleStatus.INVITED,
        origin=RoleOrigin.INVITATION,
        granted_by=invited_by,
        approved_by=approved_by,
        now=now,
    )
    expires = now + ENROLLMENT_TTL
    ticket = await issue_enrollment_ticket(
        conn,
        tenant_id=tenant_id,
        principal_id=principal,
        purpose="BOOTSTRAP",
        issued_by=f"invitation:{invited_by}",
        expires_at=expires,
    )
    await _member_event(conn, tenant, assignment, actor=invited_by)
    if invitee_email:
        for hook in _INVITATION_HOOKS.values():
            await hook(conn, tenant_id, principal, role.value, invitee_email)
    return Invitation(assignment, ticket, expires)


async def _member_event(
    conn: AsyncConnection, tenant: Tenant, a: RoleAssignment, *, actor: str | None
) -> None:
    await _event(
        conn,
        "tenant.member.changed",
        tenant,
        actor=actor,
        evidence_class=EvidenceClass.E2,
        target=("tenant_role_assignment", a.assignment_id),
        version=ROLE_VERSION[a.status],
        principal_id=a.principal_id,
        role=a.role.value,
        role_status=a.status.value,
        origin=a.origin.value,
    )


async def reissue_invitation(
    conn: AsyncConnection,
    tenant_id: str,
    assignment_id: str,
    *,
    actor: str,
    has_passkey: bool,
    now: datetime | None = None,
) -> Invitation:
    """A fresh single-use link for an invitee who has not created a passkey yet (the old link
    expired or was lost). Once a passkey exists the invitee signs in instead."""
    now = now or _now()
    a = next((x for x in await store.roles(conn) if x.assignment_id == assignment_id), None)
    if a is None:
        raise TenantError("TENANT_ROLE_NOT_FOUND")
    if a.status is not RoleStatus.INVITED:
        raise TenantError("TENANT_STATE_INVALID", "Only a pending invitation gets a new link.")
    if has_passkey:
        raise TenantError("TENANT_STATE_INVALID", "This person already created a passkey.")
    expires = now + ENROLLMENT_TTL
    ticket = await issue_enrollment_ticket(
        conn,
        tenant_id=tenant_id,
        principal_id=a.principal_id,
        purpose="BOOTSTRAP",
        issued_by=f"invitation:{actor}",
        expires_at=expires,
    )
    return Invitation(a, ticket, expires)


# Other modules react to an accepted invitation without this module importing them (module-cycle guard): they
# register at start-up (main.py). Keyed by name so registering twice is harmless.
AcceptanceHook = Callable[[AsyncConnection, str, RoleAssignment], Awaitable[None]]
InvitationHook = Callable[[AsyncConnection, str, str, str, str], Awaitable[None]]
_INVITATION_HOOKS: dict[str, InvitationHook] = {}


def register_invitation_hook(name: str, hook: InvitationHook) -> None:
    """(conn in the inviting tenant, tenant, invited principal, role, invitee email). Idempotent by name."""
    _INVITATION_HOOKS[name] = hook


_ACCEPTANCE_HOOKS: dict[str, AcceptanceHook] = {}


def register_acceptance_hook(name: str, hook: AcceptanceHook) -> None:
    _ACCEPTANCE_HOOKS[name] = hook


async def accept_invitation(
    conn: AsyncConnection, tenant_id: str, principal_id: str, now: datetime | None = None
) -> RoleAssignment:
    """The invited principal itself, authenticated: INVITED -> ACTIVE (never more than invited)."""
    now = now or _now()
    pending = [
        a
        for a in await store.roles(conn, principal_id=principal_id)
        if a.status is RoleStatus.INVITED
    ]
    if not pending:
        raise TenantError("TENANT_INVITATION_NOT_FOUND")
    tenant = await store.load_tenant(conn, tenant_id)
    if tenant is None or tenant.status in _NO_NEW_MEMBERS:
        raise TenantError("TENANT_STATE_INVALID")
    accepted: RoleAssignment | None = None
    for a in pending:
        accepted = await store.set_role_status(
            conn,
            a.assignment_id,
            expect=(RoleStatus.INVITED,),
            status=RoleStatus.ACTIVE,
            actor=principal_id,
            now=now,
        )
        if accepted is not None:
            await _member_event(conn, tenant, accepted, actor=principal_id)
            for hook in _ACCEPTANCE_HOOKS.values():
                await hook(conn, tenant_id, accepted)
    assert accepted is not None
    return accepted


async def revoke_member(
    conn: AsyncConnection,
    tenant_id: str,
    assignment_id: str,
    *,
    actor: str,
    now: datetime | None = None,
) -> RoleAssignment:
    """MEMBER/ADMIN roles only; an OWNER leaves only through the protected owner change."""
    now = now or _now()
    current = next((a for a in await store.roles(conn) if a.assignment_id == assignment_id), None)
    if current is None:
        raise TenantError("TENANT_ROLE_NOT_FOUND")
    if current.role is Role.OWNER:
        raise TenantError("TENANT_OWNER_UNAUTHORIZED", "Owners are removed by an owner change.")
    revoked = await store.set_role_status(
        conn,
        assignment_id,
        expect=(RoleStatus.INVITED, RoleStatus.ACTIVE),
        status=RoleStatus.REVOKED,
        actor=actor,
        now=now,
    )
    if revoked is None:
        raise TenantError("CONCURRENT_MODIFICATION")
    if current.status is RoleStatus.INVITED and current.origin is RoleOrigin.INVITATION:
        # The account existed only for this invitation: disable it so its link can't enroll a
        # passkey any more (registration requires an ACTIVE principal).
        await set_status(conn, current.principal_id, PrincipalStatus.DISABLED)
    tenant = await store.load_tenant(conn, tenant_id)
    assert tenant is not None
    await _member_event(conn, tenant, revoked, actor=actor)
    return revoked


# ----------------------------------------------------------------------------------- owners


async def _owner_changed(
    conn: AsyncConnection,
    tenant_id: str,
    a: RoleAssignment,
    *,
    actor: str | None,
    approvals: Sequence[str],
    cause: str,
    now: datetime,
) -> None:
    await store.bump_configuration(
        conn,
        tenant_id,
        change_kind=f"owner.{cause.lower()}",
        change_commitment=commitment(
            f"{a.principal_id}|{a.status.value}", tenant_id=tenant_id, kind="owner"
        ),
        actor_principal_id=actor,
        approval_ref=approvals[0] if approvals else None,
        now=now,
        security=True,
    )
    tenant = await store.load_tenant(conn, tenant_id)
    assert tenant is not None
    await _event(
        conn,
        "tenant.owner_changed",
        tenant,
        actor=actor,
        evidence_class=EvidenceClass.E3,
        target=("tenant_role_assignment", a.assignment_id),
        version=ROLE_VERSION[a.status],
        approvals=approvals,
        principal_id=a.principal_id,
        role_status=a.status.value,
        cause=cause,
    )


async def add_owner(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    granted_by: str,
    approved_by: str,
    now: datetime | None = None,
) -> RoleAssignment:
    """Protected owner change (tenant.owner.change, R3 + an independent approver; OVL-14)."""
    now = now or _now()
    try:
        principal, _ = await load_principal(conn, principal_id)  # RLS: this tenant's only
    except ValueError as e:
        raise TenantError("PRINCIPAL_NOT_FOUND") from e
    if principal.status is not PrincipalStatus.ACTIVE or principal.tenant_id != tenant_id:
        raise TenantError("PRINCIPAL_NOT_ACTIVE")
    a = await store.insert_role(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        role=Role.OWNER,
        status=RoleStatus.ACTIVE,
        origin=RoleOrigin.OWNER_CHANGE,
        granted_by=granted_by,
        approved_by=approved_by,
        now=now,
    )
    await _owner_changed(
        conn, tenant_id, a, actor=granted_by, approvals=(approved_by,), cause="ADDED", now=now
    )
    return a


async def remove_owner(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    principal_id: str,
    actor: str,
    approved_by: str,
    now: datetime | None = None,
) -> RoleAssignment:
    now = now or _now()
    check_owner_removal(await store.active_owner_count(conn, lock=True))
    owner = next(
        (
            a
            for a in await store.roles(conn, principal_id=principal_id)
            if a.role is Role.OWNER and a.status is RoleStatus.ACTIVE
        ),
        None,
    )
    if owner is None:
        raise TenantError("TENANT_ROLE_NOT_FOUND", "That principal is not an active OWNER.")
    revoked = await store.set_role_status(
        conn,
        owner.assignment_id,
        expect=(RoleStatus.ACTIVE,),
        status=RoleStatus.REVOKED,
        actor=actor,
        now=now,
    )
    if revoked is None:
        raise TenantError("CONCURRENT_MODIFICATION")
    await _owner_changed(
        conn, tenant_id, revoked, actor=actor, approvals=(approved_by,), cause="REMOVED", now=now
    )
    return revoked


async def activate_bootstrap_owner(
    conn: AsyncConnection, tenant_id: str, *, now: datetime | None = None
) -> RoleAssignment:
    """The organization gate passed (G-W1): the pending bootstrap OWNER becomes ACTIVE."""
    now = now or _now()
    pending = next(
        (
            a
            for a in await store.roles(conn)
            if a.role is Role.OWNER and a.status is RoleStatus.PENDING_VERIFICATION
        ),
        None,
    )
    if pending is None:
        raise TenantError("TENANT_ROLE_NOT_FOUND", "No pending bootstrap OWNER.")
    active = await store.set_role_status(
        conn,
        pending.assignment_id,
        expect=(RoleStatus.PENDING_VERIFICATION,),
        status=RoleStatus.ACTIVE,
        actor=None,
        now=now,
    )
    if active is None:
        raise TenantError("CONCURRENT_MODIFICATION")
    await _owner_changed(
        conn, tenant_id, active, actor=None, approvals=(), cause="BOOTSTRAP_GATE", now=now
    )
    tenant = await store.load_tenant(conn, tenant_id)
    assert tenant is not None
    async with in_tenant(conn, tenant.creator_tenant_id):
        await store.close_bootstrap(conn, tenant_id, "COMPLETED")
    return active


async def ensure_member(
    conn: AsyncConnection, tenant_id: str, principal_id: str, *, now: datetime | None = None
) -> str:
    """HR joiner (OVERLAY §22): a MEMBER membership from the provisioning source - a relationship
    candidate only, never authority. Idempotent; returns the resulting membership state."""
    now = now or _now()
    tenant = await store.load_tenant(conn, tenant_id)
    if tenant is None:
        return "NO_TENANT_RECORD"
    if tenant.status in _NO_NEW_MEMBERS:
        raise TenantError("TENANT_STATE_INVALID", f"No new members while {tenant.status}.")
    if await store.roles(conn, principal_id=principal_id):
        return "EXISTING"
    a = await store.insert_role(
        conn,
        tenant_id=tenant_id,
        principal_id=principal_id,
        role=Role.MEMBER,
        status=RoleStatus.ACTIVE,
        origin=RoleOrigin.PROVISIONING,
        granted_by=None,
        approved_by=None,
        now=now,
    )
    await _member_event(conn, tenant, a, actor=None)
    return "CREATED"


async def live_roles(conn: AsyncConnection, principal_id: str) -> list[RoleAssignment]:
    return await store.roles(conn, principal_id=principal_id)


__all__ = [
    "BootstrapResult",
    "ConsoleClient",
    "Invitation",
    "accept_invitation",
    "activate_bootstrap_owner",
    "add_owner",
    "bootstrap_tenant",
    "ensure_member",
    "invite",
    "live_roles",
    "register_acceptance_hook",
    "register_invitation_hook",
    "reissue_invitation",
    "remove_owner",
    "revoke_member",
    "self_signup",
    "transition",
]
