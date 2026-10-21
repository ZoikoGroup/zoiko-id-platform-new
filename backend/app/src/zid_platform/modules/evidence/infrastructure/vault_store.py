"""PostgreSQL access for the subject-evidence vault (DEC-F1 §2-3; EVIDENCE-001 §13-14, §22).

subject_key, subject_handle_map, evidence_object and legal_hold are the only mutable evidence
tables, and only in one direction: a destroyed key, an erased resolver row or a disposed object can
never be restored (trigger `evidence.no_resurrection`). Everything runs under tenant RLS.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection


def _u(value: str) -> uuid.UUID:
    return uuid.UUID(value)


# ------------------------------------------------------------------------------- subject keys


@dataclass(frozen=True, slots=True)
class SubjectKeyRow:
    tenant_id: str
    subject_ref: str
    key_id: str
    wrapped_key: bytes | None
    kek_id: str
    kek_version: int
    state: str


def _key(row: Row[Any]) -> SubjectKeyRow:
    return SubjectKeyRow(
        str(row.tenant_id),
        row.subject_ref,
        str(row.key_id),
        bytes(row.wrapped_key) if row.wrapped_key is not None else None,
        row.kek_id,
        int(row.kek_version),
        row.state,
    )


async def load_subject_key(
    conn: AsyncConnection, tenant_id: str, subject_ref: str, *, lock: bool = False
) -> SubjectKeyRow | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM evidence.subject_key WHERE tenant_id = :t AND subject_ref = :s"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": _u(tenant_id), "s": subject_ref},
        )
    ).first()
    return _key(row) if row else None


async def insert_subject_key(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    subject_ref: str,
    key_id: str,
    wrapped_key: bytes,
    kek_id: str,
    kek_version: int,
) -> SubjectKeyRow:
    """Insert unless another transaction won the race; returns whichever row now exists."""
    await conn.execute(
        text("""
            INSERT INTO evidence.subject_key (tenant_id, subject_ref, key_id, wrapped_key, kek_id,
                kek_version)
            VALUES (:t, :s, :k, :w, :kek, :v)
            ON CONFLICT (tenant_id, subject_ref) DO NOTHING
        """),
        {
            "t": _u(tenant_id),
            "s": subject_ref,
            "k": _u(key_id),
            "w": wrapped_key,
            "kek": kek_id,
            "v": kek_version,
        },
    )
    row = await load_subject_key(conn, tenant_id, subject_ref)
    assert row is not None
    return row


async def destroy_subject_key(conn: AsyncConnection, tenant_id: str, subject_ref: str) -> bool:
    result = await conn.execute(
        text("""
            UPDATE evidence.subject_key SET wrapped_key = NULL, state = 'DESTROYED',
                destroyed_at = clock_timestamp()
            WHERE tenant_id = :t AND subject_ref = :s AND state = 'ACTIVE'
        """),
        {"t": _u(tenant_id), "s": subject_ref},
    )
    return bool(result.rowcount)


# ---------------------------------------------------------------------------- identity resolver


async def handle_row(conn: AsyncConnection, tenant_id: str, handle: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM evidence.subject_handle_map WHERE tenant_id = :t AND handle = :h"),
            {"t": _u(tenant_id), "h": handle},
        )
    ).first()


async def insert_handle(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    handle: str,
    domain: str,
    subject_ref: str,
    ciphertext: bytes,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.subject_handle_map (tenant_id, handle, domain, subject_ref,
                principal_ciphertext)
            VALUES (:t, :h, :d, :s, :c)
            ON CONFLICT (tenant_id, handle) DO NOTHING
        """),
        {"t": _u(tenant_id), "h": handle, "d": domain, "s": subject_ref, "c": ciphertext},
    )


