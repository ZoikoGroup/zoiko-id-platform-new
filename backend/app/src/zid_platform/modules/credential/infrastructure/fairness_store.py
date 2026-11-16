"""Storage for the disparity check (T44; CR-092): outcome counts per cohort read from the stored checks, and the hold per cohort.

The counts are aggregates over `cred.claim_check` (the cohort and the automated answer each check recorded about itself); no name, subject or tenant
leaves this module. The hold table is platform state (migration credential_0015)."""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.credential.domain import fairness as fa


async def outcome_counts(conn: AsyncConnection, since: datetime) -> dict[str, fa.Counts]:
    """Automated name matches since `since`, per cohort. Runs across tenants (the worker role)."""
    rows = (
        await conn.execute(
            text(
                "SELECT assertion -> 'fairness' ->> 'cohort' AS cohort, count(*) AS total, "
                "count(*) FILTER (WHERE assertion -> 'fairness' ->> 'automated' = 'NOT_MATCHED') AS adverse "
                "FROM cred.claim_check WHERE created_at >= :since AND assertion -> 'fairness' ->> 'cohort' IS NOT NULL "
                "GROUP BY 1"
            ),
            {"since": since},
        )
    ).all()
    return {r.cohort: fa.Counts(int(r.total), int(r.adverse)) for r in rows}


async def held_cohorts(conn: AsyncConnection) -> frozenset[str]:
    rows = (await conn.execute(text("SELECT cohort FROM cred.fairness_hold WHERE held"))).all()
    return frozenset(r.cohort for r in rows)


async def save(
    conn: AsyncConnection, findings: Iterable[fa.Finding], now: datetime
) -> list[fa.Finding]:
    """Write each finding as its cohort's current state. Returns the findings of cohorts that were NOT held before and are now."""
    newly: list[fa.Finding] = []
    for f in findings:
        before = (
            await conn.execute(
                text("SELECT held FROM cred.fairness_hold WHERE cohort = :c FOR UPDATE"),
                {"c": f.cohort},
            )
        ).first()
        detail = {
            "total": f.total,
            "adverse": f.adverse,
            "rate": round(f.rate, 6),
            "reference_rate": None if f.reference_rate is None else round(f.reference_rate, 6),
            "judged": f.judged,
        }
        await conn.execute(
            text(
                "INSERT INTO cred.fairness_hold (cohort, held, reason, detail, evaluated_at, held_since) "
                "VALUES (:c, :h, :r, CAST(:d AS jsonb), :n, CASE WHEN :h THEN CAST(:n AS timestamptz) END) "
                "ON CONFLICT (cohort) DO UPDATE SET held = EXCLUDED.held, reason = EXCLUDED.reason, detail = EXCLUDED.detail, "
                "evaluated_at = EXCLUDED.evaluated_at, "
                "held_since = CASE WHEN EXCLUDED.held THEN coalesce(cred.fairness_hold.held_since, EXCLUDED.held_since) END"
            ),
            {
                "c": f.cohort,
                "h": f.held,
                "r": f.reason.value if f.reason else None,
                "d": json.dumps(detail, sort_keys=True),
                "n": now,
            },
        )
        if f.held and not (before is not None and before.held):
            newly.append(f)
    return newly
