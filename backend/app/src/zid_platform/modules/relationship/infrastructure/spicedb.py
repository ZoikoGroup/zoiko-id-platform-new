"""SpiceDB relationship projection (DEC-C4; ZID-AUTHZ-001 §7, §7.1, §23.1; P0-04; AT-05).

PostgreSQL (org.relationship_edge) is the authority; SpiceDB is a revisioned projection used for fast
traversal. Only the projector writes to it. Every projection batch records relation_revision ->
ZedToken in authz.relation_publication, and checks choose consistency by risk class:

    R0  minimize_latency
    R1  at_least_as_fresh(last projected token)
    R2  at_least_as_fresh(latest token); if the projection is behind the required revision, wait
        briefly for the projector, then deny AUTHORITY_STALE
    R3  fully_consistent; any failure -> STATE_UNAVAILABLE

A stale projection may deny but never grants a new R2/R3 allow. Uses SpiceDB's HTTP API (DEC-D4).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.relationship.domain.relationship import (
    Relationship,
    RelationshipStatus,
    RelationshipType,
    projection_fresh_enough,
)
from zid_platform.modules.relationship.infrastructure.edge_store import edges_since

# One definition; each relation type is a SpiceDB relation. Tuple form: object#relation@subject,
# i.e. "the object has <relation> to the subject" (MEMBER_OF: org#member_of@person).
SCHEMA = (
    "definition principal {\n"
    + "".join(f"    relation {t.value.lower()}: principal\n" for t in RelationshipType)
    + "}\n"
)

R2_FRESHNESS_WAIT_S = 0.25  # "short bounded wait" before AUTHORITY_STALE (DEC-C4)


class SpiceDBError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SpiceDB:
    """Minimal async client for the SpiceDB HTTP gateway."""

    base_url: str
    preshared_key: str
    timeout_s: float = 2.0

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_s) as client:
            resp = await client.post(
                path, json=body, headers={"Authorization": f"Bearer {self.preshared_key}"}
            )
        if resp.status_code != 200:
            raise SpiceDBError(f"{path}: HTTP {resp.status_code} {resp.text[:200]}")
        return resp.json()  # type: ignore[no-any-return]

    async def write_schema(self, schema: str = SCHEMA) -> None:
        await self._post("/v1/schema/write", {"schema": schema})

    async def write(self, updates: list[dict[str, Any]]) -> str:
        if not updates:
            raise SpiceDBError("empty write")
        out = await self._post("/v1/relationships/write", {"updates": updates})
        return str(out["writtenAt"]["token"])

    async def check(
        self, obj: str, relation: str, subject: str, consistency: dict[str, Any]
    ) -> bool:
        out = await self._post(
            "/v1/permissions/check",
            {
                "consistency": consistency,
                "resource": {"objectType": "principal", "objectId": obj},
                "permission": relation,
                "subject": {"object": {"objectType": "principal", "objectId": subject}},
            },
        )
        return out.get("permissionship") == "PERMISSIONSHIP_HAS_PERMISSION"


def _update(rel: Relationship, now: datetime) -> dict[str, Any]:
    live = rel.status is RelationshipStatus.ACTIVE and (rel.valid_to is None or now < rel.valid_to)
    return {
        "operation": "OPERATION_TOUCH" if live else "OPERATION_DELETE",
        "relationship": {
            "resource": {"objectType": "principal", "objectId": rel.object_ref},
            "relation": rel.relationship_type.value.lower(),
            "subject": {
                "object": {"objectType": "principal", "objectId": rel.subject_principal_id}
            },
        },
    }


async def project(
    conn: AsyncConnection, spicedb: SpiceDB, tenant_id: str, *, batch: int = 500
) -> int:
    """Project every edge changed since the checkpoint, in revision order. Returns the new checkpoint.

    Runs inside a tenant transaction; the checkpoint row is locked so two projectors for one tenant
    serialize. SpiceDB writes are idempotent (TOUCH/DELETE), so a crash before the checkpoint update
    only means the same batch is replayed.
    """
    row = (
        await conn.execute(
            text(
                "SELECT relation_revision, projected_revision FROM authz.relation_publication "
                "WHERE tenant_id = :t FOR UPDATE"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        return 0
    edges = await edges_since(conn, tenant_id, row.projected_revision)
    if not edges:
        return int(row.projected_revision)
    now = datetime.now(UTC)
    token = ""
    for i in range(0, len(edges), batch):
        token = await spicedb.write([_update(e, now) for e in edges[i : i + batch]])
    projected = max(e.relation_revision for e in edges)
    await conn.execute(
        text(
            "UPDATE authz.relation_publication SET projected_revision = :p, projected_zedtoken = :z, "
            "projected_at = now() WHERE tenant_id = :t"
        ),
        {"p": projected, "z": token, "t": uuid.UUID(tenant_id)},
    )
    return projected


async def rebuild(
    conn: AsyncConnection, spicedb: SpiceDB, tenant_id: str, *, batch: int = 500
) -> str:
    """DR drill (DEC-C4): replay the authoritative edges into an empty SpiceDB and refresh the token.

    Revisions don't move; only the ZedToken changes, because the projection now lives elsewhere.
    """
    await spicedb.write_schema()
    edges = await edges_since(conn, tenant_id, 0)
    now = datetime.now(UTC)
    token = ""
    for i in range(0, len(edges), batch):
        token = await spicedb.write([_update(e, now) for e in edges[i : i + batch]])
    if token:
        await conn.execute(
            text(
                "UPDATE authz.relation_publication SET projected_zedtoken = :z, projected_at = now() "
                "WHERE tenant_id = :t"
            ),
            {"z": token, "t": uuid.UUID(tenant_id)},
        )
    return token


@dataclass(frozen=True, slots=True)
class RelationCheck:
    allowed: bool
    codes: tuple[str, ...]
    relation_revision: int  # the revision the decision actually evaluated (goes into the receipt)


async def _checkpoint(conn: AsyncConnection, tenant_id: str) -> tuple[int, int, str | None]:
    row = (
        await conn.execute(
            text(
                "SELECT relation_revision, projected_revision, projected_zedtoken FROM authz.relation_publication "
                "WHERE tenant_id = :t"
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        return 0, 0, None
    return int(row.relation_revision), int(row.projected_revision), row.projected_zedtoken


async def check_relation(
    conn: AsyncConnection,
    spicedb: SpiceDB,
    *,
    tenant_id: str,
    obj: str,
    relation: RelationshipType,
    subject: str,
    risk_level: int,
    required_revision: int | None = None,
) -> RelationCheck:
    """Relationship fact lookup with the risk class's consistency (relationships are facts, not authority)."""
    authoritative, projected, token = await _checkpoint(conn, tenant_id)
    required = authoritative if required_revision is None else required_revision
    if risk_level == 2 and not projection_fresh_enough(projected, required, risk_level):
        await asyncio.sleep(R2_FRESHNESS_WAIT_S)
        _, projected, token = await _checkpoint(conn, tenant_id)
        if not projection_fresh_enough(projected, required, risk_level):
            return RelationCheck(False, ("AUTHORITY_STALE", "RELATION_STATE_STALE"), projected)
    if risk_level >= 3:
        consistency: dict[str, Any] = {"fullyConsistent": True}
        if projected < required:
            return RelationCheck(False, ("STATE_UNAVAILABLE",), projected)
    elif risk_level >= 1 and token:
        consistency = {"atLeastAsFresh": {"token": token}}
    else:
        consistency = {"minimizeLatency": True}
    try:
        allowed = await spicedb.check(obj, relation.value.lower(), subject, consistency)
    except (SpiceDBError, httpx.HTTPError):
        if risk_level >= 2:
            return RelationCheck(False, ("STATE_UNAVAILABLE",), projected)
        raise
    return RelationCheck(
        allowed, ("RELATION_STATE_FRESH",) if allowed else ("RELATIONSHIP_MISSING",), projected
    )
