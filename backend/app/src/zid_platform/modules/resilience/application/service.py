"""Governed recovery operations, cell state and DR exercises (ZID-RESILIENCE-001 §11-20, §25-30;
ZID-TENANCY-001 §15-16; ZID-SRE-001 §17-18; DEC-G1; G-W7).

Every operation runs the runbook of its kind strictly in order (`runbooks.check_step`). AUTO steps
are the platform's own checks and changes:
- ASSESS / PREPARE: residency-approved target, replica lag within the RPO, recovery keys and
  one-AZ-loss headroom from the target's latest readiness report; a failure BLOCKS the operation
  (a controlled DENY beats split-brain) and is retried after the condition is fixed;
- ADVANCE_EPOCHS: the tenant's placement epoch and its C3 writer epoch move to the target cell in
  one transaction - requests and ledger writes carrying the old epochs are refused from then on;
- ROUTE: cached placement dropped, the directory publisher sends the new record;
- RECONCILE: open mandate reservations become RECONCILIATION_REQUIRED (DR never frees authority).
CONFIRM steps record the operator's evidence reference for an infrastructure action performed
outside the platform; VALIDATE / PROBES / VERIFY_INTEGRITY also need every required probe passed.

Who may run what (SEC-001 §11-13): FAILOVER/FAILBACK need an ACTIVE `platform.dr.execute` grant,
MIGRATION/RESTORE `platform.migration.execute` (both R3, two-person approved), cell state and
exercises `platform.cell.manage`; re-checked on every step, not just at declaration.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_db import in_tenant
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.mandate.ports import advance_writer, quarantine_open_reservations
from zid_platform.modules.operator_access.ports import check_capability
from zid_platform.modules.resilience.domain.runbooks import (
    PROBES,
    RUNBOOKS,
    TARGETS,
    GateFailure,
    Kind,
    OpStatus,
    ResilienceError,
    StepType,
    assess,
    check_probes,
    check_step,
)
from zid_platform.modules.resilience.infrastructure import store
from zid_platform.modules.resilience.infrastructure.store import Operation
from zid_platform.modules.tenant.ports import advance_placement, forget, load_residency

CAPABILITY = {
    Kind.FAILOVER: "platform.dr.execute",
    Kind.FAILBACK: "platform.dr.execute",
    Kind.MIGRATION: "platform.migration.execute",
    Kind.RESTORE: "platform.migration.execute",
}
CELL_CAPABILITY = "platform.cell.manage"

# canonical event names (event-catalogue.yaml; failback/restore failure: proposed, CR-016)
STARTED = {
    Kind.FAILOVER: "tenant.failover.started",
    Kind.MIGRATION: "tenant.migration.started",
    Kind.FAILBACK: "tenant.failback.started",
}
COMPLETED = {
    Kind.FAILOVER: "tenant.failover.completed",
    Kind.MIGRATION: "tenant.migration.completed",
    Kind.FAILBACK: "tenant.failback.completed",
    Kind.RESTORE: "restore.completed",
}
FAILED = {
    Kind.FAILOVER: "tenant.failover.failed",
    Kind.MIGRATION: "tenant.migration.failed",
    Kind.FAILBACK: "tenant.failback.failed",
    Kind.RESTORE: "restore.failed",
}
STEP_EVENTS = {(Kind.MIGRATION, "QUIESCE"): "tenant.migration.quiesced"}
PROBE_STEPS = frozenset(PROBES)


@dataclass(frozen=True, slots=True)
class ResilienceSettings:
    operator_tenant_id: str
    cell_id: str
    cell_uuids: Mapping[str, str] = field(default_factory=dict)

    def cell_uuid(self, cell_id: str) -> uuid.UUID:
        """The cell's id in agent.writer_fence: configured, else derived (stable per cell name)."""
        configured = self.cell_uuids.get(cell_id)
        return (
            uuid.UUID(configured)
            if configured
            else uuid.uuid5(uuid.NAMESPACE_URL, f"zoiko-cell:{cell_id}")
        )


