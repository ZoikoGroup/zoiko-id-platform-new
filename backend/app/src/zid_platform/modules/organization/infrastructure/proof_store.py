"""Persistence for proof-document records (`org.kyb_proof`, migration organization_0006). Rows are tenant-scoped (RLS) and append-only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class ProofRow:
    proof_id: str
    case_id: str
    kind: str
    media_type: str
    size_bytes: int
    content_hash: str
    object_id: str
    label: str
    scan_status: str
    policy_version: str
    uploaded_by: str
    created_at: datetime


_COLS = (
    "proof_id, case_id, kind, media_type, size_bytes, content_hash, object_id, label, scan_status, "
    "policy_version, uploaded_by, created_at"
)


def _row(r: Any) -> ProofRow:
    return ProofRow(
        proof_id=str(r.proof_id),
        case_id=str(r.case_id),
        kind=r.kind,
        media_type=r.media_type,
        size_bytes=int(r.size_bytes),
        content_hash=r.content_hash,
        object_id=str(r.object_id),
        label=r.label,
        scan_status=r.scan_status,
        policy_version=r.policy_version,
        uploaded_by=str(r.uploaded_by),
        created_at=r.created_at,
    )


async def by_key(conn: AsyncConnection, tenant_id: str, key: str) -> ProofRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM org.kyb_proof WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": uuid.UUID(tenant_id), "k": key},
        )
    ).first()
    return _row(row) if row else None


async def by_content(
    conn: AsyncConnection, tenant_id: str, case_id: str, content_hash: str
) -> ProofRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM org.kyb_proof WHERE tenant_id = :t AND case_id = :c AND content_hash = :h"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "h": content_hash},
        )
    ).first()
    return _row(row) if row else None


async def count(conn: AsyncConnection, tenant_id: str, case_id: str) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM org.kyb_proof WHERE tenant_id = :t AND case_id = :c"),
                {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
            )
        ).scalar_one()
    )


async def load(
    conn: AsyncConnection, tenant_id: str, case_id: str, proof_id: str
) -> ProofRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM org.kyb_proof WHERE tenant_id = :t AND case_id = :c AND proof_id = :p"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "p": uuid.UUID(proof_id)},
        )
    ).first()
    return _row(row) if row else None


async def for_case(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[ProofRow]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM org.kyb_proof WHERE tenant_id = :t AND case_id = :c ORDER BY created_at, proof_id"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def insert(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    proof_id: str,
    case_id: str,
    kind: str,
    media_type: str,
    size_bytes: int,
    content_hash: str,
    object_id: str,
    label: str,
    scan_status: str,
    policy_version: str,
    idempotency_key: str,
    uploaded_by: str,
    now: datetime,
) -> bool:
    """True if recorded; False if the same key or the same content for this claim won a race (nothing is written twice)."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO org.kyb_proof (proof_id, tenant_id, case_id, kind, media_type, size_bytes, content_hash, object_id, label, "
                "scan_status, policy_version, idempotency_key, uploaded_by, created_at) "
                "VALUES (:id, :t, :c, :kind, :media, :size, :hash, :obj, :label, :scan, :pv, :key, :by, :at) "
                "ON CONFLICT DO NOTHING RETURNING proof_id"
            ),
            {
                "id": uuid.UUID(proof_id), "t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "kind": kind, "media": media_type,
                "size": size_bytes, "hash": content_hash, "obj": uuid.UUID(object_id), "label": label, "scan": scan_status,
                "pv": policy_version, "key": idempotency_key, "by": uuid.UUID(uploaded_by), "at": now,
            },
        )
    ).first()  # fmt: skip
    return row is not None
