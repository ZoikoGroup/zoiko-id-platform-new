"""Reminders about approval requests that are about to expire (ZID-UX-WF-013C section 05; slice L-13, CR-053).

A request that nobody has decided, or that is approved and waiting to be applied, lapses at its expiry and has to be asked for again. A
request inside a reminder window raises one event (`governance.approval.reminder_due`), which the alerts centre shows as a task. Once per
request and window: the reminder is recorded when it is raised. The worker runs this for every organization; one request failing never
stops another. Nothing in the event names a person, a payload or a reason.
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant
from zid_outbox import emit, next_version

from zid_platform.modules.tenant.infrastructure.reminder_policy import reminder_policy

log = logging.getLogger("zid.approvals.reminders")
_OPEN = ("PENDING_APPROVAL", "PARTIALLY_APPROVED", "APPROVED_READY")


async def remind_due(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    """tenant id -> reminders raised in this pass."""
    policy = reminder_policy()
    out: dict[str, int] = {}
    if not policy.enabled:
        return out
    for window in policy.windows_hours:
        due = (
            await conn.execute(
                text(
                    "SELECT c.tenant_id, c.case_id, c.tier, c.state, c.expires_at FROM platform.approval_case c "
                    "WHERE c.state = ANY(:open) AND c.expires_at > :now AND c.expires_at <= :now + make_interval(hours => :w) "
                    "AND NOT EXISTS (SELECT 1 FROM platform.approval_reminder r WHERE r.case_id = c.case_id AND r.window_hours = :w) "
                    "ORDER BY c.expires_at"
                ),
                {"open": list(_OPEN), "now": now, "w": window},
            )
        ).all()
        for row in due:
            tenant, case = str(row.tenant_id), str(row.case_id)
            try:
                async with conn.begin_nested(), in_tenant(conn, tenant):
                    raised = await conn.execute(
                        text(
                            "INSERT INTO platform.approval_reminder (tenant_id, case_id, window_hours, raised_at) "
                            "VALUES (:t, :c, :w, :now) ON CONFLICT DO NOTHING RETURNING case_id"
                        ),
                        {"t": row.tenant_id, "c": row.case_id, "w": window, "now": now},
                    )
                    if raised.first() is None:  # another pass raised it first
                        continue
                    hours_left = max(0, int((row.expires_at - now).total_seconds() // 3600))
                    await emit(
                        conn, name="governance.approval.reminder_due", tenant_id=tenant, aggregate_type="approval_case",
                        aggregate_id=case, aggregate_version=await next_version(conn, "approval_case", case),
                        data={"case_id": case, "level": row.tier, "state": row.state, "window_hours": window, "hours_left": hours_left},
                    )  # fmt: skip
                out[tenant] = out.get(tenant, 0) + 1
            except Exception:
                log.exception("could not remind about the request %s", case)
    return out