@dataclass(frozen=True, slots=True)
class Actor:
    principal_id: str
    tenant_id: str  # the operator tenant (the connection's RLS scope)


@dataclass(frozen=True, slots=True)
class StepResult:
    operation: Operation
    step: str
    report: list[dict[str, Any]]


def commitment(value: str) -> str:
    """What events carry instead of operator free text (reasons, scenarios): SHA-256, hex."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _resource(op_tenant: str | None, cell: str) -> str:
    return f"tenant:{op_tenant}" if op_tenant else f"cell:{cell}"


async def _require(
    conn: AsyncConnection,
    actor: Actor,
    capability: str,
    tenant: str | None,
    resource: str,
    now: datetime,
) -> str:
    check = await check_capability(
        conn,
        operator_principal_id=actor.principal_id,
        capability_name=capability,
        tenant_id=tenant,
        resource=resource,
        now=now,
    )
    if not check.allowed:
        raise ResilienceError(
            "OPERATOR_CAPABILITY_REQUIRED", f"{capability} on {resource}: {check.reason}"
        )
    return str(check.jit_grant_id)


async def _evidence(
    conn: AsyncConnection,
    name: str,
    *,
    tenant: str,
    aggregate: tuple[str, str, int],
    actor: Actor | None,
    target: tuple[str, str],
    data: dict[str, Any],
    outcome: str = "SUCCESS",
    now: datetime,
) -> None:
    """One event plus its E3 AuditRecord (operational change; ids and codes only). Durations are
    carried as decimal strings: evidence holds no floats (EVIDENCE canonicalization)."""
    data = {k: (f"{v:.3f}" if isinstance(v, float) else v) for k, v in data.items()}
    event_id = await emit(
        conn,
        name=name,
        tenant_id=tenant,
        aggregate_type=aggregate[0],
        aggregate_id=aggregate[1],
        aggregate_version=aggregate[2],
        data=data,
        classification="RESTRICTED",
    )
    await record_audit(
        conn,
        tenant,
        AuditFacts(
            event_type=name,
            actor_type="OPERATOR" if actor else "SYSTEM",
            action=name.rsplit(".", 1)[-1],
            target_type=target[0],
            target_ref=target[1],
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor.principal_id if actor else None,
            source_service="resilience",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, str | int | bool | type(None))},
        ),
    )


def _op_data(op: Operation, **extra: Any) -> dict[str, Any]:
    return {
        "operation_id": op.operation_id,
        "tenant_id": op.tenant_id,
        "kind": op.kind.value,
        "source_cell_id": op.source_cell_id,
        "target_cell_id": op.target_cell_id,
        "status": op.status.value,
        "steps_done": op.steps_done,
        **extra,
    }


async def _op_event(
    conn: AsyncConnection,
    name: str,
    op: Operation,
    version: int,
    actor: Actor | None,
    now: datetime,
    **extra: Any,
) -> None:
    """An operation event, recorded in the moved tenant's scope (a cell restore: the operator's)."""
    tenant = op.tenant_id or (actor.tenant_id if actor else "")
    scope = in_tenant(conn, op.tenant_id) if op.tenant_id else nullcontext(conn)
    async with scope:
        await _evidence(
            conn,
            name,
            tenant=tenant,
            aggregate=("recovery_operation", op.operation_id, version),
            actor=actor,
            target=("recovery_operation", op.operation_id),
            data=_op_data(op, **extra),
            outcome="FAILURE" if name.endswith(".failed") else "SUCCESS",
            now=now,
        )


# ---------------------------------------------------------------------------------- declare


async def declare(
    conn: AsyncConnection,
    s: ResilienceSettings,
    actor: Actor,
    *,
    kind: Kind,
    tenant_id: str | None,
    target_cell_id: str,
    now: datetime,
) -> Operation:
    """Open one operation. A tenant operation starts from the tenant's current home cell; a
    RESTORE without a tenant restores the cell itself into an isolated environment."""
    if kind is not Kind.RESTORE and not tenant_id:
        raise ResilienceError("RECOVERY_TENANT_REQUIRED", f"A {kind.value} moves one tenant.")
    jit = await _require(
        conn, actor, CAPABILITY[kind], tenant_id, _resource(tenant_id, target_cell_id), now
    )
    epoch: int | None = None
    source = target_cell_id
    if tenant_id:
        async with in_tenant(conn, tenant_id):
            residency = await load_residency(conn, tenant_id)
        if residency is None:
            raise ResilienceError(
                "RESIDENCY_PLACEMENT_REQUIRED", "The tenant has no placement decision."
            )
        source, epoch = residency.home_cell_id, residency.placement_epoch
        if kind is not Kind.RESTORE and source == target_cell_id:
            raise ResilienceError(
                "RECOVERY_TARGET_INVALID", "The target is already the tenant's home cell."
            )
    op = await store.insert_operation(
        conn,
        {
            "tenant": tenant_id,
            "kind": kind.value,
            "source": source,
            "target": target_cell_id,
            "actor": actor.principal_id,
            "jit": jit,
            "epoch": epoch,
        },
    )
    if kind in STARTED:
        await _op_event(conn, STARTED[kind], op, 1, actor, now, jit_grant_id=jit)
    else:
        await _evidence(
            conn,
            "recovery.started",
            tenant=actor.tenant_id,
            aggregate=("recovery_operation", op.operation_id, 1),
            actor=actor,
            target=("recovery_operation", op.operation_id),
            data=_op_data(op, jit_grant_id=jit),
            now=now,
        )
    return op


# ---------------------------------------------------------------------------------- advance


async def _auto(
    conn: AsyncConnection,
    s: ResilienceSettings,
    actor: Actor,
    op: Operation,
    step: str,
    now: datetime,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Run one AUTO step. Returns (gate report, operation field changes)."""
    tenant = op.tenant_id
    assert tenant is not None  # RESTORE has no AUTO steps
    if step in ("ASSESS", "PREPARE"):
        async with in_tenant(conn, tenant):
            residency = await load_residency(conn, tenant)
        if residency is None:
            raise ResilienceError(
                "RESIDENCY_PLACEMENT_REQUIRED", "The tenant has no placement decision."
            )
        # a failover goes only to a pre-approved recovery cell; a planned move to any cell the
        # tenant's residency does not prohibit (the transfer gateway re-checks each data class)
        permitted = (
            residency.permitted_recovery_cells if op.kind is Kind.FAILOVER else (op.target_cell_id,)
        )
        report = assess(
            target=await store.readiness(conn, op.target_cell_id),
            permitted_targets=permitted,
            prohibited=residency.prohibited_cells,
            target_cell=op.target_cell_id,
            needs_replica=op.kind
            is not Kind.MIGRATION,  # a migration copies; DR promotes a replica
        )
        return report, {}
    if step == "ADVANCE_EPOCHS":
        async with in_tenant(conn, tenant):
            epoch = await advance_placement(
                conn, tenant, new_home=op.target_cell_id, actor=actor.principal_id
            )
            writer = await advance_writer(conn, tenant, s.cell_uuid(op.target_cell_id))
            data = {
                "tenant_id": tenant,
                "operation_id": op.operation_id,
                "previous_cell_id": op.source_cell_id,
                "home_cell_id": op.target_cell_id,
                "placement_epoch": epoch,
            }
            await _evidence(
                conn,
                "tenant.placement.changed",
                tenant=tenant,
                aggregate=("tenant_placement", tenant, epoch),
                actor=actor,
                target=("tenant", tenant),
                data=data,
                now=now,
            )
            await _evidence(
                conn,
                "writer.epoch.changed",
                tenant=tenant,
                aggregate=("writer_fence", tenant, writer),
                actor=actor,
                target=("tenant", tenant),
                data={
                    "tenant_id": tenant,
                    "operation_id": op.operation_id,
                    "cell_id": op.target_cell_id,
                    "writer_epoch": writer,
                },
                now=now,
            )
            if op.kind is Kind.MIGRATION:
                await _evidence(
                    conn,
                    "tenant.migration.cutover",
                    tenant=tenant,
                    aggregate=("recovery_operation", op.operation_id, 1000 + epoch),
                    actor=actor,
                    target=("recovery_operation", op.operation_id),
                    data=_op_data(op, placement_epoch=epoch),
                    now=now,
                )
        forget(tenant)
        return [
            {
                "gate_id": "epochs.advanced",
                "status": "PASS",
                "code": None,
                "detail": f"placement {epoch}, writer {writer}",
            }
        ], {"epoch_after": epoch}
    if step == "ROUTE":
        forget(tenant)  # this cell stops serving the old placement; the directory pass republishes
        return [
            {
                "gate_id": "routing.switched",
                "status": "PASS",
                "code": None,
                "detail": op.target_cell_id,
            }
        ], {}
    if step == "RECONCILE":
        async with in_tenant(conn, tenant):
            n = await quarantine_open_reservations(conn)
            if n:
                await _evidence(
                    conn,
                    "mandate.reconciliation.required",
                    tenant=tenant,
                    aggregate=("recovery_operation", op.operation_id, 2000),
                    actor=actor,
                    target=("tenant", tenant),
                    data={"tenant_id": tenant, "operation_id": op.operation_id, "reservations": n},
                    now=now,
                )
        return [
            {
                "gate_id": "reservations.quarantined",
                "status": "PASS",
                "code": None,
                "detail": str(n),
            }
        ], {}
    raise AssertionError(step)  # pragma: no cover - every AUTO step is handled above


