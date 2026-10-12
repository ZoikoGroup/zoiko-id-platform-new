"""Risk-class enforcement contract (DEC-B1; ZID-REQ-001 §4; ZID-ARCH-002 §11).

The published contract is contracts/baseline/risk-classes.yaml; this module implements it and
test_risk_contract.py keeps the two identical. Pure domain rules, no I/O.

Floors come from the action catalog. A tenant may raise a floor; lowering R2/R3 needs an
unexpired exception approved by both Product and Security, and never breaks R3 fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import IntEnum, StrEnum


class RiskClass(IntEnum):
    R0 = 0
    R1 = 1
    R2 = 2
    R3 = 3


class EvidenceClass(IntEnum):
    E0 = 0
    E1 = 1
    E2 = 2
    E3 = 3


class FailurePosture(StrEnum):
    DEGRADE_PER_TENANT_POLICY = "DEGRADE_PER_TENANT_POLICY"
    USE_BOUNDED_CACHE = "USE_BOUNDED_CACHE"  # R2 only, and only where explicitly approved
    FAIL_CLOSED = "FAIL_CLOSED"


@dataclass(frozen=True, slots=True)
class ClassContract:
    revocation_slo: timedelta
    revocation_slo_cross_party: timedelta | None
    failure_posture: FailurePosture
    evidence_floor: EvidenceClass
    online_decision: bool
    mandate_reservation: bool
    commit_recheck: bool


CONTRACT: dict[RiskClass, ClassContract] = {
    RiskClass.R0: ClassContract(
        timedelta(minutes=15),
        None,
        FailurePosture.DEGRADE_PER_TENANT_POLICY,
        EvidenceClass.E0,
        online_decision=False,
        mandate_reservation=False,
        commit_recheck=False,
    ),
    RiskClass.R1: ClassContract(
        timedelta(seconds=60),
        None,
        FailurePosture.DEGRADE_PER_TENANT_POLICY,
        EvidenceClass.E1,
        online_decision=False,
        mandate_reservation=False,
        commit_recheck=False,
    ),
    RiskClass.R2: ClassContract(
        timedelta(seconds=10),
        None,
        FailurePosture.FAIL_CLOSED,
        EvidenceClass.E2,
        online_decision=True,
        mandate_reservation=False,
        commit_recheck=True,
    ),
    RiskClass.R3: ClassContract(
        timedelta(seconds=2),
        timedelta(seconds=5),
        FailurePosture.FAIL_CLOSED,
        EvidenceClass.E3,
        online_decision=True,
        mandate_reservation=True,
        commit_recheck=True,
    ),
}

EXCEPTION_APPROVERS = frozenset({"Product", "Security"})
MAX_LEVELS_LOWERED = 1
LOWEST_CLASS_FOR_R3_ACTION = RiskClass.R2


class RiskContractError(ValueError):
    """A request to weaken the risk contract that the platform floor does not allow."""


@dataclass(frozen=True, slots=True)
class WeakeningException:
    """A recorded, expiring exception that lowers one action's floor for one tenant (DEC-B1 §3)."""

    action: str
    tenant_id: str
    lowered_to: RiskClass
    approved_by: frozenset[str]
    compensating_control: str
    expires_at: datetime
    approval_ref: str

    def is_valid_for(self, action: str, tenant_id: str, now: datetime) -> bool:
        return (
            self.action == action
            and self.tenant_id == tenant_id
            and now < self.expires_at
            and EXCEPTION_APPROVERS <= self.approved_by
            and bool(self.compensating_control.strip())
            and bool(self.approval_ref.strip())
        )


def effective_risk_class(
    action: str,
    tenant_id: str,
    floor: RiskClass,
    *,
    requested: RiskClass | None = None,
    tenant_override: RiskClass | None = None,
    exception: WeakeningException | None = None,
    now: datetime,
) -> RiskClass:
    """The class a request is evaluated at. Callers and tenants can only raise it.

    An expired, mis-scoped or under-approved exception is ignored, so the request re-blocks at
    its catalog floor automatically when the exception lapses.
    """
    base = floor
    if exception is not None and exception.is_valid_for(action, tenant_id, now):
        if floor - exception.lowered_to > MAX_LEVELS_LOWERED:
            raise RiskContractError(f"{action}: an exception may lower a class by one level only")
        if floor is RiskClass.R3 and exception.lowered_to < LOWEST_CLASS_FOR_R3_ACTION:
            raise RiskContractError(
                f"{action}: R3 actions never go below R2 (R3 fail-closed floor)"
            )
        base = min(floor, exception.lowered_to)
    raised = [c for c in (requested, tenant_override) if c is not None]
    return max([base, *raised])


def failure_posture(risk: RiskClass, *, bounded_cache_approved: bool = False) -> FailurePosture:
    """What a PEP does when required fresh authority or security state is unavailable."""
    if risk is RiskClass.R2 and bounded_cache_approved:
        return FailurePosture.USE_BOUNDED_CACHE
    return CONTRACT[risk].failure_posture


def revocation_slo(risk: RiskClass, *, cross_party: bool = False) -> timedelta:
    c = CONTRACT[risk]
    if cross_party and c.revocation_slo_cross_party is not None:
        return c.revocation_slo_cross_party
    return c.revocation_slo


def evidence_floor(risk: RiskClass) -> EvidenceClass:
    return CONTRACT[risk].evidence_floor
