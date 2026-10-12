"""PostgreSQL store for relationship facts (ZID-DATA-001 §4.2; ZID-AUTHZ-001 §7.1; DEC-C4).

Every material mutation takes the tenant's next relation_revision with one atomic upsert on
authz.relation_publication (row lock serializes writers; a trigger refuses non-increasing values),
then stamps the edge with it, in the same transaction. The SpiceDB projector later reads edges
in revision order. Domain validation (cross-tenant, state machine) runs before any write.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipError,
    RelationshipStatus,
    RelationshipType,
    RevisionClock,
    mutate,
)


async def next_revision(conn: AsyncConnection, tenant_id: str) -> int:
    row = (
        await conn.execute(
            text("""
                INSERT INTO authz.relation_publication (tenant_id, relation_revision) VALUES (:t, 1)
                ON CONFLICT (tenant_id) DO UPDATE
                    SET relation_revision = authz.relation_publication.relation_revision + 1, updated_at = now()
                RETURNING relation_revision
            """),
            {"t": uuid.UUID(tenant_id)},
        )
    ).one()
    return int(row.relation_revision)


async def current_revision(conn: AsyncConnection, tenant_id: str) -> int:
    row = (
        await conn.execute(
            text("SELECT relation_revision FROM authz.relation_publication WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return int(row.relation_revision) if row else 0


async def record_edge(conn: AsyncConnection, draft: Relationship) -> Relationship:
    """Insert a new ACTIVE/PENDING fact at the tenant's next revision."""
    rev = await next_revision(conn, draft.tenant_id)
    rel = replace(draft, relation_revision=rev)  # replace() re-runs __post_init__ validation
    await conn.execute(
        text("""
            INSERT INTO org.relationship_edge (relationship_id, tenant_id, subject_principal_id, relation_type,
                object_principal_id, object_tenant_id, cross_tenant_trust_id, source_type, assurance_class,
                status, valid_from, valid_to, relation_revision)
            VALUES (:id, :t, :s, CAST(:ty AS org.relation_type), :o, :ot, :trust, :src, :a,
                    CAST(:st AS org.relation_status), :vf, :vt, :rev)
        """),
        {
            "id": uuid.UUID(rel.relationship_id),
            "t": uuid.UUID(rel.tenant_id),
            "s": uuid.UUID(rel.subject_principal_id),
            "ty": rel.relationship_type.value,
            "o": uuid.UUID(rel.object_ref),
            "ot": uuid.UUID(rel.object_tenant_id),
            "trust": uuid.UUID(rel.cross_tenant_trust_id) if rel.cross_tenant_trust_id else None,
            "src": rel.source,
            "a": rel.assurance,
            "st": rel.status.value,
            "vf": rel.valid_from,
            "vt": rel.valid_to,
            "rev": rev,
        },
    )
    await _changed(conn, rel)
    return rel


async def _changed(conn: AsyncConnection, rel: Relationship) -> None:
    await emit(
        conn,
        name="relationship.edge.changed",
        tenant_id=rel.tenant_id,
        aggregate_type="relation_publication",
        aggregate_id=rel.tenant_id,
        aggregate_version=rel.relation_revision,
        data={
            "relationship_id": rel.relationship_id,
            "relation_type": rel.relationship_type.value,
            "status": rel.status.value,
            "relation_revision": rel.relation_revision,
        },
    )


async def load_edge(conn: AsyncConnection, relationship_id: str) -> Relationship:
    row = (
        await conn.execute(
            text("SELECT * FROM org.relationship_edge WHERE relationship_id = :id FOR UPDATE"),
            {"id": uuid.UUID(relationship_id)},
        )
    ).first()
    if row is None:
        raise RelationshipError("RELATIONSHIP_NOT_FOUND")
    return Relationship(
        relationship_id=str(row.relationship_id),
        tenant_id=str(row.tenant_id),
        subject_principal_id=str(row.subject_principal_id),
        subject_tenant_id=str(row.tenant_id),
        relationship_type=RelationshipType(row.relation_type),
        object_ref=str(row.object_principal_id),
        object_tenant_id=str(row.object_tenant_id),
        source=row.source_type,
        assurance=row.assurance_class,
        status=RelationshipStatus(row.status),
        valid_from=row.valid_from,
        valid_to=row.valid_to,
        relation_revision=row.relation_revision,
        cross_tenant_trust_id=str(row.cross_tenant_trust_id) if row.cross_tenant_trust_id else None,
    )


async def change_edge(
    conn: AsyncConnection, relationship_id: str, target: RelationshipStatus, now: datetime
) -> Relationship:
    """State change at the next revision; the old fact is kept with its superseded time."""
    rel = await load_edge(conn, relationship_id)
    _, _ = mutate(
        rel, target, RevisionClock(rel.tenant_id, rel.relation_revision)
    )  # legality check only
    rev = await next_revision(conn, rel.tenant_id)
    new, _ = mutate(rel, target, RevisionClock(rel.tenant_id, rev - 1))
    await conn.execute(
        text("""
            UPDATE org.relationship_edge
            SET status = CAST(:st AS org.relation_status), relation_revision = :rev,
                superseded_at = CASE WHEN :st IN ('REVOKED', 'SUPERSEDED', 'EXPIRED') THEN :now ELSE superseded_at END
            WHERE relationship_id = :id
        """),
        {
            "st": target.value,
            "rev": new.relation_revision,
            "now": now,
            "id": uuid.UUID(relationship_id),
        },
    )
    await _changed(conn, new)
    return new


async def edges_since(
    conn: AsyncConnection, tenant_id: str, after_revision: int
) -> list[Relationship]:
    """Edges changed after a revision, in revision order: the projector's input."""
    rows = (
        await conn.execute(
            text(
                "SELECT relationship_id FROM org.relationship_edge WHERE tenant_id = :t AND relation_revision > :r "
                "ORDER BY relation_revision"
            ),
            {"t": uuid.UUID(tenant_id), "r": after_revision},
        )
    ).all()
    return [await load_edge(conn, str(r.relationship_id)) for r in rows]
