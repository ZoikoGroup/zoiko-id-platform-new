"""Scheduled integrity verification of the evidence ledger (EVIDENCE-001 §25; DEC-F2).

A ledger is only defensible if tampering is *found*, not merely detectable by whoever happens to look. This pass, run by the
evidence worker, re-verifies each partition on a schedule: the hash chain, every checkpoint's Merkle root, link and signature,
the entry signatures, and each anchored checkpoint's external timestamp against the TSA trust list. A failure raises the
existing `evidence.integrity.failed` security incident (routed to the SECURITY role by the communication module) once per
partition until it verifies clean again, and is counted and logged.

Verification is O(entries) per partition, so a pass checks at most `batch` partitions and a partition is rechecked only after
`interval`. State is per worker process: a restart re-verifies everything once, which is the safe direction.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_telemetry.metrics import REGISTRY

from zid_platform.modules.evidence.application import ledger
from zid_platform.modules.evidence.application.runtime import EvidenceRuntime
from zid_platform.modules.evidence.infrastructure import ledger_store as store

log = logging.getLogger("zid.evidence.integrity")

VERIFICATIONS = REGISTRY.counter(
    "zid_evidence_integrity_verifications_total",
    "Scheduled evidence partition verifications by result",
    ("result",),
)
LAST_RUN = REGISTRY.gauge(
    "zid_evidence_integrity_last_run_timestamp_seconds",
    "When the scheduled evidence integrity check last ran (it runs even when nothing was due)",
    (),
)
OLDEST_UNSEALED = REGISTRY.gauge(
    "zid_evidence_oldest_unsealed_seconds",
    "Age of the oldest ledger entry that no checkpoint covers yet",
    (),
)
UNANCHORED = REGISTRY.gauge(
    "zid_evidence_unanchored_checkpoints",
    "Checkpoints sealed without an external timestamp (at most the first 1000 are counted)",
    (),
)
_NEVER = datetime.min.replace(tzinfo=UTC)


async def record_lag(conn: AsyncConnection, now: datetime) -> None:
    """Publish how far sealing and anchoring are behind: the numbers an alert needs to say "the ledger is not being sealed"."""
    async with store.platform_scope(conn):
        pending = await store.unsealed_partitions(conn)
        unanchored = await store.unanchored_checkpoints(conn, 1000)
    ages = [(now - r.oldest).total_seconds() for r in pending if r.oldest is not None]
    OLDEST_UNSEALED.set(max(ages, default=0.0))
    UNANCHORED.set(float(len(unanchored)))


@dataclass(slots=True)
class IntegrityState:
    """When each partition was last verified, and which are currently failing (so an incident is raised once, not every pass)."""

    last_verified: dict[str, datetime] = field(default_factory=dict)
    failing: set[str] = field(default_factory=set)


_STATE = IntegrityState()


@dataclass(slots=True)
class IntegrityRun:
    verified: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    errored: list[str] = field(default_factory=list)


async def verify_due(
    conn: AsyncConnection,
    *,
    rt: EvidenceRuntime,
    now: datetime,
    interval: timedelta,
    batch: int = 2,
    state: IntegrityState | None = None,
) -> IntegrityRun:
    """Verify up to `batch` partitions that have not been verified for `interval`, the longest-waiting first."""
    state = state or _STATE
    run = IntegrityRun()
    async with store.platform_scope(conn):
        partitions = await store.partitions_with_entries(conn)
    due = sorted(
        (p for p in partitions if now - state.last_verified.get(p, _NEVER) >= interval),
        key=lambda p: (state.last_verified.get(p, _NEVER), p),
    )
    for pid in due[: max(batch, 0)]:
        try:
            report = await ledger.verify_partition(
                conn,
                pid,
                keys=rt.trusted_keys(),
                compromised=rt.compromised,
                timestamps=rt.timestamp_verifier,
                emit_incident=pid not in state.failing,
            )
        except Exception:
            # a partition that cannot even be read is not "verified": say so, and keep checking the others
            log.exception("evidence integrity verification could not run", extra={"partition": pid})
            VERIFICATIONS.inc(result="error")
            run.errored.append(pid)
            continue
        state.last_verified[pid] = now
        if report.ok:
            state.failing.discard(pid)
            VERIFICATIONS.inc(result="ok")
            run.verified.append(pid)
        else:
            state.failing.add(pid)
            VERIFICATIONS.inc(result="failed")
            run.failed.append(pid)
            log.error(
                "EVIDENCE INTEGRITY FAILED",
                extra={
                    "partition": pid,
                    "first_broken_sequence": report.first_broken_sequence,
                    "kinds": sorted({p["kind"] for p in report.problems}),
                },
            )
    LAST_RUN.set(now.timestamp())
    return run


__all__ = ["IntegrityRun", "IntegrityState", "record_lag", "verify_due"]
