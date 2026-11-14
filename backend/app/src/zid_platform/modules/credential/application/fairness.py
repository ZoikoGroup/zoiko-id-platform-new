"""The disparity check and the hold it applies (T44; CR-092).

`check_disparity` (worker pass, across tenants, off unless the deployment turns it on) counts the automated name matches of the last window per
cohort, judges them against the OWNER-APPROVED threshold (none shipped: it then fails closed and holds every cohort with outcomes), stores the hold of
each cohort, and for each cohort newly held announces it once (`credential.fairness.path_held`) and raises one mandatory alert on the platform.

`apply_hold` is what a name match does with the answer: a held cohort's automated NOT_MATCHED becomes AMBIGUOUS, which is the route to a person the
credential module already has. It never changes any other answer, so a hold can add a person and cannot add a pass."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.credential.domain import fairness as fa
from zid_platform.modules.credential.domain.claims import SubjectMatch
from zid_platform.modules.credential.domain.disclosure_events import (
    FAIRNESS_HOLD_ALERT as HELD_ALERT,
)
from zid_platform.modules.credential.infrastructure import fairness_store as store
from zid_platform.modules.credential.infrastructure.registries import fairness_threshold
from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert

WINDOW = timedelta(days=90)
PLATFORM_TENANT = "00000000-0000-0000-0000-000000000000"  # the platform partition: the hold is not any tenant's data
HELD_REASON = "FAIRNESS_HOLD"


def apply_hold(
    subject: SubjectMatch, holder_name: str, held: frozenset[str]
) -> tuple[SubjectMatch, dict[str, Any]]:
    """(the answer to use, what to record about the match). The record carries the cohort and the automated answer, never the name."""
    cohort = fa.name_cohort(holder_name)
    on_hold = subject is SubjectMatch.NOT_MATCHED and cohort in held
    used = SubjectMatch.AMBIGUOUS if on_hold else subject
    return used, {"cohort": cohort, "automated": subject.value, "held": on_hold}


async def check_disparity(
    conn: AsyncConnection, now: datetime, *, window: timedelta = WINDOW
) -> dict[str, int]:
    findings = fa.evaluate(await store.outcome_counts(conn, now - window), fairness_threshold())
    newly = await store.save(conn, findings, now)
    for f in newly:
        assert f.reason is not None
        await emit(
            conn,
            name="credential.fairness.path_held",
            tenant_id=PLATFORM_TENANT,
            aggregate_type="fairness_cohort",
            aggregate_id=f.cohort,
            aggregate_version=int(now.timestamp() * 1000),
            data={
                "cohort": f.cohort,
                "reason": f.reason.value,
                "total": f.total,
                "adverse": f.adverse,
                "rate": round(f.rate, 6),
                "reference_rate": None if f.reference_rate is None else round(f.reference_rate, 6),
            },
        )
        await raise_alert(
            conn,
            PLATFORM_TENANT,
            AlertDraft(
                severity=Severity.HIGH,
                kind=HELD_ALERT,
                title="An automated path was held for human review",
                body=f"Automated 'different person' answers for names in the {f.cohort} cohort now go to a person ({f.reason.value}).",
                object_type="fairness_cohort",
                object_ref=f.cohort,
                href="/admin/credentials",
                recommended_action="Review the cohort's outcomes and, if the owners have approved a threshold, check it is the one in force.",
                is_task=True,
                mandatory=True,
                correlation_key=f"{HELD_ALERT}:{f.cohort}",
            ),
            now=now,
        )
    return {
        "cohorts": len(findings),
        "held": sum(1 for f in findings if f.held),
        "newly_held": len(newly),
    }
