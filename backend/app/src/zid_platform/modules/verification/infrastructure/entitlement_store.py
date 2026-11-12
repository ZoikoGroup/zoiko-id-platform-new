"""Provider entitlement statements in PostgreSQL (ZID-RCG-003 RCG-G03; CR-074). Platform configuration: not tenant data, no RLS.

Append-only: recording a statement never edits another, and an earlier one keeps answering for the moment it was the latest. The domain rules live
in `domain/entitlement.py`; the table repeats the ones that must hold even for a caller that skips the domain code (migration verification_0008).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.entitlement import (
    Decision,
    Grant,
    Scope,
    Status,
    check_grant,
    check_scope,
    decide,
)

_COLUMNS = "grant_id, provider, country, capability, purpose, status, effective_at, valid_from, valid_until, contract_ref, terms_ref, recorded_by, recorded_at"


def _grant(r: Any) -> Grant:
    return Grant(
        grant_id=r.grant_id,
        scope=Scope(r.provider, r.country, r.capability, r.purpose),
        status=Status(r.status),
        effective_at=r.effective_at,
        valid_from=r.valid_from,
        valid_until=r.valid_until,
        contract_ref=r.contract_ref,
        terms_ref=r.terms_ref,
        recorded_by=r.recorded_by,
        recorded_at=r.recorded_at,
    )


def _key(s: Scope) -> dict[str, str]:
    return {"p": s.provider, "c": s.country, "k": s.capability, "u": s.purpose}


async def history(conn: AsyncConnection, scope: Scope) -> list[Grant]:
    """Every statement for the exact scope, oldest first."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLUMNS} FROM verify.provider_entitlement "
                "WHERE provider = :p AND country = :c AND capability = :k AND purpose = :u ORDER BY effective_at"
            ),
            _key(scope),
        )
    ).all()
    return [_grant(r) for r in rows]


async def record(conn: AsyncConnection, grant: Grant, *, now: datetime) -> Grant:
    """Check the statement against the domain rules and the ones before it, then append it."""
    check_grant(grant, now=now, previous=await history(conn, grant.scope))
    s = grant.scope
    await conn.execute(
        text(
            f"INSERT INTO verify.provider_entitlement ({_COLUMNS}) VALUES (:id, :p, :c, :k, :u, :st, :ea, :vf, :vu, :cr, :tr, :by, :ra)"
        ),
        {
            "id": grant.grant_id,
            **_key(s),
            "st": grant.status.value,
            "ea": grant.effective_at,
            "vf": grant.valid_from,
            "vu": grant.valid_until,
            "cr": grant.contract_ref,
            "tr": grant.terms_ref,
            "by": grant.recorded_by,
            "ra": grant.recorded_at,
        },
    )
    return grant


async def decide_scope(conn: AsyncConnection, scope: Scope, *, at: datetime) -> Decision:
    check_scope(scope)
    return decide(await history(conn, scope), scope, at)


async def decide_scopes(
    conn: AsyncConnection, scopes: Sequence[Scope], *, at: datetime
) -> dict[Scope, Decision]:
    return {s: await decide_scope(conn, s, at=at) for s in scopes}


async def scopes_of(
    conn: AsyncConnection, *, provider: str | None = None, limit: int = 200
) -> list[Scope]:
    """The distinct scopes anyone has recorded a statement for, optionally one provider's."""
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT provider, country, capability, purpose FROM verify.provider_entitlement "
                "WHERE CAST(:p AS text) IS NULL OR provider = :p ORDER BY provider, country, capability, purpose LIMIT :n"
            ),
            {"p": provider, "n": limit},
        )
    ).all()
    return [Scope(r.provider, r.country, r.capability, r.purpose) for r in rows]
