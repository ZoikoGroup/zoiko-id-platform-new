"""Workload bindings listing and environment quarantine (DEC-F7 §3-4; CR-014). Tenant RLS."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection


async def bindings(conn: AsyncConnection, *, status: str | None = None) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.workload_binding WHERE (CAST(:s AS text) IS NULL OR status = :s) "
                    "ORDER BY valid_from DESC"
                ),
                {"s": status},
            )
        ).all()
    )


async def active_binding(conn: AsyncConnection, workload_identity_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM agent.workload_binding WHERE workload_identity_id = :w AND status = 'ACTIVE'"
            ),
            {"w": workload_identity_id},
        )
    ).first()


async def quarantine(
    conn: AsyncConnection, tenant_id: str, environment_id: str, reason: str, by: str | None
) -> int | None:
    """Quarantine an environment; returns the new security revision (None: already quarantined)."""
    revision = (
        (
            await conn.execute(
                text("""
                UPDATE agent.workload_binding SET security_revision = security_revision + 1
                WHERE environment_id = :e RETURNING security_revision
            """),
                {"e": environment_id},
            )
        )
        .scalars()
        .all()
    )
    rev = max(revision, default=0) + 1
    result = await conn.execute(
        text("""
            INSERT INTO agent.environment_quarantine (quarantine_id, tenant_id, environment_id,
                reason_code, security_revision, quarantined_by)
            VALUES (:id, :t, :e, :r, :rev, :by)
            ON CONFLICT (tenant_id, environment_id) WHERE lifted_at IS NULL DO NOTHING
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "e": environment_id,
            "r": reason,
            "rev": rev,
            "by": uuid.UUID(by) if by else None,
        },
    )
    return rev if result.rowcount else None


async def lift(conn: AsyncConnection, environment_id: str, by: str) -> bool:
    result = await conn.execute(
        text("""
            UPDATE agent.environment_quarantine SET lifted_at = now(), lifted_by = :by
            WHERE environment_id = :e AND lifted_at IS NULL
        """),
        {"e": environment_id, "by": uuid.UUID(by)},
    )
    return bool(result.rowcount)


async def add_attestation(
    conn: AsyncConnection,
    *,
    attestation_id: str,
    tenant_id: str,
    workload_identity_id: str,
    environment_id: str,
    artifact_digest: str,
    level: int,
    verifier: str,
    claims_commitment: str,
    status: str,
    failure_code: str | None,
    valid_until: Any,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.workload_attestation (attestation_id, tenant_id, workload_identity_id, "
            "environment_id, artifact_digest, level, verifier, claims_commitment, status, failure_code, valid_until) "
            "VALUES (:a, :t, :w, :e, :d, :l, :v, :c, :s, :f, :u)"
        ),
        {
            "a": uuid.UUID(attestation_id),
            "t": uuid.UUID(tenant_id),
            "w": workload_identity_id,
            "e": environment_id,
            "d": artifact_digest,
            "l": level,
            "v": verifier,
            "c": claims_commitment,
            "s": status,
            "f": failure_code,
            "u": valid_until,
        },
    )


async def verified_level(
    conn: AsyncConnection,
    workload_identity_id: str,
    environment_id: str,
    artifact_digest: str,
    now: Any,
) -> int:
    """The highest attestation level recorded as VALID and still in date for exactly this workload, environment and
    artifact; 0 when there is none. The token request's own claim is never an input."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(level), 0) FROM agent.workload_attestation WHERE status = 'VALID' "
                    "AND workload_identity_id = :w AND environment_id = :e AND artifact_digest = :d "
                    "AND valid_until > :n"
                ),
                {"w": workload_identity_id, "e": environment_id, "d": artifact_digest, "n": now},
            )
        ).scalar_one()
    )
