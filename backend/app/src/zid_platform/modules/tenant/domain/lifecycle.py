"""Tenant lifecycle, roles and OWNER bootstrap rules (ZID-OVERLAY-001 §2-4; ZID-DATA-001 §2.1; G-W0).

Pure rules, no I/O. The happy path is `deployment.OnboardingState` (DRAFT -> ... -> ACTIVE, SHADOW
always before enforcement); this module adds the rest of the §2 state machine:

    DRAFT -> ORGANIZATION_VERIFYING -> CONFIGURING -> INTEGRATING -> SHADOW -> PILOT -> ACTIVE
    CONFIGURING / INTEGRATING / ORGANIZATION_VERIFYING / SHADOW -> BLOCKED (NEEDS_REMEDIATION)
        BLOCKED -> the state it was blocked from, once remediated
    SHADOW -> INTEGRATING, PILOT -> SHADOW, ACTIVE -> PILOT   (rollback to the previous safe stage)
    any onboarding/production state -> SUSPENDED -> the state it was suspended from (governed)
    anything before CLOSED -> OFFBOARDING -> CLOSED; an abandoned DRAFT may close directly

No transition is ever inferred from elapsed time (§2): every move names who made it and why.
Roles (§4): OWNER is tenant administration, never legal signing authority; an invitation creates a
MEMBER (or ADMIN when an administrator says so) and never an OWNER; OWNER comes only from the
bootstrap gate or a protected, second-administrator-approved owner change.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.tenant.domain.deployment import Enforcement, OverlaySplit


class TenantError(Exception):
    """A domain refusal; `code` is a stable machine contract (mapped to HTTP in the API layer)."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class TenantStatus(StrEnum):
    DRAFT = "DRAFT"  # tenant record exists; no production trust established
    ORGANIZATION_VERIFYING = "ORGANIZATION_VERIFYING"
    CONFIGURING = "CONFIGURING"
    BLOCKED = "BLOCKED"  # NEEDS_REMEDIATION
    INTEGRATING = "INTEGRATING"
    SHADOW = "SHADOW"  # Zoiko evaluates, never enforces
    PILOT = "PILOT"  # scoped enforcement
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"  # production trust/enforcement disabled
    OFFBOARDING = "OFFBOARDING"
    CLOSED = "CLOSED"  # no runtime trust; retained evidence only


S = TenantStatus
ONBOARDING = (S.ORGANIZATION_VERIFYING, S.CONFIGURING, S.INTEGRATING, S.SHADOW, S.PILOT)
BLOCKABLE = frozenset({S.ORGANIZATION_VERIFYING, S.CONFIGURING, S.INTEGRATING, S.SHADOW})
SUSPENDABLE = frozenset({*ONBOARDING, S.ACTIVE})
RETURNS = frozenset({S.BLOCKED, S.SUSPENDED})  # leave to the state recorded on entry

_FORWARD: dict[TenantStatus, TenantStatus] = {
    S.DRAFT: S.ORGANIZATION_VERIFYING,
    S.ORGANIZATION_VERIFYING: S.CONFIGURING,
    S.CONFIGURING: S.INTEGRATING,
    S.INTEGRATING: S.SHADOW,
    S.SHADOW: S.PILOT,
    S.PILOT: S.ACTIVE,
}
ROLLBACK: dict[TenantStatus, TenantStatus] = {
    S.SHADOW: S.INTEGRATING,
    S.PILOT: S.SHADOW,
    S.ACTIVE: S.PILOT,
}


class Move(StrEnum):
    ADVANCE = "ADVANCE"
    BLOCK = "BLOCK"
    UNBLOCK = "UNBLOCK"
    ROLLBACK = "ROLLBACK"
    SUSPEND = "SUSPEND"
    RESUME = "RESUME"
    OFFBOARD = "OFFBOARD"
    CLOSE = "CLOSE"


@dataclass(frozen=True, slots=True)
class Transition:
    from_status: TenantStatus
    to_status: TenantStatus
    move: Move
    status_before: TenantStatus | None  # what BLOCKED/SUSPENDED will return to


