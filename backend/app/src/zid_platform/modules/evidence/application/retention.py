"""Scheduled retention of receipts and audit records (EVIDENCE-001 §22; DEC-F1 §4).

    E1 audit / E2 receipt / E3 evidence: kept for the retention period of their class
    (ZID_EVIDENCE_RETENTION_DAYS, set by the DPO; unset = keep), unless a legal hold covers them.

What is purged is the record body (the pseudonymous receipt / audit row and its annotations and
E3 material inputs). The ledger entry - position, record hash, chain link, signature - and every
checkpoint stay, so the partition still verifies end to end and the record's existence and digest
remain provable; the deletion itself is evidenced (`evidence.retention.deleted`). Runs only in the
worker's retention transaction (the database refuses these deletes anywhere else).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.evidence.application.vault import holding
from zid_platform.modules.evidence.infrastructure import vault_store

TABLES = {
    "decision_receipt": ("receipt_id", "receipt_hash"),
    "audit_record": ("audit_id", "record_hash"),
}


async def _subject_ref_of(conn: AsyncConnection, tenant_id: str, handle: str | None) -> str | None:
    if not handle:
        return None
    row = await vault_store.handle_row(conn, tenant_id, handle)
    return row.subject_ref if row is not None else None


async def purge_expired(
    conn: AsyncConnection,
    retention_days: Mapping[str, int],
    *,
    now: datetime | None = None,
    limit: int = 500,
) -> dict[str, int]:
    """Worker pass (BYPASSRLS role). `retention_days`: evidence class -> days (E1/E2/E3)."""
    now = now or datetime.now(UTC)
    counts = {"purged": 0, "held": 0}
    if not retention_days:
        return counts
    await conn.execute(text("SELECT set_config('app.evidence_retention', 'purge', true)"))
    try:
        for table, (id_col, hash_col) in TABLES.items():
            for cls, days in sorted(retention_days.items()):
                cutoff = now - timedelta(days=int(days))
                rows = (
                    await conn.execute(
                        text(f"""
                            SELECT {id_col} AS id, tenant_id, {hash_col} AS h, actor_handle,
                                   evidence_class::text AS cls
                                   {", material_object_id" if table == "decision_receipt" else ", NULL AS material_object_id"}
                            FROM evidence.{table}
                            WHERE evidence_class::text = :c AND created_at < :cut AND tenant_id IS NOT NULL
                            ORDER BY created_at LIMIT :n
                        """),
                        {"c": cls, "cut": cutoff, "n": limit},
                    )
                ).all()
                for r in rows:
                    tenant = str(r.tenant_id)
                    ref = await _subject_ref_of(conn, tenant, r.actor_handle)
                    if await holding(
                        conn, tenant, subject_ref=ref, object_type=f"evidence.{table}", now=now
                    ):
                        counts["held"] += 1
                        continue
                    if table == "decision_receipt":
                        await conn.execute(
                            text("DELETE FROM evidence.receipt_annotation WHERE receipt_id = :id"),
                            {"id": r.id},
                        )
                    await conn.execute(
                        text(f"DELETE FROM evidence.{table} WHERE {id_col} = :id"), {"id": r.id}
                    )
                    if r.material_object_id is not None:
                        await vault_store.dispose_object(conn, str(r.material_object_id), "EXPIRED")
                    counts["purged"] += 1
                    await emit(
                        conn,
                        name="evidence.retention.deleted",
                        tenant_id=tenant,
                        aggregate_type=f"retention_{table}",
                        aggregate_id=str(uuid.UUID(str(r.id))),
                        aggregate_version=1,
                        data={
                            "record_type": table,
                            "record_id": str(r.id),
                            "record_hash": r.h,
                            "evidence_class": r.cls,
                            "retention_days": int(days),
                        },
                    )
    finally:
        await conn.execute(text("SELECT set_config('app.evidence_retention', '', true)"))
    return counts


__all__ = ["purge_expired"]
