"""Evidence checkpointer: signed Merkle checkpoints and RFC 3161 anchoring (DEC-F2 §2-3; EVID-W3).

Runs in the platform image's `worker` process (DEC-D6) as the checkpointer role, which reads every
partition (BYPASSRLS); each partition is then sealed under its own tenant / platform scope.

- `seal_partition` takes the partition lock (no append races the batch), commits the entries after
  the last checkpoint into one Merkle root, links to the previous checkpoint and signs the manifest
  with the EVIDENCE_SIGNING key (never a token key).
- A TSA outage leaves a checkpoint unanchored, to be anchored on a later pass (`anchor_pending`),
  unless the profile requires a synchronous timestamp: then nothing is sealed and the entries stay
  visibly unsealed (EVID-25: never reported as sealed).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto import Signer, sign_jws
from zid_evidence import CHECKPOINT_TYP, CheckpointManifest, Digest, batch_root, format_time

from zid_platform.modules.evidence.application.ledger import (
    LedgerConfig,
    current_config,
    emit_for_partition,
    manifest_from_row,
    partition_scope,
)
from zid_platform.modules.evidence.domain.ledger import Cadence, LedgerError, Partition
from zid_platform.modules.evidence.infrastructure import ledger_store as store
from zid_platform.modules.evidence.infrastructure.tsa import TimestampAuthority, TimestampError


@dataclass(frozen=True, slots=True)
class SealedCheckpoint:
    checkpoint_id: str
    partition_id: str
    first_sequence: int
    last_sequence: int
    merkle_root: str
    checkpoint_hash: str
    external_timestamp_ref: str | None


@asynccontextmanager
async def tenant_scope(conn: AsyncConnection, partition: Partition) -> AsyncIterator[None]:
    """Tenant RLS context for a tenant partition (worker passes start without one)."""
    if partition.is_platform:
        yield
        return
    previous = (
        await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))
    ).scalar_one()
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": partition.tenant_id}
    )
    try:
        yield
    finally:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": previous or ""}
        )


async def seal_partition(
    conn: AsyncConnection,
    partition_id: str,
    *,
    signer: Signer,
    max_batch: int = 10_000,
    tsa: TimestampAuthority | None = None,
    require_timestamp: bool = False,
    config: LedgerConfig | None = None,
) -> SealedCheckpoint | None:
    """Seal the entries after the partition's last checkpoint; None when there is nothing new."""
    cfg = config or current_config()
    partition = Partition.parse(partition_id)
    async with partition_scope(conn, partition), tenant_scope(conn, partition):
        await store.lock_partition(conn, partition.id)
        last_cp = await store.last_checkpoint(conn, partition.id)
        first = 1 if last_cp is None else int(last_cp.last_sequence) + 1
        rows = await store.entries(conn, partition.id, first, first + max_batch - 1)
        if not rows:
            return None
        last = first + len(rows) - 1
        if int(rows[0].sequence_no) != first or int(rows[-1].sequence_no) != last:
            raise LedgerError("EVIDENCE_LEDGER_CONFLICT", "ledger sequence has a gap")
        previous = manifest_from_row(last_cp) if last_cp is not None else None
        created = await store.db_clock(conn)
        manifest = CheckpointManifest(
            checkpoint_id=str(uuid7()),
            partition_id=partition.id,
            first_sequence=first,
            last_sequence=last,
            merkle_root=batch_root([Digest.parse(r.entry_hash) for r in rows], alg=cfg.hash_alg),
            previous_checkpoint_hash=previous.hash(alg=cfg.hash_alg) if previous else None,
            created_at=format_time(created),
            signer_key_id=signer.kid,
            alg=signer.alg,
        )
        # KMS signers block on the network: keep them off the event loop
        signature = await asyncio.to_thread(
            sign_jws, signer, manifest.to_dict(), typ=CHECKPOINT_TYP
        )
        public_jwk = await asyncio.to_thread(signer.public_jwk)
        checkpoint_hash = manifest.hash(alg=cfg.hash_alg)
        timestamp_ref: str | None = None
        if tsa is not None:
            try:
                timestamp_ref = await tsa.timestamp(checkpoint_hash)
            except TimestampError as e:
                if require_timestamp:
                    raise LedgerError("EVIDENCE_TIME_UNTRUSTED", f"TSA required: {e}") from e
        await store.insert_checkpoint(
            conn,
            {
                "checkpoint_id": manifest.checkpoint_id,
                "partition_id": partition.id,
                "tenant_id": partition.tenant_id,
                "first_sequence": first,
                "last_sequence": last,
                "merkle_root": str(manifest.merkle_root),
                "previous_checkpoint_hash": (
                    str(manifest.previous_checkpoint_hash)
                    if manifest.previous_checkpoint_hash
                    else None
                ),
                "checkpoint_hash": str(checkpoint_hash),
                "created_at": created,
                "signer_key_id": signer.kid,
                "alg": signer.alg,
                "checkpoint_signature": signature,
                "signer_public_jwk": public_jwk,
                "external_timestamp_ref": timestamp_ref,
            },
        )
        await emit_for_partition(
            conn,
            partition,
            name="evidence.checkpoint.created",
            aggregate_type="evidence_partition",
            aggregate_id=partition.id,
            aggregate_version=last,
            data={
                "checkpoint_id": manifest.checkpoint_id,
                "partition_id": partition.id,
                "first_sequence": first,
                "last_sequence": last,
                "merkle_root": str(manifest.merkle_root),
                "signer_key_id": signer.kid,
                "anchored": timestamp_ref is not None,
            },
        )
    return SealedCheckpoint(
        manifest.checkpoint_id,
        partition.id,
        first,
        last,
        str(manifest.merkle_root),
        str(checkpoint_hash),
        timestamp_ref,
    )


