"""Tenant offboarding to CLOSED (ZID-OVERLAY-001 §2 OFFBOARDING, §31; TENANCY-001 §23; OVL-30;
G-W5).

OFFBOARDING is entered by a governed, second-administrator-approved request (tenant API). This pass
then drains the tenant: integrations disabled (connectors quarantined - credentials revoked, sources
suspended - and applications retired), every live session revoked, and the tenant CLOSED (no
runtime trust; CLOSED tenants never authenticate again). Data export, deletion and retention run
through the privacy module's governed jobs and retention schedules; evidence is retained per policy.
Idempotent: a crash mid-way re-runs safely on the next pass.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.ports import offboard_integrations
from zid_platform.modules.session.ports import revoke_tenant_sessions
from zid_platform.modules.tenant.ports import Move, TenantStatus, load_tenant, transition


async def complete_offboarding(
    conn: AsyncConnection, tenant_id: str, *, now: datetime
) -> dict[str, object]:
    tenant = await load_tenant(conn, tenant_id, lock=True)
    if tenant is None or tenant.status is not TenantStatus.OFFBOARDING:
        return {"tenant_id": tenant_id, "skipped": True}
    drained = await offboard_integrations(conn, tenant_id, actor=None)
    sessions = await revoke_tenant_sessions(conn, reason="tenant_offboarded")
    await transition(
        conn,
        tenant_id,
        Move.CLOSE,
        reason_code="OFFBOARDING_COMPLETE",
        actor=None,
        actor_type="SERVICE",
        gate_report=[
            {"gate_id": "offboarding.integrations", "status": "PASS", **drained},
            {"gate_id": "offboarding.sessions", "status": "PASS", "revoked": sessions},
        ],
        now=now,
    )
    return {"tenant_id": tenant_id, **drained, "sessions_revoked": sessions, "status": "CLOSED"}


async def offboarding_pass(conn: AsyncConnection, now: datetime) -> list[dict[str, object]]:
    """Worker pass (BYPASSRLS): every OFFBOARDING tenant, one at a time under its own scope."""
    rows = (
        await conn.execute(
            text("SELECT tenant_id FROM platform.tenant WHERE status = 'OFFBOARDING'")
        )
    ).all()
    out = []
    for r in rows:
        tenant = str(r.tenant_id)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        out.append(await complete_offboarding(conn, tenant, now=now))
    return out
