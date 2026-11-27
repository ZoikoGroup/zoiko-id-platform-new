"""Residency and sovereignty rules (ZID-TENANCY-001 §4-6, §11, §13, §17, §19; DEC-G1). Pure.

Configuration: `backend/config/residency-profiles` (SRP-0..SRP-3, data classes, cross-border
default) and `backend/config/cells/cells.yaml` (cell -> provider region, jurisdiction, supported
profiles, recovery). Cell ids are deployment identities, never inferred from cloud region names.

Rules, stricter always wins:
- placement: the home cell must support the tenant's profile (RESIDENCY_PROFILE_UNSUPPORTED);
- storage:   DC0 anywhere; DC1 in the home legal zone unless SRP-0; DC2 in the home legal zone
             (SRP-0: anywhere); DC3 and KEY_MATERIAL in the home cell's jurisdiction only;
- transfer:  leaving the home cell's legal zone with DC1-DC3 (or the jurisdiction with DC3, or any
             cell at all for SRP-3) needs an ACTIVE approved transfer rule for exactly that data
             class, destination and purpose, under a mechanism the tenant's jurisdiction pack
             supports. Unknown destinations are DEPENDENCY_RESIDENCY_UNKNOWN (fail closed);
- recovery:  only permitted recovery cells (BACKUP_REGION_DENIED);
- operators: SRP-2/3 restrict operator jurisdiction (SOVEREIGN_OPERATOR_DENIED).
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

PROFILES = ("SRP-0", "SRP-1", "SRP-2", "SRP-3")
DATA_CLASSES = ("DC0", "DC1", "DC2", "DC3", "KEY_MATERIAL")
# privacy data categories (PRIV-001 §2) -> placement data classes (TENANCY-001 §5)
CATEGORY_CLASS = {"D0": "DC0", "D1": "DC2", "D2": "DC2", "D3": "DC3", "D4": "DC3"}


class ResidencyError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Cell:
    cell_id: str
    region: str | None
    country: str | None
    legal_zone: str | None
    supported_profiles: tuple[str, ...]
    approved_recovery_cells: tuple[str, ...]
    recovery_only: bool
    display_name: str | None = (
        None  # a plain place name for people ("United Kingdom (London)"); never the cell id
    )


@dataclass(frozen=True, slots=True)
class Placement:
    """One subsystem's declared location for a cell (the deployment inventory, not an assumption)."""

    region: str | None
    country: str | None
    dedicated: bool = False


@dataclass(frozen=True, slots=True)
class Catalog:
    """The cell catalog and residency profiles this release runs with (loaded by infrastructure)."""

    cells: Mapping[str, Cell]
    profiles: Mapping[str, Any]
    coverage_subsystems: tuple[str, ...] = ()
    extra: Mapping[str, Any] = field(default_factory=dict)
    # cell id -> subsystem -> where that subsystem actually keeps data, as declared in the deployment inventory
    placements: Mapping[str, Mapping[str, Placement]] = field(default_factory=dict)

    def cell(self, cell_id: str) -> Cell:
        try:
            return self.cells[cell_id]
        except KeyError as e:
            raise ResidencyError("DEPENDENCY_RESIDENCY_UNKNOWN", f"unknown cell {cell_id}") from e


@dataclass(frozen=True, slots=True)
class Residency:
    tenant_id: str
    profile: str
    home_cell_id: str
    permitted_recovery_cells: tuple[str, ...]
    prohibited_cells: tuple[str, ...]
    operator_jurisdictions: tuple[str, ...]
    key_profile: str
    jurisdiction_pack_id: str
    placement_epoch: int
    revision: int

    @property
    def level(self) -> int:
        return PROFILES.index(self.profile)

    @property
    def isolation_mode(self) -> str:
        """EDR-002 §4: dedicated tenant infrastructure (SRP-3) or shared multi-tenant cells; derived, so it is
        versioned with the profile."""
        return "DEDICATED" if self.profile == "SRP-3" else "SHARED"


@dataclass(frozen=True, slots=True)
class TransferRule:
    rule_id: str
    data_class: str
    destination_cell_id: str
    purpose_code: str
    mechanism: str
    jurisdiction_pack_id: str
    field_allowlist: tuple[str, ...]
    valid_from: datetime
    valid_to: datetime | None
    status: str

    def active(self, now: datetime) -> bool:
        return (
            self.status == "ACTIVE"
            and self.valid_from <= now
            and (self.valid_to is None or now < self.valid_to)
        )


def check_profile(cat: Catalog, profile: str) -> str:
    if profile not in PROFILES:
        raise ResidencyError("RESIDENCY_PROFILE_UNSUPPORTED", f"unknown profile {profile}")
    if profile not in cat.profiles:
        raise ResidencyError("RESIDENCY_PROFILE_UNSUPPORTED", f"{profile} is not configured")
    return profile


