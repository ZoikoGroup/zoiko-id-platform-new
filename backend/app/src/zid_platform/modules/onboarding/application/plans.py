"""Onboarding plan use cases: versions, approval, the stage moves and rollback (ZID-OVERLAY-001
§17-20, §25, §29; OVL-19..22; G-W4).

Every stage move re-evaluates its gates in the same transaction and records the machine-readable
report on the tenant's lifecycle transition; there is no "launch anyway". Entering PILOT and ACTIVE
and activating a pilot stage need an approval by a second administrator; rollback is the safer
direction and needs none, and a rollback executed during PILOT is the plan's rollback-tested proof
(§29 "Tested restoration to prior safe stage") that ACTIVE requires.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.integration.ports import active_application_count
from zid_platform.modules.onboarding.application.gates import configuring_gates
from zid_platform.modules.onboarding.domain.gates import (
    Gate,
    GateStatus,
    OnboardingError,
    require_pass,
)
from zid_platform.modules.onboarding.domain.plan import (
    ExitCriteria,
    PlanStatus,
    Scope,
    StageStatus,
    shadow_gates,
)
from zid_platform.modules.onboarding.infrastructure import store
from zid_platform.modules.onboarding.infrastructure.store import Plan, Stage
from zid_platform.modules.tenant.ports import (
    Move,
    TenantError,
    TenantStatus,
    load_tenant,
    set_fields,
    transition,
)

S = TenantStatus
_MODES = ("O0", "O1", "O2", "O3")


def _now() -> datetime:
    return datetime.now(UTC)


async def _record(
    conn: AsyncConnection,
    tenant_id: str,
    name: str,
    *,
    target: tuple[str, str],
    actor: str,
    data: dict[str, Any],
    evidence: EvidenceClass = EvidenceClass.E2,
    approvals: Sequence[str] = (),
) -> None:
    payload = {**data, "evidence_class": evidence.value}
    event_id = await emit(
        conn,
        name=name,
        tenant_id=tenant_id,
        aggregate_type=target[0],
        aggregate_id=target[1],
        aggregate_version=int(_now().timestamp() * 1_000_000),
        data=payload,
    )
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=name,
            actor_type="HUMAN",
            action=name.rsplit(".", 1)[-1],
            target_type=target[0],
            target_ref=target[1],
            outcome="SUCCESS",
            evidence_class=evidence,
            occurred_at=_now(),
            actor_principal_id=actor,
            source_service="onboarding",
            approval_refs=tuple(approvals),
            source_event_id=event_id,
            details={k: v for k, v in data.items() if isinstance(v, str | int | bool | type(None))},
        ),
    )


def plan_data(p: Plan) -> dict[str, Any]:
    return {
        "plan_id": p.plan_id,
        "version": p.version,
        "status": p.status.value,
        "target_mode": p.target_mode,
    }


async def create_plan(
    conn: AsyncConnection, tenant_id: str, body: Mapping[str, Any], *, author: str
) -> Plan:
    if body.get("target_mode") not in _MODES:
        raise OnboardingError("VALIDATION_FAILED", "target_mode is O0..O3.")
    Scope.parse(body.get("scope") or {})
    criteria = ExitCriteria.parse(body.get("exit_criteria"))
    tenant = await load_tenant(conn, tenant_id)
    if tenant is None:
        raise OnboardingError("TENANT_NOT_FOUND")
    if tenant.status in (S.SUSPENDED, S.OFFBOARDING, S.CLOSED):
        raise OnboardingError("TENANT_STATE_INVALID", f"No plans while {tenant.status}.")
    p = await store.insert_plan(
        conn,
        tenant_id,
        {**body, "exit_criteria": criteria.view(), "author": author},
    )
    await _record(
        conn,
        tenant_id,
        "onboarding.plan.changed",
        target=("onboarding_plan", p.plan_id),
        actor=author,
        data=plan_data(p),
    )
    return p


async def approve_plan(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str, approver: str
) -> Plan:
    p = await store.load_plan(conn, plan_id, lock=True)
    if p is None:
        raise OnboardingError("ONBOARDING_PLAN_NOT_FOUND")
    if p.status is not PlanStatus.DRAFT:
        raise OnboardingError("ONBOARDING_PLAN_STATE_INVALID", f"The plan is {p.status}.")
    if approver == p.author:
        raise OnboardingError("APPROVER_IS_AUTHOR", "The plan author cannot approve it.")
    p = await store.approve_plan(conn, plan_id, approver, _now())
    await set_fields(conn, tenant_id, onboarding_plan_id=plan_id)
    await _record(
        conn,
        tenant_id,
        "onboarding.plan.changed",
        target=("onboarding_plan", plan_id),
        actor=actor,
        data=plan_data(p),
        approvals=(approver,),
    )
    return p


async def _approved(conn: AsyncConnection, plan_id: str) -> Plan:
    p = await store.load_plan(conn, plan_id, lock=True)
    if p is None:
        raise OnboardingError("ONBOARDING_PLAN_NOT_FOUND")
    if p.status is not PlanStatus.APPROVED:
        raise OnboardingError(
            "ONBOARDING_PLAN_NOT_APPROVED", "Only the approved plan drives onboarding."
        )
    return p


async def stage_gates(conn: AsyncConnection, tenant_id: str, plan: Plan) -> list[Gate]:
    """The gates guarding the exit of the tenant's current stage (OVERLAY §25)."""
    tenant = await load_tenant(conn, tenant_id)
    assert tenant is not None
    if tenant.status is S.CONFIGURING:
        return await configuring_gates(conn, tenant_id)
    if tenant.status is S.INTEGRATING:
        apps = await active_application_count(conn)
        return [
            Gate("plan.approved", "INTEGRATING", GateStatus.PASS),
            Gate(
                "applications.active",
                "INTEGRATING",
                GateStatus.PASS if apps else GateStatus.FAIL,
                None if apps else "PEP_CONTEXT_INCOMPLETE",
                f"{apps} active application(s)",
            ),
        ]
    if tenant.status is S.SHADOW:
        since = plan.shadow_started_at or plan.created_at
        return shadow_gates(await store.shadow_stats(conn, since), plan.exit_criteria)
    if tenant.status is S.PILOT:
        since = plan.pilot_started_at or plan.created_at
        stats = await store.shadow_stats(conn, since)
        tested = plan.rollback_tested_at is not None
        return [
            Gate(
                "pilot.false_allows",
                "PILOT",
                GateStatus.PASS if stats.false_allows == 0 else GateStatus.FAIL,
                None if stats.false_allows == 0 else "PILOT_GATE_FAILED",
                f"{stats.false_allows} critical false allows since pilot start",
            ),
            Gate(
                "pilot.rollback_tested",
                "PILOT",
                GateStatus.PASS if tested else GateStatus.FAIL,
                None if tested else "ROLLBACK_PLAN_UNTESTED",
                "A rollback to SHADOW has been executed and recovered from during this plan."
                if tested
                else "Execute and recover from one rollback during PILOT first.",
            ),
        ]
    return []


