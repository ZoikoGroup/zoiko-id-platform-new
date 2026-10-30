"""The tenant's current signed entitlement snapshot, shared by every process (`commercial.entitlement_snapshot`; CR-045)."""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def load(conn: AsyncConnection) -> tuple[int, dict[str, Any], str] | None:
    """(revision, document, signature) of the tenant's row (RLS scopes the read), or None."""
    row = (
        await conn.execute(
            text("SELECT revision, doc, signed FROM commercial.entitlement_snapshot")
        )
    ).first()
    if row is None:
        return None
    doc = row.doc if isinstance(row.doc, dict) else json.loads(row.doc)
    return int(row.revision), doc, str(row.signed)


async def lock(conn: AsyncConnection, tenant_id: str) -> None:
    """Serialize publishers of one tenant's snapshot, so two processes cannot both announce the same change."""
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"),
        {"k": f"snapshot:{tenant_id}"},
    )


async def current(conn: AsyncConnection) -> tuple[int, str] | None:
    row = (
        await conn.execute(
            text("SELECT revision, fingerprint FROM commercial.entitlement_snapshot")
        )
    ).first()
    return None if row is None else (int(row.revision), str(row.fingerprint))


async def save(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    revision: int,
    fingerprint: str,
    catalog_version: str,
    issued_at: datetime,
    expires_at: datetime,
    doc: dict[str, object],
    signed: str,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO commercial.entitlement_snapshot
                (tenant_id, revision, fingerprint, catalog_version, issued_at, expires_at, doc, signed)
            VALUES (:t, :r, :fp, :cv, :ia, :ea, CAST(:doc AS jsonb), :sig)
            ON CONFLICT (tenant_id) DO UPDATE SET
                revision = EXCLUDED.revision, fingerprint = EXCLUDED.fingerprint,
                catalog_version = EXCLUDED.catalog_version, issued_at = EXCLUDED.issued_at,
                expires_at = EXCLUDED.expires_at, doc = EXCLUDED.doc, signed = EXCLUDED.signed,
                published_at = now()
        """),
        {
            "t": uuid.UUID(tenant_id),
            "r": revision,
            "fp": fingerprint,
            "cv": catalog_version,
            "ia": issued_at,
            "ea": expires_at,
            "doc": json.dumps(doc, sort_keys=True, separators=(",", ":")),
            "sig": signed,
        },
    )
