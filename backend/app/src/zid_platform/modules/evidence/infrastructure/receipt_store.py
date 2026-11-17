"""PostgreSQL access for receipts, audit records, annotations and custody records (EVIDENCE-001 §3,
§7, §18, §20). All append-only (database triggers); the ledger commits each by (record_type, id).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

# record_type in the ledger -> (table, id column, hash column)
LEDGERED: dict[str, tuple[str, str, str]] = {
    "decision_receipt": ("decision_receipt", "receipt_id", "receipt_hash"),
    "audit_record": ("audit_record", "audit_id", "record_hash"),
    "receipt_annotation": ("receipt_annotation", "annotation_id", "record_hash"),
    "custody_record": ("custody_record", "custody_id", "record_hash"),
    "erasure_tombstone": ("erasure_tombstone", "tombstone_id", "record_hash"),
    "artifact": ("artifact", "artifact_id", "content_hash"),
}


def _u(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


async def insert_receipt(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.decision_receipt (receipt_id, tenant_id, decision_id,
                evidence_class, decision_type, outcome, reason_codes, actor_handle,
                represented_handle, action, resource_class, purpose_id, policy_release_id,
                relation_revision, security_revision, risk_class, input_commitment,
                material_object_id, previous_receipt_id, retention_class, body, receipt_hash,
                effective_at)
            VALUES (:receipt_id, :tenant_id, :decision_id,
                CAST(:evidence_class AS evidence.evidence_class), :decision_type, :outcome,
                :reason_codes, :actor_handle, :represented_handle, :action, :resource_class,
                :purpose_id, :policy_release_id, :relation_revision, :security_revision,
                :risk_class, :input_commitment, :material_object_id, :previous_receipt_id,
                :retention_class, CAST(:body AS jsonb), :receipt_hash, :effective_at)
        """),
        {
            **v,
            "receipt_id": _u(v["receipt_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "decision_id": _u(v["decision_id"]),
            "policy_release_id": _u(v["policy_release_id"]),
            "material_object_id": _u(v["material_object_id"]),
            "previous_receipt_id": _u(v["previous_receipt_id"]),
            "reason_codes": list(v["reason_codes"]),
            "body": json.dumps(v["body"]),
        },
    )


async def bump_aggregate(
    conn: AsyncConnection, tenant_id: str, bucket: date, action: str, outcome: str
) -> None:
    """E0: counts and outcome categories only, no subject-level receipt (DEC-F1 §1)."""
    await conn.execute(
        text("""
            INSERT INTO evidence.decision_aggregate (tenant_id, bucket, action, outcome, decisions)
            VALUES (:t, :b, :a, :o, 1)
            ON CONFLICT (tenant_id, bucket, action, outcome)
            DO UPDATE SET decisions = evidence.decision_aggregate.decisions + 1
        """),
        {"t": uuid.UUID(tenant_id), "b": bucket, "a": action, "o": outcome},
    )


async def insert_audit(conn: AsyncConnection, v: Mapping[str, Any]) -> bool:
    """False when the source event was already recorded (a redelivered outbox event)."""
    result = await conn.execute(
        text("""
            INSERT INTO evidence.audit_record (audit_id, tenant_id, event_type, actor_type,
                actor_handle, target_type, target_ref, action, outcome, reason_code,
                evidence_class, source_event_id, body, record_hash, occurred_at)
            VALUES (:audit_id, :tenant_id, :event_type, :actor_type, :actor_handle, :target_type,
                :target_ref, :action, :outcome, :reason_code,
                CAST(:evidence_class AS evidence.evidence_class), :source_event_id,
                CAST(:body AS jsonb), :record_hash, :occurred_at)
            ON CONFLICT (source_event_id) DO NOTHING
        """),
        {
            **v,
            "audit_id": _u(v["audit_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "source_event_id": _u(v["source_event_id"]),
            "body": json.dumps(v["body"]),
        },
    )
    return bool(result.rowcount)


async def insert_annotation(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.receipt_annotation (annotation_id, tenant_id, receipt_id, kind,
                body, record_hash, author_handle)
            VALUES (:annotation_id, :tenant_id, :receipt_id, :kind, CAST(:body AS jsonb),
                :record_hash, :author_handle)
        """),
        {
            **v,
            "annotation_id": _u(v["annotation_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "receipt_id": _u(v["receipt_id"]),
            "body": json.dumps(v["body"]),
        },
    )


async def insert_custody(conn: AsyncConnection, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO evidence.custody_record (custody_id, tenant_id, case_ref, evidence_item_id,
                custodian_handle, custody_action, purpose, export_mode, package_hash, body,
                record_hash)
            VALUES (:custody_id, :tenant_id, :case_ref, :evidence_item_id, :custodian_handle,
                :custody_action, :purpose, :export_mode, :package_hash, CAST(:body AS jsonb),
                :record_hash)
        """),
        {
            **v,
            "custody_id": _u(v["custody_id"]),
            "tenant_id": _u(v["tenant_id"]),
            "body": json.dumps(v["body"]),
        },
    )


RECEIPT_LIST = (
    "receipt_id, decision_id, evidence_class::text AS evidence_class, decision_type, outcome, "
    "reason_codes, actor_handle, action, resource_class, risk_class, policy_release_id, "
    "receipt_hash, effective_at, created_at"
)


async def load_receipt(conn: AsyncConnection, receipt_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT *, evidence_class::text AS evidence_class FROM evidence.decision_receipt "
                "WHERE receipt_id = :id"
            ),
            {"id": uuid.UUID(receipt_id)},
        )
    ).first()