def plan_transition(
    current: TenantStatus, move: Move, *, status_before: TenantStatus | None = None
) -> Transition:
    """The single legal target of `move` from `current`, or TENANT_STATE_INVALID."""
    target: TenantStatus | None = None
    before: TenantStatus | None = None
    if move is Move.ADVANCE:
        target = _FORWARD.get(current)
    elif move is Move.BLOCK and current in BLOCKABLE:
        target, before = S.BLOCKED, current
    elif move is Move.UNBLOCK and current is S.BLOCKED:
        target = status_before
    elif move is Move.ROLLBACK:
        target = ROLLBACK.get(current)
    elif move is Move.SUSPEND and current in SUSPENDABLE:
        target, before = S.SUSPENDED, current
    elif move is Move.RESUME and current is S.SUSPENDED:
        target = status_before
    elif move is Move.OFFBOARD and current not in (S.DRAFT, S.OFFBOARDING, S.CLOSED):
        target = S.OFFBOARDING
    elif move is Move.CLOSE and current in (S.DRAFT, S.OFFBOARDING):
        target = S.CLOSED
    if target is None:
        raise TenantError("TENANT_STATE_INVALID", f"{move.value} is not possible from {current}.")
    return Transition(current, target, move, before)


def enforcement_for(status: TenantStatus) -> Enforcement:
    """What decisions mean for this tenant: SHADOW only evaluates; PILOT enforces its scope."""
    if status is S.SHADOW:
        return Enforcement.EVALUATE_ONLY
    if status in (S.PILOT, S.ACTIVE):
        return Enforcement.ENFORCE
    return Enforcement.NONE


def serves_runtime(status: TenantStatus | None) -> bool:
    """SUSPENDED and CLOSED tenants get no decisions; None = tenant not managed by this record."""
    return status not in (S.SUSPENDED, S.CLOSED)


# ------------------------------------------------------------------------------------- roles


class Role(StrEnum):
    OWNER = "OWNER"  # tenant administration; never business/legal signing authority
    ADMIN = "ADMIN"
    APPROVER = "APPROVER"  # ZID-ENG-DEC-007: an independent, scoped checker; holds no authority grant until a request is approved
    OPERATOR = "OPERATOR"
    VIEWER = "VIEWER"
    MEMBER = "MEMBER"


class RoleStatus(StrEnum):
    PENDING_VERIFICATION = "PENDING_VERIFICATION"  # bootstrap OWNER before the organization gate
    INVITED = "INVITED"
    ACTIVE = "ACTIVE"
    REVOKED = "REVOKED"


class RoleOrigin(StrEnum):
    BOOTSTRAP = "BOOTSTRAP"
    INVITATION = "INVITATION"
    OWNER_CHANGE = "OWNER_CHANGE"
    PROVISIONING = "PROVISIONING"


def check_invitation(role: Role) -> Role:
    """OVL-13/14: invitation acceptance never creates OWNER; MEMBER unless an admin says ADMIN."""
    if role is Role.OWNER:
        raise TenantError(
            "TENANT_OWNER_UNAUTHORIZED",
            "OWNER is granted only by the bootstrap gate or a protected owner change.",
        )
    return role


def check_owner_removal(active_owners: int) -> None:
    if active_owners <= 1:
        raise TenantError("TENANT_LAST_OWNER", "A tenant always keeps at least one OWNER.")


# --------------------------------------------------------------------------- configuration

SLUG = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
MAX_OPEN_BOOTSTRAPS = 3  # open DRAFT tenants a single creator may hold (abuse/quota bound)
SPLITS = tuple(OverlaySplit)


def check_slug(slug: str) -> str:
    if not SLUG.fullmatch(slug):
        raise TenantError(
            "VALIDATION_FAILED",
            "tenant_slug is 1-63 lower-case letters, digits and inner hyphens.",
        )
    return slug


def check_mode(mode: str) -> OverlaySplit:
    try:
        return OverlaySplit(mode)
    except ValueError as e:
        raise TenantError("VALIDATION_FAILED", "deployment_mode is O0, O1, O2 or O3.") from e


__all__ = [
    "MAX_OPEN_BOOTSTRAPS",
    "Move",
    "Role",
    "RoleOrigin",
    "RoleStatus",
    "TenantError",
    "TenantStatus",
    "Transition",
    "check_invitation",
    "check_mode",
    "check_owner_removal",
    "check_slug",
    "enforcement_for",
    "plan_transition",
    "serves_runtime",
]
