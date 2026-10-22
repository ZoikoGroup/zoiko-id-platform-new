"""Evidence ledger rules: partitions, evidence classes, checkpoint cadence, clock integrity
(EVIDENCE-001 §6, §10-11; DEC-F2 §2, §4).

Pure functions and value objects; no I/O.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

PLATFORM_SCOPE = "platform-security"  # value of app.evidence_scope for the platform partition
# The platform-security partition has no tenant; its outbox events carry the nil tenant id.
PLATFORM_TENANT_ID = "00000000-0000-0000-0000-000000000000"
_CELL = re.compile(r"^[a-z0-9-]{1,40}$")
_RECORD_TYPE = re.compile(r"^[a-z][a-z0-9_.]{0,63}$")


class EvidenceClass(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"


class LedgerError(Exception):
    """A ledger rule refused the operation; `code` is a canonical EVIDENCE-001 §24 error code."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Partition:
    """`tenant:<tenant_id>:<cell>` (tenant evidence) or `platform-security:<cell>`."""

    tenant_id: str | None
    cell: str

    def __post_init__(self) -> None:
        if not _CELL.match(self.cell):
            raise LedgerError("EVIDENCE_SCHEMA_INVALID", "cell id must match [a-z0-9-]{1,40}")
        if self.tenant_id is not None:
            try:
                canonical = str(uuid.UUID(self.tenant_id))
            except ValueError as e:
                raise LedgerError("EVIDENCE_SCHEMA_INVALID", "tenant id must be a UUID") from e
            if canonical != self.tenant_id:
                raise LedgerError("EVIDENCE_SCHEMA_INVALID", "tenant id must be canonical")

    @property
    def id(self) -> str:
        if self.tenant_id is None:
            return f"{PLATFORM_SCOPE}:{self.cell}"
        return f"tenant:{self.tenant_id}:{self.cell}"

    @property
    def is_platform(self) -> bool:
        return self.tenant_id is None

    @classmethod
    def parse(cls, partition_id: str) -> Partition:
        parts = partition_id.split(":")
        if len(parts) == 2 and parts[0] == PLATFORM_SCOPE:
            return cls(None, parts[1])
        if len(parts) == 3 and parts[0] == "tenant":
            return cls(parts[1], parts[2])
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "unknown partition id form")


def check_record_type(record_type: str) -> None:
    if not _RECORD_TYPE.match(record_type):
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "record_type must match [a-z][a-z0-9_.]{0,63}")


def check_record_id(record_id: str) -> None:
    if not 1 <= len(record_id) <= 200:
        raise LedgerError("EVIDENCE_SCHEMA_INVALID", "record_id must be 1-200 characters")


def check_clock(db_now: datetime, app_now: datetime, tolerance: timedelta) -> None:
    """DEC-F2 §4: beyond tolerance, new E3 sealing is blocked (fail closed)."""
    if abs(db_now - app_now) > tolerance:
        raise LedgerError(
            "EVIDENCE_TIME_UNTRUSTED",
            f"database and service clocks differ by more than {tolerance.total_seconds()}s",
        )


@dataclass(frozen=True, slots=True)
class Cadence:
    """Checkpoint cadence (DEC-F2 §2): a partition holding unsealed E3 entries is sealed sooner
    than one holding only E0-E2. `max_batch` bounds each checkpoint's Merkle batch."""

    interval: timedelta = timedelta(minutes=5)
    e3_interval: timedelta = timedelta(seconds=30)
    max_batch: int = 10_000

    def __post_init__(self) -> None:
        if self.e3_interval > self.interval:
            raise ValueError("E3 checkpoints must be at least as frequent as the default cadence")
        if self.max_batch < 1:
            raise ValueError("max_batch must be positive")

    def due(self, *, oldest_unsealed: datetime, has_e3: bool, now: datetime) -> bool:
        return now - oldest_unsealed >= (self.e3_interval if has_e3 else self.interval)
