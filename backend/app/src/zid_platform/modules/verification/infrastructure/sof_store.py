"""Source of funds cases (DTV-28; CR-096). Fixed statements only; row-level security scopes every read to the caller's company."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

CASE_COLUMNS = (
    "case_id, subject_principal_id, country, questionnaire_code, questionnaire_version, questionnaire_hash, state, answers, evidence, problems, "
    "created_by, submitted_by, submitted_at, first_reviewer, valid_until, created_at, updated_at, erased_at, row_version"
)


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def insert_case(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject: str,
    country: str,
    code: str,
    version: str,
    content_hash: str,
    created_by: str,
) -> dict[str, Any]:
    case_id = uuid.uuid4()
    row = (
        await conn.execute(
            text(
                "INSERT INTO verify.sof_case (case_id, tenant_id, subject_principal_id, country, questionnaire_code, questionnaire_version, "
                "questionnaire_hash, created_by) VALUES (:c, :t, :s, :k, :qc, :qv, :qh, :by) RETURNING "
                + CASE_COLUMNS
            ),
            {
                "c": case_id,
                "t": uuid.UUID(tenant_id),
                "s": uuid.UUID(subject),
                "k": country,
                "qc": code,
                "qv": version,
                "qh": content_hash,
                "by": uuid.UUID(created_by),
            },
        )
    ).first()
    assert row is not None
    return _row(row)


async def load_case(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT " + CASE_COLUMNS + " FROM verify.sof_case WHERE case_id = :c"
    row = (
        await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"c": uuid.UUID(case_id)})
    ).first()
    return _row(row) if row else None


async def save_submission(
    conn: AsyncConnection,
    case_id: str,
    *,
    answers: dict[str, Any],
    evidence: list[dict[str, str]],
    problems: list[str],
    state: str,
    submitted_by: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.sof_case SET answers = CAST(:a AS jsonb), evidence = CAST(:e AS jsonb), problems = CAST(:p AS text[]), state = :s, "
            "submitted_by = :by, submitted_at = :now, updated_at = :now, row_version = row_version + 1 WHERE case_id = :c"
        ),
        {
            "a": json.dumps(answers),
            "e": json.dumps(evidence),
            "p": problems,
            "s": state,
            "by": uuid.UUID(submitted_by),
            "now": now,
            "c": uuid.UUID(case_id),
        },
    )


async def set_state(
    conn: AsyncConnection,
    case_id: str,
    state: str,
    *,
    valid_until: datetime | None,
    first_reviewer: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.sof_case SET state = :s, valid_until = :v, first_reviewer = coalesce(CAST(:r AS uuid), first_reviewer), "
            "updated_at = :now, row_version = row_version + 1 WHERE case_id = :c"
        ),
        {"s": state, "v": valid_until, "r": first_reviewer, "now": now, "c": uuid.UUID(case_id)},
    )


async def add_decision(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    case_id: str,
    decision: str,
    from_state: str,
    to_state: str,
    reasons: list[str],
    decided_by: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.sof_decision (decision_id, case_id, tenant_id, decision, from_state, to_state, reason_codes, decided_by, decided_at) "
            "VALUES (:d, :c, :t, :dec, :f, :to, CAST(:r AS text[]), :by, :now)"
        ),
        {
            "d": uuid.uuid4(),
            "c": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "dec": decision,
            "f": from_state,
            "to": to_state,
            "r": reasons,
            "by": uuid.UUID(decided_by),
            "now": now,
        },
    )


async def decisions_for(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT decision, from_state, to_state, reason_codes, decided_by, decided_at FROM verify.sof_decision "
                "WHERE case_id = :c ORDER BY decided_at, decision_id"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def list_cases(conn: AsyncConnection, state: str | None, limit: int) -> list[dict[str, Any]]:
    """The queue: no answers and no evidence, only what a reviewer needs to pick a case."""
    rows = (
        await conn.execute(
            text(
                "SELECT case_id, subject_principal_id, country, questionnaire_version, state, submitted_at, valid_until, created_at "
                "FROM verify.sof_case WHERE (CAST(:s AS text) IS NULL OR state = :s) AND erased_at IS NULL "
                "ORDER BY coalesce(submitted_at, created_at), case_id LIMIT :n"
            ),
            {"s": state, "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def cases_for_subject(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT case_id, state, created_at FROM verify.sof_case WHERE subject_principal_id = :p"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [_row(r) for r in rows]


async def erase_case(conn: AsyncConnection, case_id: str, now: datetime) -> None:
    """Governed erasure: the answers and the evidence references go, and the case can no longer be decided. State and the decision record stay."""
    await conn.execute(
        text(
            "UPDATE verify.sof_case SET answers = '{}'::jsonb, evidence = '[]'::jsonb, problems = '{}', erased_at = :now, updated_at = :now, "
            "state = CASE WHEN state IN ('CLEARED', 'NOT_CLEARED_FINAL', 'CANCELED') THEN state ELSE 'CANCELED' END, "
            "valid_until = CASE WHEN state = 'CLEARED' THEN valid_until ELSE NULL END, row_version = row_version + 1 WHERE case_id = :c"
        ),
        {"now": now, "c": uuid.UUID(case_id)},
    )
