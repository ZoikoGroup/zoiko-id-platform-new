"""Evidence ledger service: append, verify, prove, export (EVIDENCE-001 §6, §10-11.1; DEC-F2).

- `append` canonicalizes (RFC 8785) and hashes the record, takes the partition lock, reads the
  database clock, and writes the next hash-chained entry. Re-appending the same record with the same
  digest returns the existing entry (a retried producer never creates a second entry, EVID-24); a
  different digest under the same record id is EVIDENCE_LEDGER_CONFLICT. An E3 append is refused
  (EVIDENCE_TIME_UNTRUSTED) when the database and service clocks disagree beyond tolerance.
- `verify_partition` recomputes the chain, every checkpoint's Merkle root, link and signature, and
  reports the first broken sequence. A failure emits `evidence.integrity.failed` (security incident).
- `inclusion_proof` / `export_bundle` produce what an offline verifier needs (`zid_evidence`).

Callers run these inside their tenant transaction (RLS): a tenant only ever sees its own partition.
The platform-security partition (tenant_id None) is reachable only through these functions, which
open the platform scope for their own statements.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto import Signer, sign_jws
from zid_evidence import (
    CanonicalizationError,
    CheckpointManifest,
    Digest,
    LedgerEntry,
    SignatureError,
    batch_root,
    build_bundle,
    entry_hash,
    entry_leaf,
    format_time,
    hash_json,
    merkle,
    verify_chain,
    verify_checkpoint_signature,
    verify_compact,
)
from zid_outbox import emit

from zid_platform.modules.evidence.domain.ledger import (
    PLATFORM_TENANT_ID,
    EvidenceClass,
    LedgerError,
    Partition,
    check_clock,
    check_record_id,
    check_record_type,
)
from zid_platform.modules.evidence.infrastructure import ledger_store as store

ENTRY_SIGNATURE_TYP = "zid-evidence-entry+jws"
RecordLoader = Callable[[AsyncConnection, str], Awaitable[Mapping[str, Any] | None]]
_LOADERS: dict[str, RecordLoader] = {}


def _utcnow() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class LedgerConfig:
    """Set once at start-up from Settings (`configure`); tests pass their own per call."""

    cell_id: str = "local-1"
    hash_alg: str = "sha-256"
    skew_tolerance: timedelta = timedelta(seconds=2)
    clock: Callable[[], datetime] = _utcnow


_config = LedgerConfig()


def configure(config: LedgerConfig) -> None:
    global _config
    _config = config


def current_config() -> LedgerConfig:
    return _config


def register_record_loader(record_type: str, loader: RecordLoader) -> None:
    """Let the module that owns a record type supply the record for export bundles (F-W2)."""
    check_record_type(record_type)
    _LOADERS[record_type] = loader


@dataclass(frozen=True, slots=True)
class LedgerRef:
    partition_id: str
    sequence_no: int
    record_type: str
    record_id: str
    record_hash: str
    entry_hash: str
    evidence_class: str
    committed_at: datetime


def _ref(row: Row[Any]) -> LedgerRef:
    return LedgerRef(
        row.partition_id,
        int(row.sequence_no),
        row.record_type,
        row.record_id,
        row.record_hash,
        row.entry_hash,
        row.evidence_class,
        row.committed_at,
    )


def _opt_time(value: datetime | None) -> str | None:
    return format_time(value) if value is not None else None


def entry_from_row(row: Row[Any]) -> tuple[LedgerEntry, Digest]:
    """Raises ValueError when a stored row cannot even be read as a ledger entry."""
    prev = row.previous_entry_hash
    entry = LedgerEntry(
        partition_id=row.partition_id,
        sequence_no=int(row.sequence_no),
        record_id=row.record_id,
        record_type=row.record_type,
        record_hash=Digest.parse(row.record_hash),
        previous_entry_hash=Digest.parse(prev) if prev is not None else None,
        committed_at=format_time(row.committed_at),
        evidence_class=row.evidence_class,
        effective_at=_opt_time(row.effective_at),
        observed_at=_opt_time(row.observed_at),
    )
    return entry, Digest.parse(row.entry_hash)


def manifest_from_row(row: Row[Any]) -> CheckpointManifest:
    prev = row.previous_checkpoint_hash
    return CheckpointManifest(
        checkpoint_id=str(row.checkpoint_id),
        partition_id=row.partition_id,
        first_sequence=int(row.first_sequence),
        last_sequence=int(row.last_sequence),
        merkle_root=Digest.parse(row.merkle_root),
        previous_checkpoint_hash=Digest.parse(prev) if prev is not None else None,
        created_at=format_time(row.created_at),
        signer_key_id=row.signer_key_id,
        alg=row.alg,
    )


@asynccontextmanager
async def partition_scope(conn: AsyncConnection, partition: Partition) -> AsyncIterator[None]:
    if partition.is_platform:
        async with store.platform_scope(conn):
            yield
    else:
        yield


async def emit_for_partition(
    conn: AsyncConnection,
    partition: Partition,
    *,
    name: str,
    aggregate_type: str,
    aggregate_id: str,
    aggregate_version: int,
    data: Mapping[str, Any],
) -> None:
    """Outbox events of the platform-security partition carry the nil (platform) tenant id."""
    if not partition.is_platform:
        await emit(
            conn,
            name=name,
            tenant_id=str(partition.tenant_id),
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            data=data,
        )
        return
    previous = (
        await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))
    ).scalar_one()
    await conn.execute(
        text("SELECT set_config('app.tenant_id', :t, true)"), {"t": PLATFORM_TENANT_ID}
    )
    try:
        await emit(
            conn,
            name=name,
            tenant_id=PLATFORM_TENANT_ID,
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            aggregate_version=aggregate_version,
            data=data,
        )
    finally:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": previous or ""}
        )


async def append(
    conn: AsyncConnection,
    *,
    tenant_id: str | None,
    record_type: str,
    record_id: str,
    record: Mapping[str, Any],
    evidence_class: EvidenceClass | str,
    effective_at: datetime | None = None,
    observed_at: datetime | None = None,
    cell_id: str | None = None,
    config: LedgerConfig | None = None,
    signer: Signer | None = None,
) -> LedgerRef:
    """Commit `record` to the ledger of tenant `tenant_id` (None: platform-security partition).

    `signer` (EVIDENCE_SIGNING): E3 entries also carry a per-record signature over the entry
    position and hash (EVIDENCE-001 §2.1 "per-record signature where required")."""
    cfg = config or _config
    try:
        cls = EvidenceClass(evidence_class)
    except ValueError as e:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown evidence class") from e
    check_record_type(record_type)
    check_record_id(record_id)
    try:
        record_hash = hash_json(record, alg=cfg.hash_alg)
    except CanonicalizationError as e:
        raise LedgerError("EVIDENCE_CANONICALIZATION_FAILED", str(e)) from e
    partition = Partition(tenant_id, cell_id or cfg.cell_id)
    async with partition_scope(conn, partition):
        await store.lock_partition(conn, partition.id)
        existing = await store.find_entry(conn, partition.id, record_type, record_id)
        if existing is not None:
            if Digest.parse(existing.record_hash).matches(record_hash):
                return _ref(existing)
            raise LedgerError(
                "EVIDENCE_LEDGER_CONFLICT", "record already committed with a different digest"
            )
        db_now = await store.db_clock(conn)
        if cls is EvidenceClass.E3:
            check_clock(db_now, cfg.clock(), cfg.skew_tolerance)
        last = await store.head(conn, partition.id)
        entry = LedgerEntry(
            partition_id=partition.id,
            sequence_no=1 if last is None else int(last.sequence_no) + 1,
            record_id=record_id,
            record_type=record_type,
            record_hash=record_hash,
            previous_entry_hash=None if last is None else Digest.parse(last.entry_hash),
            committed_at=format_time(db_now),
            evidence_class=cls.value,
            effective_at=_opt_time(effective_at),
            observed_at=_opt_time(observed_at),
        )
        digest = entry_hash(entry, alg=cfg.hash_alg)
        signature = None
        if signer is not None and cls is EvidenceClass.E3:
            signature = sign_jws(
                signer, entry_signature_payload(entry, digest), typ=ENTRY_SIGNATURE_TYP
            )
        await store.insert_entry(
            conn,
            {
                "partition_id": partition.id,
                "sequence_no": entry.sequence_no,
                "tenant_id": tenant_id,
                "record_type": record_type,
                "record_id": record_id,
                "record_hash": str(record_hash),
                "previous_entry_hash": (
                    str(entry.previous_entry_hash) if entry.previous_entry_hash else None
                ),
                "entry_hash": str(digest),
                "evidence_class": cls.value,
                "committed_at": db_now,
                "effective_at": effective_at,
                "observed_at": observed_at,
                "signer_key_id": signer.kid if signer is not None and signature else None,
                "entry_signature": signature,
            },
        )
    return LedgerRef(
        partition.id,
        entry.sequence_no,
        record_type,
        record_id,
        str(record_hash),
        str(digest),
        cls.value,
        db_now,
    )


def entry_signature_payload(entry: LedgerEntry, digest: Digest) -> dict[str, Any]:
    return {
        "partition_id": entry.partition_id,
        "sequence_no": entry.sequence_no,
        "record_type": entry.record_type,
        "record_id": entry.record_id,
        "record_hash": str(entry.record_hash),
        "entry_hash": str(digest),
    }


@dataclass(slots=True)
class PartitionReport:
    partition_id: str
    entries: int = 0
    checkpoints: int = 0
    sealed_through: int = 0
    problems: list[dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    @property
    def first_broken_sequence(self) -> int | None:
        seqs = [p["sequence_no"] for p in self.problems if p.get("sequence_no") is not None]
        return min(seqs) if seqs else None

    def problem(self, kind: str, sequence_no: int | None, detail: str, **extra: Any) -> None:
        self.problems.append({"kind": kind, "sequence_no": sequence_no, "detail": detail, **extra})

    def to_dict(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "ok": self.ok,
            "entries": self.entries,
            "checkpoints": self.checkpoints,
            "sealed_through": self.sealed_through,
            "unsealed": self.entries - self.sealed_through,
            "first_broken_sequence": self.first_broken_sequence,
            "problems": self.problems,
        }


async def verify_partition(
    conn: AsyncConnection,
    partition_id: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    emit_incident: bool = True,
) -> PartitionReport:
    """Recompute the hash chain and every checkpoint (root, link, signature) of a partition.

    `keys`: trusted public keys of the evidence signing purpose, historic ones included."""
    partition = Partition.parse(partition_id)
    report = PartitionReport(partition.id)
    async with partition_scope(conn, partition):
        rows = await store.entries(conn, partition.id, 1)
        cps = await store.checkpoints(conn, partition.id)
    report.entries, report.checkpoints = len(rows), len(cps)
    pairs: list[tuple[LedgerEntry, Digest]] = []
    stored: dict[int, Digest] = {}
    for row in rows:
        try:
            pair = entry_from_row(row)
        except ValueError as e:
            report.problem("entry_unreadable", int(row.sequence_no), str(e))
            break
        pairs.append(pair)
        stored[pair[0].sequence_no] = pair[1]
    brk = verify_chain(pairs)
    if brk is not None:
        report.problem("chain", brk.sequence_no, brk.reason)
    for row, (entry, digest) in zip(rows, pairs, strict=False):
        if row.entry_signature is None:
            continue
        try:
            header, payload = verify_compact(
                row.entry_signature, keys=keys, typ=ENTRY_SIGNATURE_TYP
            )
            if header.get("kid") != row.signer_key_id or payload != entry_signature_payload(
                entry, digest
            ):
                raise SignatureError("entry signature does not cover this entry")
        except SignatureError as e:
            report.problem("entry_signature", entry.sequence_no, str(e))
    expected_first, previous = 1, None
    for row in cps:
        manifest = manifest_from_row(row)
        first, last = manifest.first_sequence, manifest.last_sequence
        if first != expected_first:
            report.problem(
                "checkpoint_gap", min(first, expected_first), "checkpoints not contiguous"
            )
        link = (
            previous.hash(alg=manifest.previous_checkpoint_hash.alg)
            if (previous is not None and manifest.previous_checkpoint_hash is not None)
            else None
        )
        if (previous is None) != (manifest.previous_checkpoint_hash is None) or (
            link is not None
            and manifest.previous_checkpoint_hash is not None
            and not link.matches(manifest.previous_checkpoint_hash)
        ):
            report.problem("checkpoint_link", first, "previous_checkpoint_hash does not link")
        own = Digest.parse(row.checkpoint_hash)
        if not manifest.hash(alg=own.alg).matches(own):
            report.problem("checkpoint_hash", first, "checkpoint_hash does not match manifest")
        leaves = [stored.get(s) for s in range(first, last + 1)]
        if any(h is None for h in leaves):
            report.problem("checkpoint_range", first, "checkpoint covers missing entries")
        else:
            root = batch_root([h for h in leaves if h is not None], alg=manifest.merkle_root.alg)
            if not root.matches(manifest.merkle_root):
                report.problem("merkle_root", first, "entries do not match the signed root")
        try:
            verify_checkpoint_signature(manifest, row.checkpoint_signature, keys=keys)
        except SignatureError as e:
            report.problem(
                "checkpoint_signature", first, str(e), checkpoint_id=str(row.checkpoint_id)
            )
        expected_first, previous = last + 1, manifest
        report.sealed_through = last
    if not report.ok and emit_incident:
        await report_integrity_failure(conn, partition, report)
    return report


async def report_integrity_failure(
    conn: AsyncConnection, partition: Partition, report: PartitionReport
) -> None:
    """Security incident: a hash, signature or ledger inconsistency (EVIDENCE-001 §25)."""
    await emit_for_partition(
        conn,
        partition,
        name="evidence.integrity.failed",
        aggregate_type="evidence_incident",
        aggregate_id=str(uuid7()),
        aggregate_version=1,
        data={
            "partition_id": partition.id,
            "first_broken_sequence": report.first_broken_sequence,
            "kinds": sorted({p["kind"] for p in report.problems}),
        },
    )


@dataclass(frozen=True, slots=True)
class Proof:
    entry: LedgerEntry
    entry_digest: Digest
    manifest: CheckpointManifest
    signature: str
    signer_public_jwk: dict[str, Any]
    external_timestamp_ref: str | None
    path: list[bytes]
    entry_signature: str | None = None
    entry_signer_key_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        alg = self.manifest.merkle_root.alg
        return {
            "ledger_entry": {**self.entry.to_dict(), "entry_hash": str(self.entry_digest)},
            "checkpoint": {
                **self.manifest.to_dict(),
                "signature": self.signature,
                "external_timestamp_ref": self.external_timestamp_ref,
            },
            "inclusion_proof": {
                "leaf_index": self.entry.sequence_no - self.manifest.first_sequence,
                "tree_size": self.manifest.tree_size,
                "hashes": [str(Digest(alg, h)) for h in self.path],
            },
        }


async def _locate(conn: AsyncConnection, record_id: str, record_type: str | None) -> Row[Any]:
    rows = await store.find_record(conn, record_id, record_type)
    if not rows:
        raise LedgerError("NOT_FOUND", "no ledger entry for this record")
    if len(rows) > 1:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "record id is ambiguous; give record_type")
    return rows[0]


async def inclusion_proof(
    conn: AsyncConnection,
    record_id: str,
    *,
    record_type: str | None = None,
    platform: bool = False,
) -> Proof:
    """The checkpoint that seals the record and the Merkle path to its root.

    EVIDENCE_CHECKPOINT_MISSING while the entry is not yet sealed (never reported as sealed)."""
    if platform:
        async with store.platform_scope(conn):
            return await inclusion_proof(conn, record_id, record_type=record_type)
    row = await _locate(conn, record_id, record_type)
    cp = await store.checkpoint_covering(conn, row.partition_id, int(row.sequence_no))
    if cp is None:
        raise LedgerError("EVIDENCE_CHECKPOINT_MISSING", "entry not yet sealed by a checkpoint")
    manifest = manifest_from_row(cp)
    batch = await store.entries(
        conn, row.partition_id, manifest.first_sequence, manifest.last_sequence
    )
    digests = [Digest.parse(r.entry_hash) for r in batch]
    if len(digests) != manifest.tree_size:
        raise LedgerError("EVIDENCE_LEDGER_CONFLICT", "checkpoint covers missing entries")
    entry, digest = entry_from_row(row)
    path = merkle.inclusion_proof(
        entry.sequence_no - manifest.first_sequence,
        [entry_leaf(d) for d in digests],
        alg=manifest.merkle_root.alg,
    )
    return Proof(
        entry,
        digest,
        manifest,
        cp.checkpoint_signature,
        dict(cp.signer_public_jwk),
        cp.external_timestamp_ref,
        path,
        row.entry_signature,
        row.signer_key_id,
    )


async def export_bundle(
    conn: AsyncConnection,
    record_id: str,
    *,
    keys: Sequence[Mapping[str, Any]],
    record_type: str | None = None,
    record: Mapping[str, Any] | None = None,
    platform: bool = False,
    withhold_record: bool = False,
) -> dict[str, Any]:
    """Self-contained bundle for offline verification (`python -m zid_evidence verify`).

    The record comes from the caller or from the loader its owning module registered; without
    either - or with `withhold_record` (a redacted disclosure) - the bundle is structural only
    (record digest committed, record withheld)."""
    proof = await inclusion_proof(conn, record_id, record_type=record_type, platform=platform)
    if withhold_record:
        record = None
    elif record is None and proof.entry.record_type in _LOADERS:
        record = await _LOADERS[proof.entry.record_type](conn, record_id)
    kid = proof.manifest.signer_key_id
    published = [dict(k) for k in keys if k.get("kid") == kid]
    return build_bundle(
        record_type=proof.entry.record_type,
        record_id=record_id,
        record=record,
        entry=proof.entry,
        entry_digest=proof.entry_digest,
        manifest=proof.manifest,
        checkpoint_signature=proof.signature,
        proof=proof.path,
        keys=published or [proof.signer_public_jwk],
        external_timestamp_ref=proof.external_timestamp_ref,
        entry_signature=proof.entry_signature,
        signer_key_id=proof.entry_signer_key_id,
    )