async def _move(
    conn: AsyncConnection,
    tenant_id: str,
    plan: Plan,
    *,
    expect: TenantStatus,
    code: str,
    reason: str,
    actor: str,
    approvals: Sequence[str] = (),
) -> tuple[str, list[Gate]]:
    tenant = await load_tenant(conn, tenant_id, lock=True)
    assert tenant is not None
    if tenant.status is not expect:
        raise OnboardingError(
            "TENANT_STATE_INVALID", f"The tenant is {tenant.status}, not {expect}."
        )
    report = require_pass(await stage_gates(conn, tenant_id, plan), code)
    try:
        t = await transition(
            conn,
            tenant_id,
            Move.ADVANCE,
            reason_code=reason,
            actor=actor,
            gate_report=[g.view() for g in report],
            approval_ref=approvals[0] if approvals else None,
            approvals=approvals,
        )
    except TenantError as e:
        raise OnboardingError(e.code, e.detail) from e
    return t.status.value, report


async def start_integration(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str
) -> tuple[str, list[Gate]]:
    plan = await _approved(conn, plan_id)
    return await _move(
        conn,
        tenant_id,
        plan,
        expect=S.CONFIGURING,
        code="PILOT_GATE_FAILED",
        reason="CONFIGURATION_COMPLETE",
        actor=actor,
    )


async def start_shadow(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str
) -> tuple[str, list[Gate]]:
    plan = await _approved(conn, plan_id)
    out = await _move(
        conn,
        tenant_id,
        plan,
        expect=S.INTEGRATING,
        code="PILOT_GATE_FAILED",
        reason="SHADOW_STARTED",
        actor=actor,
    )
    await store.stamp_plan(conn, plan_id, "shadow_started_at", _now())
    await _record(
        conn,
        tenant_id,
        "onboarding.shadow_started",
        target=("onboarding_plan", plan_id),
        actor=actor,
        data=plan_data(plan),
    )
    return out


