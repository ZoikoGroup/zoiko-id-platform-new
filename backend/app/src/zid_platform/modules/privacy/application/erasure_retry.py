"""Retrying a provider deletion that did not arrive, and alerting when it never does (ZID-DTV-WF-04 QA-023; CR-113). A worker pass over every tenant.

    PROVIDER task   overdue (past its deletion SLA) or FAILED, and not yet alerted: the provider is asked again (`privacy.erasure.deletion_requested`,
                    the integration that sends deletion requests consumes it) with a growing wait between attempts (RETRY_WAITS). When the attempts
                    run out the task is FAILED with RETRIES_EXHAUSTED and one alert is raised (`privacy.erasure.deletion_failed`, priority).
    FIRST_PARTY     a FAILED first-party task (no disposer for the store) is an engineering fault, never retried blindly: it is alerted once.

A task is never completed here: only a verified provider receipt (or, for backups, an operator attestation) acknowledges it, and the existing CHECK
refuses an ACKNOWLEDGED task without a receipt hash. Every event carries ids, codes and the attempt number; never content.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.application import control
from zid_platform.modules.privacy.application import service as v1

# PROVISIONAL (CR-113): the wait after each attempt; the owners may change it. Five attempts over about ten days.
RETRY_WAITS = (
    timedelta(hours=1),
    timedelta(hours=6),
    timedelta(days=1),
    timedelta(days=3),
    timedelta(days=5),
)
ALERT_VERSION = 100  # the alert's aggregate version, after any number of attempts


def next_step(attempts: int) -> tuple[str, timedelta | None]:
    """Pure: ("RETRY", wait) for attempt number attempts + 1, or ("ALERT", None) when the attempts have run out."""
    if attempts >= len(RETRY_WAITS):
        return "ALERT", None
    return "RETRY", RETRY_WAITS[attempts]


async def erasure_retry_pass(conn: AsyncConnection, *, now: datetime) -> dict[str, Any]:
    retried = alerted = 0
    rows = (
        await conn.execute(
            text(
                "SELECT task_id, tenant_id, job_id, target_kind, target_ref, state, failure, attempts, due_at, asset_classes "
                "FROM privacy.erasure_task WHERE alerted_at IS NULL AND ("
                "  (target_kind = 'PROVIDER' AND (state = 'FAILED' OR (state = 'PENDING' AND due_at IS NOT NULL AND due_at < :now)) "
                "   AND (next_attempt_at IS NULL OR next_attempt_at <= :now)) "
                "  OR (target_kind = 'FIRST_PARTY_STORE' AND state = 'FAILED')"
                ") ORDER BY due_at NULLS LAST, task_id FOR UPDATE SKIP LOCKED"
            ),
            {"now": now},
        )
    ).all()
    for t in rows:
        tenant = str(t.tenant_id)
        common = {
            "job_id": str(t.job_id),
            "task_id": str(t.task_id),
            "target_kind": t.target_kind,
            "target_ref": t.target_ref,
            "asset_classes": list(t.asset_classes or []),
        }
        step, wait = next_step(int(t.attempts)) if t.target_kind == "PROVIDER" else ("ALERT", None)
        if step == "RETRY" and wait is not None:
            attempt = int(t.attempts) + 1
            await conn.execute(
                text(
                    "UPDATE privacy.erasure_task SET attempts = :a, next_attempt_at = :n, state = 'PENDING', updated_at = now() "
                    "WHERE task_id = :t"
                ),
                {"a": attempt, "n": now + wait, "t": t.task_id},
            )
            await control._settle(conn, str(t.job_id))
            await v1._event(
                conn,
                tenant,
                "privacy.erasure.deletion_requested",
                ("erasure_task", str(t.task_id)),
                {
                    **common,
                    "attempt": attempt,
                    "previous_failure": t.failure,
                    "due_at": t.due_at.isoformat() if t.due_at else None,
                },
                actor=None,
                version=attempt,
            )
            retried += 1
            continue
        failure = "RETRIES_EXHAUSTED" if t.target_kind == "PROVIDER" else (t.failure or "FAILED")
        await conn.execute(
            text(
                "UPDATE privacy.erasure_task SET state = 'FAILED', failure = :f, alerted_at = :now, updated_at = now() WHERE task_id = :t"
            ),
            {"f": failure, "now": now, "t": t.task_id},
        )
        await control._settle(conn, str(t.job_id))
        await v1._event(
            conn,
            tenant,
            "privacy.erasure.deletion_failed",
            ("erasure_task", str(t.task_id)),
            {**common, "failure": failure, "attempts": int(t.attempts)},
            actor=None,
            version=ALERT_VERSION,
            outcome="FAILURE",
            reason=failure,
        )
        alerted += 1
    return {"retried": retried, "alerted": alerted}


__all__ = ["RETRY_WAITS", "erasure_retry_pass", "next_step"]
