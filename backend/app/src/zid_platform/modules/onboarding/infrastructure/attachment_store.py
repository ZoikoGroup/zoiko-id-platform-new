"""Persistence for approval-request attachments (`platform.approval_attachment`, migration onboarding_0006). Tenant-scoped (RLS), append-only."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class AttachmentRow:
    attachment_id: str
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
    expires_at: datetime
    created_at: datetime


_COLS = (
    "attachment_id, case_id, kind, media_type, size_bytes, content_hash, object_id, label, scan_status, "
    "policy_version, uploaded_by, expires_at, created_at"
)


def _row(r: Any) -> AttachmentRow:
    return AttachmentRow(
        attachment_id=str(r.attachment_id),
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
        expires_at=r.expires_at,
        created_at=r.created_at,
    )


async def by_key(conn: AsyncConnection, tenant_id: str, key: str) -> AttachmentRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM platform.approval_attachment WHERE tenant_id = :t AND idempotency_key = :k"
            ),
            {"t": uuid.UUID(tenant_id), "k": key},
        )
    ).first()
    return _row(row) if row else None


async def by_content(
    conn: AsyncConnection, tenant_id: str, case_id: str, content_hash: str
) -> AttachmentRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM platform.approval_attachment "
                "WHERE tenant_id = :t AND case_id = :c AND content_hash = :h"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "h": content_hash},
        )
    ).first()
    return _row(row) if row else None


async def count(conn: AsyncConnection, tenant_id: str, case_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.approval_attachment WHERE tenant_id = :t AND case_id = :c"
                ),
                {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
            )
        ).scalar_one()
    )


async def load(
    conn: AsyncConnection, tenant_id: str, case_id: str, attachment_id: str
) -> AttachmentRow | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM platform.approval_attachment "
                "WHERE tenant_id = :t AND case_id = :c AND attachment_id = :a"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "a": uuid.UUID(attachment_id)},
        )
    ).first()
    return _row(row) if row else None


async def for_case(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[AttachmentRow]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM platform.approval_attachment "
                "WHERE tenant_id = :t AND case_id = :c ORDER BY created_at, attachment_id"
            ),
            {"t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def insert(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    attachment_id: str,
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
    expires_at: datetime,
    now: datetime,
) -> bool:
    """True if recorded; False if the same key or the same content for this request won a race (nothing is written twice)."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO platform.approval_attachment (attachment_id, tenant_id, case_id, kind, media_type, size_bytes, "
                "content_hash, object_id, label, scan_status, policy_version, idempotency_key, uploaded_by, expires_at, created_at) "
                "VALUES (:id, :t, :c, :kind, :media, :size, :hash, :obj, :label, :scan, :pv, :key, :by, :exp, :at) "
                "ON CONFLICT DO NOTHING RETURNING attachment_id"
            ),
            {
                "id": uuid.UUID(attachment_id), "t": uuid.UUID(tenant_id), "c": uuid.UUID(case_id), "kind": kind, "media": media_type,
                "size": size_bytes, "hash": content_hash, "obj": uuid.UUID(object_id), "label": label, "scan": scan_status,
                "pv": policy_version, "key": idempotency_key, "by": uuid.UUID(uploaded_by), "exp": expires_at, "at": now,
            },
        )
    ).first()  # fmt: skip
    return row is not None