def check_placement(cat: Catalog, profile: str, home_cell_id: str, recovery: Sequence[str]) -> None:
    """TENANCY §13: the home cell supports the profile; recovery cells are real, approved for the
    home cell, and (SRP-1+) inside the same legal zone; SRP-3 never shares a recovery boundary."""
    check_profile(cat, profile)
    home = cat.cell(home_cell_id)
    if home.recovery_only:
        raise ResidencyError("CELL_NOT_AUTHORITATIVE", f"{home_cell_id} is recovery-only")
    if profile not in home.supported_profiles:
        raise ResidencyError(
            "RESIDENCY_PROFILE_UNSUPPORTED", f"{home_cell_id} does not support {profile}"
        )
    for rid in recovery:
        rc = cat.cell(rid)
        if rid not in home.approved_recovery_cells:
            raise ResidencyError("BACKUP_REGION_DENIED", f"{rid} is not an approved recovery cell")
        if PROFILES.index(profile) >= 1 and rc.legal_zone != home.legal_zone:
            raise ResidencyError("BACKUP_REGION_DENIED", f"{rid} is outside {home.legal_zone}")


def _class_rank(data_class: str) -> int:
    if data_class not in DATA_CLASSES:
        raise ResidencyError("DATA_CLASS_PLACEMENT_DENIED", f"unknown data class {data_class}")
    return 4 if data_class == "KEY_MATERIAL" else DATA_CLASSES.index(data_class)


def check_storage(cat: Catalog, r: Residency, data_class: str, cell_id: str) -> None:
    """May `data_class` of this tenant be stored/processed in `cell_id`? (TENANCY §5-6)."""
    rank = _class_rank(data_class)
    if cell_id in r.prohibited_cells:
        raise ResidencyError(
            "DATA_CLASS_PLACEMENT_DENIED", f"{cell_id} is prohibited for the tenant"
        )
    if rank == 0:
        return
    home, dest = cat.cell(r.home_cell_id), cat.cell(cell_id)
    if cell_id == r.home_cell_id:
        return
    if r.profile == "SRP-3":
        raise ResidencyError(
            "DATA_CLASS_PLACEMENT_DENIED", "SRP-3 data stays in its dedicated cell"
        )
    if rank >= 3 and (dest.country != home.country or r.level >= 2):
        raise ResidencyError("DATA_CLASS_PLACEMENT_DENIED", f"{data_class} stays in {home.country}")
    if r.level >= 1 and dest.legal_zone != home.legal_zone:
        raise ResidencyError(
            "DATA_CLASS_PLACEMENT_DENIED", f"{data_class} stays in {home.legal_zone}"
        )


@dataclass(frozen=True, slots=True)
class TransferDecision:
    allowed: bool
    code: str
    rule_id: str | None = None
    field_allowlist: tuple[str, ...] = ()
    cross_border: bool = False


def check_transfer(
    cat: Catalog,
    r: Residency,
    *,
    data_class: str,
    destination_cell_id: str,
    purpose_code: str,
    rules: Iterable[TransferRule],
    pack_mechanisms: Sequence[str],
    now: datetime,
) -> TransferDecision:
    """Cross-cell transfer gateway (TENANCY §11): default deny for restricted classes."""
    rank = _class_rank(data_class)
    if data_class == "KEY_MATERIAL":
        return TransferDecision(False, "KEY_BOUNDARY_MISMATCH")
    try:
        dest = cat.cell(destination_cell_id)
    except ResidencyError:
        return TransferDecision(False, "DEPENDENCY_RESIDENCY_UNKNOWN")
    if destination_cell_id in r.prohibited_cells:
        return TransferDecision(False, "RESIDENCY_TRANSFER_DENIED")
    if rank == 0 or destination_cell_id == r.home_cell_id:
        return TransferDecision(True, "ALLOWED")
    try:
        check_storage(cat, r, data_class, destination_cell_id)
        crosses = False
    except ResidencyError:
        crosses = True
    home = cat.cell(r.home_cell_id)
    cross_border = dest.country != home.country
    if not crosses and not cross_border:
        return TransferDecision(True, "ALLOWED")
    for rule in rules:
        if (
            rule.active(now)
            and rule.data_class == data_class
            and rule.destination_cell_id == destination_cell_id
            and rule.purpose_code == purpose_code
            and rule.jurisdiction_pack_id == r.jurisdiction_pack_id
        ):
            if rule.mechanism not in pack_mechanisms:
                return TransferDecision(False, "RESIDENCY_TRANSFER_DENIED", rule.rule_id)
            return TransferDecision(
                True, "ALLOWED_BY_RULE", rule.rule_id, rule.field_allowlist, cross_border
            )
    return TransferDecision(False, "RESIDENCY_TRANSFER_DENIED", cross_border=cross_border)


def minimize(payload: Mapping[str, Any], allowlist: Sequence[str]) -> dict[str, Any]:
    """TEN-22: an approved transfer carries allowlisted fields only (unknown fields dropped)."""
    allowed = set(allowlist)
    return {k: v for k, v in payload.items() if k in allowed}


def check_recovery(r: Residency, cell_id: str) -> None:
    if cell_id not in r.permitted_recovery_cells:
        raise ResidencyError("BACKUP_REGION_DENIED", f"{cell_id} is not a permitted recovery cell")


