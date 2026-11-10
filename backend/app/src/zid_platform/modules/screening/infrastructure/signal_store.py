"""Persistence for velocity attempts, signals and their review items (CR-068).

Only hashed identifiers are stored. A signal row is append-only; a review item records who resolved it, with what outcome and which
evidence, and is never deleted."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.screening.domain.signals import Action, Dimension


async def record_event(
    conn: AsyncConnection,
    tenant_id: str,
    dimension: Dimension,
    key_hash: str,
    action: Action,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.screening_velocity_event (event_id, tenant_id, dimension, key_hash, action, occurred_at) "
            "VALUES (:i, :t, :d, :k, :a, :n)"
        ),
        {
            "i": uuid.uuid4(),
            "t": tenant_id,
            "d": dimension.value,
            "k": key_hash,
            "a": action.value,
            "n": now,
        },
    )


async def count_events(
    conn: AsyncConnection,
    tenant_id: str,
    dimension: Dimension,
    key_hash: str,
    action: Action,
    window: timedelta,
    now: datetime,
) -> int:
    """Attempts in (now - window, now]: one exactly `window` old has aged out."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) AS n FROM verify.screening_velocity_event WHERE tenant_id = :t AND dimension = :d "
                "AND key_hash = :k AND action = :a AND occurred_at > :from AND occurred_at <= :now"
            ),
            {
                "t": tenant_id,
                "d": dimension.value,
                "k": key_hash,
                "a": action.value,
                "from": now - window,
                "now": now,
            },
        )
    ).one()
    return int(row.n)


@dataclass(frozen=True, slots=True)
class ReviewRow:
    review_id: str
    signal_id: str
    dedupe_key: str
    status: str
    notify_state: str
    outcome: str | None
    resolved_by: str | None
    rationale: str | None
    evidence_ref: str | None
    source: str
    rule: str
    detail: dict[str, Any]
    risk_tier: str


_SELECT = (
    "SELECT r.review_id, r.signal_id, r.dedupe_key, r.status, r.notify_state, r.outcome, r.resolved_by, r.rationale, "
    "r.evidence_ref, s.source, s.rule, s.detail, s.risk_tier FROM verify.screening_signal_review r "
    "JOIN verify.screening_signal s ON s.signal_id = r.signal_id "
)


def _row(r: Any) -> ReviewRow:
    return ReviewRow(
        str(r.review_id),
        str(r.signal_id),
        r.dedupe_key,
        r.status,
        r.notify_state,
        r.outcome,
        str(r.resolved_by) if r.resolved_by else None,
        r.rationale,
        r.evidence_ref,
        r.source,
        r.rule,
        r.detail,
        r.risk_tier,
    )


async def open_review_for(
    conn: AsyncConnection, tenant_id: str, dedupe_key: str
) -> ReviewRow | None:
    r = (
        await conn.execute(
            text(
                _SELECT + "WHERE r.tenant_id = :t AND r.dedupe_key = :k AND r.status <> 'RESOLVED'"
            ),
            {"t": tenant_id, "k": dedupe_key},
        )
    ).first()
    return _row(r) if r else None


async def create_signal_and_review(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    source: str,
    subject_ref: str,
    rule: str,
    detail: dict[str, Any],
    risk_tier: str,
    dedupe_key: str,
    now: datetime,
) -> tuple[ReviewRow, bool]:
    """(the open review, True when this call created it). A second signal with the same dedupe key finds the open item and adds nothing."""
    existing = await open_review_for(conn, tenant_id, dedupe_key)
    if existing is not None:
        return existing, False
    signal_id, review_id = uuid.uuid4(), uuid.uuid4()
    try:
        async with (
            conn.begin_nested()
        ):  # a concurrent twin loses on the unique index and undoes only itself
            await conn.execute(
                text(
                    "INSERT INTO verify.screening_signal (signal_id, tenant_id, source, subject_ref, rule, detail, risk_tier, raised_at) "
                    "VALUES (:i, :t, :s, :r, :u, CAST(:d AS jsonb), :k, :n)"
                ),
                {
                    "i": signal_id,
                    "t": tenant_id,
                    "s": source,
                    "r": subject_ref,
                    "u": rule,
                    "d": json.dumps(detail),
                    "k": risk_tier,
                    "n": now,
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO verify.screening_signal_review (review_id, tenant_id, signal_id, dedupe_key, status, created_at) "
                    "VALUES (:i, :t, :s, :k, 'OPEN', :n)"
                ),
                {"i": review_id, "t": tenant_id, "s": signal_id, "k": dedupe_key, "n": now},
            )
    except IntegrityError:
        winner = await open_review_for(conn, tenant_id, dedupe_key)
        if winner is None:
            raise
        return winner, False
    created = await get_review(conn, tenant_id, str(review_id))
    assert created is not None
    return created, True


async def get_review(
    conn: AsyncConnection, tenant_id: str, review_id: str, *, lock: bool = False
) -> ReviewRow | None:
    r = (
        await conn.execute(
            text(
                _SELECT
                + "WHERE r.tenant_id = :t AND r.review_id = :i"
                + (" FOR UPDATE OF r" if lock else "")
            ),
            {"t": tenant_id, "i": review_id},
        )
    ).first()
    return _row(r) if r else None


async def list_open_reviews(conn: AsyncConnection, tenant_id: str) -> list[ReviewRow]:
    rows = (
        await conn.execute(
            text(
                _SELECT
                + "WHERE r.tenant_id = :t AND r.status <> 'RESOLVED' ORDER BY r.created_at, r.review_id"
            ),
            {"t": tenant_id},
        )
    ).all()
    return [_row(r) for r in rows]


async def set_notify_state(
    conn: AsyncConnection, tenant_id: str, review_id: str, state: str
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.screening_signal_review SET notify_state = :s WHERE tenant_id = :t AND review_id = :i"
        ),
        {"s": state, "t": tenant_id, "i": review_id},
    )


async def mark_challenged(
    conn: AsyncConnection, tenant_id: str, review_id: str, *, statement_hash: str, evidence_ref: str
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.screening_signal_review SET status = 'CHALLENGED', challenge_hash = :h, challenge_ref = :e "
            "WHERE tenant_id = :t AND review_id = :i AND status <> 'RESOLVED'"
        ),
        {"h": statement_hash, "e": evidence_ref, "t": tenant_id, "i": review_id},
    )


async def mark_resolved(
    conn: AsyncConnection,
    tenant_id: str,
    review_id: str,
    *,
    outcome: str,
    reviewer_id: str,
    rationale: str,
    evidence_ref: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.screening_signal_review SET status = 'RESOLVED', outcome = :o, resolved_by = :b, rationale = :r, "
            "evidence_ref = :e, resolved_at = :n WHERE tenant_id = :t AND review_id = :i AND status <> 'RESOLVED'"
        ),
        {
            "o": outcome,
            "b": reviewer_id,
            "r": rationale,
            "e": evidence_ref,
            "n": now,
            "t": tenant_id,
            "i": review_id,
        },
    )