async def start_pilot(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str, approver: str
) -> tuple[str, list[Gate]]:
    plan = await _approved(conn, plan_id)
    out = await _move(
        conn,
        tenant_id,
        plan,
        expect=S.SHADOW,
        code="SHADOW_EXIT_CRITERIA_FAILED",
        reason="PILOT_STARTED",
        actor=actor,
        approvals=(approver,),
    )
    await store.stamp_plan(conn, plan_id, "pilot_started_at", _now())
    await _record(
        conn,
        tenant_id,
        "onboarding.pilot_started",
        target=("onboarding_plan", plan_id),
        actor=actor,
        data=plan_data(plan),
        evidence=EvidenceClass.E3,
        approvals=(approver,),
    )
    return out


async def activate(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str, approver: str
) -> tuple[str, list[Gate]]:
    plan = await _approved(conn, plan_id)
    out = await _move(
        conn,
        tenant_id,
        plan,
        expect=S.PILOT,
        code="PILOT_GATE_FAILED",
        reason="PRODUCTION_GATE_PASSED",
        actor=actor,
        approvals=(approver,),
    )
    for st in await store.stages(conn, active_only=True):
        await store.set_stage(conn, st.stage_id, StageStatus.COMPLETED, approver=None, now=_now())
    return out


async def rollback(
    conn: AsyncConnection, tenant_id: str, plan_id: str, *, actor: str, reason: str
) -> str:
    """To the previous safe enforcement stage (§20); active pilot scopes stop enforcing at once."""
    plan = await _approved(conn, plan_id)
    tenant = await load_tenant(conn, tenant_id, lock=True)
    assert tenant is not None
    was = tenant.status
    try:
        t = await transition(conn, tenant_id, Move.ROLLBACK, reason_code=reason, actor=actor)
    except TenantError as e:
        raise OnboardingError(e.code, e.detail) from e
    rolled = await store.roll_back_active_stages(conn, _now())
    if was is S.PILOT:
        await store.stamp_plan(conn, plan_id, "rollback_tested_at", _now())
    await _record(
        conn,
        tenant_id,
        "onboarding.rollback",
        target=("onboarding_plan", plan_id),
        actor=actor,
        evidence=EvidenceClass.E3,
        data={
            **plan_data(plan),
            "from_status": was.value,
            "to_status": t.status.value,
            "stages_rolled_back": rolled,
            "reason_code": reason,
        },
    )
    return t.status.value


# ------------------------------------------------------------------------------------- stages


async def add_stage(
    conn: AsyncConnection,
    tenant_id: str,
    plan_id: str,
    *,
    name: str,
    scope: Mapping[str, Any],
    actor: str,
) -> Stage:
    await _approved(conn, plan_id)
    st = await store.insert_stage(
        conn,
        tenant_id=tenant_id,
        plan_id=plan_id,
        name=name,
        scope=Scope.parse(scope),
        created_by=actor,
    )
    await _record(
        conn,
        tenant_id,
        "onboarding.stage.changed",
        target=("onboarding_stage", st.stage_id),
        actor=actor,
        data={"stage_id": st.stage_id, "status": st.status.value, "max_risk": st.scope.max_risk},
    )
    return st


async def activate_stage(
    conn: AsyncConnection, tenant_id: str, stage_id: str, *, actor: str, approver: str
) -> Stage:
    """A scope starts enforcing; only while the tenant is in PILOT (§19). An R3 scope is its own
    explicit step: nothing enables R3 because lower-risk scopes went well."""
    st = await store.load_stage(conn, stage_id, lock=True)
    if st is None:
        raise OnboardingError("ONBOARDING_STAGE_NOT_FOUND")
    tenant = await load_tenant(conn, tenant_id)
    assert tenant is not None
    if tenant.status is not S.PILOT:
        raise OnboardingError(
            "PILOT_GATE_FAILED", "Scopes enforce only while the tenant is in PILOT."
        )
    if st.status is not StageStatus.PLANNED:
        raise OnboardingError("ONBOARDING_STAGE_STATE_INVALID", f"The stage is {st.status}.")
    st = await store.set_stage(conn, stage_id, StageStatus.ACTIVE, approver=approver, now=_now())
    await _record(
        conn,
        tenant_id,
        "onboarding.stage.changed",
        target=("onboarding_stage", stage_id),
        actor=actor,
        evidence=EvidenceClass.E3,
        approvals=(approver,),
        data={"stage_id": stage_id, "status": st.status.value, "max_risk": st.scope.max_risk},
    )
    return st
