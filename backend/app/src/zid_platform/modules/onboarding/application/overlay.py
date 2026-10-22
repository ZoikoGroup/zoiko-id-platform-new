"""The overlay verdict on the AuthZEN path (ZID-OVERLAY-001 §12, §18-19; OVL-16..21; G-W4).

Called by the AuthZEN endpoint after the PDP decided. It finds the PEP's registered application
(its enforceable obligations and required context), the tenant's active pilot scopes, applies the
pure `overlay_verdict`, and - while the tenant is in SHADOW or PILOT - records one minimized shadow
observation (Zoiko vs incumbent outcome, context completeness, latency; the subject only as a
commitment). A shadow observation never changes the business side effect: in EVALUATE_ONLY the
incumbent still decides.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import commitment
from zid_platform.modules.integration.ports import pep_application
from zid_platform.modules.onboarding.domain.plan import (
    Verdict,
    classify,
    overlay_verdict,
)
from zid_platform.modules.onboarding.infrastructure import store

_OBSERVED = ("SHADOW", "PILOT")


async def apply_overlay(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    tenant_status: str | None,
    client_id: str,
    subject: str,
    action: str,
    risk: int,
    decision_id: str,
    outcome: str,
    decision_allow: bool,
    obligations: Iterable[str],
    present_context: frozenset[str],
    incumbent: str | None,
    latency_ms: float,
) -> Verdict:
    app = await pep_application(conn, client_id)
    in_scope = False
    if tenant_status == "PILOT":
        in_scope = any(
            st.scope.covers(
                application_id=app.application_id if app else None,
                action=action,
                risk=risk,
                subject=subject,
            )
            for st in await store.stages(conn, active_only=True)
        )
    obligation_types = [str(o) for o in obligations]
    verdict = overlay_verdict(
        tenant_status=tenant_status,
        decision_allow=decision_allow,
        risk=risk,
        obligations=obligation_types,
        supported_obligations=app.pep_obligations if app else None,
        required_context=app.pep_context_fields if app else frozenset(),
        present_context=present_context,
        in_enforcing_scope=in_scope,
    )
    if tenant_status in _OBSERVED:
        plan = await store.approved_plan(conn)
        required = app.pep_context_fields if app else frozenset()
        await store.record_observation(
            conn,
            tenant_id,
            {
                "plan": plan.plan_id if plan else None,
                "decision": decision_id,
                "app": app.application_id if app else None,
                "action": action,
                "risk": risk,
                "outcome": outcome,
                "allow": verdict.allow,
                "incumbent": incumbent if incumbent in ("ALLOW", "DENY") else None,
                "mismatch": classify(verdict.allow, incumbent).value,
                "complete": required <= present_context,
                "enforcement": verdict.enforcement.value,
                "subject": commitment(subject, tenant_id=tenant_id, kind="shadow-subject"),
                "latency": round(latency_ms, 3),
            },
        )
    return verdict