async def advance(
    conn: AsyncConnection,
    s: ResilienceSettings,
    actor: Actor,
    operation_id: str,
    *,
    step: str,
    evidence_ref: str | None,
    probes: Mapping[str, Any] | None,
    now: datetime,
) -> StepResult:
    """Run the next step. A failed gate does not raise: the operation is left BLOCKED with the
    canonical code and the gate report (both committed), so the API answers 409 after commit."""
    op = await store.load_operation(conn, operation_id, lock=True)
    if op is None:
        raise ResilienceError("RECOVERY_OPERATION_NOT_FOUND", "No such recovery operation.")
    if op.status not in (OpStatus.OPEN, OpStatus.BLOCKED):
        raise ResilienceError("RECOVERY_OPERATION_CLOSED", f"The operation is {op.status.value}.")
    await _require(
        conn,
        actor,
        CAPABILITY[op.kind],
        op.tenant_id,
        _resource(op.tenant_id, op.target_cell_id),
        now,
    )
    spec = check_step(op.kind, op.steps_done, step)
    changes: dict[str, Any] = {}
    try:
        if spec.type is StepType.CONFIRM:
            if not evidence_ref:
                raise ResilienceError(
                    "RECOVERY_EVIDENCE_REQUIRED",
                    f"{step} needs the reference of the evidence it was done.",
                )
            report = check_probes(probes, PROBES[step]) if step in PROBE_STEPS else []
        else:
            report, changes = await _auto(conn, s, actor, op, step, now)
    except GateFailure as e:
        seq = await store.log_step(
            conn,
            op.operation_id,
            step=step,
            outcome="BLOCKED",
            actor=actor.principal_id,
            evidence_ref=evidence_ref,
            report=e.report,
        )
        op = await store.update_operation(
            conn, op.operation_id, status=OpStatus.BLOCKED.value, blocked_code=e.code
        )
        await _op_event(
            conn, FAILED[op.kind], op, seq + 1, actor, now, step=step, reason_code=e.code
        )
        return StepResult(op, step, e.report)

    seq = await store.log_step(
        conn,
        op.operation_id,
        step=step,
        outcome="DONE",
        actor=actor.principal_id,
        evidence_ref=evidence_ref,
        report=report,
    )
    done = op.steps_done + 1
    fields: dict[str, Any] = {
        "steps_done": done,
        "status": OpStatus.OPEN.value,
        "blocked_code": None,
        **changes,
    }
    finished = done == len(RUNBOOKS[op.kind])
    if finished:
        rto = (now - op.declared_at).total_seconds()
        fields.update(status=OpStatus.COMPLETED.value, completed_at=now, observed_rto_s=rto)
    op = await store.update_operation(conn, op.operation_id, **fields)
    if (op.kind, step) in STEP_EVENTS:
        await _op_event(conn, STEP_EVENTS[(op.kind, step)], op, seq + 1, actor, now)
    if finished:
        await _op_event(
            conn, COMPLETED[op.kind], op, seq + 1, actor, now, observed_rto_s=op.observed_rto_s
        )
        target = TARGETS[op.kind]
        if op.observed_rto_s is not None and op.observed_rto_s > target.rto_s:
            await _slo_breach(
                conn,
                actor,
                op.operation_id,
                op.kind.value,
                op.target_cell_id,
                observed=op.observed_rto_s,
                target=target.rto_s,
                objective="RTO",
                now=now,
            )
    return StepResult(op, step, report)


