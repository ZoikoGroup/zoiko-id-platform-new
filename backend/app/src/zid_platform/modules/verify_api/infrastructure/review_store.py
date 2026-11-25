"""Review cases in PostgreSQL (CR-167). Company reads and writes are scoped by row-level security; the operator plane places and releases holds inside `go_live_store.operator_scope`.

A case is changed only with its row locked (`get_case(..., lock=True)`) and its version bumped (`bump`), so two changes cannot interleave. The event log is append-only (a trigger refuses any update) and each event carries the
digest of the one before it."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.domain import review as r

_CASE = (
    "case_id, tenant_id, session_id, case_type, parent_case_id, status, case_version, queue, risk_class, four_eyes_required, required_checks, findings, policy_version, region, "
    "first_reviewer, final_outcome, final_decision_id, created_by, created_at, sla_due_at, updated_at"
)


def _row(x: Any) -> dict[str, Any]:
    return dict(x._mapping)


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


# ------------------------------------------------------------------------------- cases


async def insert_case(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    session_id: str,
    case_type: str,
    parent: str | None,
    queue: str,
    four_eyes: bool,
    required: list[str],
    policy_version: str,
    region: str,
    created_by: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_case (case_id, tenant_id, session_id, case_type, parent_case_id, status, queue, four_eyes_required, required_checks, policy_version, region, created_by, sla_due_at) "
            "VALUES (:c, :t, :s, :ct, :p, 'QUEUED', :q, :f, :req, :pv, :rg, :by, :due)"
        ),
        {
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "s": session_id,
            "ct": case_type,
            "p": parent,
            "q": queue,
            "f": four_eyes,
            "req": required,
            "pv": policy_version,
            "rg": region,
            "by": uuid.UUID(created_by),
            "due": now + r.SLA,
        },
    )


async def get_case(
    conn: AsyncConnection, case_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_review_case WHERE case_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": case_id},
        )
    ).first()
    return _row(row) if row else None


async def list_cases(
    conn: AsyncConnection, *, status: str | None, queue: str | None, limit: int
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_review_case WHERE (CAST(:s AS text) IS NULL OR status = :s) AND (CAST(:q AS text) IS NULL OR queue = :q) ORDER BY sla_due_at, created_at LIMIT :n"
            ),
            {"s": status, "q": queue, "n": limit},
        )
    ).all()
    return [_row(x) for x in rows]


async def case_for_session(
    conn: AsyncConnection, session_id: str, *, open_only: bool = True
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_review_case WHERE session_id = :s"
                + (" AND status <> 'APPROVED_FINAL'" if open_only else "")
                + " ORDER BY created_at DESC LIMIT 1"
            ),
            {"s": session_id},
        )
    ).first()
    return _row(row) if row else None


async def appeal_of(conn: AsyncConnection, parent: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_review_case WHERE parent_case_id = :p ORDER BY created_at DESC LIMIT 1"
            ),
            {"p": parent},
        )
    ).first()
    return _row(row) if row else None


async def bump(
    conn: AsyncConnection,
    case_id: str,
    *,
    status: str,
    now: datetime,
    queue: str | None = None,
    first_reviewer: str | None = None,
    findings: dict[str, str] | None = None,
    final: tuple[str, str] | None = None,
) -> None:
    """Move a case to a new state and a new version. `final` is (outcome, decision id)."""
    await conn.execute(
        text(
            "UPDATE verify.api_review_case SET status = :s, case_version = case_version + 1, updated_at = :n, queue = COALESCE(:q, queue), "
            "first_reviewer = COALESCE(:fr, first_reviewer), findings = COALESCE(CAST(:f AS jsonb), findings), "
            "final_outcome = COALESCE(:fo, final_outcome), final_decision_id = COALESCE(:fd, final_decision_id) WHERE case_id = :c"
        ),
        {
            "c": case_id,
            "s": status,
            "n": now,
            "q": queue,
            "fr": _u(first_reviewer),
            "f": json.dumps(findings) if findings is not None else None,
            "fo": final[0] if final else None,
            "fd": final[1] if final else None,
        },
    )


# ------------------------------------------------------------------------------- leases


async def expire_leases(conn: AsyncConnection, case_id: str, now: datetime) -> None:
    """A lease past its time is released, so it can be claimed again. The runtime role cannot delete: the row stays, marked."""
    await conn.execute(
        text(
            "UPDATE verify.api_review_lease SET released_at = :n WHERE case_id = :c AND released_at IS NULL AND expires_at <= :n"
        ),
        {"c": case_id, "n": now},
    )


async def live_lease(conn: AsyncConnection, case_id: str, now: datetime) -> r.Lease | None:
    row = (
        await conn.execute(
            text(
                "SELECT lease_id, reviewer, expires_at, released_at FROM verify.api_review_lease WHERE case_id = :c AND released_at IS NULL ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": case_id},
        )
    ).first()
    if row is None:
        return None
    return r.Lease(
        str(row.lease_id),
        str(row.reviewer),
        row.expires_at,
        row.released_at is not None or row.expires_at <= now,
    )


async def insert_lease(
    conn: AsyncConnection,
    *,
    lease_id: str,
    case_id: str,
    tenant_id: str,
    reviewer: str,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_lease (lease_id, case_id, tenant_id, reviewer, expires_at) VALUES (:l, :c, :t, :r, :x)"
        ),
        {
            "l": uuid.UUID(lease_id),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(reviewer),
            "x": expires_at,
        },
    )


async def release_lease(conn: AsyncConnection, case_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_review_lease SET released_at = :n WHERE case_id = :c AND released_at IS NULL"
        ),
        {"c": case_id, "n": now},
    )


async def extend_lease(conn: AsyncConnection, case_id: str, expires_at: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_review_lease SET expires_at = :x WHERE case_id = :c AND released_at IS NULL"
        ),
        {"c": case_id, "x": expires_at},
    )


# ------------------------------------------------------------------------------- evidence


async def insert_evidence(
    conn: AsyncConnection,
    *,
    evidence_id: str,
    case_id: str,
    tenant_id: str,
    kind: str,
    label: str,
    classification: str,
    content_hash: str,
    summary: dict[str, Any],
    detail: dict[str, Any],
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_evidence (evidence_id, case_id, tenant_id, kind, label, classification, content_hash, summary, detail) "
            "VALUES (:e, :c, :t, :k, :l, :cl, :h, CAST(:s AS jsonb), CAST(:d AS jsonb))"
        ),
        {
            "e": evidence_id,
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "k": kind,
            "l": label,
            "cl": classification,
            "h": content_hash,
            "s": json.dumps(summary),
            "d": json.dumps(detail),
        },
    )


async def evidence(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT evidence_id, kind, label, classification, content_hash, summary FROM verify.api_review_evidence WHERE case_id = :c ORDER BY created_at, evidence_id"
            ),
            {"c": case_id},
        )
    ).all()
    return [_row(x) for x in rows]


async def evidence_detail(
    conn: AsyncConnection, case_id: str, evidence_id: str
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT evidence_id, kind, label, classification, content_hash, summary, detail FROM verify.api_review_evidence WHERE case_id = :c AND evidence_id = :e"
            ),
            {"c": case_id, "e": evidence_id},
        )
    ).first()
    return _row(row) if row else None


async def count_reveals(conn: AsyncConnection, case_id: str) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM verify.api_review_reveal WHERE case_id = :c"),
                {"c": case_id},
            )
        ).scalar_one()
    )


async def insert_reveal(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    evidence_id: str,
    reviewer: str,
    reason: str,
    expires_at: datetime,
) -> str:
    rid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_reveal (reveal_id, case_id, tenant_id, evidence_id, reviewer, reason, expires_at) VALUES (:i, :c, :t, :e, :r, :why, :x)"
        ),
        {
            "i": uuid.UUID(rid),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "e": evidence_id,
            "r": uuid.UUID(reviewer),
            "why": reason,
            "x": expires_at,
        },
    )
    return rid


# ------------------------------------------------------------------------------- proposals and second reviews


async def insert_proposal(
    conn: AsyncConnection,
    *,
    proposal_id: str,
    case_id: str,
    tenant_id: str,
    case_version: int,
    reviewer: str,
    outcome: str,
    reason_code: str,
    manifest: str,
    policy_version: str,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_review_proposal SET status = 'SUPERSEDED' WHERE case_id = :c AND status = 'OPEN'"
        ),
        {"c": case_id},
    )
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_proposal (proposal_id, case_id, tenant_id, case_version, reviewer, outcome, reason_code, evidence_manifest_hash, policy_version) "
            "VALUES (:p, :c, :t, :v, :r, :o, :rc, :m, :pv)"
        ),
        {
            "p": uuid.UUID(proposal_id),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "v": case_version,
            "r": uuid.UUID(reviewer),
            "o": outcome,
            "rc": reason_code,
            "m": manifest,
            "pv": policy_version,
        },
    )


async def open_proposal(conn: AsyncConnection, case_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT proposal_id, case_id, case_version, reviewer, outcome, reason_code, evidence_manifest_hash, policy_version, status, created_at FROM verify.api_review_proposal WHERE case_id = :c AND status = 'OPEN' ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": case_id},
        )
    ).first()
    return _row(row) if row else None


async def set_proposal_status(conn: AsyncConnection, proposal_id: str, status: str) -> None:
    await conn.execute(
        text("UPDATE verify.api_review_proposal SET status = :s WHERE proposal_id = :p"),
        {"s": status, "p": uuid.UUID(proposal_id)},
    )


async def insert_second(
    conn: AsyncConnection,
    *,
    proposal_id: str,
    case_id: str,
    tenant_id: str,
    reviewer: str,
    first_reviewer: str,
    decision: str,
    answers: dict[str, Any],
    reason_code: str | None,
    note: str | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_second (approval_id, proposal_id, case_id, tenant_id, reviewer, first_reviewer, decision, challenge_answers, reason_code, note) "
            "VALUES (:a, :p, :c, :t, :r, :f, :d, CAST(:ans AS jsonb), :rc, :n)"
        ),
        {
            "a": uuid.uuid4(),
            "p": uuid.UUID(proposal_id),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(reviewer),
            "f": uuid.UUID(first_reviewer),
            "d": decision,
            "ans": json.dumps(answers),
            "rc": reason_code,
            "n": note,
        },
    )


# ------------------------------------------------------------------------------- the event chain


async def append_event(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    event_type: str,
    actor: str,
    reason: str | None,
    data: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    last = (
        await conn.execute(
            text(
                "SELECT seq, digest FROM verify.api_review_event WHERE case_id = :c ORDER BY seq DESC LIMIT 1"
            ),
            {"c": case_id},
        )
    ).first()
    seq, prev = (last.seq + 1, last.digest) if last else (1, r.GENESIS)
    digest = r.event_digest(
        prev,
        seq=seq,
        case_id=case_id,
        event_type=event_type,
        actor=actor,
        at=now,
        reason=reason,
        data=data,
    )
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_event (case_id, seq, tenant_id, event_type, actor, reason, data, prev_digest, digest, created_at) "
            "VALUES (:c, :s, :t, :ty, :a, :r, CAST(:d AS jsonb), :p, :dg, :n)"
        ),
        {
            "c": case_id,
            "s": seq,
            "t": uuid.UUID(tenant_id),
            "ty": event_type,
            "a": actor,
            "r": reason,
            "d": json.dumps(data, default=str),
            "p": prev,
            "dg": digest,
            "n": now,
        },
    )
    return {"seq": seq, "digest": digest}


async def events(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT case_id, seq, event_type, actor, reason, data, prev_digest, digest, created_at FROM verify.api_review_event WHERE case_id = :c ORDER BY seq"
            ),
            {"c": case_id},
        )
    ).all()
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- holds (the operator plane writes)


async def active_hold(conn: AsyncConnection, case_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "SELECT hold_id, case_id, reason_code, placed_at FROM verify.api_review_hold WHERE case_id = :c AND status = 'ACTIVE'"
            ),
            {"c": case_id},
        )
    ).first()
    return _row(row) if row else None


async def place_hold(
    conn: AsyncConnection, *, case_id: str, tenant_id: str, reason_code: str, operator: str
) -> str:
    hid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_hold (hold_id, case_id, tenant_id, reason_code, placed_by) VALUES (:h, :c, :t, :r, :o)"
        ),
        {
            "h": uuid.UUID(hid),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "r": reason_code,
            "o": uuid.UUID(operator),
        },
    )
    return hid


async def release_hold(
    conn: AsyncConnection, case_id: str, operator: str, note: str, now: datetime
) -> bool:
    row = (
        await conn.execute(
            text(
                "UPDATE verify.api_review_hold SET status = 'RELEASED', released_by = :o, released_at = :n, note = :note WHERE case_id = :c AND status = 'ACTIVE' AND placed_by <> :o RETURNING hold_id"
            ),
            {"c": case_id, "o": uuid.UUID(operator), "n": now, "note": note},
        )
    ).first()
    return row is not None


async def cases_for_operator(conn: AsyncConnection, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM verify.api_review_case WHERE status <> 'APPROVED_FINAL' ORDER BY sla_due_at LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(x) for x in rows]


# ------------------------------------------------------------------------------- advice


async def count_advice(conn: AsyncConnection, case_id: str) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM verify.api_review_advice WHERE case_id = :c"),
                {"c": case_id},
            )
        ).scalar_one()
    )


async def insert_advice(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    case_version: int,
    model_version: str,
    requested_by: str,
    status: str,
    body: dict[str, Any],
    flags: list[str],
) -> str:
    aid = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.api_review_advice (advice_id, case_id, tenant_id, case_version, model_version, requested_by, status, body, flags) "
            "VALUES (:a, :c, :t, :v, :m, :r, :s, CAST(:b AS jsonb), :f)"
        ),
        {
            "a": uuid.UUID(aid),
            "c": case_id,
            "t": uuid.UUID(tenant_id),
            "v": case_version,
            "m": model_version,
            "r": uuid.UUID(requested_by),
            "s": status,
            "b": json.dumps(body),
            "f": flags,
        },
    )
    return aid


async def advice_for(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT advice_id, case_version, model_version, status, body, flags, human_feedback, created_at FROM verify.api_review_advice WHERE case_id = :c ORDER BY created_at DESC LIMIT 10"
            ),
            {"c": case_id},
        )
    ).all()
    return [_row(x) for x in rows]


async def set_feedback(conn: AsyncConnection, advice_id: str, case_id: str, feedback: str) -> bool:
    row = (
        await conn.execute(
            text(
                "UPDATE verify.api_review_advice SET human_feedback = :f WHERE advice_id = :a AND case_id = :c RETURNING advice_id"
            ),
            {"f": feedback, "a": uuid.UUID(advice_id), "c": case_id},
        )
    ).first()
    return row is not None


async def reviewers_of(conn: AsyncConnection, case_id: str) -> set[str]:
    """Everyone who took part in a case's decision: those who proposed and those who gave a second review."""
    rows = (
        await conn.execute(
            text(
                "SELECT reviewer FROM verify.api_review_proposal WHERE case_id = :c UNION SELECT reviewer FROM verify.api_review_second WHERE case_id = :c"
            ),
            {"c": case_id},
        )
    ).all()
    return {str(x.reviewer) for x in rows}
