"""Onboarding plan, shadow evaluation, progressive enforcement and the overlay verdict
(ZID-OVERLAY-001 §12-13, §17-20, §29; OVL-16..22; G-W4).

Pure rules, no I/O.

- A plan is versioned: a new version supersedes, an approved version is never edited (§17).
- Shadow (§18): Zoiko evaluates real traffic and records its outcome next to the incumbent's; it
  never changes the business side effect. Exit needs coverage, mismatch, latency and *zero* false
  allows (a Zoiko ALLOW where the incumbent/expected answer is DENY blocks the action, OVL-20).
- Pilot (§19): enforcement only inside explicit stage scopes (applications, actions, risk ceiling,
  population); R3 is never enabled by R0/R1 pilot results, only by a stage that names it.
- The overlay verdict (§12): a PEP that cannot enforce an obligation, or an R2/R3 request without
  the context its PEP profile requires, gets DENY; outside an enforcing scope the decision is
  returned as EVALUATE_ONLY (the incumbent still decides); SUSPENDED tenants get DENY.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zid_platform.modules.onboarding.domain.gates import Gate, GateStatus, OnboardingError

# Obligations the platform fulfils itself (the receipt is written by Zoiko, not the PEP).
PLATFORM_FULFILLED = frozenset({"DECISION_RECEIPT"})


class PlanStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    SUPERSEDED = "SUPERSEDED"


class StageStatus(StrEnum):
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"
    COMPLETED = "COMPLETED"


class Mismatch(StrEnum):
    MATCH = "MATCH"
    FALSE_ALLOW = "FALSE_ALLOW"  # Zoiko ALLOW where the expected answer is DENY: blocking
    FALSE_DENY = "FALSE_DENY"  # analysed and resolved before pilot
    NO_INCUMBENT = "NO_INCUMBENT"


class Enforcement(StrEnum):
    ENFORCE = "ENFORCE"
    EVALUATE_ONLY = "EVALUATE_ONLY"


@dataclass(frozen=True, slots=True)
class ExitCriteria:
    """§18 "Exit criteria" and §29 pilot gates; the plan may only tighten the defaults."""

    min_observations: int = 100
    min_context_coverage: float = 0.999
    r3_context_coverage: float = 1.0
    max_mismatch_rate: float = 0.01
    max_false_allows: int = 0
    max_p95_ms: float = 60.0

    @staticmethod
    def parse(raw: Mapping[str, Any] | None) -> ExitCriteria:
        d = ExitCriteria()
        raw = raw or {}
        c = ExitCriteria(
            min_observations=int(raw.get("min_observations", d.min_observations)),
            min_context_coverage=float(raw.get("min_context_coverage", d.min_context_coverage)),
            r3_context_coverage=float(raw.get("r3_context_coverage", d.r3_context_coverage)),
            max_mismatch_rate=float(raw.get("max_mismatch_rate", d.max_mismatch_rate)),
            max_false_allows=int(raw.get("max_false_allows", d.max_false_allows)),
            max_p95_ms=float(raw.get("max_p95_ms", d.max_p95_ms)),
        )
        if (
            c.max_false_allows != 0
            or c.min_context_coverage < d.min_context_coverage
            or c.r3_context_coverage < 1.0
            or c.min_observations < 1
            or not 0 <= c.max_mismatch_rate <= d.max_mismatch_rate
        ):
            raise OnboardingError(
                "VALIDATION_FAILED",
                "Exit criteria may only be stricter than the platform floor (0 false allows, "
                ">= 99.9% context coverage, 100% for R3).",
            )
        return c

    def view(self) -> dict[str, Any]:
        return {
            "min_observations": self.min_observations,
            "min_context_coverage": self.min_context_coverage,
            "r3_context_coverage": self.r3_context_coverage,
            "max_mismatch_rate": self.max_mismatch_rate,
            "max_false_allows": self.max_false_allows,
            "max_p95_ms": self.max_p95_ms,
        }


@dataclass(frozen=True, slots=True)
class Scope:
    applications: frozenset[str]  # empty = every registered application
    actions: frozenset[str]  # empty = every action up to max_risk
    max_risk: int  # 0..3
    principals: frozenset[str]  # empty = the whole population of those applications

    @staticmethod
    def parse(raw: Mapping[str, Any]) -> Scope:
        risk = int(raw.get("max_risk", 1))
        if not 0 <= risk <= 3:
            raise OnboardingError("VALIDATION_FAILED", "max_risk is 0..3")
        return Scope(
            frozenset(str(a) for a in raw.get("applications") or ()),
            frozenset(str(a) for a in raw.get("actions") or ()),
            risk,
            frozenset(str(p) for p in raw.get("principals") or ()),
        )

    def view(self) -> dict[str, Any]:
        return {
            "applications": sorted(self.applications),
            "actions": sorted(self.actions),
            "max_risk": self.max_risk,
            "principals": sorted(self.principals),
        }

    def covers(self, *, application_id: str | None, action: str, risk: int, subject: str) -> bool:
        return (
            risk <= self.max_risk
            and (not self.applications or application_id in self.applications)
            and (not self.actions or action in self.actions)
            and (not self.principals or subject in self.principals)
        )


def classify(zoiko_allow: bool, incumbent: str | None) -> Mismatch:
    if incumbent not in ("ALLOW", "DENY"):
        return Mismatch.NO_INCUMBENT
    if zoiko_allow and incumbent == "DENY":
        return Mismatch.FALSE_ALLOW
    if not zoiko_allow and incumbent == "ALLOW":
        return Mismatch.FALSE_DENY
    return Mismatch.MATCH


@dataclass(frozen=True, slots=True)
class ShadowStats:
    observations: int
    context_complete: int
    r3_observations: int
    r3_context_complete: int
    compared: int  # with an incumbent outcome
    mismatches: int  # FALSE_ALLOW + FALSE_DENY
    false_allows: int
    p95_ms: float | None


def shadow_gates(stats: ShadowStats, c: ExitCriteria, stage: str = "SHADOW") -> list[Gate]:
    def gate(gid: str, ok: bool, code: str, detail: str) -> Gate:
        return Gate(
            gid, stage, GateStatus.PASS if ok else GateStatus.FAIL, None if ok else code, detail
        )

    cov = stats.context_complete / stats.observations if stats.observations else 0.0
    r3 = stats.r3_context_complete / stats.r3_observations if stats.r3_observations else 1.0
    rate = stats.mismatches / stats.compared if stats.compared else 0.0
    code = "SHADOW_EXIT_CRITERIA_FAILED"
    return [
        gate(
            "shadow.observations",
            stats.observations >= c.min_observations,
            code,
            f"{stats.observations}/{c.min_observations}",
        ),
        gate(
            "shadow.context_coverage",
            cov >= c.min_context_coverage,
            "PEP_CONTEXT_INCOMPLETE",
            f"{cov:.4f}",
        ),
        gate(
            "shadow.r3_context_coverage",
            r3 >= c.r3_context_coverage,
            "PEP_CONTEXT_INCOMPLETE",
            f"{r3:.4f}",
        ),
        gate(
            "shadow.false_allows",
            stats.false_allows <= c.max_false_allows,
            code,
            f"{stats.false_allows} critical false allows",
        ),
        gate("shadow.mismatch_rate", rate <= c.max_mismatch_rate, code, f"{rate:.4f}"),
        gate(
            "shadow.latency_p95",
            stats.p95_ms is None or stats.p95_ms <= c.max_p95_ms,
            code,
            f"{stats.p95_ms}",
        ),
    ]


@dataclass(frozen=True, slots=True)
class Verdict:
    enforcement: Enforcement
    allow: bool  # what the PEP must do when ENFORCE
    reason_codes: tuple[str, ...]


def overlay_verdict(
    *,
    tenant_status: str | None,
    decision_allow: bool,
    risk: int,
    obligations: Iterable[str],
    supported_obligations: frozenset[str] | None,  # None: no registered PEP profile
    required_context: frozenset[str],
    present_context: frozenset[str],
    in_enforcing_scope: bool,
) -> Verdict:
    reasons: list[str] = []
    allow = decision_allow
    if tenant_status in ("SUSPENDED", "CLOSED"):
        return Verdict(Enforcement.ENFORCE, False, ("TENANT_SUSPENDED",))
    if supported_obligations is not None:
        unsupported = {o.upper() for o in obligations} - supported_obligations - PLATFORM_FULFILLED
        if allow and unsupported:  # OVL-17: an obligation the PEP can't enforce means DENY
            allow = False
            reasons.append("PEP_OBLIGATION_UNSUPPORTED")
    missing = required_context - present_context
    if allow and risk >= 2 and missing:
        allow = False
        reasons.append("PEP_CONTEXT_INCOMPLETE")
    if tenant_status in ("SHADOW",) or (tenant_status == "PILOT" and not in_enforcing_scope):
        return Verdict(Enforcement.EVALUATE_ONLY, allow, tuple(reasons))
    if tenant_status in (
        "DRAFT",
        "ORGANIZATION_VERIFYING",
        "CONFIGURING",
        "BLOCKED",
        "INTEGRATING",
    ):
        return Verdict(Enforcement.EVALUATE_ONLY, allow, (*reasons, "TENANT_NOT_ENFORCING"))
    return Verdict(Enforcement.ENFORCE, allow, tuple(reasons))