async def abort(
    conn: AsyncConnection,
    s: ResilienceSettings,
    actor: Actor,
    operation_id: str,
    *,
    reason: str,
    now: datetime,
) -> Operation:
    """Abandon an operation before cutover. After ADVANCE_EPOCHS the new writer is authoritative:
    the old epochs are invalid forever, so the only way back is a governed FAILBACK."""
    op = await store.load_operation(conn, operation_id, lock=True)
    if op is None:
        raise ResilienceError("RECOVERY_OPERATION_NOT_FOUND", "No such recovery operation.")
    if op.status not in (OpStatus.OPEN, OpStatus.BLOCKED):
        raise ResilienceError("RECOVERY_OPERATION_CLOSED", f"The operation is {op.status.value}.")
    await _require(
        conn,
        actor,
        CAPABILITY[op.kind],
        op.tenant_id,
        _resource(op.tenant_id, op.target_cell_id),
        now,
    )
    if op.epoch_after is not None:
        raise ResilienceError(
            "RECOVERY_ABORT_AFTER_CUTOVER",
            "The epochs already advanced; complete it or run a failback.",
        )
    seq = await store.log_step(
        conn,
        op.operation_id,
        step="ABORT",
        outcome="ABORTED",
        actor=actor.principal_id,
        evidence_ref=None,
        report=[],
    )
    op = await store.update_operation(
        conn, op.operation_id, status=OpStatus.ABORTED.value, blocked_code=None, completed_at=now
    )
    await _op_event(
        conn,
        FAILED[op.kind],
        op,
        seq + 1,
        actor,
        now,
        reason_code="ABORTED",
        reason_commitment=commitment(reason),
    )
    return op


