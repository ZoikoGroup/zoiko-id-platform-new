"""Onboarding gates: machine-readable results behind every lifecycle move (ZID-OVERLAY-001 §25, §29;
G-W1..W4).

"Every displayed gate is backed by a machine-readable onboarding requirement/result" and there is
no "Launch anyway": a transition happens only when every *blocking* gate for it is PASS. WARN never
blocks; FAIL and PENDING always do. Pure rules, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum


class GateStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"
    PENDING = "PENDING"


@dataclass(frozen=True, slots=True)
class Gate:
    gate_id: str  # stable, e.g. "organization.root"
    stage: str  # the tenant status this gate guards the exit of
    status: GateStatus
    code: str | None = None  # canonical reason code when not PASS
    detail: str = ""
    blocking: bool = True

    def view(self) -> dict[str, object]:
        return {
            "gate_id": self.gate_id,
            "stage": self.stage,
            "status": self.status.value,
            "code": self.code,
            "detail": self.detail,
            "blocking": self.blocking,
        }


class OnboardingError(Exception):
    def __init__(self, code: str, detail: str = "", gates: tuple[Gate, ...] = ()) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.gates = gates


def blockers(gates: Iterable[Gate]) -> list[Gate]:
    return [
        g
        for g in gates
        if g.blocking and g.status is not GateStatus.PASS and g.status is not GateStatus.WARN
    ]


def require_pass(gates: Iterable[Gate], code: str) -> list[Gate]:
    """The gate report to record; raises `code` with the blockers when any blocking gate fails."""
    report = list(gates)
    blocked = blockers(report)
    if blocked:
        raise OnboardingError(
            code,
            "; ".join(f"{g.gate_id}: {g.code or g.status.value}" for g in blocked),
            tuple(report),
        )
    return report


# Tenant administration only - never business authority (OVERLAY §4: OWNER is not signing
# authority). These are what the verified representative's seeded grant carries, and what an Administrator receives
# from the owner. It covers the company console: read access to the company's plan, billing, usage, settings, alerts,
# contacts, webhooks, credentials, agents and mandates, and the management of company settings, alerts, contacts,
# webhooks and budgets. It does not include issuing credentials, creating mandates or registering agents: those
# commit the company to something and need their own grant.
# Recorded as the second person on an onboarding step the OWNER approved alone (ZID_ONBOARDING_APPROVAL=owner,
# CR-036): the database refuses an approver equal to the author, and nobody who did not act is named as approver.
OWNER_APPROVER = "00000000-0000-4000-8000-0000000000d2"
OWNER_APPROVAL_ACK = "accept-no-second-admin"


def require_owner_approval_ack(mode: str, environment: str, ack: str | None) -> None:
    """A deployed cell runs owner approval only when told so in so many words; local, test and dev need nothing."""
    if (
        mode == "owner"
        and environment not in ("local", "test", "dev")
        and ack != OWNER_APPROVAL_ACK
    ):
        raise RuntimeError(
            "ZID_ONBOARDING_APPROVAL=owner removes the second-administrator approval from onboarding; outside "
            f"local/test/dev it needs ZID_ONBOARDING_OWNER_ACK={OWNER_APPROVAL_ACK}"
        )


# Held by the OWNER but never handed down to an Administrator: they commit the company's money or identity. Changing
# the card invoices are charged to is one of them (CR-034). To let administrators do it too, empty this set and
# re-issue the administrators' grants (entrypoints/refresh_admin_grants.py).
OWNER_ONLY_ACTIONS = frozenset({"commercial.payment.manage"})

TENANT_ADMIN_ACTIONS = (
    "tenant.member.manage",
    "tenant.owner.change",
    "tenant.lifecycle.manage",
    "tenant.residency.manage",
    "tenant.identity_provider.change",
    "tenant.mfa_policy.change",
    "tenant.onboarding.manage",
    "tenant.onboarding.activate",
    "tenant.domain.manage",
    "integration.application.manage",
    "integration.connector.manage",
    "integration.mapping.manage",
    "provisioning.source.manage",
    "provisioning.source.read",
    "signals.stream.manage",
    "signals.stream.read",
    "operator.access_policy.manage",
    "operator.lockbox.approve",
    "authority.grant.read",
    "authority.approval.read",
    "evidence.read",
    "evidence.verify",
    "privacy.read",
    "identity.read",
    "organization.read",
    # company console, read
    "commercial.read",
    "communication.read",
    "configuration.read",
    "credential.read",
    "machine.agent.read",
    "agent.governance.read",
    "agent.policy.read",
    "agent.budget.read",
    "agent.audit.read",
    "agent.daara.read",
    "agent.review.read",
    "mandate.read",
    "metering.read",
    "notification.alert.read",
    "webhook.endpoint.read",
    # company administration, change
    "commercial.budget.manage",
    "commercial.plan.request",
    "commercial.payment.manage",
    "mandate.manage",
    "communication.contact.manage",
    "configuration.manage",
    "notification.alert.manage",
    "webhook.endpoint.manage",
)
