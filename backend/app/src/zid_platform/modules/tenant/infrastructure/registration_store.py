"""platform.tenant_registration: what a company declared when it registered (tenant_0007)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class Registration:
    tenant_id: str
    register_namespace: str
    register_identifier: str
    lei: str | None
    notice_version: str
    notice_accepted_at: datetime
    home_cell_id: str
    declared_at: datetime


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    namespace: str,
    identifier: str,
    lei: str | None,
    notice_version: str,
    accepted_at: datetime,
    home_cell_id: str,
) -> None:
    """`conn` is scoped to the new tenant (RLS)."""
    await conn.execute(
        text("""
            INSERT INTO platform.tenant_registration (tenant_id, register_namespace, register_identifier,
                lei, notice_version, notice_accepted_at, home_cell_id, declared_at)
            VALUES (:t, :ns, :id, :lei, :v, :at, :cell, :at)
        """),
        {
            "t": uuid.UUID(tenant_id),
            "ns": namespace,
            "id": identifier,
            "lei": lei,
            "v": notice_version,
            "at": accepted_at,
            "cell": home_cell_id,
        },
    )


async def load(conn: AsyncConnection, tenant_id: str) -> Registration | None:
    row = (
        await conn.execute(
            text("SELECT * FROM platform.tenant_registration WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        return None
    return Registration(
        str(row.tenant_id),
        row.register_namespace,
        row.register_identifier,
        row.lei,
        row.notice_version,
        row.notice_accepted_at,
        row.home_cell_id,
        row.declared_at,
    )
