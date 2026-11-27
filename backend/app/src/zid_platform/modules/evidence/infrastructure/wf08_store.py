"""PostgreSQL access for the WF08 services: authority snapshots, status lists, export requests (CR-170). Constant SQL only; every value is a bound parameter."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection


def _u(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


# ------------------------------------------------------------------------------------------------------------------ snapshots


async def insert_snapshot(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.authority_snapshot (snapshot_id, tenant_id, decision_ref, subject_ref, decision_at, verdict, reasons, body,
                snapshot_digest, signature, signer_kid, created_by)
            VALUES (:snapshot_id, :tenant_id, :decision_ref, :subject_ref, :decision_at, :verdict, :reasons, CAST(:body AS jsonb),
                :snapshot_digest, :signature, :signer_kid, :created_by)
        """),
        {
            **v,
            "snapshot_id": _u(v["snapshot_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "created_by": _u(v.get("created_by")),
            "reasons": list(v["reasons"]),
            "body": json.dumps(v["body"]),
        },
    )


async def load_snapshot(conn: AsyncConnection, decision_ref: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM evidence.authority_snapshot WHERE decision_ref = :d"),
            {"d": decision_ref},
        )
    ).first()


# ---------------------------------------------------------------------------------------------------------------- status lists


async def ensure_list(
    conn: AsyncConnection, tenant_id: str, purpose: str, size_bits: int
) -> Row[Any]:
    await conn.execute(
        text(
            "INSERT INTO evidence.status_list (list_id, tenant_id, purpose, size_bits) VALUES (:l, :t, :p, :s) ON CONFLICT (tenant_id, purpose) DO NOTHING"
        ),
        {"l": uuid.uuid4(), "t": uuid.UUID(tenant_id), "p": purpose, "s": size_bits},
    )
    row = (
        await conn.execute(
            text("SELECT * FROM evidence.status_list WHERE purpose = :p"), {"p": purpose}
        )
    ).first()
    assert row is not None
    return row


async def slot_for(conn: AsyncConnection, list_id: uuid.UUID, subject_ref: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM evidence.status_slot WHERE list_id = :l AND subject_ref = :s"),
            {"l": list_id, "s": subject_ref},
        )
    ).first()


async def insert_slot(
    conn: AsyncConnection, tenant_id: str, list_id: uuid.UUID, position: int, subject_ref: str
) -> bool:
    """False when that position is already taken (the caller draws another)."""
    result = await conn.execute(
        text("""
            INSERT INTO evidence.status_slot (slot_id, tenant_id, list_id, position, subject_ref) VALUES (:i, :t, :l, :p, :s)
            ON CONFLICT (list_id, position) DO NOTHING
        """),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "l": list_id,
            "p": position,
            "s": subject_ref,
        },
    )
    return bool(result.rowcount)


async def insert_change(
    conn: AsyncConnection,
    tenant_id: str,
    slot_id: uuid.UUID,
    change: str,
    reason: str,
    changed_by: str | None,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO evidence.status_change (change_id, tenant_id, slot_id, change, reason, changed_by) VALUES (:i, :t, :s, :c, :r, :b)"
        ),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "s": slot_id,
            "c": change,
            "r": reason,
            "b": _u(changed_by),
        },
    )


async def marked_positions(conn: AsyncConnection, list_id: uuid.UUID) -> list[int]:
    """Positions whose latest change is SET."""
    rows = (
        await conn.execute(
            text("""
                SELECT s.position FROM evidence.status_slot s
                JOIN LATERAL (SELECT change FROM evidence.status_change c WHERE c.slot_id = s.slot_id ORDER BY c.seq DESC LIMIT 1) last ON true
                WHERE s.list_id = :l AND last.change = 'SET'
            """),
            {"l": list_id},
        )
    ).all()
    return [int(r.position) for r in rows]


