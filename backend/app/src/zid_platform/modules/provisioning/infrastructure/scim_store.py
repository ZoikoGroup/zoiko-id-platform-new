"""Provisioning sources and SCIM resources in PostgreSQL (ZID-AUTH-001 §11).

Inside the caller's tenant transaction (RLS). Writes are optimistic on `version` so a stale
If-Match (or a concurrent update) fails instead of overwriting. Every change emits
provisioning.resource.changed with ids, operation and version only - never attribute values.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit


class ScimError(ValueError):
    def __init__(self, status: int, scim_type: str | None, detail: str) -> None:
        super().__init__(detail)
        self.status, self.scim_type, self.detail = status, scim_type, detail


@dataclass(frozen=True, slots=True)
class Source:
    source_id: str
    tenant_id: str
    client_id: str
    display_name: str
    status: str


@dataclass(frozen=True, slots=True)
class Resource:
    resource_id: str
    tenant_id: str
    source_id: str
    resource_type: str
    external_id: str | None
    principal_id: str | None
    user_name: str | None
    display_name: str | None
    active: bool
    attributes: dict[str, Any]
    version: int
    created_at: datetime
    updated_at: datetime


_RCOLS = (
    "resource_id, tenant_id, source_id, resource_type, external_id, principal_id, user_name, "
    "display_name, active, attributes, version, created_at, updated_at"
)


def _resource(r: object) -> Resource:
    m = r._mapping  # type: ignore[attr-defined]
    return Resource(
        resource_id=str(m["resource_id"]),
        tenant_id=str(m["tenant_id"]),
        source_id=str(m["source_id"]),
        resource_type=m["resource_type"],
        external_id=m["external_id"],
        principal_id=str(m["principal_id"]) if m["principal_id"] else None,
        user_name=m["user_name"],
        display_name=m["display_name"],
        active=m["active"],
        attributes=dict(m["attributes"]),
        version=m["version"],
        created_at=m["created_at"],
        updated_at=m["updated_at"],
    )


async def create_source(conn: AsyncConnection, s: Source) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.provisioning_source (source_id, tenant_id, client_id, "
            "display_name, status) VALUES (:id, :t, :c, :n, :st)"
        ),
        {
            "id": uuid.UUID(s.source_id),
            "t": uuid.UUID(s.tenant_id),
            "c": s.client_id,
            "n": s.display_name,
            "st": s.status,
        },
    )
    await _connector_changed(conn, s, 1)


async def _connector_changed(conn: AsyncConnection, s: Source, version: int) -> None:
    await emit(
        conn,
        name="provisioning.connector_changed",
        tenant_id=s.tenant_id,
        aggregate_type="provisioning_source",
        aggregate_id=s.source_id,
        aggregate_version=version,
        data={"source_id": s.source_id, "client_id": s.client_id, "status": s.status},
    )


async def source_for_client(conn: AsyncConnection, client_id: str) -> Source | None:
    row = (
        await conn.execute(
            text(
                "SELECT source_id, tenant_id, client_id, display_name, status "
                "FROM platform.provisioning_source WHERE client_id = :c"
            ),
            {"c": client_id},
        )
    ).first()
    if row is None:
        return None
    return Source(
        str(row.source_id), str(row.tenant_id), row.client_id, row.display_name, row.status
    )


async def list_sources(conn: AsyncConnection) -> list[Source]:
    rows = (
        await conn.execute(
            text(
                "SELECT source_id, tenant_id, client_id, display_name, status "
                "FROM platform.provisioning_source ORDER BY created_at DESC, source_id"
            )
        )
    ).all()
    return [
        Source(str(r.source_id), str(r.tenant_id), r.client_id, r.display_name, r.status)
        for r in rows
    ]


async def set_source_status(conn: AsyncConnection, source_id: str, status: str) -> Source | None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.provisioning_source SET status = :st, revision = revision + 1, "
                "updated_at = now() WHERE source_id = :s AND status <> 'RETIRED' "
                "RETURNING source_id, tenant_id, client_id, display_name, status, revision"
            ),
            {"s": uuid.UUID(source_id), "st": status},
        )
    ).first()
    if row is None:
        return None
    s = Source(str(row.source_id), str(row.tenant_id), row.client_id, row.display_name, row.status)
    await _connector_changed(conn, s, row.revision)
    return s


async def insert(conn: AsyncConnection, r: Resource) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    f"INSERT INTO platform.scim_resource ({_RCOLS}) VALUES (:id, :t, :s, :ty, :ext, "
                    ":p, :un, :dn, :active, CAST(:attrs AS jsonb), :v, :c, :u)"
                ),
                {
                    "id": uuid.UUID(r.resource_id),
                    "t": uuid.UUID(r.tenant_id),
                    "s": uuid.UUID(r.source_id),
                    "ty": r.resource_type,
                    "ext": r.external_id,
                    "p": uuid.UUID(r.principal_id) if r.principal_id else None,
                    "un": r.user_name,
                    "dn": r.display_name,
                    "active": r.active,
                    "attrs": json.dumps(r.attributes),
                    "v": r.version,
                    "c": r.created_at,
                    "u": r.updated_at,
                },
            )
    except IntegrityError as e:
        raise ScimError(409, "uniqueness", "userName or externalId already exists") from e
    await changed(conn, r, "created")


async def changed(conn: AsyncConnection, r: Resource, op: str) -> None:
    await emit(
        conn,
        name="provisioning.resource.changed",
        tenant_id=r.tenant_id,
        aggregate_type=f"scim_{r.resource_type.lower()}",
        aggregate_id=r.resource_id,
        aggregate_version=r.version if op != "deleted" else r.version + 1,
        data={
            "resource_id": r.resource_id,
            "resource_type": r.resource_type,
            "source_id": r.source_id,
            "principal_id": r.principal_id,
            "operation": op,
            "version": r.version,
        },
    )


async def load(
    conn: AsyncConnection,
    source_id: str,
    resource_type: str,
    resource_id: str,
    *,
    lock: bool = False,
) -> Resource:
    try:
        rid = uuid.UUID(resource_id)
    except ValueError as e:
        raise ScimError(404, None, "resource not found") from e
    row = (
        await conn.execute(
            text(
                f"SELECT {_RCOLS} FROM platform.scim_resource WHERE resource_id = :r "
                "AND source_id = :s AND resource_type = :ty AND deleted_at IS NULL"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": rid, "s": uuid.UUID(source_id), "ty": resource_type},
        )
    ).first()
    if row is None:
        raise ScimError(404, None, "resource not found")
    return _resource(row)


async def update(conn: AsyncConnection, old: Resource, new: Resource) -> None:
    try:
        async with conn.begin_nested():
            result = await conn.execute(
                text(
                    "UPDATE platform.scim_resource SET external_id = :ext, user_name = :un, "
                    "display_name = :dn, active = :active, attributes = CAST(:attrs AS jsonb), "
                    "version = :v, updated_at = :u WHERE resource_id = :id AND version = :old"
                ),
                {
                    "id": uuid.UUID(new.resource_id),
                    "ext": new.external_id,
                    "un": new.user_name,
                    "dn": new.display_name,
                    "active": new.active,
                    "attrs": json.dumps(new.attributes),
                    "v": new.version,
                    "u": new.updated_at,
                    "old": old.version,
                },
            )
    except IntegrityError as e:
        raise ScimError(409, "uniqueness", "userName or externalId already exists") from e
    if result.rowcount != 1:
        raise ScimError(412, None, "resource changed concurrently")
    await changed(conn, new, "updated")


async def mark_deleted(conn: AsyncConnection, r: Resource, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE platform.scim_resource SET deleted_at = :now, active = false "
            "WHERE resource_id = :id"
        ),
        {"id": uuid.UUID(r.resource_id), "now": now},
    )
    await conn.execute(
        text(
            "UPDATE platform.scim_group_member SET removed_at = :now "
            "WHERE (group_id = :id OR member_id = :id) AND removed_at IS NULL"
        ),
        {"id": uuid.UUID(r.resource_id), "now": now},
    )
    await changed(conn, r, "deleted")


async def query(
    conn: AsyncConnection,
    source_id: str,
    resource_type: str,
    *,
    attribute: str | None,
    value: str | None,
    start: int,
    count: int,
) -> tuple[int, list[Resource]]:
    column = {
        "userName": "lower(user_name)",
        "externalId": "external_id",
        "displayName": "display_name",
    }
    where = "source_id = :s AND resource_type = :ty AND deleted_at IS NULL"
    params: dict[str, object] = {"s": uuid.UUID(source_id), "ty": resource_type}
    if attribute is not None:
        where += f" AND {column[attribute]} = :v"
        params["v"] = value.lower() if attribute == "userName" and value else value
    total = (
        await conn.execute(
            text(f"SELECT count(*) FROM platform.scim_resource WHERE {where}"), params
        )
    ).scalar_one()
    rows = (
        await conn.execute(
            text(
                f"SELECT {_RCOLS} FROM platform.scim_resource WHERE {where} "
                "ORDER BY created_at, resource_id OFFSET :o LIMIT :n"
            ),
            {**params, "o": max(start - 1, 0), "n": count},
        )
    ).all()
    return int(total), [_resource(r) for r in rows]


async def members(conn: AsyncConnection, group_id: str) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT member_id FROM platform.scim_group_member WHERE group_id = :g "
                "AND removed_at IS NULL ORDER BY added_at"
            ),
            {"g": uuid.UUID(group_id)},
        )
    ).all()
    return [str(r.member_id) for r in rows]


async def set_members(
    conn: AsyncConnection, *, tenant_id: str, group_id: str, add: list[str], remove: list[str]
) -> None:
    for m in remove:
        await conn.execute(
            text(
                "UPDATE platform.scim_group_member SET removed_at = now() WHERE group_id = :g "
                "AND member_id = :m AND removed_at IS NULL"
            ),
            {"g": uuid.UUID(group_id), "m": uuid.UUID(m)},
        )
    for m in add:
        await conn.execute(
            text(
                "INSERT INTO platform.scim_group_member (group_id, member_id, tenant_id) "
                "VALUES (:g, :m, :t) ON CONFLICT (group_id, member_id) "
                "DO UPDATE SET removed_at = NULL, added_at = now()"
            ),
            {"g": uuid.UUID(group_id), "m": uuid.UUID(m), "t": uuid.UUID(tenant_id)},
        )
