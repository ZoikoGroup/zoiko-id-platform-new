"""Storage for evidence uploads (`evidence_0005`). Inside the caller's tenant transaction (RLS). The bytes are a column of the row."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

_META = (
    "upload_id, tenant_id, subject_principal_id, uploaded_by, declared_type, sniffed_type, size_bytes, declared_sha256, "
    "stored_sha256, exif_stripped, state, reject_reason, scan_state, scanner, created_at, token_expires_at, expires_at, "
    "content_at, erased_at"
)


async def insert(
    conn: AsyncConnection,
    *,
    upload_id: str,
    tenant_id: str,
    subject: str,
    uploaded_by: str,
    declared_type: str,
    size_bytes: int,
    sha256: str,
    now: datetime,
    token_expires_at: datetime,
    expires_at: datetime,
) -> None:
    await conn.execute(
        text(
            "INSERT INTO evidence.upload (upload_id, tenant_id, subject_principal_id, uploaded_by, declared_type, size_bytes, "
            "declared_sha256, created_at, token_expires_at, expires_at) "
            "VALUES (:i, :t, :s, :u, :d, :z, :h, :n, :te, :ex)"
        ),
        {
            "i": uuid.UUID(upload_id),
            "t": uuid.UUID(tenant_id),
            "s": uuid.UUID(subject),
            "u": uuid.UUID(uploaded_by),
            "d": declared_type,
            "z": size_bytes,
            "h": sha256,
            "n": now,
            "te": token_expires_at,
            "ex": expires_at,
        },
    )


async def load(
    conn: AsyncConnection, upload_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    row = (
        (
            await conn.execute(
                text(
                    f"SELECT {_META} FROM evidence.upload WHERE upload_id = :i"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"i": uuid.UUID(upload_id)},
            )
        )
        .mappings()
        .first()
    )
    return dict(row) if row else None


async def load_content(conn: AsyncConnection, upload_id: str) -> bytes | None:
    row = (
        await conn.execute(
            text(
                "SELECT content FROM evidence.upload WHERE upload_id = :i AND state = 'READY' AND scan_state = 'CLEAN'"
            ),
            {"i": uuid.UUID(upload_id)},
        )
    ).first()
    return bytes(row[0]) if row and row[0] is not None else None


async def accept(
    conn: AsyncConnection,
    upload_id: str,
    *,
    content: bytes,
    sniffed_type: str,
    stored_sha256: str,
    exif_stripped: bool,
    scanner: str,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE evidence.upload SET state = 'READY', scan_state = 'CLEAN', scanner = :sc, content = :c, sniffed_type = :st, "
            "stored_sha256 = :h, exif_stripped = :x, content_at = :n WHERE upload_id = :i AND state = 'AWAITING_CONTENT'"
        ),
        {
            "sc": scanner,
            "c": content,
            "st": sniffed_type,
            "h": stored_sha256,
            "x": exif_stripped,
            "n": now,
            "i": uuid.UUID(upload_id),
        },
    )


async def reject(
    conn: AsyncConnection,
    upload_id: str,
    *,
    reason: str,
    scan_state: str = "NOT_SCANNED",
    scanner: str | None = None,
    sniffed_type: str | None = None,
    now: datetime,
) -> None:
    """A refused upload keeps no bytes (the table CHECK would refuse them anyway) and the reason is recorded."""
    await conn.execute(
        text(
            "UPDATE evidence.upload SET state = 'REJECTED', reject_reason = :r, scan_state = :ss, scanner = :sc, "
            "sniffed_type = :st, content = NULL, content_at = :n WHERE upload_id = :i AND state = 'AWAITING_CONTENT'"
        ),
        {
            "r": reason,
            "ss": scan_state,
            "sc": scanner,
            "st": sniffed_type,
            "n": now,
            "i": uuid.UUID(upload_id),
        },
    )


async def erase(conn: AsyncConnection, upload_id: str, *, state: str, now: datetime) -> bool:
    """Remove the bytes and mark the upload DELETED or EXPIRED. The row stays as the record that it existed."""
    result = await conn.execute(
        text(
            "UPDATE evidence.upload SET state = :s, content = NULL, erased_at = :n "
            "WHERE upload_id = :i AND state IN ('READY', 'AWAITING_CONTENT')"
        ),
        {"s": state, "n": now, "i": uuid.UUID(upload_id)},
    )
    return bool(result.rowcount)


async def expired_ids(conn: AsyncConnection, now: datetime, limit: int = 200) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT upload_id FROM evidence.upload WHERE state = 'READY' AND expires_at <= :n ORDER BY expires_at LIMIT :l"
            ),
            {"n": now, "l": limit},
        )
    ).all()
    return [str(r[0]) for r in rows]


async def by_subject(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    f"SELECT {_META} FROM evidence.upload WHERE subject_principal_id = :s ORDER BY created_at"
                ),
                {"s": uuid.UUID(subject)},
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def delete_subject(conn: AsyncConnection, tenant_id: str, subject: str) -> int:
    """Governed erasure of a person: their uploads go entirely (bytes included), through the one controlled function."""
    return int(
        (
            await conn.execute(
                text("SELECT evidence.erase_uploads(:t, :s)"),
                {"t": uuid.UUID(tenant_id), "s": uuid.UUID(subject)},
            )
        ).scalar_one()
    )
