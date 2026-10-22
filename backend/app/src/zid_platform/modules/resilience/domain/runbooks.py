"""Recovery runbooks as enforced state machines (ZID-RESILIENCE-001 §11, §14-20; ZID-TENANCY-001
§15-16; ZID-SRE-001 §17-18; DEC-G1 §4-6; G-W7).

Pure rules, no I/O. A recovery operation (cell disaster failover, planned migration, failback,
restore) is an ordered list of steps; a step is either
- CONFIRM: an infrastructure action (fence the old writer, promote a replica, activate recovery
  keys, copy/verify data, restore into an isolated environment) performed outside the platform and
  confirmed by the operator with an evidence reference, or
- AUTO: a check or change the platform makes itself (residency, replica lag vs RPO, recovery keys,
  one-AZ-loss capacity, epoch advance, routing, reconciliation).
Steps run strictly in order; nothing is skipped, so there is no "promote before fence" and no
"route before validate". The safety rule of RESILIENCE §0.2: a controlled DENY (unavailability) is
safer than split-brain - any failed gate leaves the operation BLOCKED with its canonical code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ResilienceError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class Kind(StrEnum):
    FAILOVER = "FAILOVER"
    MIGRATION = "MIGRATION"
    FAILBACK = "FAILBACK"
    RESTORE = "RESTORE"


class StepType(StrEnum):
    CONFIRM = "CONFIRM"
    AUTO = "AUTO"


class OpStatus(StrEnum):
    OPEN = "OPEN"
    BLOCKED = "BLOCKED"  # a gate failed; fix and retry the same step, or abort
    COMPLETED = "COMPLETED"
    ABORTED = "ABORTED"


@dataclass(frozen=True, slots=True)
class Step:
    name: str
    type: StepType
    detail: str


C, A = StepType.CONFIRM, StepType.AUTO
RUNBOOKS: dict[Kind, tuple[Step, ...]] = {
    # RESILIENCE §15 cell disaster failover (declared at creation)
    Kind.FAILOVER: (
        Step(
            "FREEZE_OLD_WRITER", C, "Old cell writes stopped and its writer fenced (leases revoked)"
        ),
        Step(
            "ASSESS",
            A,
            "Residency-approved target, replica lag within RPO, recovery keys, capacity",
        ),
        Step(
            "PROMOTE_DATA", C, "DR databases/brokers/object paths promoted under a new generation"
        ),
        Step("ACTIVATE_KEYS", C, "Approved recovery KMS/HSM key path available (no key export)"),
        Step(
            "ADVANCE_EPOCHS", A, "placement_epoch and C3 writer epoch advanced to the recovery cell"
        ),
        Step(
            "VALIDATE", C, "Synthetic authn, authz ALLOW+DENY, mandate, evidence, revocation probes"
        ),
        Step("ROUTE", A, "Routing switched: cached placement dropped, new record published"),
        Step("RECONCILE", A, "Open mandate reservations quarantined as RECONCILIATION_REQUIRED"),
    ),
    # TENANCY §15 governed cell migration (single writer throughout)
    Kind.MIGRATION: (
        Step("PREPARE", A, "Destination profile, residency and capacity validated"),
        Step(
            "COPY_VERIFIED", C, "Encrypted full copy + delta verified (counts, hashes, revisions)"
        ),
        Step("QUIESCE", C, "Sensitive writes drained; in-flight mandate reservations settled"),
        Step("ADVANCE_EPOCHS", A, "Cutover: destination becomes writer under a new epoch"),
        Step("VALIDATE", C, "Auth/authz/session/integration/evidence canaries passed"),
        Step("ROUTE", A, "Global locator publishes the new home cell and epoch"),
        Step("RETIRE_OLD_COPY", C, "Old copy retained only for the rollback window, then erased"),
    ),
    # RESILIENCE §16 / SRE §18 failback: a planned migration back, never an automatic reverse
    Kind.FAILBACK: (
        Step("PREPARE", A, "Original cell rebuilt as non-authoritative; residency and capacity"),
        Step("RESYNC_VERIFIED", C, "Replication from the recovery writer verified complete"),
        Step("FENCE_RECOVERY_WRITER", C, "Recovery writer fenced before the original takes over"),
        Step("ADVANCE_EPOCHS", A, "New placement/writer epochs; old epochs invalid forever"),
        Step("VALIDATE", C, "Integrity checks and mandate reconciliation complete"),
        Step("ROUTE", A, "Routing switched back"),
    ),
    # RESILIENCE §20 restore / point-in-time recovery into an isolated environment
    Kind.RESTORE: (
        Step("SELECT_POINT", C, "Backup/PITR point and key availability selected"),
        Step("RESTORE_ISOLATED", C, "Restored into an isolated recovery environment"),
        Step(
            "VERIFY_INTEGRITY", C, "Hashes, DB consistency, evidence chain, erasure still in force"
        ),
        Step("PROBES", C, "Synthetic end-to-end probes passed in the restored environment"),
    ),
}

# Recovery objectives used by the ASSESS gate (DEC-G1 §4; cells.yaml recovery_tiers)
RPO_LIMIT_S = {"T2": 30.0, "T1": 5.0}
MIN_HEADROOM = 0.30  # capacity admission (DEC-B2): one-AZ-loss headroom after promotion


def next_step(kind: Kind, done: int) -> Step | None:
    steps = RUNBOOKS[kind]
    return steps[done] if done < len(steps) else None


def check_step(kind: Kind, done: int, requested: str) -> Step:
    step = next_step(kind, done)
    if step is None:
        raise ResilienceError("RECOVERY_OPERATION_COMPLETE", "Every step is done.")
    if step.name != requested:
        raise ResilienceError(
            "RECOVERY_STEP_OUT_OF_ORDER", f"The next step is {step.name}, not {requested}."
        )
    return step


@dataclass(frozen=True, slots=True)
class Readiness:
    cell_id: str
    replica_lag_s: float | None
    backup_age_s: float | None
    recovery_keys_ready: bool
    one_az_loss_headroom: float | None
    status: str  # cell state: ACTIVE | DEGRADED | DRAINING | QUARANTINED

    def view(self) -> dict[str, Any]:
        return {
            "cell_id": self.cell_id,
            "status": self.status,
            "replica_lag_s": self.replica_lag_s,
            "backup_age_s": self.backup_age_s,
            "recovery_keys_ready": self.recovery_keys_ready,
            "one_az_loss_headroom": self.one_az_loss_headroom,
        }


def assess(
    *,
    target: Readiness | None,
    permitted_targets: tuple[str, ...],
    prohibited: tuple[str, ...],
    target_cell: str,
    rpo_tier: str = "T2",
    needs_replica: bool = True,
) -> list[dict[str, Any]]:
    """The ASSESS / PREPARE gate (RESILIENCE §11, §13.1, §21; RES-10, RES-29). Returns the gate
    report; raises with the first canonical code when a blocking gate fails."""
    report: list[dict[str, Any]] = []

    def gate(gid: str, ok: bool, code: str, detail: str) -> None:
        report.append(
            {
                "gate_id": gid,
                "status": "PASS" if ok else "FAIL",
                "code": None if ok else code,
                "detail": detail,
            }
        )

    gate(
        "residency.target_approved",
        target_cell in permitted_targets and target_cell not in prohibited,
        "RESIDENCY_FAILOVER_BLOCKED",
        f"{target_cell} must be an approved recovery/migration cell of this tenant",
    )
    gate("target.reported", target is not None, "RECOVERY_REPLICA_TOO_STALE", "no readiness report")
    if target is not None:
        gate(
            "target.not_quarantined",
            target.status != "QUARANTINED",
            "CELL_UNAVAILABLE",
            target.status,
        )
        if needs_replica:
            lag = target.replica_lag_s
            gate(
                "replica.lag_within_rpo",
                lag is not None and lag <= RPO_LIMIT_S[rpo_tier],
                "RECOVERY_REPLICA_TOO_STALE",
                f"lag {lag}s vs {RPO_LIMIT_S[rpo_tier]}s",
            )
            gate("keys.recovery_ready", target.recovery_keys_ready, "RECOVERY_KEY_UNAVAILABLE", "")
        head = target.one_az_loss_headroom
        gate(
            "capacity.one_az_loss",
            head is not None and head >= MIN_HEADROOM,
            "CAPACITY_FAILOVER_BLOCKED",
            f"headroom {head} vs {MIN_HEADROOM}",
        )
    failed = [g for g in report if g["status"] == "FAIL"]
    if failed:
        raise GateFailure(str(failed[0]["code"]), report)
    return report


class GateFailure(ResilienceError):
    def __init__(self, code: str, report: list[dict[str, Any]]) -> None:
        super().__init__(
            code, "; ".join(f"{g['gate_id']}: {g['code']}" for g in report if g["status"] == "FAIL")
        )
        self.report = report


def check_probes(
    results: Mapping[str, Any] | None, required: tuple[str, ...]
) -> list[dict[str, Any]]:
    """VALIDATE / PROBES: every required probe reported and passed (no partial credit)."""
    results = results or {}
    report = [
        {
            "gate_id": f"probe.{p}",
            "status": "PASS" if results.get(p) is True else "FAIL",
            "code": None if results.get(p) is True else "RECOVERY_VALIDATION",
            "detail": "",
        }
        for p in required
    ]
    if any(g["status"] == "FAIL" for g in report):
        raise GateFailure("RECOVERY_VALIDATION", report)
    return report


PROBES = {
    "VALIDATE": (
        "authentication",
        "authorization_allow",
        "authorization_deny",
        "mandate",
        "evidence",
        "revocation",
    ),
    "PROBES": ("authentication", "authorization_allow", "authorization_deny", "evidence"),
    "VERIFY_INTEGRITY": ("database_consistency", "evidence_chain", "erasure_preserved"),
}


@dataclass(frozen=True, slots=True)
class Objectives:
    rto_s: float
    rpo_s: float


# Exercise targets per kind (DEC-G1 §4: T1/T2 RTO 5 min; RPO 30 s target; restore T2)
TARGETS = {
    Kind.FAILOVER: Objectives(300.0, 30.0),
    Kind.MIGRATION: Objectives(3600.0, 0.0),
    Kind.FAILBACK: Objectives(3600.0, 0.0),
    Kind.RESTORE: Objectives(4 * 3600.0, 300.0),
}
