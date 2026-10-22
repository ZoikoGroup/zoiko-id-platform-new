"""Incident lifecycle, readiness reviews, change freezes and the SLO / change-policy views
(ZID-SRE-001 §5-6, §15-17, §23-24, §26, §29-30; G-W8).

Who may do what (all behind the operator gate, AAL-Z3, no delegation):
- declare, update, act on, resolve and close incidents; record a PRR; FREEZE changes: any
  registered, ACTIVE operator in a NORMAL security state - declaring and protective actions are
  never delayed by an approval (§16.1);
- LIFT a freeze: an ACTIVE `platform.change.manage` JIT grant (R2, independently approved),
  because unfreezing re-opens risk.
Every change is an event plus an E2 AuditRecord in the operator tenant's partition.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit
from zid_telemetry.slo import (
    AlertClass,
    BudgetState,
    ChangeClass,
    ChangeDecision,
    WindowedSli,
    alert_class,
    budget_remaining,
    budget_state,
    change_decision,
    evaluate,
)

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.operations.domain.catalogue import Catalogue, Service
from zid_platform.modules.operations.domain.incidents import (
    ROLES,
    GateResult,
    IncidentStatus,
    OperationsError,
    Severity,
    check_close,
    check_gates,
    check_move,
    check_resolve,
    readiness_verdict,
)
from zid_platform.modules.operations.infrastructure import store
from zid_platform.modules.operations.infrastructure.store import Incident
from zid_platform.modules.operator_access.ports import check_capability, is_active_operator

UNFREEZE_CAPABILITY = "platform.change.manage"


@dataclass(frozen=True, slots=True)
class Actor:
    principal_id: str
    tenant_id: str  # the operator tenant


def commitment(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def _responder(conn: AsyncConnection, actor: Actor) -> None:
    if not await is_active_operator(conn, actor.tenant_id, actor.principal_id):
        raise OperationsError(
            "OPERATOR_PROFILE_MISSING", "Only a registered, active operator may do this."
        )


async def _record(
    conn: AsyncConnection,
    actor: Actor,
    name: str,
    *,
    aggregate: tuple[str, str, int],
    target: tuple[str, str],
    data: dict[str, Any],
    now: datetime,
    outcome: str = "SUCCESS",
) -> None:
    event_id = await emit(
        conn,
        name=name,
        tenant_id=actor.tenant_id,
        aggregate_type=aggregate[0],
        aggregate_id=aggregate[1],
        aggregate_version=aggregate[2],
        data=data,
        classification="RESTRICTED",
    )
    await record_audit(
        conn,
        actor.tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="OPERATOR",
            action=name.rsplit(".", 1)[-1],
            target_type=target[0],
            target_ref=target[1],
            outcome=outcome,
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor.principal_id,
            source_service="operations",
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, str | int | bool | type(None))},
        ),
    )


def _inc_data(i: Incident, **extra: Any) -> dict[str, Any]:
    return {
        "incident_id": i.incident_id,
        "severity": i.severity.value,
        "status": i.status.value,
        "security": i.security,
        "services": ",".join(i.services),
        "cells": ",".join(i.cells),
        **extra,
    }


def _check_roles(roles: Mapping[str, str]) -> dict[str, str]:
    bad = [r for r in roles if r not in ROLES]
    if bad:
        raise OperationsError("INCIDENT_ROLE_UNKNOWN", ", ".join(bad))
    return {str(k): str(v) for k, v in roles.items()}


# ---------------------------------------------------------------------------------- incidents


async def declare(
    conn: AsyncConnection,
    cat: Catalogue,
    actor: Actor,
    *,
    title: str,
    severity: Severity,
    security: bool,
    services: Sequence[str],
    cells: Sequence[str],
    roles: Mapping[str, str],
    now: datetime,
) -> Incident:
    """Any qualified responder, immediately; the declarer is incident commander unless named."""
    await _responder(conn, actor)
    unknown = [s for s in services if cat.service(s) is None]
    if unknown:
        raise OperationsError("SERVICE_UNKNOWN", ", ".join(unknown))
    security = security or severity is Severity.SEV0  # a SEV0 is a security/integrity event (§15)
    assigned = {"INCIDENT_COMMANDER": actor.principal_id, **_check_roles(roles)}
    inc = await store.insert_incident(
        conn,
        {
            "title": title,
            "severity": severity.value,
            "security": security,
            "services": services,
            "cells": cells,
            "roles": assigned,
            "actor": actor.principal_id,
        },
    )
    await store.append_entry(
        conn,
        inc.incident_id,
        kind="DECLARED",
        actor=actor.principal_id,
        note=title,
        data={"severity": severity.value, "roles": assigned},
    )
    await _record(
        conn,
        actor,
        "ops.incident.declared",
        aggregate=("incident", inc.incident_id, inc.version),
        target=("incident", inc.incident_id),
        data=_inc_data(inc),
        now=now,
    )
    return inc


async def _open(conn: AsyncConnection, incident_id: str) -> Incident:
    inc = await store.load_incident(conn, incident_id, lock=True)
    if inc is None:
        raise OperationsError("INCIDENT_NOT_FOUND", "No such incident.")
    if inc.status is IncidentStatus.CLOSED:
        raise OperationsError("INCIDENT_CLOSED", "The incident is closed.")
    return inc


async def update(
    conn: AsyncConnection,
    actor: Actor,
    incident_id: str,
    *,
    severity: Severity | None,
    status: IncidentStatus | None,
    roles: Mapping[str, str] | None,
    reason: str,
    now: datetime,
) -> Incident:
    """Severity up or down, status, role assignments: each change a timeline entry (§16.1)."""
    await _responder(conn, actor)
    inc = await _open(conn, incident_id)
    if inc.status is IncidentStatus.RESOLVED and status is not None:
        raise OperationsError("INCIDENT_INVALID_TRANSITION", "A resolved incident only closes.")
    fields: dict[str, Any] = {}
    if severity is not None and severity is not inc.severity:
        fields["severity"] = severity.value
        if severity is Severity.SEV0:
            fields["security"] = True
        await store.append_entry(
            conn,
            incident_id,
            kind="SEVERITY",
            actor=actor.principal_id,
            note=reason,
            data={"from": inc.severity.value, "to": severity.value},
        )
    if status is not None and status is not inc.status:
        check_move(inc.status, status)
        fields["status"] = status.value
        if status is IncidentStatus.RECOVERY_VALIDATION and inc.mitigated_at is None:
            fields["mitigated_at"] = now
        await store.append_entry(
            conn,
            incident_id,
            kind="STATUS",
            actor=actor.principal_id,
            note=reason,
            data={"from": inc.status.value, "to": status.value},
        )
    if roles:
        merged = {**inc.roles, **_check_roles(roles)}
        fields["roles"] = merged
        await store.append_entry(
            conn, incident_id, kind="ROLE", actor=actor.principal_id, note=reason, data=dict(roles)
        )
    if not fields:
        return inc
    new = await store.update_incident(conn, incident_id, **fields)
    if "severity" in fields:
        await _record(
            conn,
            actor,
            "ops.incident.severity_changed",
            aggregate=("incident", incident_id, new.version),
            target=("incident", incident_id),
            data=_inc_data(
                new, previous_severity=inc.severity.value, reason_commitment=commitment(reason)
            ),
            now=now,
        )
    else:
        await _record(
            conn,
            actor,
            "ops.incident.updated",
            aggregate=("incident", incident_id, new.version),
            target=("incident", incident_id),
            data=_inc_data(new),
            now=now,
        )
    return new


async def add_entry(
    conn: AsyncConnection,
    actor: Actor,
    incident_id: str,
    *,
    kind: str,
    note: str,
    data: Mapping[str, Any],
) -> int:
    """ACTION / DECISION / COMMUNICATION: the timestamped decision timeline (§17, SRE-25)."""
    if kind not in ("ACTION", "DECISION", "COMMUNICATION"):
        raise OperationsError("INCIDENT_ENTRY_KIND", kind)
    await _responder(conn, actor)
    await _open(conn, incident_id)
    return await store.append_entry(
        conn, incident_id, kind=kind, actor=actor.principal_id, note=note, data=data
    )


async def resolve(
    conn: AsyncConnection, actor: Actor, incident_id: str, *, validation: str, now: datetime
) -> Incident:
    await _responder(conn, actor)
    inc = await _open(conn, incident_id)
    check_resolve(inc.status, inc.severity, inc.security, inc.roles, validation)
    await store.append_entry(
        conn, incident_id, kind="RESOLVED", actor=actor.principal_id, note=validation, data={}
    )
    new = await store.update_incident(
        conn,
        incident_id,
        status=IncidentStatus.RESOLVED.value,
        resolved_at=now,
        mitigated_at=inc.mitigated_at or now,
    )
    await _record(
        conn,
        actor,
        "ops.incident.resolved",
        aggregate=("incident", incident_id, new.version),
        target=("incident", incident_id),
        data=_inc_data(new, duration_s=int((now - inc.declared_at).total_seconds())),
        now=now,
    )
    return new


async def add_action_item(
    conn: AsyncConnection,
    actor: Actor,
    incident_id: str,
    *,
    title: str,
    owner: str | None,
    due: date | None,
) -> str:
    await _responder(conn, actor)
    await _open(conn, incident_id)
    return await store.add_action_item(conn, incident_id, title=title, owner=owner, due=due)


async def verify_action_item(
    conn: AsyncConnection, actor: Actor, incident_id: str, item_id: str
) -> None:
    await _responder(conn, actor)
    for iid, item in await store.action_items(conn, incident_id):
        if iid == item_id and item.owner == actor.principal_id:
            raise OperationsError(
                "PIR_VERIFICATION_NOT_INDEPENDENT", "An item is never verified by its owner."
            )
    await store.verify_action_item(conn, incident_id, item_id, actor.principal_id)


async def close(
    conn: AsyncConnection, actor: Actor, incident_id: str, *, pir_ref: str | None, now: datetime
) -> Incident:
    await _responder(conn, actor)
    inc = await _open(conn, incident_id)
    items = [i for _, i in await store.action_items(conn, incident_id)]
    check_close(inc.status, inc.severity, pir_ref, items)
    await store.append_entry(
        conn,
        incident_id,
        kind="CLOSED",
        actor=actor.principal_id,
        note=pir_ref or "closed",
        data={"action_items": len(items)},
    )
    new = await store.update_incident(
        conn, incident_id, status=IncidentStatus.CLOSED.value, closed_at=now, pir_ref=pir_ref
    )
    await _record(
        conn,
        actor,
        "ops.incident.closed",
        aggregate=("incident", incident_id, new.version),
        target=("incident", incident_id),
        data=_inc_data(new, action_items=len(items)),
        now=now,
    )
    return new


# -------------------------------------------------------------------- readiness and freezes


async def record_review(
    conn: AsyncConnection,
    cat: Catalogue,
    actor: Actor,
    *,
    service_id: str,
    release_ref: str,
    gates: Sequence[GateResult],
    now: datetime,
) -> dict[str, Any]:
    await _responder(conn, actor)
    if cat.service(service_id) is None:
        raise OperationsError("SERVICE_UNKNOWN", service_id)
    checked = check_gates(gates)
    ready, blocking = readiness_verdict(checked, now.date())
    rid = await store.insert_review(
        conn,
        service_id=service_id,
        release_ref=release_ref,
        ready=ready,
        actor=actor.principal_id,
        gates=checked,
    )
    await _record(
        conn,
        actor,
        "ops.readiness_review.recorded",
        aggregate=("readiness_review", rid, 1),
        target=("service", service_id),
        data={
            "review_id": rid,
            "service_id": service_id,
            "release_ref": release_ref,
            "production_ready": ready,
            "blocking_gates": len(blocking),
        },
        outcome="SUCCESS" if ready else "PARTIAL",
        now=now,
    )
    return {
        "review_id": rid,
        "service_id": service_id,
        "production_ready": ready,
        "blocking": blocking,
    }


async def readiness(conn: AsyncConnection, service_id: str, today: date) -> dict[str, Any]:
    latest = await store.latest_review(conn, service_id)
    if latest is None:
        return {
            "service_id": service_id,
            "production_ready": False,
            "review_id": None,
            "blocking": [{"gate": "*", "reason": "NOT_REVIEWED"}],
        }
    row, gates = latest
    ready, blocking = readiness_verdict(gates, today)  # re-evaluated: accepted risks expire
    return {
        "service_id": service_id,
        "review_id": str(row["review_id"]),
        "release_ref": row["release_ref"],
        "production_ready": ready,
        "blocking": blocking,
        "recorded_at": row["recorded_at"].isoformat(),
    }


async def freeze(
    conn: AsyncConnection,
    cat: Catalogue,
    actor: Actor,
    service_id: str,
    *,
    reason: str,
    incident_id: str | None,
    now: datetime,
) -> str:
    await _responder(conn, actor)
    if cat.service(service_id) is None:
        raise OperationsError("SERVICE_UNKNOWN", service_id)
    fid = await store.freeze(
        conn, service_id, reason=reason, incident_id=incident_id, actor=actor.principal_id
    )
    await _record(
        conn,
        actor,
        "ops.change_freeze.applied",
        aggregate=("change_freeze", fid, 1),
        target=("service", service_id),
        data={
            "freeze_id": fid,
            "service_id": service_id,
            "reason_code": reason,
            "incident_id": incident_id,
        },
        now=now,
    )
    return fid


async def unfreeze(conn: AsyncConnection, actor: Actor, service_id: str, *, now: datetime) -> str:
    check = await check_capability(
        conn,
        operator_principal_id=actor.principal_id,
        capability_name=UNFREEZE_CAPABILITY,
        tenant_id=None,
        resource=f"service:{service_id}",
        now=now,
    )
    if not check.allowed:
        raise OperationsError(
            "OPERATOR_CAPABILITY_REQUIRED", f"{UNFREEZE_CAPABILITY}: {check.reason}"
        )
    fid = await store.lift(
        conn, service_id, actor=actor.principal_id, grant=str(check.jit_grant_id)
    )
    if fid is None:
        raise OperationsError("CHANGE_NOT_FROZEN", "The service has no active freeze.")
    await _record(
        conn,
        actor,
        "ops.change_freeze.lifted",
        aggregate=("change_freeze", fid, 2),
        target=("service", service_id),
        data={"freeze_id": fid, "service_id": service_id, "jit_grant_id": check.jit_grant_id},
        now=now,
    )
    return fid


# --------------------------------------------------------------------- SLOs and change policy


def slo_view(svc: Service, slis: Mapping[str, Mapping[str, WindowedSli]]) -> list[dict[str, Any]]:
    """In-process burn rates of this process's windows (production alerting evaluates the same
    windows fleet-wide in Prometheus)."""
    out = []
    for slo in svc.slos:
        sli = slis.get(slo.sli, {}).get(svc.service_id)
        if sli is None:
            out.append(
                {"slo_id": slo.slo_id, "sli": slo.sli, "target": slo.target, "state": "UNKNOWN"}
            )
            continue
        windows = evaluate(sli.counts, slo.target)
        bad, total = sli.counts(3 * 86400)
        remaining = budget_remaining(bad, total, slo.target)
        out.append(
            {
                "slo_id": slo.slo_id,
                "sli": slo.sli,
                "target": slo.target,
                "events": int(total),
                "good_ratio": None if total == 0 else round(1 - bad / total, 6),
                "budget_remaining": round(remaining, 4),
                "budget_state": budget_state(remaining).value,
                "alert": alert_class(windows).value,
                "windows": [
                    {
                        "window": w.window,
                        "long_burn": round(w.long_burn, 3),
                        "short_burn": round(w.short_burn, 3),
                        "factor": w.factor,
                        "class": w.alert.value,
                        "firing": w.firing,
                    }
                    for w in windows
                ],
            }
        )
    return out


def worst_state(slos: list[dict[str, Any]]) -> BudgetState:
    order = list(BudgetState)
    states = [BudgetState(s["budget_state"]) for s in slos if "budget_state" in s]
    return max(states, key=order.index) if states else BudgetState.NORMAL


def policy(slos: list[dict[str, Any]], change: ChangeClass, frozen: bool) -> ChangeDecision:
    return change_decision(worst_state(slos), change, frozen=frozen)


def paging(slos: list[dict[str, Any]]) -> tuple[bool, bool]:
    return (
        any(s.get("alert") == AlertClass.PAGE.value for s in slos),
        any(s.get("alert") == AlertClass.TICKET.value for s in slos),
    )
