"""Storage for credential trust: monitors, consent grants, issuer outreach, eligibility decisions, appeals (CR-069).

Inside the caller's tenant transaction (RLS). Grants are withdrawn, never deleted; eligibility decisions are append-only;
an appeal is resolved by a person other than the one who raised it, and never edits the check it is about.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def live_checks(conn: AsyncConnection, subject_principal_id: str) -> list[dict[str, Any]]:
    """The subject's current (not superseded) checks, newest first."""
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT check_id, claim_type, decision, pack_id, pack_version, source_checked_at, assertion, "
                    "created_at FROM cred.claim_check "
                    "WHERE subject_principal_id = :s AND superseded_at IS NULL ORDER BY created_at DESC"
                ),
                {"s": uuid.UUID(subject_principal_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def insert_monitor(
    conn: AsyncConnection,
    *,
    check_id: str,
    tenant_id: str,
    claim_type: str,
    status: str,
    expires_at: datetime | None,
    next_check_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO cred.claim_monitor (check_id, tenant_id, claim_type, status, expires_at, next_check_at) "
            "VALUES (:c, :t, :ct, :st, :e, :n) ON CONFLICT (check_id) DO NOTHING"
        ),
        {
            "c": uuid.UUID(check_id),
            "t": uuid.UUID(tenant_id),
            "ct": claim_type,
            "st": status,
            "e": expires_at,
            "n": next_check_at,
        },
    )


async def load_monitor(conn: AsyncConnection, check_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM cred.claim_monitor WHERE check_id = :c FOR UPDATE"),
                {"c": uuid.UUID(check_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def save_monitor(
    conn: AsyncConnection,
    *,
    check_id: str,
    status: str,
    seq: int | None,
    observed_at: datetime | None,
    seen: list[str],
    next_check_at: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.claim_monitor SET status = :st, last_source_seq = :q, last_observed_at = :o, "
            "seen_events = :seen, next_check_at = :n, updated_at = now() WHERE check_id = :c"
        ),
        {
            "st": status,
            "q": seq,
            "o": observed_at,
            "seen": seen,
            "n": next_check_at,
            "c": uuid.UUID(check_id),
        },
    )


async def due_monitors(
    conn: AsyncConnection, now: datetime, limit: int = 200
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT m.check_id, m.tenant_id, m.claim_type, m.status, m.expires_at, m.next_check_at, m.review_reason "
                    "FROM cred.claim_monitor m JOIN cred.claim_check c ON c.check_id = m.check_id "
                    "WHERE c.superseded_at IS NULL AND m.next_check_at <= :n "
                    "ORDER BY m.next_check_at LIMIT :l"
                ),
                {"n": now, "l": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def insert_grant(conn: AsyncConnection, tenant_id: str, g: Any) -> None:
    await conn.execute(
        text(
            "INSERT INTO cred.consent_grant (grant_id, tenant_id, subject_principal_id, recipient, consumer, "
            "purpose, fields, parent_id, created_at, expires_at) "
            "VALUES (:id, :t, :s, :r, :c, :p, :f, :par, :ca, :ea)"
        ),
        {
            "id": uuid.UUID(g.grant_id),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(g.subject),
            "r": g.recipient,
            "c": g.consumer,
            "p": g.purpose,
            "f": sorted(g.fields),
            "par": uuid.UUID(g.parent_id) if g.parent_id else None,
            "ca": g.created_at,
            "ea": g.expires_at,
        },
    )


async def load_grants(conn: AsyncConnection, grant_id: str) -> dict[str, dict[str, Any]]:
    """The grant and every ancestor, keyed by id."""
    rows = (
        (
            await conn.execute(
                text(
                    "WITH RECURSIVE chain AS (SELECT * FROM cred.consent_grant WHERE grant_id = :g "
                    "UNION SELECT p.* FROM cred.consent_grant p JOIN chain c ON p.grant_id = c.parent_id) "
                    "SELECT grant_id, subject_principal_id, recipient, consumer, purpose, fields, parent_id, "
                    "created_at, expires_at, withdrawn_at FROM chain"
                ),
                {"g": uuid.UUID(grant_id)},
            )
        )
        .mappings()
        .all()
    )
    return {str(r["grant_id"]): dict(r) for r in rows}


async def withdraw_grant(conn: AsyncConnection, grant_id: str, now: datetime) -> bool:
    r = await conn.execute(
        text(
            "UPDATE cred.consent_grant SET withdrawn_at = :n WHERE grant_id = :g AND withdrawn_at IS NULL"
        ),
        {"n": now, "g": uuid.UUID(grant_id)},
    )
    return bool(r.rowcount)


async def insert_outreach(
    conn: AsyncConnection,
    tenant_id: str,
    r: Any,
    subject: str | None = None,
    claim: dict[str, Any] | None = None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO cred.outreach_request (request_id, tenant_id, issuer_id, claim_type, nonce_hash, "
            "requested_by, created_at, deadline, subject_principal_id, claim) "
            "VALUES (:id, :t, :i, :c, :n, :rb, :ca, :d, :s, CAST(:cl AS jsonb))"
        ),
        {
            "id": uuid.UUID(r.request_id),
            "t": uuid.UUID(tenant_id),
            "i": r.issuer_id,
            "c": r.claim_type,
            "n": r.nonce_hash,
            "rb": uuid.UUID(r.requested_by),
            "ca": r.created_at,
            "d": r.deadline,
            "s": uuid.UUID(subject) if subject else None,
            "cl": json.dumps(claim) if claim is not None else None,
        },
    )


async def conclude_outreach(
    conn: AsyncConnection, request_id: str, check_id: str, now: datetime
) -> None:
    """Link the outreach to the check it produced and forget the claimed values: they have done their job."""
    await conn.execute(
        text(
            "UPDATE cred.outreach_request SET check_id = :c, claim = NULL, concluded_at = :n "
            "WHERE request_id = :r AND concluded_at IS NULL"
        ),
        {"c": uuid.UUID(check_id), "n": now, "r": uuid.UUID(request_id)},
    )


async def load_outreach(conn: AsyncConnection, request_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM cred.outreach_request WHERE request_id = :r FOR UPDATE"),
                {"r": uuid.UUID(request_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def save_outreach(
    conn: AsyncConnection,
    request_id: str,
    *,
    state: str,
    response_digest: bytes | None,
    outcome: str | None,
    reviews: dict[str, bool],
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.outreach_request SET state = :s, response_digest = :d, outcome = :o, "
            "reviews = CAST(:rv AS jsonb) WHERE request_id = :r"
        ),
        {
            "s": state,
            "d": response_digest,
            "o": outcome,
            "rv": json.dumps(reviews),
            "r": uuid.UUID(request_id),
        },
    )


async def insert_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_principal_id: str,
    kind: str,
    policy_id: str,
    policy_version: int,
    outcome: str,
    reasons: list[str],
    restrictions: list[str],
    check_ids: list[str],
    consumer: str | None,
) -> str:
    decision_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.eligibility_decision (decision_id, tenant_id, subject_principal_id, kind, "
            "policy_id, policy_version, outcome, reasons, restrictions, check_ids, consumer) "
            "VALUES (:id, :t, :s, :k, :p, :v, :o, :r, :rs, :c, :co)"
        ),
        {
            "id": uuid.UUID(decision_id),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(subject_principal_id),
            "k": kind,
            "p": policy_id,
            "v": policy_version,
            "o": outcome,
            "r": reasons,
            "rs": restrictions,
            "c": [uuid.UUID(c) for c in check_ids],
            "co": consumer,
        },
    )
    return decision_id


async def insert_appeal(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    check_id: str,
    raised_by: str,
    reason: str,
    correction: dict[str, Any],
) -> str:
    appeal_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO cred.claim_appeal (appeal_id, tenant_id, check_id, raised_by, reason, correction) "
            "VALUES (:a, :t, :c, :rb, :r, CAST(:co AS jsonb))"
        ),
        {
            "a": uuid.UUID(appeal_id),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(check_id),
            "rb": uuid.UUID(raised_by),
            "r": reason,
            "co": json.dumps(correction),
        },
    )
    return appeal_id


async def resolve_appeal(
    conn: AsyncConnection, appeal_id: str, *, state: str, resolved_by: str, resolution: str
) -> bool:
    """Only an OPEN appeal, and only by someone other than whoever raised it."""
    r = await conn.execute(
        text(
            "UPDATE cred.claim_appeal SET state = :s, resolved_by = :b, resolution = :r, resolved_at = now() "
            "WHERE appeal_id = :a AND state = 'OPEN' AND raised_by <> :b"
        ),
        {"s": state, "b": uuid.UUID(resolved_by), "r": resolution, "a": uuid.UUID(appeal_id)},
    )
    return bool(r.rowcount)


async def next_event_version(conn: AsyncConnection, aggregate_type: str, aggregate_id: str) -> int:
    """The next outbox version for an aggregate: versions must be unique and rising per aggregate, so a repeated event
    (a recheck announced again tomorrow) takes the next number rather than reusing one."""
    row = await conn.execute(
        text(
            "SELECT coalesce(max(aggregate_version), 0) + 1 FROM events.outbox_event "
            "WHERE aggregate_type = :t AND aggregate_id = :i"
        ),
        {"t": aggregate_type, "i": aggregate_id},
    )
    return int(row.scalar_one())


# ------------------------------------------------------------------------------------------------------ dead letters


async def dead_letter(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    check_id: str,
    event_id: str,
    status: str,
    observed_at: datetime,
    sequence: int | None,
    reason: str,
) -> tuple[str, bool]:
    """Keep an event that could not be applied. One row per (check, event): a redelivery only counts another attempt.
    Returns (letter_id, first_time)."""
    letter_id = uuid.uuid4()
    row = (
        await conn.execute(
            text(
                "INSERT INTO cred.source_dead_letter (letter_id, tenant_id, check_id, event_id, status, observed_at, "
                "sequence, reason) VALUES (:l, :t, :c, :e, :s, :o, :q, :r) "
                "ON CONFLICT (tenant_id, check_id, event_id) DO UPDATE "
                "SET attempts = cred.source_dead_letter.attempts + 1, reason = EXCLUDED.reason "
                "RETURNING letter_id, (xmax = 0) AS first_time"
            ),
            {
                "l": letter_id,
                "t": uuid.UUID(tenant_id),
                "c": uuid.UUID(check_id),
                "e": event_id,
                "s": status,
                "o": observed_at,
                "q": sequence,
                "r": reason,
            },
        )
    ).one()
    return str(row[0]), bool(row[1])


async def list_dead_letters(
    conn: AsyncConnection, *, state: str = "OPEN", limit: int = 100
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT letter_id, check_id, event_id, status, observed_at, sequence, reason, attempts, state, "
                    "received_at, resolved_at FROM cred.source_dead_letter WHERE state = :s "
                    "ORDER BY received_at LIMIT :n"
                ),
                {"s": state, "n": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def load_dead_letter(conn: AsyncConnection, letter_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text("SELECT * FROM cred.source_dead_letter WHERE letter_id = :l FOR UPDATE"),
                {"l": uuid.UUID(letter_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def resolve_dead_letter(
    conn: AsyncConnection, letter_id: str, *, state: str, by: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.source_dead_letter SET state = :s, resolved_at = :n, resolved_by = :b "
            "WHERE letter_id = :l AND state = 'OPEN'"
        ),
        {"s": state, "n": now, "b": uuid.UUID(by), "l": uuid.UUID(letter_id)},
    )


# ----------------------------------------------------------------------------------------------------------- dossier


async def subject_decisions(
    conn: AsyncConnection, subject_principal_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT decision_id, kind, policy_id, policy_version, outcome, reasons, restrictions, check_ids, "
                    "consumer, created_at FROM cred.eligibility_decision WHERE subject_principal_id = :s "
                    "ORDER BY created_at DESC LIMIT :n"
                ),
                {"s": uuid.UUID(subject_principal_id), "n": limit},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def load_decision(conn: AsyncConnection, decision_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT decision_id, subject_principal_id, kind, policy_id, policy_version, outcome, reasons, "
                    "restrictions, check_ids, consumer, created_at FROM cred.eligibility_decision "
                    "WHERE decision_id = :d"
                ),
                {"d": uuid.UUID(decision_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def subject_appeals(conn: AsyncConnection, subject_principal_id: str) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT a.appeal_id, a.check_id, a.state, a.created_at, a.resolved_at FROM cred.claim_appeal a "
                    "JOIN cred.claim_check c ON c.check_id = a.check_id WHERE c.subject_principal_id = :s "
                    "ORDER BY a.created_at DESC"
                ),
                {"s": uuid.UUID(subject_principal_id)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def latest_outcome(
    conn: AsyncConnection, subject_principal_id: str, policy_id: str, kind: str
) -> str | None:
    """The outcome of the most recent earlier decision for this person, policy and kind, or None if there is none."""
    row = (
        await conn.execute(
            text(
                "SELECT outcome FROM cred.eligibility_decision WHERE subject_principal_id = :s AND policy_id = :p "
                "AND kind = :k ORDER BY created_at DESC LIMIT 1"
            ),
            {"s": uuid.UUID(subject_principal_id), "p": policy_id, "k": kind},
        )
    ).first()
    return str(row[0]) if row else None


async def flag_for_review(
    conn: AsyncConnection,
    *,
    issuer_ids: Sequence[str],
    statement_id: str,
    reason: str,
    now: datetime,
) -> list[dict[str, Any]]:
    """Flag the CURRENT proofs issued under any of these issuers for review and make their source recheck due now. A proof the same statement
    already flagged is left alone, so one statement flags a proof once. Runs across tenants (the worker role)."""
    rows = (
        (
            await conn.execute(
                text(
                    "UPDATE cred.claim_monitor m SET review_statement_id = :s, review_reason = :r, review_requested_at = :n, "
                    "next_check_at = LEAST(m.next_check_at, :n), updated_at = :n FROM cred.claim_check c "
                    "WHERE c.check_id = m.check_id AND c.superseded_at IS NULL "
                    "AND c.assertion ->> 'issuer_ref' = ANY(CAST(:ids AS text[])) "
                    "AND m.review_statement_id IS DISTINCT FROM :s "
                    "RETURNING m.check_id, m.tenant_id, m.claim_type"
                ),
                {"s": statement_id, "r": reason, "n": now, "ids": list(issuer_ids)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]
