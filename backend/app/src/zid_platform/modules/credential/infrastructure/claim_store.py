"""Claim checks (`cred.claim_check`, CR-069). Inside the caller's tenant transaction (RLS).

A row holds the normalized assertion and a digest of what was claimed, never the claimed values or any document.
Rows are immutable; a newer check for the same subject and claim type supersedes the older one.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def insert_check(
    conn: AsyncConnection,
    *,
    check_id: str,
    tenant_id: str,
    subject_principal_id: str,
    claim_type: str,
    decision: str,
    assurance_tier: str,
    pack_id: str,
    pack_version: int,
    jurisdiction: dict[str, str],
    claimed_digest: bytes,
    source_id: str,
    source_reference: str | None,
    source_result: str,
    source_checked_at: datetime,
    assertion: dict[str, Any],
) -> None:
    await conn.execute(
        text(
            "UPDATE cred.claim_check SET superseded_at = now() "
            "WHERE subject_principal_id = :s AND claim_type = :c AND superseded_at IS NULL"
        ),
        {"s": uuid.UUID(subject_principal_id), "c": claim_type},
    )
    await conn.execute(
        text(
            "INSERT INTO cred.claim_check (check_id, tenant_id, subject_principal_id, claim_type, decision, "
            "assurance_tier, pack_id, pack_version, jurisdiction, claimed_digest, source_id, source_reference, "
            "source_result, source_checked_at, assertion) VALUES (:id, :t, :s, :c, :d, :a, :pid, :pv, "
            "CAST(:j AS jsonb), :dg, :sid, :sref, :sr, :sca, CAST(:asr AS jsonb))"
        ),
        {
            "id": uuid.UUID(check_id),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(subject_principal_id),
            "c": claim_type,
            "d": decision,
            "a": assurance_tier,
            "pid": pack_id,
            "pv": pack_version,
            "j": json.dumps(jurisdiction),
            "dg": claimed_digest,
            "sid": source_id,
            "sref": source_reference,
            "sr": source_result,
            "sca": source_checked_at,
            "asr": json.dumps(assertion),
        },
    )


async def load_check(conn: AsyncConnection, check_id: str) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    "SELECT check_id, subject_principal_id, claim_type, decision, pack_id, pack_version, "
                    "source_id, source_checked_at, assertion, created_at, superseded_at "
                    "FROM cred.claim_check WHERE check_id = :id"
                ),
                {"id": uuid.UUID(check_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None
