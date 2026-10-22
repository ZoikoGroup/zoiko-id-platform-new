"""Incidents, production readiness reviews and operational state (ZID-SRE-001 §15-17, §24, §26,
§30; OPS-18/19/26/28; SRE-25/32). Pure rules, no I/O.

Incidents: any qualified responder declares, with no approval step (§16.1); severity moves up or
down with a timestamped reason; one incident commander; a security incident also needs a
Security Lead before it can resolve (a SEV0 is always a security incident). RESOLVED needs a validation statement; CLOSED (after
the post-incident review) needs, for SEV0/SEV1, every action item owned, dated and verified closed
by someone other than its owner (OPS-28).

PRR: the 13 gates of §24; a service is production-ready only when every gate PASSes or carries a
formally accepted, unexpired risk with a named owner and compensating control (SRE-32, OPS-26).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from typing import Any


class OperationsError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class Severity(StrEnum):
    SEV0 = "SEV0"
    SEV1 = "SEV1"
    SEV2 = "SEV2"
    SEV3 = "SEV3"
    SEV4 = "SEV4"

    @property
    def major(self) -> bool:
        return self in (Severity.SEV0, Severity.SEV1)


class IncidentStatus(StrEnum):
    DECLARED = "DECLARED"  # triage: scope, severity, commander
    MITIGATING = "MITIGATING"  # respond: contain -> mitigate, preserve evidence
    RECOVERY_VALIDATION = "RECOVERY_VALIDATION"  # restored, not yet declared stable
    RESOLVED = "RESOLVED"  # stable; PIR pending
    CLOSED = "CLOSED"  # PIR done and its action items verified


_NEXT = {
    IncidentStatus.DECLARED: {IncidentStatus.MITIGATING, IncidentStatus.RECOVERY_VALIDATION},
    IncidentStatus.MITIGATING: {IncidentStatus.RECOVERY_VALIDATION},
    IncidentStatus.RECOVERY_VALIDATION: {
        IncidentStatus.MITIGATING
    },  # regression: back to mitigation
}

ROLES = ("INCIDENT_COMMANDER", "OPERATIONS_LEAD", "COMMUNICATIONS_LEAD", "SCRIBE", "SECURITY_LEAD")


def check_move(current: IncidentStatus, target: IncidentStatus) -> None:
    if target not in _NEXT.get(current, set()):
        raise OperationsError("INCIDENT_INVALID_TRANSITION", f"{current.value} -> {target.value}")


def check_resolve(
    status: IncidentStatus,
    severity: Severity,
    security: bool,
    roles: Mapping[str, str],
    validation: str,
) -> None:
    if status not in (
        IncidentStatus.MITIGATING,
        IncidentStatus.RECOVERY_VALIDATION,
        IncidentStatus.DECLARED,
    ):
        raise OperationsError("INCIDENT_INVALID_TRANSITION", f"{status.value} -> RESOLVED")
    if "INCIDENT_COMMANDER" not in roles:
        raise OperationsError(
            "INCIDENT_COMMANDER_REQUIRED", "An incident commander must be assigned."
        )
    if security and "SECURITY_LEAD" not in roles:
        raise OperationsError(
            "INCIDENT_SECURITY_LEAD_REQUIRED", "A security incident needs a Security Lead."
        )
    if len(validation.strip()) < 10:
        raise OperationsError(
            "INCIDENT_VALIDATION_REQUIRED", "Say how service and controls were proven healthy."
        )


@dataclass(frozen=True, slots=True)
class ActionItem:
    title: str
    owner: str | None
    due: date | None
    verified_by: str | None  # who verified the item closed (never its owner)


def check_close(
    status: IncidentStatus, severity: Severity, pir_ref: str | None, items: Sequence[ActionItem]
) -> None:
    if status is not IncidentStatus.RESOLVED:
        raise OperationsError("INCIDENT_INVALID_TRANSITION", f"{status.value} -> CLOSED")
    if not severity.major:
        return
    if not pir_ref:
        raise OperationsError(
            "PIR_REQUIRED", "A SEV0/SEV1 incident closes only with its post-incident review."
        )
    for item in items:
        if not item.owner or item.due is None:
            raise OperationsError(
                "PIR_ACTION_UNOWNED", f"'{item.title}' needs an owner and a due date."
            )
        if not item.verified_by:
            raise OperationsError("PIR_ACTION_OPEN", f"'{item.title}' is not verified closed.")
        if item.verified_by == item.owner:
            raise OperationsError(
                "PIR_VERIFICATION_NOT_INDEPENDENT", f"'{item.title}' was verified by its owner."
            )


# -------------------------------------------------------------------------- readiness review

PRR_GATES = (
    "ownership",
    "architecture",
    "slos",
    "telemetry",
    "runbooks",
    "capacity",
    "ha_dr",
    "security",
    "data",
    "evidence",
    "dependencies",
    "support",
    "open_risks",
)


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    ACCEPTED = "ACCEPTED"  # risk formally accepted: owner, expiry, compensating control


@dataclass(frozen=True, slots=True)
class GateResult:
    gate: str
    status: GateStatus
    evidence_ref: str | None
    risk_owner: str | None
    risk_expires: date | None
    compensating_control: str | None


def check_gates(results: Iterable[GateResult]) -> list[GateResult]:
    seen: dict[str, GateResult] = {}
    for r in results:
        if r.gate not in PRR_GATES:
            raise OperationsError("PRR_GATE_UNKNOWN", r.gate)
        if r.gate in seen:
            raise OperationsError("PRR_GATE_DUPLICATED", r.gate)
        if r.status is GateStatus.PASS and not r.evidence_ref:
            raise OperationsError(
                "PRR_EVIDENCE_REQUIRED", f"{r.gate} PASS needs its evidence reference."
            )
        if r.status is GateStatus.ACCEPTED and not (
            r.risk_owner and r.risk_expires and r.compensating_control
        ):
            raise OperationsError(
                "PRR_ACCEPTANCE_INCOMPLETE",
                f"{r.gate}: an accepted risk needs owner, expiry and compensating control.",
            )
        seen[r.gate] = r
    return list(seen.values())


def readiness_verdict(
    results: Iterable[GateResult], today: date
) -> tuple[bool, list[dict[str, Any]]]:
    """(production_ready, blocking gates). A missing gate blocks like a FAIL; an accepted risk
    past its expiry blocks again (SRE-32)."""
    by_gate = {r.gate: r for r in results}
    blocking = []
    for g in PRR_GATES:
        r = by_gate.get(g)
        if r is None:
            blocking.append({"gate": g, "reason": "NOT_REVIEWED"})
        elif r.status is GateStatus.FAIL:
            blocking.append({"gate": g, "reason": "FAILED"})
        elif (
            r.status is GateStatus.ACCEPTED
            and r.risk_expires is not None
            and r.risk_expires < today
        ):
            blocking.append({"gate": g, "reason": "ACCEPTANCE_EXPIRED"})
    return not blocking, blocking


# ------------------------------------------------------------------------- operational state


class OpState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    AT_RISK = "AT_RISK"
    PARTIAL_OUTAGE = "PARTIAL_OUTAGE"
    MAJOR_OUTAGE = "MAJOR_OUTAGE"
    SECURITY_CONTAINMENT = "SECURITY_CONTAINMENT"
    FAILOVER_IN_PROGRESS = "FAILOVER_IN_PROGRESS"
    RECOVERY_VALIDATION = "RECOVERY_VALIDATION"
    CHANGE_FROZEN = "CHANGE_FROZEN"
    UNKNOWN = "UNKNOWN"


def operational_state(
    *,
    ready: bool | None,
    paging: bool,
    ticketing: bool,
    availability_short: float | None,
    frozen: bool,
    open_incident: tuple[Severity, IncidentStatus, bool] | None,
    failover_in_progress: bool,
) -> OpState:
    """§30, most severe applicable state first. Unknown health is reported as UNKNOWN, never
    HEALTHY, so high-risk paths treat it conservatively."""
    if ready is None:
        return OpState.UNKNOWN
    if open_incident is not None:
        _sev, status, security = open_incident
        if security and status in (IncidentStatus.DECLARED, IncidentStatus.MITIGATING):
            return OpState.SECURITY_CONTAINMENT
        if status is IncidentStatus.RECOVERY_VALIDATION:
            return OpState.RECOVERY_VALIDATION
    if failover_in_progress:
        return OpState.FAILOVER_IN_PROGRESS
    if not ready or (availability_short is not None and availability_short < 0.5):
        return OpState.MAJOR_OUTAGE
    if paging:
        return OpState.PARTIAL_OUTAGE
    if open_incident is not None and open_incident[1] is not IncidentStatus.RESOLVED:
        return OpState.DEGRADED
    if ticketing:
        return OpState.AT_RISK
    if frozen:
        return OpState.CHANGE_FROZEN
    return OpState.HEALTHY


def day(d: datetime) -> date:
    return d.date()
