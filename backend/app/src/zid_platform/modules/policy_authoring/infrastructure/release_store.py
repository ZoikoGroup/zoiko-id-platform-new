"""Policy releases in PostgreSQL (ZID-AUTHZ-001 §18, §19; DEC-C3).

- Leaving VALIDATING requires the pre-publication checks (schema, release cases, authority invariant).
- Publishing requires independent reviewers (one; two for R3), checked by the release state machine.
- Activating a release retires the previous ACTIVE one in the same transaction; the database allows
  only one ACTIVE release per tenant and refuses edits to published source.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.policy_runtime.ports import (
    PolicyCase,
    PolicyError,
    PolicyRelease,
    ReleaseStatus,
    advance,
    require_publishable,
)


async def create_release(
    conn: AsyncConnection, release: PolicyRelease, *, policy_name: str, version: int
) -> None:
    pid = (
        await conn.execute(
            text("""
                INSERT INTO authz.policy_definition (policy_id, tenant_id, policy_name) VALUES (:id, :t, :n)
                ON CONFLICT (tenant_id, policy_name) DO UPDATE SET policy_name = EXCLUDED.policy_name
                RETURNING policy_id
            """),
            {"id": uuid.uuid4(), "t": uuid.UUID(release.tenant_id), "n": policy_name},
        )
    ).scalar_one()
    await conn.execute(
        text("""
            INSERT INTO authz.policy_release (release_id, tenant_id, policy_id, version, source_text, source_hash,
                schema_version, cases, status, author_principal_id, reviewers)
            VALUES (:id, :t, :p, :v, :src, :h, :sv, CAST(:cases AS jsonb), CAST(:st AS authz.policy_release_status),
                    :author, :rev)
        """),
        {
            "id": uuid.UUID(release.release_id),
            "t": uuid.UUID(release.tenant_id),
            "p": pid,
            "v": version,
            "src": release.policies,
            "h": release.content_hash,
            "sv": release.schema_version,
            "cases": json.dumps([_case(c) for c in release.cases]),
            "st": release.status.value,
            "author": uuid.UUID(release.author),
            "rev": [uuid.UUID(r) for r in release.reviewers],
        },
    )


def _case(c: PolicyCase) -> dict[str, object]:
    return {
        "name": c.name,
        "principal": c.principal,
        "action": c.action,
        "resource": c.resource,
        "context": c.context,
        "expect_allow": c.expect_allow,
    }


def _release(row: Row[Any]) -> PolicyRelease:
    m = row._mapping
    return PolicyRelease(
        release_id=str(m["release_id"]),
        tenant_id=str(m["tenant_id"]),
        policies=m["source_text"],
        schema_version=m["schema_version"],
        status=ReleaseStatus(m["status"]),
        cases=tuple(PolicyCase(**c) for c in m["cases"]),
        reviewers=tuple(str(r) for r in m["reviewers"]),
        author=str(m["author_principal_id"]),
        effective_at=m["released_at"],
    )


async def load_release(conn: AsyncConnection, release_id: str) -> PolicyRelease:
    row = (
        await conn.execute(
            text("SELECT * FROM authz.policy_release WHERE release_id = :r"),
            {"r": uuid.UUID(release_id)},
        )
    ).first()
    if row is None:
        raise PolicyError("POLICY_RELEASE_NOT_FOUND")
    return _release(row)


async def active_release(conn: AsyncConnection, tenant_id: str) -> PolicyRelease | None:
    row = (
        await conn.execute(
            text("SELECT * FROM authz.policy_release WHERE tenant_id = :t AND status = 'ACTIVE'"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return _release(row) if row else None


async def add_reviewer(conn: AsyncConnection, release_id: str, reviewer_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE authz.policy_release SET reviewers = array_append(reviewers, :r) "
            "WHERE release_id = :id AND NOT (:r = ANY(reviewers))"
        ),
        {"r": uuid.UUID(reviewer_id), "id": uuid.UUID(release_id)},
    )


async def move(
    conn: AsyncConnection,
    release_id: str,
    target: ReleaseStatus,
    *,
    catalog: Mapping[str, Mapping[str, Any]],
    now: datetime,
    high_risk: bool = False,
) -> PolicyRelease:
    rel = await load_release(conn, release_id)
    if rel.status is ReleaseStatus.VALIDATING and target is ReleaseStatus.REVIEW:
        require_publishable(rel, catalog)  # AUTHZ §18.1 blocking checks
    new = advance(rel, target, high_risk=high_risk)
    if target is ReleaseStatus.ACTIVE:
        await conn.execute(
            text(
                "UPDATE authz.policy_release SET status = 'RETIRED' WHERE tenant_id = :t AND status = 'ACTIVE'"
            ),
            {"t": uuid.UUID(rel.tenant_id)},
        )
    await conn.execute(
        text(
            "UPDATE authz.policy_release SET status = CAST(:s AS authz.policy_release_status), "
            "released_at = CASE WHEN :s = 'ACTIVE' THEN :now ELSE released_at END WHERE release_id = :id"
        ),
        {"s": target.value, "now": now, "id": uuid.UUID(release_id)},
    )
    return new


async def next_version(conn: AsyncConnection, tenant_id: str, policy_name: str) -> int:
    v = (
        await conn.execute(
            text(
                "SELECT coalesce(max(r.version), 0) + 1 FROM authz.policy_release r "
                "JOIN authz.policy_definition d USING (policy_id) WHERE d.tenant_id = :t AND d.policy_name = :n"
            ),
            {"t": uuid.UUID(tenant_id), "n": policy_name},
        )
    ).scalar_one()
    return int(v)


async def update_draft(
    conn: AsyncConnection, release_id: str, *, policies: str, cases: tuple[PolicyCase, ...]
) -> PolicyRelease:
    """A DRAFT's source and cases change in place (the trigger refuses this once it leaves DRAFT)."""
    rel = await load_release(conn, release_id)
    new = PolicyRelease(
        rel.release_id,
        rel.tenant_id,
        policies,
        rel.schema_version,
        rel.status,
        cases,
        rel.reviewers,
        rel.author,
    )
    await conn.execute(
        text(
            "UPDATE authz.policy_release SET source_text = :src, source_hash = :h, cases = CAST(:cases AS jsonb) "
            "WHERE release_id = :id AND status = 'DRAFT'"
        ),
        {
            "src": policies,
            "h": new.content_hash,
            "cases": json.dumps([_case(c) for c in cases]),
            "id": uuid.UUID(release_id),
        },
    )
    return new


async def list_releases(conn: AsyncConnection, limit: int = 200) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("""
                SELECT r.release_id, d.policy_name, r.version, r.status, r.source_hash, r.author_principal_id,
                       r.reviewers, r.released_at, jsonb_array_length(r.cases) AS case_count
                FROM authz.policy_release r JOIN authz.policy_definition d USING (policy_id)
                ORDER BY d.policy_name, r.version DESC LIMIT :n
            """),
            {"n": limit},
        )
    ).all()
    return [
        {
            "release_id": str(r.release_id),
            "policy_name": r.policy_name,
            "version": int(r.version),
            "status": r.status,
            "content_hash": r.source_hash,
            "author_principal_id": str(r.author_principal_id),
            "reviewers": [str(x) for x in r.reviewers],
            "released_at": r.released_at.isoformat() if r.released_at else None,
            "case_count": int(r.case_count or 0),
        }
        for r in rows
    ]
