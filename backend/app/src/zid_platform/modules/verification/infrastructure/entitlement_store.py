"""Storage for provider entitlements (verify.provider_entitlement). Scope and status only."""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain.provider_adapter import Entitlement, EntitlementStatus

COLUMNS = (
    "entitlement_id, provider_id, service_code, countries, purposes, processing_regions, valid_from, valid_until, "
    "assurance, cost_rank, status, status_reason, status_changed_at"
)


async def insert(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    created_by: str,
    provider_id: str,
    service_code: str,
    countries: Sequence[str],
    purposes: Sequence[str],
    regions: Sequence[str],
    valid_from: date,
    valid_until: date | None,
    assurance: int,
    cost_rank: int,
) -> str:
    entitlement_id = str(uuid.uuid4())
    await conn.execute(
        text(
            "INSERT INTO verify.provider_entitlement (entitlement_id, tenant_id, provider_id, service_code, countries, "
            "purposes, processing_regions, valid_from, valid_until, assurance, cost_rank, status, created_by) VALUES "
            "(CAST(:i AS uuid), CAST(:t AS uuid), :p, :s, :c, :pu, :r, :vf, :vu, :a, :k, 'ACTIVE', CAST(:by AS uuid))"
        ),
        {
            "i": entitlement_id,
            "t": tenant_id,
            "p": provider_id,
            "s": service_code,
            "c": list(countries),
            "pu": list(purposes),
            "r": list(regions),
            "vf": valid_from,
            "vu": valid_until,
            "a": assurance,
            "k": cost_rank,
            "by": created_by,
        },
    )
    return entitlement_id


async def live(conn: AsyncConnection, provider_id: str, service_code: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT {COLUMNS} FROM verify.provider_entitlement "
                    "WHERE provider_id = :p AND service_code = :s AND status <> 'REVOKED' FOR UPDATE"
                ),
                {"p": provider_id, "s": service_code},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def set_status(
    conn: AsyncConnection, entitlement_id: str, status: str, reason: str | None
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.provider_entitlement SET status = :st, status_reason = :r, status_changed_at = now() "
            "WHERE entitlement_id = CAST(:i AS uuid)"
        ),
        {"st": status, "r": reason, "i": entitlement_id},
    )


async def listing(conn: AsyncConnection, provider_id: str | None) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT {COLUMNS} FROM verify.provider_entitlement "
                    "WHERE (CAST(:p AS text) IS NULL OR provider_id = :p) ORDER BY provider_id, service_code, status_changed_at DESC"
                ),
                {"p": provider_id},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


def to_domain(row: Mapping[str, Any]) -> Entitlement:
    return Entitlement(
        provider_id=row["provider_id"],
        service_code=row["service_code"],
        countries=frozenset(c.upper() for c in row["countries"]),
        purposes=frozenset(row["purposes"]),
        processing_regions=frozenset(row["processing_regions"]),
        valid_from=row["valid_from"],
        valid_until=row["valid_until"],
        assurance=int(row["assurance"]),
        status=EntitlementStatus(row["status"]),
        cost_rank=int(row["cost_rank"]),
    )
