"""PostgreSQL persistence of tenant domain verification (G-W2; CR-015)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.domains import DomainStatus, Method
from zid_platform.modules.tenant.domain.lifecycle import TenantError


@dataclass(frozen=True, slots=True)
class Domain:
    domain_id: str
    tenant_id: str
    domain: str
    method: Method
    challenge_token: str
    status: DomainStatus
    created_at: datetime
    expires_at: datetime
    verified_at: datetime | None
    last_checked_at: datetime | None
    reverify_due_at: datetime | None
    last_error: str | None


def _domain(r: Row[Any]) -> Domain:
    m = r._mapping
    return Domain(
        str(m["domain_id"]),
        str(m["tenant_id"]),
        m["domain"],
        Method(m["method"]),
        m["challenge_token"],
        DomainStatus(m["status"]),
        m["created_at"],
        m["expires_at"],
        m["verified_at"],
        m["last_checked_at"],
        m["reverify_due_at"],
        m["last_error"],
    )


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    domain: str,
    method: Method,
    token: str,
    created_by: str,
    expires_at: datetime,
) -> Domain:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tenant_domain (domain_id, tenant_id, domain, method,
                    challenge_token, created_by, expires_at)
                VALUES (:id, :t, :d, :m, :tok, :by, :exp) RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "d": domain,
                "m": method.value,
                "tok": token,
                "by": uuid.UUID(created_by),
                "exp": expires_at,
            },
        )
    ).one()
    return _domain(row)


async def load(conn: AsyncConnection, domain_id: str, *, lock: bool = False) -> Domain | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_domain WHERE domain_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(domain_id)},
        )
    ).first()
    return _domain(row) if row else None


async def list_domains(conn: AsyncConnection) -> list[Domain]:
    rows = (
        await conn.execute(text("SELECT * FROM platform.tenant_domain ORDER BY created_at DESC"))
    ).all()
    return [_domain(r) for r in rows]


async def set_status(
    conn: AsyncConnection,
    domain_id: str,
    status: DomainStatus,
    *,
    now: datetime,
    error: str | None = None,
    reverify_due_at: datetime | None = None,
) -> Domain:
    try:
        async with conn.begin_nested():  # a conflict must not abort the caller's transaction
            row = (
                await conn.execute(
                    text("""
                    UPDATE platform.tenant_domain SET status = :s, last_checked_at = :now,
                        last_error = :err,
                        verified_at = CASE WHEN :s = 'VERIFIED' AND verified_at IS NULL
                                           THEN CAST(:now AS timestamptz) ELSE verified_at END,
                        reverify_due_at = CAST(:due AS timestamptz)
                    WHERE domain_id = :id RETURNING *
                """),
                    {
                        "s": status.value,
                        "now": now,
                        "err": error,
                        "due": reverify_due_at,
                        "id": uuid.UUID(domain_id),
                    },
                )
            ).one()
    except IntegrityError as e:
        if "tenant_domain_verified_once" in str(e.orig):
            raise TenantError(
                "DOMAIN_ALREADY_CLAIMED",
                "Another tenant holds this domain; the platform's administrative review decides.",
            ) from e
        raise
    return _domain(row)


async def due_for_reverification(conn: AsyncConnection, now: datetime) -> list[Domain]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_domain WHERE status = 'VERIFIED' "
                "AND reverify_due_at <= :now ORDER BY reverify_due_at LIMIT 500"
            ),
            {"now": now},
        )
    ).all()
    return [_domain(r) for r in rows]


async def verified_count(conn: AsyncConnection) -> int:
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM platform.tenant_domain WHERE status = 'VERIFIED'")
            )
        ).scalar_one()
    )
