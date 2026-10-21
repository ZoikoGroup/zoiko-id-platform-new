"""Immutable artifact registry, provenance graph and evidence cases (EVIDENCE-001 §8-9, §18).

    artifact_id = await register_artifact(conn, tenant, "POLICY_RELEASE", release_id, version, body)
    await add_edges(conn, tenant, receipt_id, [(subject, relation, object), ...])
    graph = await provenance(conn, entity_type, entity_id, depth=3)

Artifacts are registered once per (type, ref, version) and committed to the ledger; a different
body under the same identity is refused (an ACTIVE artifact is immutable, EVID-08). Provenance
edges reference immutable ids only and never cross a tenant (the edge carries one tenant_id).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_evidence import Digest, hash_json
from zid_outbox import emit

from zid_platform.modules.evidence.application.runtime import EvidenceRuntime, current_runtime
from zid_platform.modules.evidence.application.vault import actor_handle
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.domain.receipts import check_minimized

ARTIFACT_TYPES = frozenset(
    {
        "POLICY_RELEASE",
        "ACTION_DEFINITION",
        "SCHEMA_RELEASE",
        "JURISDICTION_RULE_PACK",
        "VERIFICATION_PROFILE",
        "CREDENTIAL_TRUST_PROFILE",
        "SIGNAL_RULE_RELEASE",
        "AGENT_BLUEPRINT",
        "SIGNING_MATRIX_RELEASE",
        "CONSENT_NOTICE_VERSION",
    }
)
RELATIONS = frozenset(
    {"used", "wasGeneratedBy", "wasAssociatedWith", "wasAttributedTo", "wasDerivedFrom", "wasInformedBy", "actedOnBehalfOf"}
)
CASE_KINDS = frozenset({"INVESTIGATION", "AUDIT", "LEGAL", "INCIDENT", "DATA_RIGHTS"})


@asynccontextmanager
async def _artifact_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    prev = (await conn.execute(text("SELECT current_setting('app.evidence_scope', true)"))).scalar_one()
    await conn.execute(text("SELECT set_config('app.evidence_scope', 'artifacts', true)"))
    try:
        yield
    finally:
        await conn.execute(text("SELECT set_config('app.evidence_scope', :s, true)"), {"s": prev or ""})


async def register_artifact(
    conn: AsyncConnection,
    tenant_id: str | None,
    artifact_type: str,
    artifact_ref: str,
    version: str,
    body: Mapping[str, Any],
) -> tuple[str, str]:
    """(artifact_id, content_hash). Idempotent for identical content; refuses changed content."""
    if artifact_type not in ARTIFACT_TYPES:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown artifact type")
    content_hash = str(hash_json(dict(body), alg="sha-256"))
    tenant_key = tenant_id or "00000000-0000-0000-0000-000000000000"
    async with _artifact_scope(conn):
        existing = (
            await conn.execute(
                text("""
                    SELECT artifact_id, content_hash FROM evidence.artifact
                    WHERE coalesce(tenant_id, '00000000-0000-0000-0000-000000000000'::uuid) = :t
                      AND artifact_type = :ty AND artifact_ref = :r AND version = :v
                """),
                {"t": uuid.UUID(tenant_key), "ty": artifact_type, "r": artifact_ref, "v": version},
            )
        ).first()
        if existing is not None:
            if existing.content_hash != content_hash:
                raise LedgerError("EVIDENCE_LEDGER_CONFLICT", f"{artifact_type} {artifact_ref}@{version} changed in place")
            return str(existing.artifact_id), content_hash
        artifact_id = str(uuid7())
        inserted = await conn.execute(
            text("""
                INSERT INTO evidence.artifact (artifact_id, tenant_id, artifact_type, artifact_ref,
                    version, content_hash, body)
                VALUES (:id, :t, :ty, :r, :v, :h, CAST(:b AS jsonb))
                ON CONFLICT DO NOTHING
            """),
            {
                "id": uuid.UUID(artifact_id),
                "t": uuid.UUID(tenant_id) if tenant_id else None,
                "ty": artifact_type,
                "r": artifact_ref,
                "v": version,
                "h": content_hash,
                "b": json.dumps(dict(body), default=str),
            },
        )
        if not inserted.rowcount:  # a concurrent transaction registered it first
            return await register_artifact(conn, tenant_id, artifact_type, artifact_ref, version, body)
    return artifact_id, content_hash


async def artifact(conn: AsyncConnection, tenant_id: str | None, artifact_type: str, artifact_ref: str) -> list[dict[str, Any]]:
    async with _artifact_scope(conn):
        rows = (
            await conn.execute(
                text("""
                    SELECT artifact_id, tenant_id, artifact_type, artifact_ref, version, content_hash,
                           body, registered_at
                    FROM evidence.artifact WHERE artifact_type = :ty AND artifact_ref = :r
                      AND (tenant_id IS NULL OR tenant_id = CAST(:t AS uuid))
                    ORDER BY registered_at
                """),
                {"ty": artifact_type, "r": artifact_ref, "t": tenant_id},
            )
        ).all()
    return [dict(r._mapping) for r in rows]


@dataclass(frozen=True, slots=True)
class Node:
    kind: str  # entity | activity | agent
    type: str
    ref: str


async def add_edges(
    conn: AsyncConnection, tenant_id: str, edges: Iterable[tuple[Node, str, Node]], *, receipt_id: str | None = None
) -> int:
    n = 0
    for subj, relation, obj in edges:
        if relation not in RELATIONS:
            raise LedgerError("EVIDENCE_SCHEMA_INVALID", f"unknown PROV relation {relation}")
        check_minimized({"s": subj.ref, "o": obj.ref})
        await conn.execute(
            text("""
                INSERT INTO evidence.provenance_edge (edge_id, tenant_id, relation, subject_kind,
                    subject_type, subject_ref, object_kind, object_type, object_ref, receipt_id)
                VALUES (:id, :t, :rel, :sk, :st, :sr, :ok, :ot, :orf, :rid)
            """),
            {
                "id": uuid7(),
                "t": uuid.UUID(tenant_id),
                "rel": relation,
                "sk": subj.kind,
                "st": subj.type,
                "sr": subj.ref,
                "ok": obj.kind,
                "ot": obj.type,
                "orf": obj.ref,
                "rid": uuid.UUID(receipt_id) if receipt_id else None,
            },
        )
        n += 1
    return n


async def provenance(conn: AsyncConnection, entity_type: str, entity_ref: str, *, depth: int = 3, limit: int = 200) -> dict[str, Any]:
    """Bounded lineage around one node (both directions), as a PROV-compatible view (§8.1)."""
    seen: set[tuple[str, str]] = {(entity_type, entity_ref)}
    frontier = [(entity_type, entity_ref)]
    edges: list[dict[str, Any]] = []
    for _ in range(max(1, min(depth, 5))):
        nxt: list[tuple[str, str]] = []
        for t, r in frontier:
            rows = (
                await conn.execute(
                    text("""
                        SELECT relation, subject_kind, subject_type, subject_ref, object_kind,
                               object_type, object_ref, receipt_id, recorded_at
                        FROM evidence.provenance_edge
                        WHERE (subject_type = :t AND subject_ref = :r) OR (object_type = :t AND object_ref = :r)
                        ORDER BY recorded_at LIMIT :n
                    """),
                    {"t": t, "r": r, "n": limit},
                )
            ).all()
            for e in rows:
                d = {k: (str(v) if isinstance(v, uuid.UUID) else v.isoformat() if isinstance(v, datetime) else v)
                     for k, v in dict(e._mapping).items()}
                if d not in edges:
                    edges.append(d)
                for node in ((e.subject_type, e.subject_ref), (e.object_type, e.object_ref)):
                    if node not in seen:
                        seen.add(node)
                        nxt.append(node)
            if len(edges) >= limit:
                break
        frontier = nxt
        if not frontier or len(edges) >= limit:
            break
    return {
        "root": {"type": entity_type, "ref": entity_ref},
        "nodes": [{"type": t, "ref": r} for t, r in sorted(seen)],
        "edges": edges,
        "truncated": len(edges) >= limit,
    }


# --------------------------------------------------------------------------------------- cases


async def open_case(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    kind: str,
    purpose: str,
    reference: str,
    opened_by: str,
    expires_at: datetime,
    legal_basis: str | None = None,
    rt: EvidenceRuntime | None = None,
) -> str:
    """A case binds access to a purpose and an expiry (EVIDENCE §21 "case-bound purpose")."""
    if kind not in CASE_KINDS:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown case kind")
    rt = rt or current_runtime()
    case_id = str(uuid7())
    handle = await actor_handle(conn, tenant_id, opened_by, rt=rt)
    await conn.execute(
        text("""
            INSERT INTO evidence.evidence_case (case_id, tenant_id, case_kind, purpose, legal_basis,
                reference_commitment, opened_by_handle, expires_at)
            VALUES (:id, :t, :k, :p, :lb, :ref, :h, :exp)
        """),
        {
            "id": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "k": kind,
            "p": purpose,
            "lb": legal_basis,
            "ref": str(hash_json({"tenant": tenant_id, "case": reference}, alg="sha-256")),
            "h": handle,
            "exp": expires_at,
        },
    )
    await emit(
        conn,
        name="evidence.case.created",
        tenant_id=tenant_id,
        aggregate_type="evidence_case",
        aggregate_id=case_id,
        aggregate_version=1,
        data={"case_id": case_id, "case_kind": kind, "evidence_class": "E3"},
    )
    return case_id


async def load_case(conn: AsyncConnection, case_id: str) -> Any:
    return (
        await conn.execute(text("SELECT * FROM evidence.evidence_case WHERE case_id = :c"), {"c": uuid.UUID(case_id)})
    ).first()


async def require_open_case(conn: AsyncConnection, case_id: str, now: datetime) -> Any:
    case = await load_case(conn, case_id)
    if case is None:
        raise LedgerError("NOT_FOUND", "unknown case")
    if case.status != "OPEN" or case.expires_at <= now:
        raise LedgerError("EVIDENCE_ACCESS_DENIED", "the case is closed or expired")
    return case


async def close_case(conn: AsyncConnection, case_id: str) -> None:
    await conn.execute(
        text("UPDATE evidence.evidence_case SET status = 'CLOSED', closed_at = clock_timestamp() WHERE case_id = :c AND status = 'OPEN'"),
        {"c": uuid.UUID(case_id)},
    )


async def cases(conn: AsyncConnection, *, status: str | None = None) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT case_id, case_kind, purpose, legal_basis, status, opened_at, expires_at, closed_at "
                "FROM evidence.evidence_case WHERE (CAST(:s AS text) IS NULL OR status = :s) ORDER BY opened_at DESC"
            ),
            {"s": status},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


def case_ref(case_id: str) -> str:
    """The case reference custody records are bound to (stored as a tenant-scoped commitment)."""
    return f"case:{case_id}"


async def case_items(conn: AsyncConnection, tenant_id: str, case_id: str) -> list[dict[str, Any]]:
    commitment = str(hash_json({"tenant": tenant_id, "case": case_ref(case_id)}, alg="sha-256"))
    rows = (
        await conn.execute(
            text(
                "SELECT custody_id, evidence_item_id, custody_action, purpose, export_mode, package_hash, created_at "
                "FROM evidence.custody_record WHERE case_ref = :c ORDER BY created_at"
            ),
            {"c": commitment},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


def digest_matches(stored: str, body: Mapping[str, Any]) -> bool:
    return hash_json(dict(body), alg="sha-256").matches(Digest.parse(stored))


__all__ = [
    "ARTIFACT_TYPES",
    "Node",
    "add_edges",
    "artifact",
    "case_items",
    "case_ref",
    "cases",
    "close_case",
    "digest_matches",
    "load_case",
    "open_case",
    "provenance",
    "register_artifact",
    "require_open_case",
]
