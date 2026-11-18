"""Release gates G0 to G3 for a screening module in a market (ZID-DTV-WF-06 section 22 and ENG-20, QA-053; CR-113). Pure domain, no I/O.

A module (sanctions, PEP and associates, adverse media, ongoing monitoring) is enabled in a market only when all four gates are signed: G0 policy readiness, G1 engineering and security, G2 independent
validation, G3 market go-live. Each gate needs named roles, each role signed by a different person, and one person signs in one role only. A sign-off carries its evidence reference and expires; an open P0
or critical security defect stops every gate at once, whatever has been signed. A rescinded sign-off is gone. Nothing here approves anything: it says, for one scope, whether the written controls are in place.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class Module(StrEnum):
    SANCTIONS = "SANCTIONS"
    PEP_RCA = "PEP_RCA"
    ADVERSE_MEDIA = "ADVERSE_MEDIA"
    MONITORING = "MONITORING"


GATES: dict[str, frozenset[str]] = {
    "G0": frozenset(
        {"FOUNDER", "LEGAL", "DPO", "AML"}
    ),  # policy readiness: architecture, market policy, DPIA, lawful basis, source and provider assessment
    "G1": frozenset(
        {"ENGINEERING", "SECURITY"}
    ),  # engineering and security: tests, tenant and region proof, runbooks
    "G2": frozenset(
        {"QA", "AML", "SECURITY"}
    ),  # independent validation: match quality, fairness, accessibility, attack and outage runs
    "G3": frozenset(
        {"CTO", "DPO", "CISO", "LEGAL", "PRODUCT", "QA"}
    ),  # market go-live: named approvals, rollout, kill switch, rollback
}
ROLES = frozenset().union(*GATES.values())
MAX_VALIDITY = timedelta(days=365)


class GateError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Scope:
    market: str
    module: Module


@dataclass(frozen=True, slots=True)
class Signoff:
    scope: Scope
    gate: str
    role: str
    signer: str
    evidence_ref: str
    signed_at: datetime
    expires_at: datetime
    rescinded_at: datetime | None = None

    def live(self, now: datetime) -> bool:
        return self.rescinded_at is None and self.signed_at <= now < self.expires_at


def check_signoff(existing: Sequence[Signoff], new: Signoff, *, now: datetime) -> None:
    """Everything that can be refused before a sign-off is recorded."""
    if new.gate not in GATES:
        raise GateError("GATE_UNKNOWN", new.gate)
    if new.role not in GATES[new.gate]:
        raise GateError("ROLE_NOT_FOR_GATE", f"{new.role} does not sign {new.gate}")
    if not new.evidence_ref.strip():
        raise GateError("EVIDENCE_REQUIRED", "cite the evidence the signature rests on")
    if not timedelta(0) < new.expires_at - new.signed_at <= MAX_VALIDITY:
        raise GateError("VALIDITY_INVALID", "a sign-off lasts at most a year")
    live = [s for s in existing if s.scope == new.scope and s.live(now)]
    if any(s.gate == new.gate and s.role == new.role for s in live):
        raise GateError("ROLE_ALREADY_SIGNED", f"{new.role} has signed {new.gate} for this scope")
    if any(s.signer == new.signer and s.role != new.role for s in live):
        raise GateError("SIGNER_HOLDS_ANOTHER_ROLE", "one person signs in one role only")
    if any(s.gate == new.gate and s.signer == new.signer for s in live):
        raise GateError("SIGNER_ALREADY_SIGNED", "each role on a gate is a different person")


@dataclass(frozen=True, slots=True)
class Status:
    scope: Scope
    go: bool
    gates: dict[str, tuple[str, ...]]  # gate -> the roles still missing
    blockers: tuple[str, ...]


def status(
    scope: Scope,
    signoffs: Sequence[Signoff],
    *,
    open_p0: int,
    now: datetime,
    hold: Collection[str] = (),
) -> Status:
    """Whether the scope may run. A gate is complete when every role it names has a live sign-off; the scope is GO only when all four are, no P0 or critical defect is open, and no kill switch holds it."""
    live = [s for s in signoffs if s.scope == scope and s.live(now)]
    missing = {
        g: tuple(sorted(roles - {s.role for s in live if s.gate == g}))
        for g, roles in GATES.items()
    }
    blockers: list[str] = [f"{g}_MISSING:{','.join(m)}" for g, m in missing.items() if m]
    if open_p0 > 0:
        blockers.append(f"OPEN_P0_DEFECTS:{open_p0}")
    blockers.extend(f"HELD:{h}" for h in sorted(hold))
    return Status(scope, not blockers, missing, tuple(blockers))
