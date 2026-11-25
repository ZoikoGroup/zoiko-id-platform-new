"""Go-live requests and production grants in PostgreSQL (CR-153). Company reads are scoped by row-level security; the operator plane reads every company's inside `operator_scope`."""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_REQUEST = (
    "request_id, tenant_id, status, markets, methods, purpose_code, volume_band, use_case, terms_version, dpa_version, accepted_by, accepted_at, certification, "
    "reviewer_id, reviewed_at, decided_by, decided_at, note, created_at, updated_at"
)
_GRANT = "grant_id, tenant_id, request_id, status, markets, methods, manifest, manifest_digest, approved_by, approved_at, revoked_by, revoked_at, revoke_reason, signature"


@asynccontextmanager
async def operator_scope(conn: AsyncConnection) -> AsyncIterator[None]:
    """Let the operator plane see and decide every company's requests for the enclosed statements only."""
    await conn.execute(text("SELECT set_config('app.verify_api_scope', 'operator', true)"))
    try:
        yield
    finally:
        await conn.execute(text("SELECT set_config('app.verify_api_scope', '', true)"))


def _row(r: Any) -> dict[str, Any]:
    return dict(r._mapping)


async def sandbox_facts(conn: AsyncConnection) -> tuple[int, int]:
    """(approved, declined) sandbox decisions this company's applications have produced. Counts only."""
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE outcome = 'verified'), count(*) FILTER (WHERE outcome = 'not_verified') FROM verify.api_decision WHERE sandbox"
            )
        )
    ).one()
    return int(row[0]), int(row[1])


async def insert_request(
    conn: AsyncConnection,
    *,
    request_id: str,
    tenant_id: str,
    clean: dict[str, Any],
    accepted_by: str,
    certification: list[dict[str, Any]],
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_go_live_request (request_id, tenant_id, status, markets, methods, purpose_code, volume_band, use_case, terms_version, dpa_version, accepted_by, certification) "
            "VALUES (:i, :t, 'SUBMITTED', :mk, :me, :p, :v, :u, :tv, :dv, :ab, CAST(:c AS jsonb))"
        ),
        {
            "i": uuid.UUID(request_id),
            "t": uuid.UUID(tenant_id),
            "mk": clean["markets"],
            "me": clean["methods"],
            "p": clean["purpose_code"],
            "v": clean["volume_band"],
            "u": clean["use_case"],
            "tv": clean["terms_version"],
            "dv": clean["dpa_version"],
            "ab": uuid.UUID(accepted_by),
            "c": json.dumps(certification),
        },
    )


async def get_request(
    conn: AsyncConnection, request_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_REQUEST} FROM verify.api_go_live_request WHERE request_id = :i"
                + (" FOR UPDATE" if lock else "")
            ),
            {"i": uuid.UUID(request_id)},
        )
    ).first()
    return _row(row) if row else None


async def latest_request(conn: AsyncConnection) -> dict[str, Any] | None:
    """The company's most recent request (row-level security confines this to the company)."""
    row = (
        await conn.execute(
            text(
                f"SELECT {_REQUEST} FROM verify.api_go_live_request ORDER BY created_at DESC LIMIT 1"
            )
        )
    ).first()
    return _row(row) if row else None


async def open_requests(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Operator plane: every company's open requests, oldest first."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_REQUEST} FROM verify.api_go_live_request WHERE status IN ('SUBMITTED', 'IN_REVIEW') ORDER BY created_at"
            )
        )
    ).all()
    return [_row(r) for r in rows]


async def move_request(
    conn: AsyncConnection,
    request_id: str,
    status: str,
    now: datetime,
    *,
    reviewer: str | None = None,
    decider: str | None = None,
    note: str | None = None,
) -> None:
    await conn.execute(
        text(
            "UPDATE verify.api_go_live_request SET status = :s, updated_at = :n, "
            "reviewer_id = CASE WHEN CAST(:r AS uuid) IS NOT NULL THEN CAST(:r AS uuid) ELSE reviewer_id END, "
            "reviewed_at = CASE WHEN CAST(:r AS uuid) IS NOT NULL THEN :n ELSE reviewed_at END, "
            "decided_by = CASE WHEN CAST(:d AS uuid) IS NOT NULL THEN CAST(:d AS uuid) ELSE decided_by END, "
            "decided_at = CASE WHEN CAST(:d AS uuid) IS NOT NULL THEN :n ELSE decided_at END, "
            "note = COALESCE(:note, note) WHERE request_id = :i"
        ),
        {
            "i": uuid.UUID(request_id),
            "s": status,
            "n": now,
            "r": reviewer,
            "d": decider,
            "note": note,
        },
    )


async def insert_grant(
    conn: AsyncConnection,
    *,
    grant_id: str,
    tenant_id: str,
    request_id: str,
    markets: list[str],
    methods: list[str],
    manifest: dict[str, Any],
    digest: bytes,
    signature: str,
    approved_by: str,
    approved_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO verify.api_production_grant (grant_id, tenant_id, request_id, status, markets, methods, manifest, manifest_digest, signature, approved_by, approved_at) "
            "VALUES (:g, :t, :r, 'ACTIVE', :mk, :me, CAST(:m AS jsonb), :d, :sig, :a, :at)"
        ),
        {
            "sig": signature,
            "g": uuid.UUID(grant_id),
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(request_id),
            "mk": markets,
            "me": methods,
            "m": json.dumps(manifest),
            "d": digest,
            "a": uuid.UUID(approved_by),
            "at": approved_at,
        },
    )


async def active_grant(conn: AsyncConnection) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_GRANT} FROM verify.api_production_grant WHERE status = 'ACTIVE'")
        )
    ).first()
    return _row(row) if row else None


async def revoke_grant(
    conn: AsyncConnection, tenant_id: str, operator: str, reason: str, now: datetime
) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(
                "UPDATE verify.api_production_grant SET status = 'REVOKED', revoked_by = :o, revoked_at = :n, revoke_reason = :r "
                f"WHERE tenant_id = :t AND status = 'ACTIVE' RETURNING {_GRANT}"
            ),
            {"t": uuid.UUID(tenant_id), "o": uuid.UUID(operator), "n": now, "r": reason},
        )
    ).first()
    return _row(row) if row else None


async def production_application_ids(conn: AsyncConnection) -> list[str]:
    """The company's live production applications (to stop when its grant is revoked)."""
    rows = (
        await conn.execute(
            text(
                "SELECT application_id FROM verify.api_application WHERE environment = 'PRODUCTION' AND status <> 'RETIRED'"
            )
        )
    ).all()
    return [str(r[0]) for r in rows]