async def seal_due(
    conn: AsyncConnection,
    *,
    signer: Signer,
    cadence: Cadence,
    now: datetime | None = None,
    tsa: TimestampAuthority | None = None,
    force: bool = False,
    require_timestamp: bool = False,
) -> list[SealedCheckpoint]:
    """One checkpointer pass: seal every partition whose cadence is due (E3 sooner)."""
    now = now or datetime.now(UTC)
    async with store.platform_scope(conn):
        pending = await store.unsealed_partitions(conn)
    sealed = []
    for row in pending:
        if not force and not cadence.due(
            oldest_unsealed=row.oldest, has_e3=bool(row.has_e3), now=now
        ):
            continue
        cp = await seal_partition(
            conn,
            row.partition_id,
            signer=signer,
            max_batch=cadence.max_batch,
            tsa=tsa,
            require_timestamp=require_timestamp,
        )
        if cp is not None:
            sealed.append(cp)
    return sealed


async def anchor_pending(
    conn: AsyncConnection, tsa: TimestampAuthority, *, limit: int = 100
) -> list[str]:
    """Timestamp checkpoints sealed while the TSA was unavailable, oldest first."""
    async with store.platform_scope(conn):
        rows = await store.unanchored_checkpoints(conn, limit)
    anchored = []
    for row in rows:
        partition = Partition.parse(row.partition_id)
        try:
            ref = await tsa.timestamp(Digest.parse(row.checkpoint_hash))
        except TimestampError:
            break  # still down: retry on the next pass, in order
        async with partition_scope(conn, partition), tenant_scope(conn, partition):
            if await store.set_timestamp_ref(conn, str(row.checkpoint_id), ref):
                anchored.append(str(row.checkpoint_id))
                await emit_for_partition(
                    conn,
                    partition,
                    name="evidence.checkpoint.anchored",
                    aggregate_type="evidence_checkpoint",
                    aggregate_id=str(row.checkpoint_id),
                    aggregate_version=1,
                    data={"checkpoint_id": str(row.checkpoint_id), "partition_id": partition.id},
                )
    return anchored


__all__ = ["SealedCheckpoint", "anchor_pending", "seal_due", "seal_partition", "tenant_scope"]