def operator_jurisdictions(
    cat: Catalog, r: Residency | None, configured: Sequence[str]
) -> tuple[str, ...]:
    """Effective operator jurisdictions for a tenant (TENANCY §19): SRP-2/3 always restrict - to
    the residency record's list, intersected with any stricter tenant operator-access policy."""
    policy = tuple(j.upper() for j in configured)
    if r is None or r.level < 2:
        return policy
    sovereign = tuple(j.upper() for j in r.operator_jurisdictions) or tuple(
        c for c in (cat.cell(r.home_cell_id).country,) if c
    )
    return tuple(j for j in sovereign if not policy or j in policy) or ("--",)


def dependency_findings(cat: Catalog, r: Residency | None, locations: Sequence[str]) -> list[str]:
    """TEN-20: can a subprocessor operating in `locations` serve this tenant's profile? SRP-1+ needs every location
    inside the home legal zone, SRP-2+ inside the home country. A tenant with no residency record has no
    commitment to breach. Tokens are countries, legal zones or region names, compared upper-case."""
    if r is None or r.level < 1:
        return []
    home = cat.cell(r.home_cell_id)
    allowed = (
        {home.country.upper()}
        if r.level >= 2 and home.country
        else {x.upper() for x in (home.country, home.legal_zone, home.region) if x}
        | {
            c.country.upper()
            for c in cat.cells.values()
            if c.country and c.legal_zone == home.legal_zone
        }
    )
    return [
        f"{loc.upper()} is outside {'/'.join(sorted(allowed))}"
        for loc in locations
        if loc.upper() not in allowed
    ]


@dataclass(frozen=True, slots=True)
class CoverageFinding:
    subsystem: str
    code: str  # NOT_DECLARED | OUTSIDE_REGION | OUTSIDE_JURISDICTION | SHARED_INFRASTRUCTURE
    detail: str


def _declared(cat: Catalog, r: Residency, subsystem: str) -> Placement | None:
    return cat.placements.get(r.home_cell_id, {}).get(subsystem)


def coverage_findings(cat: Catalog, r: Residency) -> list[CoverageFinding]:
    """TENANCY §6, TEN-18/29/32: every in-scope subsystem must have a declared location and that location must
    satisfy the tenant's profile. SRP-1+ keeps every subsystem (logs, traces and security events included) in the
    home region, SRP-2+ in the home jurisdiction, SRP-3 on dedicated infrastructure with no shared fallback.
    An undeclared subsystem is a finding, never an assumption that it is fine."""
    home = cat.cell(r.home_cell_id)
    found: list[CoverageFinding] = []
    for s in cat.coverage_subsystems:
        p = _declared(cat, r, s)
        if p is None:
            found.append(
                CoverageFinding(s, "NOT_DECLARED", "no location is declared for this subsystem")
            )
            continue
        if r.level >= 1 and p.region != home.region:
            found.append(CoverageFinding(s, "OUTSIDE_REGION", f"{p.region} is not {home.region}"))
        if r.level >= 2 and p.country != home.country:
            found.append(
                CoverageFinding(s, "OUTSIDE_JURISDICTION", f"{p.country} is not {home.country}")
            )
        if r.level >= 3 and not p.dedicated:
            found.append(
                CoverageFinding(s, "SHARED_INFRASTRUCTURE", "SRP-3 needs a dedicated deployment")
            )
    return found


def coverage_matrix(cat: Catalog, r: Residency) -> list[dict[str, Any]]:
    """TENANCY §6 / TEN-32: where each in-scope subsystem keeps this tenant's data, and whether that satisfies
    the profile. `proven` is true only when no subsystem has a finding."""
    home = cat.cell(r.home_cell_id)
    bad: dict[str, list[CoverageFinding]] = {}
    for f in coverage_findings(cat, r):
        bad.setdefault(f.subsystem, []).append(f)
    out = []
    for s in cat.coverage_subsystems:
        p = _declared(cat, r, s)
        out.append(
            {
                "subsystem": s,
                "cell_id": r.home_cell_id,
                "region": p.region if p else home.region,
                "jurisdiction": p.country if p else home.country,
                "legal_zone": home.legal_zone,
                "recovery_cells": list(r.permitted_recovery_cells),
                "evidence": "configuration"
                if s not in ("authoritative_db", "kms_hsm")
                else "deployment",
                "declared": p is not None,
                "dedicated": bool(p and p.dedicated),
                "compliant": s not in bad,
                "findings": [{"code": f.code, "detail": f.detail} for f in bad.get(s, [])],
            }
        )
    return out


__all__ = [
    "CATEGORY_CLASS",
    "DATA_CLASSES",
    "PROFILES",
    "Catalog",
    "Cell",
    "CoverageFinding",
    "Placement",
    "Residency",
    "ResidencyError",
    "TransferDecision",
    "TransferRule",
    "check_placement",
    "check_profile",
    "check_recovery",
    "check_storage",
    "check_transfer",
    "coverage_findings",
    "coverage_matrix",
    "dependency_findings",
    "minimize",
    "operator_jurisdictions",
]