async def erase_handles(conn: AsyncConnection, tenant_id: str, subject_ref: str) -> int:
    result = await conn.execute(
        text("""
            UPDATE evidence.subject_handle_map SET principal_ciphertext = NULL, status = 'ERASED',
                erased_at = clock_timestamp()
            WHERE tenant_id = :t AND subject_ref = :s AND status = 'ACTIVE'
        """),
        {"t": _u(tenant_id), "s": subject_ref},
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------------------- evidence objects


async def insert_object(conn: AsyncConnection, values: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.evidence_object (evidence_object_id, tenant_id, subject_ref,
                object_type, classification, retention_class, content_hash, object_uri,
                encryption_key_ref, ciphertext, expires_at)
            VALUES (:id, :t, :s, :type, :cls, :ret, :hash, :uri, :keyref, :ct, :exp)
        """),
        {
            "id": _u(values["evidence_object_id"]),
            "t": _u(values["tenant_id"]),
            "s": values["subject_ref"],
            "type": values["object_type"],
            "cls": values["classification"],
            "ret": values["retention_class"],
            "hash": values["content_hash"],
            "uri": values["object_uri"],
            "keyref": values["encryption_key_ref"],
            "ct": values["ciphertext"],
            "exp": values["expires_at"],
        },
    )


async def load_object(conn: AsyncConnection, object_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text("SELECT * FROM evidence.evidence_object WHERE evidence_object_id = :id"),
            {"id": _u(object_id)},
        )
    ).first()


async def subject_objects(conn: AsyncConnection, tenant_id: str, subject_ref: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT evidence_object_id, object_type, status FROM evidence.evidence_object "
                    "WHERE tenant_id = :t AND subject_ref = :s ORDER BY created_at"
                ),
                {"t": _u(tenant_id), "s": subject_ref},
            )
        ).all()
    )


async def dispose_object(conn: AsyncConnection, object_id: str, status: str) -> bool:
    result = await conn.execute(
        text("""
            UPDATE evidence.evidence_object SET ciphertext = NULL, status = :st,
                disposed_at = clock_timestamp()
            WHERE evidence_object_id = :id AND status = 'STORED'
        """),
        {"id": _u(object_id), "st": status},
    )
    return bool(result.rowcount)


async def objects_due(conn: AsyncConnection, now: datetime, limit: int) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("""
                    SELECT evidence_object_id, tenant_id, subject_ref, object_type, content_hash,
                           retention_class, expires_at
                    FROM evidence.evidence_object
                    WHERE status = 'STORED' AND expires_at IS NOT NULL AND expires_at <= :now
                    ORDER BY expires_at LIMIT :n
                """),
                {"now": now, "n": limit},
            )
        ).all()
    )


# --------------------------------------------------------------------------------- legal holds


@dataclass(frozen=True, slots=True)
class Hold:
    legal_hold_id: str
    tenant_id: str
    basis: str
    reference: str
    requested_by: str
    approved_by: str
    subject_refs: tuple[str, ...]
    object_types: tuple[str, ...]
    effective_at: datetime
    review_at: datetime
    expires_at: datetime | None
    status: str
    released_at: datetime | None
    version: int

    def active(self, now: datetime) -> bool:
        return (
            self.status == "ACTIVE"
            and self.effective_at <= now
            and (self.expires_at is None or now < self.expires_at)
        )

    def covers(self, subject_ref: str | None, object_type: str | None) -> bool:
        """Scope is the intersection of the listed subjects and object types (an empty list
        leaves that dimension unrestricted; a hold always names at least one of them)."""
        subject_ok = not self.subject_refs or (subject_ref is not None and subject_ref in self.subject_refs)
        type_ok = not self.object_types or (object_type is not None and object_type in self.object_types)
        return subject_ok and type_ok


def _hold(row: Row[Any]) -> Hold:
    return Hold(
        str(row.legal_hold_id),
        str(row.tenant_id),
        row.basis,
        row.reference,
        str(row.requested_by),
        str(row.approved_by),
        tuple(row.subject_refs),
        tuple(row.object_types),
        row.effective_at,
        row.review_at,
        row.expires_at,
        row.status,
        row.released_at,
        int(row.version),
    )


async def insert_hold(conn: AsyncConnection, values: Mapping[str, Any]) -> Hold:
    row = (
        await conn.execute(
            text("""
                INSERT INTO evidence.legal_hold (legal_hold_id, tenant_id, basis, reference,
                    requested_by, approved_by, subject_refs, object_types, effective_at, review_at,
                    expires_at)
                VALUES (:id, :t, :basis, :ref, :req, :appr, :subjects, :types, :eff, :review, :exp)
                RETURNING *
            """),
            {
                "id": _u(values["legal_hold_id"]),
                "t": _u(values["tenant_id"]),
                "basis": values["basis"],
                "ref": values["reference"],
                "req": _u(values["requested_by"]),
                "appr": _u(values["approved_by"]),
                "subjects": list(values["subject_refs"]),
                "types": list(values["object_types"]),
                "eff": values["effective_at"],
                "review": values["review_at"],
                "exp": values["expires_at"],
            },
        )
    ).one()
    return _hold(row)


async def load_hold(conn: AsyncConnection, hold_id: str, *, lock: bool = False) -> Hold | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM evidence.legal_hold WHERE legal_hold_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": _u(hold_id)},
        )
    ).first()
    return _hold(row) if row else None


async def release_hold(
    conn: AsyncConnection, hold: Hold, *, by: str, reason: str, now: datetime
) -> Hold:
    row = (
        await conn.execute(
            text("""
                UPDATE evidence.legal_hold SET status = 'RELEASED', released_at = :now,
                    released_by = :by, release_reason = :reason, version = version + 1
                WHERE legal_hold_id = :id AND version = :v AND status = 'ACTIVE'
                RETURNING *
            """),
            {"id": _u(hold.legal_hold_id), "v": hold.version, "now": now, "by": _u(by), "reason": reason},
        )
    ).first()
    if row is None:
        raise LookupError("CONCURRENT_MODIFICATION")
    return _hold(row)


async def holds(conn: AsyncConnection, *, status: str | None = None, limit: int = 200) -> list[Hold]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM evidence.legal_hold WHERE (CAST(:st AS text) IS NULL OR status = :st) "
                "ORDER BY created_at DESC LIMIT :n"
            ),
            {"st": status, "n": limit},
        )
    ).all()
    return [_hold(r) for r in rows]


async def active_holds(conn: AsyncConnection, tenant_id: str, now: datetime) -> list[Hold]:
    rows = (
        await conn.execute(
            text("""
                SELECT * FROM evidence.legal_hold WHERE tenant_id = :t AND status = 'ACTIVE'
                AND effective_at <= :now AND (expires_at IS NULL OR expires_at > :now)
            """),
            {"t": _u(tenant_id), "now": now},
        )
    ).all()
    return [_hold(r) for r in rows]


# ---------------------------------------------------------------------------------- tombstones


async def insert_tombstone(conn: AsyncConnection, values: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.erasure_tombstone (tombstone_id, tenant_id, subject_ref,
                request_ref, outcome, body, record_hash)
            VALUES (:id, :t, :s, :req, :out, CAST(:body AS jsonb), :hash)
        """),
        {
            "id": _u(values["tombstone_id"]),
            "t": _u(values["tenant_id"]),
            "s": values["subject_ref"],
            "req": values["request_ref"],
            "out": values["outcome"],
            "body": json.dumps(values["body"]),
            "hash": values["record_hash"],
        },
    )


async def tombstones(conn: AsyncConnection, *, since: datetime | None = None) -> Sequence[Row[Any]]:
    return (
        await conn.execute(
            text(
                "SELECT tombstone_id, tenant_id, subject_ref, outcome, body, created_at "
                "FROM evidence.erasure_tombstone WHERE (CAST(:s AS timestamptz) IS NULL OR "
                "created_at >= :s) ORDER BY created_at"
            ),
            {"s": since},
        )
    ).all()