# ----------------------------------------------------------------------- cells and exercises


async def set_cell_status(
    conn: AsyncConnection, actor: Actor, cell_id: str, status: str, *, reason: str, now: datetime
) -> str:
    """QUARANTINED: the cell accepts no new tenant placement (tenant provisioning refuses it) and is
    no ASSESS target. Existing tenants move only through a declared failover."""
    await _require(conn, actor, CELL_CAPABILITY, None, f"cell:{cell_id}", now)
    previous = await store.cell_status(conn, cell_id)
    await store.set_cell_status(conn, cell_id, status, reason=reason, actor=actor.principal_id)
    name = "cell.quarantined" if status == "QUARANTINED" else "cell.status.changed"
    await _evidence(
        conn,
        name,
        tenant=actor.tenant_id,
        aggregate=("cell_state", str(uuid7()), 1),
        actor=actor,
        target=("cell", cell_id),
        data={
            "cell_id": cell_id,
            "previous_status": previous,
            "status": status,
            "reason_commitment": commitment(reason),
        },
        now=now,
    )
    return previous


async def report_readiness(
    conn: AsyncConnection, actor: Actor, cell_id: str, values: Mapping[str, Any], now: datetime
) -> None:
    await _require(conn, actor, CELL_CAPABILITY, None, f"cell:{cell_id}", now)
    await store.report_readiness(conn, cell_id, values)


