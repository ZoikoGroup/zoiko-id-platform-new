"""The global-minimum tenant directory record (ZID-TENANCY-001 §2, §9, §15; DEC-G3; TEN-09, TEN-23,
RES-07; G-W6).

Pure rules, no I/O.
- Only the §2 fields exist; any other key in a published record is GLOBAL_DATA_PROHIBITED (the global
  plane never receives names, emails, identities or raw domains). Aliases are short routing names.
- A record is published by the tenant's *home* cell only, as a JWS signed with that cell's pinned key.
- Epoch fencing: a record with a lower placement_epoch, or the same epoch naming another home cell,
  is MIGRATION_EPOCH_CONFLICT; record_revision must increase. A stale cell can never move a tenant.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

FIELDS = frozenset(
    {
        "tenant_id",
        "tenant_status",
        "home_cell_id",
        "recovery_cell_id",
        "placement_epoch",
        "residency_profile_id",
        "tenant_key_profile_ref",
        "routing_aliases",
        "service_entitlements",
        "policy_release_refs",
        "record_revision",
    }
)
STATUSES = frozenset({"PROVISIONING", "ACTIVE", "SUSPENDED", "MIGRATING", "DELETING", "DELETED"})
CELL = re.compile(r"^[a-z0-9-]{1,40}$")
ALIAS = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
REF = re.compile(r"^[A-Za-z0-9._:-]{1,80}$")
RECORD_TYP = "zid-directory+jwt"
DISCOVERY_TYP = "zid-discovery+jwt"

# Tenant lifecycle (OVERLAY §2) -> directory status (TENANCY §2)
LIFECYCLE_STATUS = {
    "DRAFT": "PROVISIONING",
    "ORGANIZATION_VERIFYING": "PROVISIONING",
    "CONFIGURING": "PROVISIONING",
    "BLOCKED": "PROVISIONING",
    "INTEGRATING": "PROVISIONING",
    "SHADOW": "ACTIVE",
    "PILOT": "ACTIVE",
    "ACTIVE": "ACTIVE",
    "SUSPENDED": "SUSPENDED",
    "OFFBOARDING": "DELETING",
    "CLOSED": "DELETED",
}


class LocatorError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class DirectoryRecord:
    tenant_id: str
    tenant_status: str
    home_cell_id: str
    recovery_cell_id: str | None
    placement_epoch: int
    residency_profile_id: str | None
    tenant_key_profile_ref: str | None
    routing_aliases: tuple[str, ...]
    service_entitlements: tuple[str, ...]
    policy_release_refs: tuple[str, ...]
    record_revision: int

    def view(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "tenant_status": self.tenant_status,
            "home_cell_id": self.home_cell_id,
            "recovery_cell_id": self.recovery_cell_id,
            "placement_epoch": self.placement_epoch,
            "residency_profile_id": self.residency_profile_id,
            "tenant_key_profile_ref": self.tenant_key_profile_ref,
            "routing_aliases": list(self.routing_aliases),
            "service_entitlements": list(self.service_entitlements),
            "policy_release_refs": list(self.policy_release_refs),
            "record_revision": self.record_revision,
        }


def _strs(v: Any, pattern: re.Pattern[str], name: str, limit: int = 20) -> tuple[str, ...]:
    if v is None:
        return ()
    if (
        not isinstance(v, list)
        or len(v) > limit
        or not all(isinstance(x, str) and pattern.fullmatch(x) for x in v)
    ):
        raise LocatorError("GLOBAL_DATA_PROHIBITED", f"{name} must be short opaque identifiers")
    return tuple(sorted(set(v)))


def parse_record(raw: Mapping[str, Any]) -> DirectoryRecord:
    extra = set(raw) - FIELDS
    if extra:
        raise LocatorError(
            "GLOBAL_DATA_PROHIBITED", f"fields not allowed in the global plane: {sorted(extra)}"
        )
    try:
        tenant = str(uuid.UUID(str(raw.get("tenant_id"))))
    except ValueError as e:
        raise LocatorError("VALIDATION_FAILED", "tenant_id") from e
    status = raw.get("tenant_status")
    home = raw.get("home_cell_id")
    epoch, revision = raw.get("placement_epoch"), raw.get("record_revision")
    if status not in STATUSES:
        raise LocatorError("VALIDATION_FAILED", "tenant_status")
    if not isinstance(home, str) or not CELL.fullmatch(home):
        raise LocatorError("VALIDATION_FAILED", "home_cell_id")
    recovery = raw.get("recovery_cell_id")
    if recovery is not None and (not isinstance(recovery, str) or not CELL.fullmatch(recovery)):
        raise LocatorError("VALIDATION_FAILED", "recovery_cell_id")
    if not isinstance(epoch, int) or epoch < 1 or not isinstance(revision, int) or revision < 1:
        raise LocatorError("VALIDATION_FAILED", "placement_epoch / record_revision")
    profile = raw.get("residency_profile_id")
    if profile not in (None, "SRP-0", "SRP-1", "SRP-2", "SRP-3"):
        raise LocatorError("VALIDATION_FAILED", "residency_profile_id")
    key_ref = raw.get("tenant_key_profile_ref")
    if key_ref is not None and (
        not isinstance(key_ref, str) or not re.fullmatch(r"^[A-Z_]{1,40}$", key_ref)
    ):
        raise LocatorError(
            "GLOBAL_DATA_PROHIBITED", "tenant_key_profile_ref is an opaque profile name"
        )
    return DirectoryRecord(
        tenant,
        str(status),
        home,
        recovery,
        epoch,
        profile,
        key_ref,
        _strs(raw.get("routing_aliases"), ALIAS, "routing_aliases"),
        _strs(raw.get("service_entitlements"), REF, "service_entitlements", 50),
        _strs(raw.get("policy_release_refs"), REF, "policy_release_refs"),
        revision,
    )


def check_supersedes(
    new: DirectoryRecord, current: DirectoryRecord | None, *, publisher: str
) -> None:
    """The publisher must be the record's home cell, and nothing ever goes back."""
    if new.home_cell_id != publisher:
        raise LocatorError(
            "CELL_NOT_AUTHORITATIVE", f"{publisher} may only publish tenants it is the home cell of"
        )
    if current is None:
        return
    if new.placement_epoch < current.placement_epoch or (
        new.placement_epoch == current.placement_epoch and new.home_cell_id != current.home_cell_id
    ):
        raise LocatorError(
            "MIGRATION_EPOCH_CONFLICT",
            f"epoch {new.placement_epoch} at {new.home_cell_id} does not supersede "
            f"epoch {current.placement_epoch} at {current.home_cell_id}",
        )
    if new.record_revision < current.record_revision:
        raise LocatorError("PLACEMENT_EPOCH_STALE", "an older revision of this record")


__all__ = [
    "DISCOVERY_TYP",
    "LIFECYCLE_STATUS",
    "RECORD_TYP",
    "DirectoryRecord",
    "LocatorError",
    "check_supersedes",
    "parse_record",
]
