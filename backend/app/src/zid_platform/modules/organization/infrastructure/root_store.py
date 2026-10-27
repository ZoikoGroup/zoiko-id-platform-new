"""PostgreSQL store for organizations and authority roots (ZID-DATA-001 §4.1/§4.3; ZID-ROOT-001 §13, §18).

Source lifecycle events are idempotent in the database: the event id is inserted into
org.source_event first, and a conflict means "already applied" (ROOT-24), so two copies of one
event racing each other cannot both apply. Applying an event bumps the organization's
authority_root_revision (monotonic, trigger-guarded) whenever any root changes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.organization.domain.roots import (
    Assurance,
    EventOutcome,
    Fact,
    RootAssertion,
    RootStatus,
    RootType,
    SourceEvent,
    apply_event,
)


async def create_organization(
    conn: AsyncConnection,
    *,
    organization_id: str,
    tenant_id: str,
    legal_name: str,
    jurisdiction: str,
    valid_from: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO org.organization (organization_id, tenant_id, legal_name, jurisdiction, valid_from) "
            "VALUES (:o, :t, :n, :j, :vf)"
        ),
        {
            "o": uuid.UUID(organization_id),
            "t": uuid.UUID(tenant_id),
            "n": legal_name,
            "j": jurisdiction,
            "vf": valid_from,
        },
    )


async def save_root(conn: AsyncConnection, tenant_id: str, r: RootAssertion) -> None:
    await conn.execute(
        text("""
            INSERT INTO org.authority_root_assertion (root_assertion_id, tenant_id, organization_principal_id,
                holder_principal_id, root_type, facts, assurance_class, status, jurisdiction, scope_actions,
                value_limit, currency, source_revision, fresh_until, valid_from, valid_to)
            VALUES (:id, :t, :o, :h, CAST(:ty AS org.root_type), CAST(:f AS org.root_fact[]),
                    CAST(:a AS org.root_class), CAST(:st AS org.root_status), :j, :sa, :vl, :cur, :srev,
                    :fresh, :vf, :vt)
        """),
        {
            "id": uuid.UUID(r.root_id),
            "t": uuid.UUID(tenant_id),
            "o": uuid.UUID(r.organization_id),
            "h": uuid.UUID(r.holder_principal_id),
            "ty": r.root_type.value,
            "f": sorted(f.value for f in r.facts),
            "a": r.assurance.name,
            "st": r.status.value,
            "j": r.jurisdiction,
            "sa": sorted(r.scope_actions) if r.scope_actions is not None else None,
            "vl": r.value_limit,
            "cur": r.currency,
            "srev": r.source_revision,
            "fresh": r.fresh_until,
            "vf": r.valid_from,
            "vt": r.valid_to,
        },
    )


def _root(row: Row[Any]) -> RootAssertion:
    m = row._mapping
    return RootAssertion(
        root_id=str(m["root_assertion_id"]),
        organization_id=str(m["organization_principal_id"]),
        holder_principal_id=str(m["holder_principal_id"]),
        root_type=RootType(m["root_type"]),
        facts=frozenset(Fact(f) for f in _array(m["facts"])),
        assurance=Assurance[m["assurance_class"]],
        status=RootStatus(m["status"]),
        jurisdiction=m["jurisdiction"],
        valid_from=m["valid_from"],
        valid_to=m["valid_to"],
        fresh_until=m["fresh_until"],
        source_revision=m["source_revision"],
        scope_actions=frozenset(m["scope_actions"]) if m["scope_actions"] is not None else None,
        value_limit=m["value_limit"],
        currency=m["currency"].strip() if m["currency"] else None,
    )


def _array(value: object) -> list[str]:
    """asyncpg returns custom-enum arrays as text like '{A,B}'; built-in arrays come back as lists."""
    if isinstance(value, list):
        return [str(v) for v in value]
    s = str(value).strip("{}")
    return [p for p in s.split(",") if p]


async def load_roots(conn: AsyncConnection, organization_id: str) -> list[RootAssertion]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM org.authority_root_assertion WHERE organization_principal_id = :o "
                "ORDER BY root_assertion_id"
            ),
            {"o": uuid.UUID(organization_id)},
        )
    ).all()
    return [_root(r) for r in rows]


async def load_root(conn: AsyncConnection, root_id: str) -> RootAssertion | None:
    row = (
        await conn.execute(
            text("SELECT * FROM org.authority_root_assertion WHERE root_assertion_id = :r"),
            {"r": uuid.UUID(root_id)},
        )
    ).first()
    return _root(row) if row else None


async def apply_source_event(
    conn: AsyncConnection, tenant_id: str, event: SourceEvent
) -> EventOutcome:
    """Record the event id first; only the first copy of an event changes anything."""
    inserted = await conn.execute(
        text(
            "INSERT INTO org.source_event (tenant_id, source_event_id, organization_id, kind) "
            "VALUES (:t, :e, :o, :k) ON CONFLICT DO NOTHING"
        ),
        {
            "t": uuid.UUID(tenant_id),
            "e": event.event_id,
            "o": uuid.UUID(event.organization_id),
            "k": event.kind.value,
        },
    )
    org = (
        await conn.execute(
            text(
                "SELECT can_originate_authority FROM org.organization WHERE organization_id = :o FOR UPDATE"
            ),
            {"o": uuid.UUID(event.organization_id)},
        )
    ).one()
    roots = {r.root_id: r for r in await load_roots(conn, event.organization_id)}
    if inserted.rowcount == 0:
        return EventOutcome(roots, org.can_originate_authority, applied=False)
    out = apply_event(
        event,
        roots,
        processed_event_ids=frozenset(),
        organization_active=org.can_originate_authority,
    )
    changed = [r for rid, r in out.roots.items() if roots[rid].status is not r.status]
    await _persist_statuses(conn, tenant_id, changed)
    await conn.execute(
        text(
            "UPDATE org.organization SET can_originate_authority = :c, "
            "authority_root_revision = authority_root_revision + :bump WHERE organization_id = :o"
        ),
        {
            "c": out.organization_can_originate_authority,
            "bump": 1 if changed else 0,
            "o": uuid.UUID(event.organization_id),
        },
    )
    return out


async def _persist_statuses(
    conn: AsyncConnection, tenant_id: str, roots: Iterable[RootAssertion]
) -> None:
    for r in roots:
        updated = await conn.execute(
            text(
                "UPDATE org.authority_root_assertion SET status = CAST(:s AS org.root_status), "
                "row_version = row_version + 1 WHERE root_assertion_id = :id RETURNING row_version"
            ),
            {"s": r.status.value, "id": uuid.UUID(r.root_id)},
        )
        await emit(
            conn,
            name=f"authority_root.{r.status.value.lower()}",
            tenant_id=tenant_id,
            aggregate_type="authority_root",
            aggregate_id=r.root_id,
            aggregate_version=updated.scalar_one(),
            data={
                "root_assertion_id": r.root_id,
                "organization_id": r.organization_id,
                "status": r.status.value,
            },
        )


persist_statuses = (
    _persist_statuses  # for the identity module: suspend roots of a quarantined source
)


async def root_revision(conn: AsyncConnection, organization_id: str) -> int:
    return int(
        (
            await conn.execute(
                text(
                    "SELECT authority_root_revision FROM org.organization WHERE organization_id = :o"
                ),
                {"o": uuid.UUID(organization_id)},
            )
        ).scalar_one()
    )


async def organization_exists(conn: AsyncConnection, organization_id: str) -> bool:
    """Whether this organization is registered in the caller's tenant (RLS scopes the lookup)."""
    row = (
        await conn.execute(
            text("SELECT 1 FROM org.organization WHERE organization_id = :o"),
            {"o": uuid.UUID(organization_id)},
        )
    ).first()
    return row is not None
