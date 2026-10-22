"""PostgreSQL store for principals and identifiers (ZID-DATA-001 §3; FR-IDN-001..007).

Runs inside the caller's tenant transaction (RLS). Lifecycle changes go through the domain state
machine and an optimistic row_version check; the database refuses a lower security_revision and
any change to principal identity. Identifier values are stored only as hashes of the normalized
value; scoped unique indexes turn a collision into IDENTIFIER_CONFLICT, never into a merge.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.principal.domain.principal import (
    DomainError,
    Identifier,
    Principal,
    PrincipalStatus,
    PrincipalType,
    change_status,
)


def value_hash(namespace: str, value_normalized: str) -> bytes:
    return hashlib.sha256(f"{namespace}\x00{value_normalized}".encode()).digest()


async def create_principal(conn: AsyncConnection, p: Principal) -> None:
    await conn.execute(
        text("""
            INSERT INTO core.principal (principal_id, tenant_id, principal_type, status, home_cell,
                                        sponsor_principal_id, security_revision)
            VALUES (:id, :t, CAST(:ty AS core.principal_type), CAST(:st AS core.principal_status), :cell,
                    :sp, :rev)
        """),
        {
            "id": uuid.UUID(p.principal_id),
            "t": uuid.UUID(p.tenant_id or ""),
            "ty": p.principal_type.value,
            "st": p.status.value,
            "cell": p.home_cell,
            "sp": uuid.UUID(p.sponsor_principal_id) if p.sponsor_principal_id else None,
            "rev": p.security_revision,
        },
    )


async def load_principal(conn: AsyncConnection, principal_id: str) -> tuple[Principal, int]:
    """The principal and its row_version (for the optimistic write)."""
    row = (
        await conn.execute(
            text(
                "SELECT principal_id, tenant_id, principal_type, status, home_cell, sponsor_principal_id, "
                "security_revision, superseded_by_principal_id, row_version FROM core.principal "
                "WHERE principal_id = :id"
            ),
            {"id": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None:
        raise DomainError("PRINCIPAL_NOT_FOUND")
    p = Principal(
        principal_id=str(row.principal_id),
        tenant_id=str(row.tenant_id),
        principal_type=PrincipalType(row.principal_type),
        status=PrincipalStatus(row.status),
        home_cell=row.home_cell,
        security_revision=row.security_revision,
        superseded_by=str(row.superseded_by_principal_id)
        if row.superseded_by_principal_id
        else None,
        sponsor_principal_id=str(row.sponsor_principal_id) if row.sponsor_principal_id else None,
        has_organization_profile=True,  # profile presence is checked by the organization module
    )
    return p, row.row_version


async def list_principals(
    conn: AsyncConnection,
    *,
    principal_type: PrincipalType | None = None,
    status: PrincipalStatus | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Directory rows, newest first: identifiers, type, state and times; no labels or identifiers."""
    where: list[str] = ["TRUE"]
    params: dict[str, Any] = {"lim": limit}
    if principal_type is not None:
        where.append("principal_type = CAST(:ty AS core.principal_type)")
        params["ty"] = principal_type.value
    if status is not None:
        where.append("status = CAST(:st AS core.principal_status)")
        params["st"] = status.value
    rows = (
        await conn.execute(
            text(
                "SELECT principal_id, principal_type, status, home_cell, security_revision, "
                f"created_at FROM core.principal WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC, principal_id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    return [
        {
            "principal_id": str(r.principal_id),
            "principal_type": r.principal_type,
            "status": r.status,
            "home_cell": r.home_cell,
            "security_revision": r.security_revision,
            "created_at": r.created_at.isoformat(),
        }
        for r in rows
    ]


async def set_status(
    conn: AsyncConnection,
    principal_id: str,
    target: PrincipalStatus,
    *,
    superseded_by: str | None = None,
) -> Principal:
    current, version = await load_principal(conn, principal_id)
    new = change_status(current, target, superseded_by=superseded_by)
    result = await conn.execute(
        text("""
            UPDATE core.principal
            SET status = CAST(:st AS core.principal_status), security_revision = :rev,
                superseded_by_principal_id = :sup, row_version = row_version + 1,
                disabled_at = CASE WHEN :st = 'DISABLED' THEN now() ELSE disabled_at END
            WHERE principal_id = :id AND row_version = :v
        """),
        {
            "st": new.status.value,
            "rev": new.security_revision,
            "id": uuid.UUID(principal_id),
            "v": version,
            "sup": uuid.UUID(new.superseded_by) if new.superseded_by else None,
        },
    )
    if result.rowcount != 1:
        raise DomainError("CONCURRENT_MODIFICATION")
    if new.security_revision != current.security_revision:
        await emit(
            conn,
            name="principal.security_revision.changed",
            tenant_id=new.tenant_id or "",
            aggregate_type="principal",
            aggregate_id=principal_id,
            aggregate_version=new.security_revision,
            data={
                "principal_id": principal_id,
                "status": new.status.value,
                "security_revision": new.security_revision,
            },
        )
    return new


async def bump_security_revision(conn: AsyncConnection, principal_id: str, *, reason: str) -> int:
    """Security-significant change (authenticator revoked, link changed, recovery...): every session
    observing the old revision now needs re-authentication (ZID-DATA-001 §3.1; AUTH §16)."""
    row = (
        await conn.execute(
            text(
                "UPDATE core.principal SET security_revision = security_revision + 1, "
                "row_version = row_version + 1 WHERE principal_id = :id "
                "RETURNING tenant_id, status, security_revision"
            ),
            {"id": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None:
        raise DomainError("PRINCIPAL_NOT_FOUND")
    await emit(
        conn,
        name="principal.security_revision.changed",
        tenant_id=str(row.tenant_id),
        aggregate_type="principal",
        aggregate_id=principal_id,
        aggregate_version=row.security_revision,
        data={
            "principal_id": principal_id,
            "status": row.status,
            "security_revision": row.security_revision,
            "reason": reason,
        },
    )
    return int(row.security_revision)


async def add_identifier(conn: AsyncConnection, i: Identifier) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO core.principal_identifier (identifier_id, tenant_id, principal_id, namespace,
                        uniqueness_scope, scope_key, normalized_value_hash, verification_state)
                    VALUES (:id, :t, :p, :ns, CAST(:sc AS core.uniqueness_scope), :sk, :h,
                            CAST(:vs AS core.verification_state))
                """),
                {
                    "id": uuid.UUID(i.identifier_id),
                    "t": uuid.UUID(i.tenant_id),
                    "p": uuid.UUID(i.principal_id),
                    "ns": i.namespace,
                    "sc": i.uniqueness_scope.value,
                    "sk": i.scope_key,
                    "h": value_hash(i.namespace, i.value_normalized),
                    "vs": i.verification_state.value,
                },
            )
    except IntegrityError as e:
        raise DomainError(
            "IDENTIFIER_CONFLICT", "resolve explicitly; identifiers never auto-merge"
        ) from e
