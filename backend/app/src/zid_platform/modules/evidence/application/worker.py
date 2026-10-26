"""One evidence worker pass (`python -m zid_platform.entrypoints.worker`, DEC-D6; DEC-F2 §2).

    sequence -> checkpoint (cadence; E3 sooner) -> anchor (optional TSA) -> retention sweep

Run with the evidence worker role (ZID_WORKER_DATABASE_URL: BYPASSRLS on the evidence schema,
no DELETE): it sees every tenant partition and the platform-security partition. Each step is
idempotent, so a failed pass is simply run again; nothing is ever reported sealed before it is.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.application import bulk
from zid_platform.modules.evidence.application.checkpointer import anchor_pending, seal_due
from zid_platform.modules.evidence.application.receipts import sequence_pending
from zid_platform.modules.evidence.application.retention import purge_expired
from zid_platform.modules.evidence.application.runtime import EvidenceRuntime, current_runtime
from zid_platform.modules.evidence.application.vault import (
    dispose_due_objects,
    rewrap_object_envelopes,
    rewrap_subject_keys,
)
from zid_platform.modules.evidence.infrastructure.tsa import TimestampAuthority


@dataclass(slots=True)
class WorkerReport:
    sequenced: int = 0
    checkpoints: list[str] = field(default_factory=list)
    anchored: list[str] = field(default_factory=list)
    disposed: list[str] = field(default_factory=list)
    retention: dict[str, int] = field(default_factory=dict)
    rewrapped: int = 0
    exports_erased: int = 0


async def worker_pass(
    conn: AsyncConnection,
    *,
    now: datetime | None = None,
    tsa: TimestampAuthority | None = None,
    force_seal: bool = False,
    rt: EvidenceRuntime | None = None,
) -> WorkerReport:
    rt, now = rt or current_runtime(), now or datetime.now(UTC)
    report = WorkerReport()
    report.sequenced = await sequence_pending(conn, rt=rt)
    sealed = await seal_due(
        conn, signer=rt.signer, cadence=rt.cadence, now=now, tsa=tsa, force=force_seal
    )
    report.checkpoints = [c.checkpoint_id for c in sealed]
    if tsa is not None:
        report.anchored = await anchor_pending(conn, tsa)
    report.disposed = await dispose_due_objects(conn, now)
    report.retention = await purge_expired(conn, rt.retention_days, now=now)
    report.exports_erased = await bulk.sweep_expired(conn, now)  # API-13: expiring exports
    report.rewrapped = await rewrap_subject_keys(conn, rt=rt)
    report.rewrapped += await rewrap_object_envelopes(conn, rt=rt)
    return report


__all__ = ["WorkerReport", "worker_pass"]
