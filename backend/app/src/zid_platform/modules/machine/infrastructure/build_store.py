"""Approved builds in PostgreSQL (ZID-MACHINE-001 §3, §9; CR-064). Tenant transactions only: row-level security does the scoping."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_COLUMNS = (
    "build_id, name, artifact_digest, source_ref, provenance_ref, registered_by, review_status, approved_by, "
    "registered_at, approved_at, revoked_at, revoke_reason"
)


def _iso(v: Any) -> str | None:
    return v.isoformat() if v is not None else None


def _view(r: Any) -> dict[str, Any]:
    return {
        "build_id": str(r.build_id),
        "name": r.name,
        "artifact_digest": r.artifact_digest,
        "source_ref": r.source_ref,
        "provenance_ref": r.provenance_ref,
        "registered_by": str(r.registered_by),
        "review_status": r.review_status,
        "approved_by": str(r.approved_by) if r.approved_by else None,
        "registered_at": _iso(r.registered_at),
        "approved_at": _iso(r.approved_at),
        "revoked_at": _iso(r.revoked_at),
        "revoke_reason": r.revoke_reason,
    }


async def insert(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    build_id: str,
    name: str,
    artifact_digest: str,
    source_ref: str | None,
    provenance_ref: str | None,
    registered_by: str,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.build_artifact (build_id, tenant_id, name, artifact_digest, source_ref, provenance_ref, "
            "registered_by, review_status) VALUES (:i, :t, :n, :d, :s, :p, :r, 'DRAFT')"
        ),
        {
            "i": uuid.UUID(build_id),
            "t": uuid.UUID(tenant_id),
            "n": name,
            "d": artifact_digest,
            "s": source_ref,
            "p": provenance_ref,
            "r": uuid.UUID(registered_by),
        },
    )


async def get(conn: AsyncConnection, build_id: str, *, lock: bool = False) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLUMNS} FROM agent.build_artifact WHERE build_id = :i{' FOR UPDATE' if lock else ''}"),
            {"i": uuid.UUID(build_id)},
        )
    ).first()
    return _view(row) if row else None


async def by_digest(conn: AsyncConnection, artifact_digest: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_COLUMNS} FROM agent.build_artifact WHERE artifact_digest = :d"),
            {"d": artifact_digest},
        )
    ).first()
    return _view(row) if row else None


async def list_builds(conn: AsyncConnection, *, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM agent.build_artifact "
                "WHERE (CAST(:s AS text) IS NULL OR review_status = CAST(:s AS text)) "
                "ORDER BY registered_at DESC LIMIT :n"
            ),
            {"s": status, "n": limit},
        )
    ).all()
    return [_view(r) for r in rows]


async def approve(conn: AsyncConnection, build_id: str, approver: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.build_artifact SET review_status = 'APPROVED', approved_by = :a, approved_at = now() "
            "WHERE build_id = :i"
        ),
        {"a": uuid.UUID(approver), "i": uuid.UUID(build_id)},
    )


async def revoke(conn: AsyncConnection, build_id: str, reason: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.build_artifact SET review_status = 'REVOKED', revoked_at = now(), revoke_reason = :r "
            "WHERE build_id = :i"
        ),
        {"r": reason, "i": uuid.UUID(build_id)},
    )
