"""Provider suspensions in PostgreSQL (ZID-RCG-003 RCG-G08; CR-078). Platform configuration: not tenant data, no RLS.

Append-only versions. The domain rules live in `domain/suspension.py`; the table repeats the ones that must hold even for a caller that skips the
domain code (migration verification_0009).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.suspension import (
    Scope,
    Status,
    Suspension,
    Trigger,
    check_lift,
    check_new,
)

_COLUMNS = (
    "suspension_id, version, provider, service_id, scheme, country, subdivision, purpose, profession, credential_class, trigger, status, "
    "reason, evidence_ref, effective_at, recorded_by, recorded_at"
)


def _row(r: Any) -> Suspension:
    return Suspension(
        suspension_id=r.suspension_id,
        version=r.version,
        scope=Scope(
            r.provider,
            r.service_id,
            r.scheme,
            r.country,
            r.subdivision,
            r.purpose,
            r.profession,
            r.credential_class,
        ),
        trigger=Trigger(r.trigger),
        status=Status(r.status),
        reason=r.reason,
        evidence_ref=r.evidence_ref,
        effective_at=r.effective_at,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
    )


async def history(conn: AsyncConnection, suspension_id: str) -> list[Suspension]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.provider_suspension WHERE suspension_id = :i ORDER BY version"
            ),
            {"i": suspension_id},
        )
    ).all()
    return [_row(r) for r in rows]


async def for_provider(conn: AsyncConnection, provider: str) -> list[Suspension]:
    """Every version of every suspension of this provider, oldest first (the gate asks with one provider)."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.provider_suspension WHERE provider = :p ORDER BY suspension_id, version"
            ),
            {"p": provider},
        )
    ).all()
    return [_row(r) for r in rows]


async def all_versions(conn: AsyncConnection, *, limit: int = 2000) -> list[Suspension]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.provider_suspension ORDER BY suspension_id, version LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def _insert(conn: AsyncConnection, s: Suspension) -> None:
    sc = s.scope
    await conn.execute(
        text(
            f"INSERT INTO verify.provider_suspension ({_COLUMNS}) VALUES (:id, :v, :p, :svc, :sch, :c, :sub, :pur, :prof, :cls, :t, :st, "
            ":r, :e, :ea, :by, :at)"
        ),
        {
            "id": s.suspension_id,
            "v": s.version,
            "p": sc.provider,
            "svc": sc.service_id,
            "sch": sc.scheme,
            "c": sc.country,
            "sub": sc.subdivision,
            "pur": sc.purpose,
            "prof": sc.profession,
            "cls": sc.credential_class,
            "t": s.trigger.value,
            "st": s.status.value,
            "r": s.reason,
            "e": s.evidence_ref,
            "ea": s.effective_at,
            "by": s.recorded_by,
            "at": s.recorded_at,
        },
    )


async def apply(conn: AsyncConnection, s: Suspension, *, now: datetime) -> Suspension:
    from zid_platform.modules.verification.domain.suspension import SuspensionError

    check_new(s, now=now)
    if await history(conn, s.suspension_id):
        raise SuspensionError("SUSPENSION_EXISTS", s.suspension_id)
    await _insert(conn, s)
    return s


async def lift(conn: AsyncConnection, s: Suspension, *, now: datetime) -> Suspension:
    check_lift(s, previous=await history(conn, s.suspension_id), now=now)
    await _insert(conn, s)
    return s
