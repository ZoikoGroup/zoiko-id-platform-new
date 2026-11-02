"""Persistence for company-verification cases and their append-only evidence (ZID-ED-03; CR-043). `conn` is scoped to
the tenant: row-level security does the isolation, and every query still names the tenant."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.organization.domain.kyb import KybError, KybStatus


@dataclass(frozen=True, slots=True)
class CaseRow:
    case_id: str
    tenant_id: str
    jurisdiction: str
    registration_number: str
    applicant_name: str
    entity_form: str | None
    lei: str | None
    register_id: str | None
    idempotency_key: str
    status: KybStatus
    review_reason: str | None
    requested_by: str
    current_version: int
    expiry_at: datetime | None


@dataclass(frozen=True, slots=True)
class EvidenceRow:
    evidence_id: str
    case_id: str
    version: int
    decision_status: KybStatus
    review_reason: str | None
    reasons: tuple[str, ...]
    body: dict[str, Any]
    evidence_hash: str
    jws: str
    policy_version: str
    decided_by: str | None
    rationale: str | None
    expiry_at: datetime | None
    created_at: datetime


_CASE = (
    "case_id, tenant_id, jurisdiction, registration_number, applicant_name, entity_form, lei, "
    "register_id, idempotency_key, status, review_reason, requested_by, current_version, expiry_at"
)


def _case(r: Any) -> CaseRow:
    return CaseRow(
        case_id=str(r.case_id),
        tenant_id=str(r.tenant_id),
        jurisdiction=r.jurisdiction,
        registration_number=r.registration_number,
        applicant_name=r.applicant_name,
        entity_form=r.entity_form,
        lei=r.lei,
        register_id=r.register_id,
        idempotency_key=r.idempotency_key,
        status=KybStatus(r.status),
        review_reason=r.review_reason,
        requested_by=str(r.requested_by),
        current_version=int(r.current_version),
        expiry_at=r.expiry_at,
    )


async def case_by_key(conn: AsyncConnection, tenant_id: str, key: str) -> CaseRow | None:
    row = (
        await conn.execute(
            text(f"SELECT {_CASE} FROM org.kyb_case WHERE tenant_id = :t AND idempotency_key = :k"),
            {"t": uuid.UUID(tenant_id), "k": key},
        )
    ).first()
    return _case(row) if row else None


async def load_case(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, lock: bool = False
) -> CaseRow:
    row = (
        await conn.execute(
            text(
                f"SELECT {_CASE} FROM org.kyb_case WHERE tenant_id = :t AND case_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).first()
    if row is None:
        raise KybError("CASE_NOT_FOUND")
    return _case(row)


async def create_case(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    case_id: str,
    jurisdiction: str,
    registration_number: str,
    applicant_name: str,
    entity_form: str | None,
    lei: str | None,
    register_id: str | None,
    idempotency_key: str,
    requested_by: str,
    now: datetime,
) -> bool:
    """False when the key already names a case: a concurrent twin got there first and this request adds nothing."""
    created = await conn.execute(
        text(
            "INSERT INTO org.kyb_case (case_id, tenant_id, jurisdiction, registration_number, applicant_name, "
            "entity_form, lei, register_id, idempotency_key, status, requested_by, created_at, updated_at) "
            "VALUES (:c, :t, :j, :n, :a, :f, :l, :g, :k, 'IN_PROGRESS', :r, :now, :now) "
            "ON CONFLICT (tenant_id, idempotency_key) DO NOTHING RETURNING case_id"
        ),
        {
            "c": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "j": jurisdiction,
            "n": registration_number,
            "a": applicant_name,
            "f": entity_form,
            "l": lei,
            "g": register_id,
            "k": idempotency_key,
            "r": uuid.UUID(requested_by),
            "now": now,
        },
    )
    return created.first() is not None


async def append_evidence(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    case_id: str,
    version: int,
    status: KybStatus,
    review_reason: str | None,
    reasons: tuple[str, ...],
    body: dict[str, Any],
    evidence_hash: str,
    jws: str,
    policy_version: str,
    decided_by: str | None,
    rationale: str | None,
    observed_at: datetime,
    expiry_at: datetime | None,
    now: datetime,
) -> str:
    evidence_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO org.kyb_evidence (evidence_id, tenant_id, case_id, version, decision_status, "
            "review_reason, reasons, body, evidence_hash, jws, policy_version, decided_by, rationale, "
            "observed_at, expiry_at, created_at) VALUES (:e, :t, :c, :v, :s, :rr, CAST(:rs AS jsonb), "
            "CAST(:b AS jsonb), :h, :j, :p, :d, :ra, :o, :x, :now)"
        ),
        {
            "e": uuid.UUID(evidence_id),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
            "v": version,
            "s": status.value,
            "rr": review_reason,
            "rs": json.dumps(list(reasons)),
            "b": json.dumps(body, default=str),
            "h": evidence_hash,
            "j": jws,
            "p": policy_version,
            "d": uuid.UUID(decided_by) if decided_by else None,
            "ra": rationale,
            "o": observed_at,
            "x": expiry_at,
            "now": now,
        },
    )
    await conn.execute(
        text(
            "UPDATE org.kyb_case SET status = :s, review_reason = :rr, current_version = :v, expiry_at = :x, "
            "updated_at = :now WHERE tenant_id = :t AND case_id = :c"
        ),
        {
            "s": status.value,
            "rr": review_reason,
            "v": version,
            "x": expiry_at,
            "now": now,
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
        },
    )
    return evidence_id


async def evidence_history(
    conn: AsyncConnection, tenant_id: str, case_id: str
) -> list[EvidenceRow]:
    rows = (
        await conn.execute(
            text(
                "SELECT evidence_id, case_id, version, decision_status, review_reason, reasons, body, "
                "evidence_hash, jws, policy_version, decided_by, rationale, expiry_at, created_at "
                "FROM org.kyb_evidence WHERE tenant_id = :t AND case_id = :c ORDER BY version"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        EvidenceRow(
            evidence_id=str(r.evidence_id),
            case_id=str(r.case_id),
            version=int(r.version),
            decision_status=KybStatus(r.decision_status),
            review_reason=r.review_reason,
            reasons=tuple(r.reasons or ()),
            body=dict(r.body),
            evidence_hash=r.evidence_hash,
            jws=r.jws,
            policy_version=r.policy_version,
            decided_by=str(r.decided_by) if r.decided_by else None,
            rationale=r.rationale,
            expiry_at=r.expiry_at,
            created_at=r.created_at,
        )
        for r in rows
    ]


async def latest_case_id(conn: AsyncConnection, tenant_id: str) -> str | None:
    """The most recent company-verification case of this tenant (row-level security scopes it), or None if none was opened."""
    row = (
        await conn.execute(
            text(
                "SELECT case_id FROM org.kyb_case WHERE tenant_id = :t ORDER BY created_at DESC LIMIT 1"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return None if row is None else str(row.case_id)
