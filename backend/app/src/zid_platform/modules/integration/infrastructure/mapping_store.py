"""PostgreSQL persistence of mapping releases and routing rules (G-W3; CR-015)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.integration.domain.mapping import ReleaseStatus
from zid_platform.modules.integration.domain.model import IntegrationError
from zid_platform.modules.integration.domain.routing import RouteKind, RoutingRule


@dataclass(frozen=True, slots=True)
class Release:
    release_id: str
    tenant_id: str
    connector_id: str
    version: int
    status: ReleaseStatus
    rules: list[dict[str, Any]]
    content_hash: str
    author: str
    approved_by: str | None
    simulation_report: dict[str, Any] | None
    created_at: datetime
    activated_at: datetime | None


def _release(r: Row[Any]) -> Release:
    m = r._mapping
    return Release(
        str(m["release_id"]),
        str(m["tenant_id"]),
        str(m["scope_connector_id"]),
        int(m["version"]),
        ReleaseStatus(m["status"]),
        list(m["rules"]),
        m["content_hash"],
        str(m["author_principal_id"]),
        str(m["approved_by"]) if m["approved_by"] else None,
        m["simulation_report"],
        m["created_at"],
        m["activated_at"],
    )


async def insert_release(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    connector_id: str,
    rules: Sequence[Mapping[str, Any]],
    content_hash: str,
    author: str,
) -> Release:
    version = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(version), 0) + 1 FROM platform.mapping_release "
                    "WHERE scope_connector_id = :c"
                ),
                {"c": uuid.UUID(connector_id)},
            )
        ).scalar_one()
    )
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.mapping_release (release_id, tenant_id, scope_connector_id,
                    version, rules, content_hash, author_principal_id)
                VALUES (:id, :t, :c, :v, CAST(:rules AS jsonb), :h, :a) RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "c": uuid.UUID(connector_id),
                "v": version,
                "rules": json.dumps(list(rules)),
                "h": content_hash,
                "a": uuid.UUID(author),
            },
        )
    ).one()
    return _release(row)


async def load_release(
    conn: AsyncConnection, release_id: str, *, lock: bool = False
) -> Release | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.mapping_release WHERE release_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(release_id)},
        )
    ).first()
    return _release(row) if row else None


async def releases_for(conn: AsyncConnection, connector_id: str) -> list[Release]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.mapping_release WHERE scope_connector_id = :c "
                "ORDER BY version DESC"
            ),
            {"c": uuid.UUID(connector_id)},
        )
    ).all()
    return [_release(r) for r in rows]


async def active_release(conn: AsyncConnection, connector_id: str) -> Release | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.mapping_release WHERE scope_connector_id = :c "
                "AND status = 'ACTIVE'"
            ),
            {"c": uuid.UUID(connector_id)},
        )
    ).first()
    return _release(row) if row else None


async def record_simulation(
    conn: AsyncConnection, release_id: str, report: Mapping[str, Any], now: datetime
) -> Release:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.mapping_release SET simulation_report = CAST(:r AS jsonb), "
                "simulated_at = :now, status = CASE WHEN status = 'DRAFT' THEN 'REVIEW' ELSE status END "
                "WHERE release_id = :id RETURNING *"
            ),
            {"r": json.dumps(dict(report), default=str), "now": now, "id": uuid.UUID(release_id)},
        )
    ).one()
    return _release(row)


async def activate(
    conn: AsyncConnection, release: Release, *, approved_by: str, now: datetime
) -> Release:
    """Retire the current ACTIVE release of the connector and activate this one, atomically."""
    await conn.execute(
        text(
            "UPDATE platform.mapping_release SET status = 'RETIRED', retired_at = :now "
            "WHERE scope_connector_id = :c AND status = 'ACTIVE'"
        ),
        {"c": uuid.UUID(release.connector_id), "now": now},
    )
    row = (
        await conn.execute(
            text(
                "UPDATE platform.mapping_release SET status = 'ACTIVE', approved_by = :a, "
                "activated_at = :now, retired_at = NULL WHERE release_id = :id RETURNING *"
            ),
            {"a": uuid.UUID(approved_by), "now": now, "id": uuid.UUID(release.release_id)},
        )
    ).one()
    await conn.execute(
        text(
            "UPDATE platform.integration_connector SET mapping_release_id = :r, revision = revision + 1 "
            "WHERE connector_id = :c"
        ),
        {"r": uuid.UUID(release.release_id), "c": uuid.UUID(release.connector_id)},
    )
    return _release(row)


# -------------------------------------------------------------------------------------- routing


async def insert_route(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    kind: RouteKind,
    match: str,
    connection_ref: str,
    created_by: str,
) -> RoutingRule:
    rid = uuid.uuid4()
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO platform.identity_routing_rule (rule_id, tenant_id, kind, match,
                        connection_ref, created_by)
                    VALUES (:id, :t, :k, :m, :ref, :by)
                """),
                {
                    "id": rid,
                    "t": uuid.UUID(tenant_id),
                    "k": kind.value,
                    "m": match,
                    "ref": connection_ref,
                    "by": uuid.UUID(created_by),
                },
            )
    except IntegrityError as e:
        raise IntegrationError(
            "ROUTE_CONFLICT", f"{kind} {match!r} already routes to an identity source."
        ) from e
    return RoutingRule(str(rid), kind, match, connection_ref)


async def routes(conn: AsyncConnection) -> list[RoutingRule]:
    rows = (
        await conn.execute(
            text(
                "SELECT rule_id, kind, match, connection_ref FROM platform.identity_routing_rule "
                "WHERE status = 'ACTIVE'"
            )
        )
    ).all()
    return [RoutingRule(str(r.rule_id), RouteKind(r.kind), r.match, r.connection_ref) for r in rows]


async def retire_route(conn: AsyncConnection, rule_id: str) -> bool:
    r = await conn.execute(
        text(
            "UPDATE platform.identity_routing_rule SET status = 'RETIRED' "
            "WHERE rule_id = :id AND status = 'ACTIVE'"
        ),
        {"id": uuid.UUID(rule_id)},
    )
    return bool(r.rowcount)