async def receipt_for_decision(conn: AsyncConnection, decision_id: str) -> Row[Any] | None:
    return (
        await conn.execute(
            text(
                "SELECT receipt_id FROM evidence.decision_receipt WHERE decision_id = :d "
                "ORDER BY created_at LIMIT 1"
            ),
            {"d": uuid.UUID(decision_id)},
        )
    ).first()


async def list_receipts(
    conn: AsyncConnection,
    *,
    action: str | None,
    outcome: str | None,
    evidence_class: str | None,
    actor_handle: str | None,
    before: datetime | None,
    limit: int,
) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(f"""
                    SELECT {RECEIPT_LIST} FROM evidence.decision_receipt
                    WHERE (CAST(:a AS text) IS NULL OR action = :a)
                      AND (CAST(:o AS text) IS NULL OR outcome = :o)
                      AND (CAST(:e AS text) IS NULL OR evidence_class::text = :e)
                      AND (CAST(:h AS text) IS NULL OR actor_handle = :h)
                      AND (CAST(:b AS timestamptz) IS NULL OR created_at < :b)
                    ORDER BY created_at DESC LIMIT :n
                """),
                {
                    "a": action,
                    "o": outcome,
                    "e": evidence_class,
                    "h": actor_handle,
                    "b": before,
                    "n": limit,
                },
            )
        ).all()
    )


async def receipt_annotations(conn: AsyncConnection, receipt_id: str) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text(
                    "SELECT annotation_id, kind, body, record_hash, author_handle, created_at "
                    "FROM evidence.receipt_annotation WHERE receipt_id = :r ORDER BY created_at"
                ),
                {"r": uuid.UUID(receipt_id)},
            )
        ).all()
    )


async def list_audit(
    conn: AsyncConnection, *, event_type: str | None, before: datetime | None, limit: int
) -> list[Row[Any]]:
    return list(
        (
            await conn.execute(
                text("""
                    SELECT audit_id, tenant_id, event_type, actor_type, actor_handle, target_type,
                           target_ref, action, outcome, reason_code,
                           evidence_class::text AS evidence_class, record_hash, occurred_at,
                           created_at
                    FROM evidence.audit_record
                    WHERE (CAST(:t AS text) IS NULL OR event_type = :t)
                      AND (CAST(:b AS timestamptz) IS NULL OR created_at < :b)
                    ORDER BY created_at DESC LIMIT :n
                """),
                {"t": event_type, "b": before, "n": limit},
            )
        ).all()
    )


async def export_audit(
    conn: AsyncConnection,
    *,
    after: tuple[datetime, uuid.UUID] | None,
    event_type: str | None,
    limit: int,
    settled_before: datetime,
) -> list[Row[Any]]:
    """Audit records after a cursor, oldest first, in a stable order (created_at, then id), only those old enough that every transaction that could
    still add an earlier one has finished, so a reader that follows the cursor never skips a record."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT audit_id, event_type, actor_type, actor_handle, target_type, target_ref, action, outcome, reason_code,
                           evidence_class::text AS evidence_class, record_hash, occurred_at, created_at
                    FROM evidence.audit_record
                    WHERE (CAST(:t AS text) IS NULL OR event_type = :t)
                      AND created_at <= :settled
                      AND (CAST(:ts AS timestamptz) IS NULL OR (created_at, audit_id) > (CAST(:ts AS timestamptz), CAST(:id AS uuid)))
                    ORDER BY created_at ASC, audit_id ASC LIMIT :n
                """),
                {
                    "t": event_type,
                    "settled": settled_before,
                    "ts": after[0] if after else None,
                    "id": after[1] if after else None,
                    "n": limit,
                },
            )
        ).all()
    )


async def unledgered(conn: AsyncConnection, record_type: str, limit: int) -> Sequence[Row[Any]]:
    """Records visible in this transaction that the ledger has not committed yet, oldest first."""
    table, id_col, hash_col = LEDGERED[record_type]
    cls = "evidence_class::text" if record_type in ("decision_receipt", "audit_record") else "NULL"
    created = "registered_at" if record_type == "artifact" else "created_at"
    return (
        await conn.execute(
            text(f"""
                SELECT r.{id_col}::text AS record_id, r.tenant_id, r.body, r.{hash_col} AS record_hash,
                       {cls} AS evidence_class, r.{created} AS created_at
                FROM evidence.{table} r
                WHERE NOT EXISTS (SELECT 1 FROM evidence.ledger_entry l
                                  WHERE l.record_id = r.{id_col}::text AND l.record_type = :rt)
                ORDER BY r.{created} LIMIT :n
            """),
            {"rt": record_type, "n": limit},
        )
    ).all()


async def load_body(
    conn: AsyncConnection, record_type: str, record_id: str
) -> Mapping[str, Any] | None:
    table, id_col, _ = LEDGERED[record_type]
    row = (
        await conn.execute(
            text(f"SELECT body FROM evidence.{table} WHERE {id_col} = :id"),
            {"id": uuid.UUID(record_id)},
        )
    ).first()
    return dict(row.body) if row else None


async def audit_for_target(
    conn: AsyncConnection, target_type: str, target_ref: str, *, limit: int = 500
) -> list[Row[Any]]:
    """CR-110: one object's audit trail, oldest first (an incident dossier). Codes and hashes only; the record's details stay in the ledger."""
    return list(
        (
            await conn.execute(
                text("""
                    SELECT event_type, actor_type, action, outcome, reason_code, evidence_class::text AS evidence_class, record_hash, occurred_at
                    FROM evidence.audit_record WHERE target_type = :tt AND target_ref = :tr ORDER BY occurred_at ASC, audit_id ASC LIMIT :n
                """),
                {"tt": target_type, "tr": target_ref, "n": limit},
            )
        ).all()
    )
