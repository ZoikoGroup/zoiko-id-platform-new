"""Operator access: zero standing privilege, JIT, lockbox and break-glass (DEC-F4; SEC-001 §11-13).

Pure rules, no I/O:
- the fixed **capability catalog** (risk/evidence class, customer data, two-person control,
  maximum duration, scope, break-glass eligibility);
- the **separation-of-duties** conflict sets, as data;
- the **state machine** of a JIT access request/grant
  (REQUESTED -> APPROVED -> ACTIVE -> EXPIRED | REVOKED; REQUESTED/APPROVED -> DENIED | CANCELLED)
  and of a break-glass post-review (REVIEW_PENDING -> REVIEWED);
- the approval rules: independent approver (never the requester), k distinct approvers, an
  approver may not hold an active grant that conflicts with what they approve;
- the operator jurisdiction rule for SRP-2/3 tenants (TENANCY-001 §19).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class OperatorAccessError(Exception):
    """A domain refusal; `code` is a stable machine contract (mapped to HTTP in the API layer)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class Scope(StrEnum):
    TENANT = "TENANT"  # needs a target customer tenant
    PLATFORM = "PLATFORM"  # platform-wide (keys, evidence, security policy): no target tenant
    EITHER = "EITHER"


class GrantState(StrEnum):
    REQUESTED = "REQUESTED"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    EXPIRED = "EXPIRED"
    REVOKED = "REVOKED"
    DENIED = "DENIED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset(
    {GrantState.EXPIRED, GrantState.REVOKED, GrantState.DENIED, GrantState.CANCELLED}
)
OPEN = frozenset({GrantState.REQUESTED, GrantState.APPROVED, GrantState.ACTIVE})

TRANSITIONS: dict[GrantState, frozenset[GrantState]] = {
    GrantState.REQUESTED: frozenset(
        {GrantState.APPROVED, GrantState.DENIED, GrantState.CANCELLED, GrantState.EXPIRED}
    ),
    GrantState.APPROVED: frozenset(
        {GrantState.ACTIVE, GrantState.CANCELLED, GrantState.REVOKED, GrantState.EXPIRED}
    ),
    GrantState.ACTIVE: frozenset({GrantState.EXPIRED, GrantState.REVOKED}),
}


def transition(current: GrantState, target: GrantState) -> GrantState:
    if target not in TRANSITIONS.get(current, frozenset()):
        raise OperatorAccessError(
            "OPERATOR_ACCESS_INVALID_STATE", f"{current.value} cannot become {target.value}."
        )
    return target


class LockboxState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    REVOKED = "REVOKED"  # the customer withdrew an approval: access stops at once
    EXPIRED = "EXPIRED"  # computed: PENDING/APPROVED past expires_at


class ReviewState(StrEnum):
    REVIEW_PENDING = "REVIEW_PENDING"
    REVIEWED = "REVIEWED"


class ReviewOutcome(StrEnum):
    JUSTIFIED = "JUSTIFIED"
    UNJUSTIFIED = "UNJUSTIFIED"  # a finding: goes to the security incident process


class OperatorRole(StrEnum):
    """What an operator profile may do in the operator plane. Never a data privilege by itself."""

    REQUESTER = "REQUESTER"
    APPROVER = "APPROVER"
    REVIEWER = "REVIEWER"  # break-glass post-review