async def next_version(conn: AsyncConnection, list_id: uuid.UUID) -> int:
    row = (
        await conn.execute(
            text(
                "SELECT coalesce(max(version), 0) + 1 AS v FROM evidence.status_publication WHERE list_id = :l"
            ),
            {"l": list_id},
        )
    ).one()
    return int(row.v)


async def insert_publication(
    conn: AsyncConnection,
    tenant_id: str,
    list_id: uuid.UUID,
    version: int,
    jws: str,
    issued_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.status_publication (publication_id, tenant_id, list_id, version, jws, issued_at, expires_at)
            VALUES (:i, :t, :l, :v, :j, :a, :e)
        """),
        {
            "i": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "l": list_id,
            "v": version,
            "j": jws,
            "a": issued_at,
            "e": expires_at,
        },
    )


async def latest_publication(conn: AsyncConnection, list_id: uuid.UUID) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT * FROM evidence.status_publication WHERE list_id = :l ORDER BY version DESC LIMIT 1"
            ),
            {"l": list_id},
        )
    ).first()


async def public_publication(conn: AsyncConnection, list_id: uuid.UUID) -> str | None:
    """The newest signed list, readable without a tenant: the session sets the public flag for exactly this one read."""
    await conn.execute(text("SELECT set_config('app.public_status_list', 'on', true)"))
    row = (
        await conn.execute(
            text(
                "SELECT jws FROM evidence.status_publication WHERE list_id = :l ORDER BY version DESC LIMIT 1"
            ),
            {"l": list_id},
        )
    ).first()
    return str(row.jws) if row else None


# --------------------------------------------------------------------------------------------------------------------- exports


async def insert_export_request(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.export_request (request_id, tenant_id, receipt_id, tier, recipient, purpose, legal_basis, retention_days, recipient_country,
                cross_border, needs_second_person, requested_by)
            VALUES (:request_id, :tenant_id, :receipt_id, :tier, CAST(:recipient AS jsonb), :purpose, :legal_basis, :retention_days, :recipient_country,
                :cross_border, :needs_second_person, :requested_by)
        """),
        {
            **v,
            "request_id": _u(v["request_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "receipt_id": _u(v["receipt_id"]),
            "requested_by": _u(v["requested_by"]),
            "recipient": json.dumps(v["recipient"]),
        },
    )


async def load_export_request(conn: AsyncConnection, request_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM evidence.export_request WHERE request_id = :i"),
            {"i": uuid.UUID(request_id)},
        )
    ).first()


async def list_export_requests(conn: AsyncConnection, limit: int = 50) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("SELECT * FROM evidence.export_request ORDER BY requested_at DESC LIMIT :n"),
                {"n": limit},
            )
        ).all()
    )


async def decide_export_request(
    conn: AsyncConnection, request_id: str, *, approve: bool, decided_by: str, reason: str | None
) -> bool:
    result = await conn.execute(
        text("""
            UPDATE evidence.export_request SET state = CASE WHEN :ok THEN 'APPROVED' ELSE 'REFUSED' END, decided_by = :b, decided_at = now(), decision_reason = :r
            WHERE request_id = :i AND state = 'REQUESTED'
        """),
        {"ok": approve, "b": uuid.UUID(decided_by), "r": reason, "i": uuid.UUID(request_id)},
    )
    return bool(result.rowcount)


async def issue_export_request(
    conn: AsyncConnection,
    request_id: str,
    *,
    package_digest: str,
    manifest_jws: str,
    expires_at: datetime,
) -> bool:
    result = await conn.execute(
        text("""
            UPDATE evidence.export_request SET state = 'ISSUED', issued_at = now(), expires_at = :e, package_digest = :d, manifest_jws = :j
            WHERE request_id = :i AND state IN ('REQUESTED', 'APPROVED')
        """),
        {"e": expires_at, "d": package_digest, "j": manifest_jws, "i": uuid.UUID(request_id)},
    )
    return bool(result.rowcount)
