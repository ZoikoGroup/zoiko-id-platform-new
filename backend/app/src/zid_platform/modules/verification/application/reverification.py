"""Re-verification sweep (ZID-GAP-001 v2.0 DTV-29; CR-102): a verification that has run out, or that rests on a provider no longer trusted, must
not sit in the books as VALID, and the people it concerns must be told before it lapses, not after.

Run by the worker, which sees every company and sets the company for each row it touches. Three things, each recorded once:
- an assertion past its `valid_until` becomes EXPIRED and `verification.assertion.expired` is emitted (nothing relied on it correctly before, because
  reliance checks the date; this makes the stored state and the event stream say so too);
- an assertion whose provider adapter is now QUARANTINED or RETIRED becomes STALE with the reason `PROVIDER_NO_LONGER_TRUSTED`, and
  `verification.assertion.stale` is emitted: a result a provider can no longer stand behind is a reason to ask again, never a pass;
- an assertion that lapses within `REMIND_WITHIN` raises one alert per person and claim, folded while it stays open, saying a re-verification is due.
It never revokes, never verifies and never starts a verification on its own: asking the person again is a decision for the company.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert
from zid_platform.modules.verification.domain.audit_events import REVERIFICATION_DUE

REMIND_WITHIN = timedelta(days=30)
ADAPTER_DISTRUSTED = ("QUARANTINED", "RETIRED")


async def _as_tenant(conn: AsyncConnection, tenant: str) -> None:
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})


async def sweep(conn: AsyncConnection, now: datetime) -> dict[str, int]:
    expired = (
        await conn.execute(
            text(
                "UPDATE verify.verification_assertion SET status = 'EXPIRED' WHERE status = 'VALID' AND valid_until <= :now "
                "RETURNING assertion_id, tenant_id, subject_principal_id, claim_type"
            ),
            {"now": now},
        )
    ).all()
    for r in expired:
        await _as_tenant(conn, str(r.tenant_id))
        await _emit(conn, "verification.assertion.expired", r)
    stale = (
        await conn.execute(
            text(
                "UPDATE verify.verification_assertion a SET status = 'STALE', "
                "reason_codes = array_append(a.reason_codes, 'PROVIDER_NO_LONGER_TRUSTED') "
                "WHERE a.status = 'VALID' AND EXISTS (SELECT 1 FROM verify.provider_adapter p WHERE p.tenant_id = a.tenant_id "
                "AND p.adapter_id = a.provider_id AND p.status = ANY(:bad)) "
                "RETURNING a.assertion_id, a.tenant_id, a.subject_principal_id, a.claim_type"
            ),
            {"bad": list(ADAPTER_DISTRUSTED)},
        )
    ).all()
    for r in stale:
        await _as_tenant(conn, str(r.tenant_id))
        await _emit(conn, "verification.assertion.stale", r)
    due = (
        await conn.execute(
            text(
                "SELECT DISTINCT tenant_id, subject_principal_id, claim_type FROM verify.verification_assertion "
                "WHERE status = 'VALID' AND valid_until > :now AND valid_until <= :soon"
            ),
            {"now": now, "soon": now + REMIND_WITHIN},
        )
    ).all()
    for r in due:
        await _as_tenant(conn, str(r.tenant_id))
        await raise_alert(
            conn,
            str(r.tenant_id),
            AlertDraft(
                severity=Severity.MEDIUM,
                kind=REVERIFICATION_DUE,
                title="A verification is about to lapse",
                body=f"A verified {r.claim_type} claim for a person in your company lapses within 30 days. Ask them to verify again before it does.",
                object_type="verification_subject",
                object_ref=str(r.subject_principal_id)[:128],
                href="/admin/verification",
                recommended_action="Start a new verification for this person before the old one lapses.",
                is_task=True,
                mandatory=False,
                correlation_key=f"{REVERIFICATION_DUE}:{r.subject_principal_id}:{r.claim_type}",
            ),
            now=now,
        )
    return {"expired": len(expired), "stale": len(stale), "reminded": len(due)}


async def _emit(conn: AsyncConnection, name: str, r: Any) -> None:
    await emit(
        conn,
        name=name,
        tenant_id=str(r.tenant_id),
        aggregate_type="verification_assertion",
        aggregate_id=str(r.assertion_id),
        aggregate_version=2,
        data={"assertion_id": str(r.assertion_id), "claim_type": r.claim_type},
    )