async def _slo_breach(
    conn: AsyncConnection,
    actor: Actor,
    ref: str,
    kind: str,
    cell_id: str,
    *,
    observed: float,
    target: float,
    objective: str,
    now: datetime,
) -> None:
    await _evidence(
        conn,
        "resilience.slo.breached",
        tenant=actor.tenant_id,
        aggregate=("resilience_objective", str(uuid7()), 1),
        actor=actor,
        target=("cell", cell_id),
        data={
            "reference_id": ref,
            "kind": kind,
            "cell_id": cell_id,
            "objective": objective,
            "observed_s": observed,
            "target_s": target,
        },
        outcome="FAILURE",
        now=now,
    )


async def record_exercise(
    conn: AsyncConnection,
    actor: Actor,
    *,
    kind: str,
    cell_id: str,
    scenario: str,
    operation_id: str | None,
    observed_rto_s: float | None,
    observed_rpo_s: float | None,
    findings: list[str],
    started_at: datetime,
    completed_at: datetime,
    now: datetime,
) -> dict[str, Any]:
    """A DR exercise result (RES-30; SRE-20). Pass/fail is computed against the recovery targets,
    never declared; a missed target raises `resilience.slo.breached`."""
    await _require(conn, actor, CELL_CAPABILITY, None, f"cell:{cell_id}", now)
    target = TARGETS.get(Kind(kind)) if kind in Kind.__members__ else None
    passed = not findings and (
        target is None
        or (
            (observed_rto_s is None or observed_rto_s <= target.rto_s)
            and (observed_rpo_s is None or observed_rpo_s <= target.rpo_s)
        )
    )
    eid = await store.insert_exercise(
        conn,
        {
            "kind": kind,
            "cell": cell_id,
            "scenario": scenario,
            "op": uuid.UUID(operation_id) if operation_id else None,
            "rto": observed_rto_s,
            "rpo": observed_rpo_s,
            "trto": target.rto_s if target else None,
            "trpo": target.rpo_s if target else None,
            "passed": passed,
            "findings": findings,
            "by": uuid.UUID(actor.principal_id),
            "start": started_at,
            "end": completed_at,
        },
    )
    await _evidence(
        conn,
        "dr.exercise.completed",
        tenant=actor.tenant_id,
        aggregate=("dr_exercise", eid, 1),
        actor=actor,
        target=("dr_exercise", eid),
        data={
            "exercise_id": eid,
            "kind": kind,
            "cell_id": cell_id,
            "scenario_commitment": commitment(scenario),
            "passed": passed,
            "observed_rto_s": observed_rto_s,
            "observed_rpo_s": observed_rpo_s,
            "findings": len(findings),
        },
        outcome="SUCCESS" if passed else "FAILURE",
        now=now,
    )
    if target is not None:
        for objective, observed, limit in (
            ("RTO", observed_rto_s, target.rto_s),
            ("RPO", observed_rpo_s, target.rpo_s),
        ):
            if observed is not None and observed > limit:
                await _slo_breach(
                    conn,
                    actor,
                    eid,
                    kind,
                    cell_id,
                    observed=observed,
                    target=limit,
                    objective=objective,
                    now=now,
                )
    return {
        "exercise_id": eid,
        "passed": passed,
        "target_rto_s": target.rto_s if target else None,
        "target_rpo_s": target.rpo_s if target else None,
    }