@dataclass(frozen=True, slots=True)
class Capability:
    name: str
    description: str
    risk_class: int  # R2 / R3
    evidence_class: int  # E2 / E3
    scope: Scope
    customer_data: bool  # touches customer data -> customer lockbox where the tenant requires it
    two_person: bool  # R3 / security / key / evidence / export -> two distinct approvers
    max_duration: timedelta
    break_glass: bool  # may be taken through the emergency path

    @property
    def required_approvals(self) -> int:
        return 2 if self.two_person else 1

    def view(self, max_duration: timedelta | None = None) -> dict[str, object]:
        return {
            "capability": self.name,
            "description": self.description,
            "risk_class": f"R{self.risk_class}",
            "evidence_class": f"E{self.evidence_class}",
            "scope": self.scope.value,
            "customer_data": self.customer_data,
            "required_approvals": self.required_approvals,
            "max_duration_minutes": int((max_duration or self.max_duration).total_seconds() // 60),
            "break_glass_allowed": self.break_glass,
        }


def _cap(
    name: str,
    description: str,
    *,
    risk: int,
    evidence: int,
    scope: Scope,
    customer_data: bool,
    two_person: bool,
    minutes: int,
    break_glass: bool,
) -> Capability:
    return Capability(
        name,
        description,
        risk,
        evidence,
        scope,
        customer_data,
        two_person,
        timedelta(minutes=minutes),
        break_glass,
    )


# The fixed capability catalog. There is deliberately no capability to impersonate a customer user,
# to create tenant roles or grants, or to mutate the database outside the service APIs (DEC-F4 §5).
RELEASE_ATTESTOR_ROLES = ("legal", "regulatory_ops", "security", "privacy", "qa", "vendor_mgmt")
RELEASE_APPROVER_ROLES = ("legal", "security_grc", "qa", "product_owner")


def _release_capabilities() -> tuple[Capability, ...]:
    """Market release authority (ZID-RCG-003 RCG-G10; CR-072) and official-register attestations (RCG-G02; CR-073): one capability per
    evidence-gate owner and per approver role, so the role someone signs in IS the capability they hold."""

    def cap(name: str, description: str, *, two_person: bool, minutes: int) -> Capability:
        return _cap(
            name,
            description,
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=two_person,
            minutes=minutes,
            break_glass=False,
        )

    return (
        cap(
            "regulatory.release.author",
            "Open, freeze and revise a market release; cannot sign any gate or approval of it (CR-072)",
            two_person=False,
            minutes=240,
        ),
        *(
            cap(
                f"regulatory.release.attest_{role}",
                f"Record the {role} evidence gate result of a market release (CR-072)",
                two_person=False,
                minutes=240,
            )
            for role in RELEASE_ATTESTOR_ROLES
        ),
        *(
            cap(
                f"regulatory.release.approve_{role}",
                f"Sign a market release's exact hash as its {role} approver (CR-072)",
                two_person=True,
                minutes=120,
            )
            for role in RELEASE_APPROVER_ROLES
        ),
        cap(
            "regulatory.release.launch",
            "Launch an approved market release, or withdraw a live one (CR-072)",
            two_person=True,
            minutes=60,
        ),
        cap(
            "regulatory.attestation.record",
            "Record what an official register said about a provider's certification, with its proof (CR-073)",
            two_person=False,
            minutes=240,
        ),
        cap(
            "regulatory.conformance.record",
            "Record a conformance run of the 48 acceptance cases (commit, environment, outcomes); cannot review it (CR-083)",
            two_person=False,
            minutes=240,
        ),
        cap(
            "regulatory.conformance.review",
            "Independently accept or reject a conformance run by signing its hash; never one's own run (CR-083)",
            two_person=False,
            minutes=240,
        ),
        cap(
            "regulatory.suspension.apply",
            "Emergency, exact-scope suspension of a provider's service, scheme, country or purpose (the kill switch); takes effect at once (CR-078)",
            two_person=False,
            minutes=60,
        ),
        cap(
            "regulatory.suspension.lift",
            "Lift a provider suspension (the rollback), by someone other than whoever applied it, with evidence (CR-078)",
            two_person=True,
            minutes=60,
        ),
        cap(
            "regulatory.issuer.record",
            "Record a dated statement about an issuer, its names and its accreditations, with the official source and its proof; a correction of past facts needs a reason (CR-075)",
            two_person=True,
            minutes=120,
        ),
        cap(
            "regulatory.entitlement.record",
            "Record, suspend or withdraw what Zoiko may use a provider for in a country, with the contract and terms it rests on (CR-074)",
            two_person=True,
            minutes=120,
        ),
    )


CAPABILITIES: dict[str, Capability] = {
    c.name: c
    for c in (
        _cap(
            "support.read_metadata",
            "Tenant/cell health and configuration metadata; no customer content",
            risk=2,
            evidence=2,
            scope=Scope.EITHER,
            customer_data=False,
            two_person=False,
            minutes=480,
            break_glass=True,
        ),
        _cap(
            "tenant.data.read",
            "Read customer data of one tenant for a support case",
            risk=2,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=True,
            two_person=False,
            minutes=240,
            break_glass=True,
        ),
        _cap(
            "tenant.config.change",
            "Change one tenant's configuration through the protected service APIs",
            risk=3,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=True,
        ),
        _cap(
            "tenant.data.delete",
            "Delete a closed tenant's data: crypto-erase its vault and destroy its connector secrets (TENANCY §23)",
            risk=3,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=False,  # it destroys data; it never reads any
            two_person=True,
            minutes=120,
            break_glass=False,  # never an emergency path: deletion cannot be undone
        ),
        _cap(
            "data.export",
            "Export customer data of one tenant (case-approved destination only)",
            risk=3,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=True,
            two_person=True,
            minutes=120,
            break_glass=False,  # never an emergency path for exfiltration-shaped access
        ),
        _cap(
            "evidence.admin",
            "Evidence ledger administration (never deletion or rewrite)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "key.admin",
            "Key management ceremonies and rotation",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "security.policy",
            "Platform security policy and containment controls",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=True,
        ),
        _cap(
            "platform.dr.execute",
            "Declare and run a cell-disaster failover or failback for one tenant (RESILIENCE §15)",
            risk=3,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=False,
            two_person=True,
            minutes=240,
            break_glass=True,  # a declared disaster is the emergency path
        ),
        _cap(
            "platform.migration.execute",
            "Run a governed tenant cell migration or restore (TENANCY §15; RESILIENCE §20)",
            risk=3,
            evidence=3,
            scope=Scope.EITHER,
            customer_data=False,
            two_person=True,
            minutes=480,
            break_glass=False,
        ),
        _cap(
            "platform.cell.manage",
            "Quarantine/restore a cell, record readiness and DR exercises (RESILIENCE §28)",
            risk=2,
            evidence=2,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=False,
            minutes=480,
            break_glass=True,
        ),
        _cap(
            "platform.change.manage",
            "Lift a service change freeze (SRE-001 §5.1, §23); freezing needs no grant",
            risk=2,
            evidence=2,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=False,
            minutes=240,
            break_glass=False,
        ),
        _cap(
            "commercial.catalog.manage",
            "Draft and approve price books and change plan definitions (COM-001 §6, §12; CR-024)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "commercial.billing.manage",
            "Freeze a tenant billing window and request or approve billable-usage adjustments (COM-001 7, 11; CR-025)",
            risk=3,
            evidence=3,
            scope=Scope.EITHER,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "commercial.sales.manage",
            "Commercial accounts, quotes and orders; discount approval also needs the commercial role (CR-028)",
            risk=2,
            evidence=2,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=False,
            minutes=480,
            break_glass=False,
        ),
        _cap(
            "commercial.finance.manage",
            "Finance authority: appoint approvers, sign the finance policy, open, decide and run credit and pilot-credit requests; "
            "approving also needs the named appointment (COM-CL-003, COM-CL-004; CR-054)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=False,
            minutes=240,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.author",
            "Draft or revise a jurisdiction and profession policy pack (ZID-RCG-003 RCG-G01; CR-067); cannot approve or publish it",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=False,
            minutes=240,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.approve_legal",
            "Sign a policy pack's exact hash as its legal approver (ZID-RCG-003 WF-02; CR-067)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.approve_grc",
            "Sign a policy pack's exact hash as its governance, risk and compliance approver (ZID-RCG-003 WF-02; CR-067)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.approve_security",
            "Sign a policy pack's exact hash as its security approver (ZID-RCG-003 WF-02; CR-067)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.approve_business_owner",
            "Sign a policy pack's exact hash as its business owner (ZID-RCG-003 WF-02; CR-067)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=120,
            break_glass=False,
        ),
        _cap(
            "regulatory.pack.publish",
            "Publish a fully approved policy pack, or retire one (rolling back to the last approved version) (ZID-RCG-003; CR-067)",
            risk=3,
            evidence=3,
            scope=Scope.PLATFORM,
            customer_data=False,
            two_person=True,
            minutes=60,
            break_glass=False,
        ),
        *_release_capabilities(),
        _cap(
            "prod.db.breakglass",
            "Captured, JIT-only database session (breakglass_dba)",
            risk=3,
            evidence=3,
            scope=Scope.TENANT,
            customer_data=True,
            two_person=True,
            minutes=60,
            break_glass=True,
        ),
    )
}


@dataclass(frozen=True, slots=True)
class ConflictRule:
    name: str
    capabilities: frozenset[str]
    rationale: str


# Separation of duties (DEC-F4 §4; SEC-001 §13): an operator may hold at most ONE capability of
# each set at any time, and may not approve a request for a capability that conflicts with one
# of their own active grants.
CONFLICT_SETS: tuple[ConflictRule, ...] = (
    ConflictRule(
        "no-privilege-concentration",
        frozenset({"prod.db.breakglass", "evidence.admin", "key.admin", "security.policy"}),
        "Production admin, evidence admin, key admin and security policy are never concentrated",
    ),
    ConflictRule(
        "key-vs-evidence",
        frozenset({"key.admin", "evidence.admin"}),
        "Key control does not allow evidence deletion or rewrite",
    ),
    ConflictRule(
        "support-vs-tenant-authority",
        frozenset({"support.read_metadata", "tenant.config.change"}),
        "Support cannot change tenant authority-bearing configuration",
    ),
    ConflictRule(
        "delete-vs-evidence",
        frozenset({"tenant.data.delete", "evidence.admin"}),
        "Whoever deletes a tenant's data cannot administer the evidence of that deletion",
    ),
    ConflictRule(
        "export-vs-evidence",
        frozenset({"data.export", "evidence.admin"}),
        "Whoever exports data cannot administer the evidence of that export",
    ),
    ConflictRule(
        "regulatory-pack-maker-vs-checker",
        frozenset(
            {
                "regulatory.pack.author",
                "regulatory.pack.approve_legal",
                "regulatory.pack.approve_grc",
                "regulatory.pack.approve_security",
                "regulatory.pack.approve_business_owner",
            }
        ),
        "Whoever drafts a policy pack signs none of it, and each approver holds one role only (ZID-RCG-003 T36)",
    ),
    ConflictRule(
        "regulatory-release-maker-vs-checker",
        frozenset(
            {
                "regulatory.release.author",
                *(f"regulatory.release.attest_{r}" for r in RELEASE_ATTESTOR_ROLES),
                *(f"regulatory.release.approve_{r}" for r in RELEASE_APPROVER_ROLES),
            }
        ),
        "Whoever prepares a market release signs no gate or approval of it, and holds one signing role only (ZID-RCG-003 RG-08)",
    ),
    ConflictRule(
        "regulatory-conformance-runner-vs-reviewer",
        frozenset({"regulatory.conformance.record", "regulatory.conformance.review"}),
        "Whoever records a conformance run is not its independent reviewer (ZID-RCG-003 section 12)",
    ),
)


def capability(name: str) -> Capability:
    try:
        return CAPABILITIES[name]
    except KeyError as e:
        raise OperatorAccessError("OPERATOR_CAPABILITY_UNKNOWN", name) from e


def conflicts(requested: str, held: set[str] | frozenset[str]) -> list[str]:
    """Names of the SoD rules `requested` would break alongside the capabilities in `held`."""
    return [
        r.name
        for r in CONFLICT_SETS
        if requested in r.capabilities and (set(held) - {requested}) & r.capabilities
    ]


def check_sod(requested: str, held: set[str] | frozenset[str]) -> None:
    broken = conflicts(requested, held)
    if broken:
        raise OperatorAccessError("OPERATOR_SOD_CONFLICT", ", ".join(broken))


def max_duration(cap: Capability, overrides: dict[str, int] | None = None) -> timedelta:
    """The capability's ceiling; a deployment may only LOWER it (never extend)."""
    minutes = (overrides or {}).get(cap.name)
    if minutes is not None and 0 < minutes < cap.max_duration.total_seconds() / 60:
        return timedelta(minutes=minutes)
    return cap.max_duration


def bounded_duration(requested: timedelta, ceiling: timedelta) -> timedelta:
    if requested <= timedelta(0):
        raise OperatorAccessError("OPERATOR_DURATION_INVALID", "Duration must be positive.")
    if requested > ceiling:
        raise OperatorAccessError(
            "OPERATOR_DURATION_EXCEEDED",
            f"At most {int(ceiling.total_seconds() // 60)} minutes for this capability.",
        )
    return requested


def check_scope(cap: Capability, target_tenant_id: str | None) -> None:
    if cap.scope is Scope.TENANT and not target_tenant_id:
        raise OperatorAccessError("OPERATOR_SCOPE_INVALID", f"{cap.name} needs a target tenant.")
    if cap.scope is Scope.PLATFORM and target_tenant_id:
        raise OperatorAccessError(
            "OPERATOR_SCOPE_INVALID", f"{cap.name} is platform-scoped; no tenant."
        )


def check_jurisdiction(operator_jurisdiction: str, allowed: tuple[str, ...] | list[str]) -> None:
    """SRP-2/3 tenants may restrict where operators sit (TENANCY-001 §19). Empty = unrestricted."""
    if allowed and operator_jurisdiction.upper() not in {a.upper() for a in allowed}:
        raise OperatorAccessError(
            "SOVEREIGN_OPERATOR_DENIED",
            "The tenant does not permit operators in this jurisdiction.",
        )


def check_approver(
    *,
    approver: str,
    requester: str,
    approver_roles: frozenset[str],
    already_approved: set[str],
    approver_active_capabilities: set[str],
    requested_capability: str,
) -> None:
    if approver == requester:
        raise OperatorAccessError("APPROVER_SELF_APPROVAL", "Requesters never approve their own.")
    if OperatorRole.APPROVER.value not in approver_roles:
        raise OperatorAccessError("APPROVER_ROLE_MISSING", "This operator cannot approve.")
    if approver in already_approved:
        raise OperatorAccessError("APPROVER_ALREADY_DECIDED", "One decision per approver.")
    broken = conflicts(requested_capability, approver_active_capabilities)
    if broken or requested_capability in approver_active_capabilities:
        raise OperatorAccessError(
            "APPROVER_SOD_CONFLICT", ", ".join(broken) or "approver holds the same capability"
        )


def check_reviewer(*, reviewer: str, requester: str, reviewer_roles: frozenset[str]) -> None:
    if reviewer == requester:
        raise OperatorAccessError("REVIEWER_SELF_REVIEW", "Break-glass is reviewed independently.")
    if OperatorRole.REVIEWER.value not in reviewer_roles:
        raise OperatorAccessError("REVIEWER_ROLE_MISSING", "This operator cannot review.")


def resource_matches(scope: str, resource: str) -> bool:
    """Exact match, or a trailing-`*` prefix pattern (`integration:*`). `*` alone matches all."""
    if scope.endswith("*"):
        return resource.startswith(scope[:-1])
    return scope == resource


def commitment(value: str) -> str:
    """What events carry instead of free text (case refs, reasons, notes): SHA-256, hex."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def effective_lockbox_state(state: str, expires_at: datetime, now: datetime) -> LockboxState:
    s = LockboxState(state)
    if s in (LockboxState.PENDING, LockboxState.APPROVED) and expires_at <= now:
        return LockboxState.EXPIRED
    return s
