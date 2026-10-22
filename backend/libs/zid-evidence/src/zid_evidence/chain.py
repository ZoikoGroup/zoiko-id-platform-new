"""Hash-chained ledger entries (EVIDENCE-001 §10; DEC-F2 §2).

    entry_hash = H(JCS({partition_id, sequence_no, record_id, record_type, record_hash,
                        previous_entry_hash, committed_at, effective_at, observed_at,
                        evidence_class}))

Each entry commits to its predecessor, its position and the trusted commit time, so deleting,
modifying or reordering any entry breaks every later hash (EVID-06). Times are RFC 3339 UTC with
microseconds (`2026-10-21T09:15:02.123456Z`), the precision PostgreSQL stores.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from zid_evidence.digest import Digest, hash_json

__all__ = [
    "EVIDENCE_CLASSES",
    "ChainBreak",
    "LedgerEntry",
    "entry_hash",
    "format_time",
    "parse_time",
    "verify_chain",
]

EVIDENCE_CLASSES = ("E0", "E1", "E2", "E3")


def format_time(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("evidence times must be timezone-aware")
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def parse_time(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=UTC)


@dataclass(frozen=True, slots=True)
class LedgerEntry:
    partition_id: str
    sequence_no: int
    record_id: str
    record_type: str
    record_hash: Digest
    previous_entry_hash: Digest | None
    committed_at: str  # format_time() of the evidence service's trusted commit time
    evidence_class: str
    effective_at: str | None = None
    observed_at: str | None = None

    def __post_init__(self) -> None:
        if self.evidence_class not in EVIDENCE_CLASSES:
            raise ValueError(f"unknown evidence class {self.evidence_class!r}")
        if self.sequence_no < 1:
            raise ValueError("sequence numbers start at 1")
        if (self.sequence_no == 1) != (self.previous_entry_hash is None):
            raise ValueError("only the first entry of a partition has no predecessor")

    def hash_input(self) -> dict[str, Any]:
        return {
            "partition_id": self.partition_id,
            "sequence_no": self.sequence_no,
            "record_id": self.record_id,
            "record_type": self.record_type,
            "record_hash": str(self.record_hash),
            "previous_entry_hash": (
                str(self.previous_entry_hash) if self.previous_entry_hash else None
            ),
            "committed_at": self.committed_at,
            "effective_at": self.effective_at,
            "observed_at": self.observed_at,
            "evidence_class": self.evidence_class,
        }

    def to_dict(self) -> dict[str, Any]:
        return self.hash_input()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LedgerEntry:
        prev = data.get("previous_entry_hash")
        return cls(
            partition_id=str(data["partition_id"]),
            sequence_no=int(data["sequence_no"]),
            record_id=str(data["record_id"]),
            record_type=str(data["record_type"]),
            record_hash=Digest.parse(data["record_hash"]),
            previous_entry_hash=Digest.parse(prev) if prev is not None else None,
            committed_at=str(data["committed_at"]),
            evidence_class=str(data["evidence_class"]),
            effective_at=data.get("effective_at"),
            observed_at=data.get("observed_at"),
        )


def entry_hash(entry: LedgerEntry, *, alg: str) -> Digest:
    return hash_json(entry.hash_input(), alg=alg)


@dataclass(frozen=True, slots=True)
class ChainBreak:
    sequence_no: int
    reason: str


def verify_chain(
    entries: Iterable[tuple[LedgerEntry, Digest]],
    *,
    start_previous: Digest | None = None,
    start_sequence: int = 1,
) -> ChainBreak | None:
    """Walk (entry, stored entry_hash) pairs in sequence order; return the first break, if any.

    `start_previous` / `start_sequence` let a verifier begin mid-partition from a trusted point."""
    expected_seq, previous = start_sequence, start_previous
    for entry, stored in entries:
        if entry.sequence_no != expected_seq:
            return ChainBreak(expected_seq, "sequence gap or reorder")
        if (entry.previous_entry_hash is None) != (previous is None) or (
            previous is not None
            and entry.previous_entry_hash is not None
            and not entry.previous_entry_hash.matches(previous)
        ):
            return ChainBreak(entry.sequence_no, "previous_entry_hash does not link")
        recomputed = entry_hash(entry, alg=stored.alg)
        if not recomputed.matches(stored):
            return ChainBreak(entry.sequence_no, "entry_hash mismatch")
        previous, expected_seq = stored, expected_seq + 1
    return None
