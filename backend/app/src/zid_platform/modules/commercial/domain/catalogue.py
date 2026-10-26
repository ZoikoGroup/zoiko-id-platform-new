"""Plan capabilities and the entitlement decision (ZID-COM-001 §2, §6, §29; CR-024). Pure domain, no I/O.

The decision is explicit: it returns an `Entitlement` with `allowed`, a `reason` and the numbers behind it,
and callers branch on it. There is no exception path that grants.

Enforcement has three modes so the platform can carry the catalogue before any price book is approved:

- `off`     the decision is computed and ignored;
- `shadow`  the decision is computed, a would-be denial is reported, nothing is blocked (the default);
- `enforce` a denial blocks the paid extra.

Security paths (authentication, token refresh, revocation, recovery, signal intake, decisions on existing
authority) never call the gate at all; that is a property of the callers, tested at the API.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Enforcement = Literal["off", "shadow", "enforce"]
ENFORCEMENT: tuple[Enforcement, ...] = ("off", "shadow", "enforce")

PLAN_CODES = ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE", "ENTERPRISE")
DEFAULT_PLAN = "DEVELOPER"  # a tenant with no entitlement row is on this plan (CR-024)

# Reasons a paid extra can be refused.
NOT_IN_PLAN = "NOT_IN_PLAN"
NOT_RELEASED = "NOT_RELEASED"
QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
SUSPENDED = "SUSPENDED"
UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
ENTITLEMENT_UNAVAILABLE = "ENTITLEMENT_UNAVAILABLE"
BILLING_RESTRICTED = "BILLING_RESTRICTED"


class CommercialError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(detail or code)
        self.code, self.detail = code, detail


@dataclass(frozen=True, slots=True)
class PlanCapability:
    key: str
    mode: str  # INCLUDED | ADD_ON | METERED | CONTRACT | SANDBOX | QUALIFIED | NONE
    tier: str | None
    included_count: int | None
    released: bool


@dataclass(frozen=True, slots=True)
class Override:
    capability_key: str
    grant_mode: str | None
    extra_count: int | None
    expires_at: datetime | None = None
    revoked: bool = False

    def live(self, now: datetime) -> bool:
        return not self.revoked and (self.expires_at is None or self.expires_at > now)


@dataclass(frozen=True, slots=True)
class Entitlement:
    """The outcome of one gate check. `allowed` is what the caller must obey in this enforcement mode;
    `would_allow` is the underlying rule result, kept so shadow mode can report what enforcement would do."""

    capability: str
    allowed: bool
    would_allow: bool
    reason: str | None
    mode: str | None
    limit: int | None
    used: int | None
    sandbox_only: bool
    enforcement: Enforcement
    plan_code: str
    shadow_denied: bool = False

    def view(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "allowed": self.allowed,
            "would_allow": self.would_allow,
            "reason": self.reason,
            "mode": self.mode,
            "limit": self.limit,
            "used": self.used,
            "sandbox_only": self.sandbox_only,
            "enforcement": self.enforcement,
            "plan": self.plan_code,
        }


def _apply(enforcement: Enforcement, rule: Entitlement) -> Entitlement:
    """Turn the rule result into what the caller must obey in this enforcement mode."""
    if rule.would_allow or enforcement == "enforce":
        return rule
    return Entitlement(
        capability=rule.capability,
        allowed=True,
        would_allow=False,
        reason=rule.reason,
        mode=rule.mode,
        limit=rule.limit,
        used=rule.used,
        sandbox_only=rule.sandbox_only,
        enforcement=enforcement,
        plan_code=rule.plan_code,
        shadow_denied=enforcement == "shadow",
    )


def decide(
    capability: str,
    *,
    plan_code: str,
    rule: PlanCapability | None,
    overrides: Sequence[Override],
    used: int | None,
    quantity: int,
    now: datetime,
    enforcement: Enforcement,
    state: str = "ACTIVE",
    billing_restricted: bool = False,
) -> Entitlement:
    """Whether `quantity` more of `capability` is allowed.

    - A capability whose `released` flag is false is never granted, whatever the plan or an override says.
    - INCLUDED and METERED capabilities are allowed; a counted capability (one with `included_count` or an
      override `extra_count`) is limited to `included + extras` and refuses at the cap. Metered *volumes* are
      never capped here: they go to rating.
    - ADD_ON, CONTRACT, QUALIFIED and NONE are allowed only through a live override (a signed order or an
      approved exception); SANDBOX is allowed for sandbox use only.
    """

    def result(
        allowed: bool,
        reason: str | None,
        mode: str | None,
        limit: int | None = None,
        sandbox: bool = False,
    ) -> Entitlement:
        return _apply(
            enforcement,
            Entitlement(
                capability,
                allowed,
                allowed,
                reason,
                mode,
                limit,
                used,
                sandbox,
                enforcement,
                plan_code,
            ),
        )

    if rule is None:
        return result(False, UNKNOWN_CAPABILITY, None)
    if not rule.released:
        return result(False, NOT_RELEASED, rule.mode)
    if state == "SUSPENDED":
        return result(False, SUSPENDED, rule.mode)
    if (
        billing_restricted
    ):  # past due long enough that new paid extras stop (CR-027); identity never does
        return result(False, BILLING_RESTRICTED, rule.mode)
    live = [o for o in overrides if o.capability_key == capability and o.live(now)]
    extras = sum(o.extra_count or 0 for o in live)
    granted = any(o.grant_mode for o in live)
    counted = rule.included_count is not None or extras > 0

    if rule.mode == "SANDBOX" and not granted:
        return result(True, None, rule.mode, sandbox=True)
    self_serve = rule.mode in ("INCLUDED", "METERED")
    if not self_serve and not granted and extras == 0:
        return result(False, NOT_IN_PLAN, rule.mode)
    if counted:
        limit = (rule.included_count or 0) + extras
        if limit == 0:
            return result(False, NOT_IN_PLAN, rule.mode, 0)
        if (used or 0) + quantity > limit:
            return result(False, QUOTA_EXHAUSTED, rule.mode, limit)
        return result(True, None, rule.mode, limit)
    return result(True, None, rule.mode)
