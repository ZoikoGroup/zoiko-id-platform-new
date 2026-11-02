"""Residency offers and the home-cell readiness gate (ZID-EDR-002). Pure domain, no I/O.

The offers are commercial packaging over the residency profiles SRP-0 to SRP-3; they relabel and weaken
nothing (profiles and data classes stay in `residency.py`). Rules:
- Regional (SRP-1) is sold only on a home cell that has passed its readiness gate: all five gates recorded with
  a reference (architecture, security, privacy and law, operations, commercial);
- a READY cell also names every supplier that routes its data, and each supplier declares its country coverage, data access, storage
  locations, onward transfers and subcontractors before the region is enabled (§4, external suppliers); a storage location outside the
  cell's legal zone is a finding, not a pass;
- a cell that has not passed holds that customer segment: it is refused, never moved to another region (§2);
- SANDBOX is a development stand-in and counts in local and test only, never when deployed;
- Sovereign and Dedicated are contract-gated and not generally available; Shared Global carries no sensitive data.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from zid_platform.modules.tenant.domain.residency import (
    PROFILES,
    Catalog,
    Residency,
    ResidencyError,
    dependency_findings,
)

GATES = ("architecture", "security", "privacy_and_law", "operations", "commercial")
SANDBOX_ENVIRONMENTS = frozenset({"local", "test"})
AVAILABILITIES = ("NOT_A_SENSITIVE_TIER", "STANDARD_AFTER_GATE", "CONTRACT_GATED")
CELL_STATUSES = ("NOT_READY", "SANDBOX", "READY")
SUPPLIER_FIELDS = (
    "country_coverage",
    "data_access",
    "storage_locations",
    "onward_transfers",
    "subcontractors",
)


@dataclass(frozen=True, slots=True)
class Offer:
    code: str
    profile: str
    availability: str
    sensitive_data: bool
    customer_promise: str


@dataclass(frozen=True, slots=True)
class HomeRegion:
    region: str
    cell_id: str
    wave: int | None
    serves: str


@dataclass(frozen=True, slots=True)
class CellReadiness:
    status: str = "NOT_READY"
    gates: Mapping[str, str] = field(default_factory=dict)  # gate -> recorded reference
    suppliers: Mapping[str, Mapping[str, Any]] = field(
        default_factory=dict
    )  # supplier -> its declaration (SUPPLIER_FIELDS)


@dataclass(frozen=True, slots=True)
class Offers:
    version: str
    offers: Mapping[str, Offer]
    home_regions: Mapping[str, HomeRegion]
    forbidden_claims: tuple[str, ...]
    readiness: Mapping[str, CellReadiness]
    readiness_version: str = "0"


def validate(o: Offers) -> None:
    """A registry that claims production readiness without its gates, or an unknown profile, is refused."""
    for code, offer in o.offers.items():
        if offer.profile not in PROFILES:
            raise ResidencyError("OFFER_INVALID", f"{code}: unknown profile {offer.profile}")
        if offer.availability not in AVAILABILITIES:
            raise ResidencyError("OFFER_INVALID", f"{code}: unknown availability")
        if offer.sensitive_data is False and offer.profile != "SRP-0":
            raise ResidencyError("OFFER_INVALID", f"{code}: only shared global is not sensitive")
    for cell_id, r in o.readiness.items():
        if r.status not in CELL_STATUSES:
            raise ResidencyError("OFFER_INVALID", f"{cell_id}: unknown status {r.status}")
        if r.status == "READY":
            missing = [g for g in GATES if not r.gates.get(g)]
            if missing:
                raise ResidencyError("CELL_GATES_MISSING", f"{cell_id}: needs {missing}")
            incomplete = supplier_gaps(r.suppliers)
            if incomplete:
                raise ResidencyError(
                    "CELL_SUPPLIERS_MISSING",
                    f"{cell_id}: supplier declarations incomplete: {incomplete}",
                )


def supplier_gaps(suppliers: Mapping[str, Mapping[str, Any]]) -> list[str]:
    """`supplier.field` for everything a READY cell has not yet had declared; no supplier at all is a gap too."""
    if not suppliers:
        return ["no supplier declared"]
    return [
        f"{name}.{f}"
        for name, d in sorted(suppliers.items())
        for f in SUPPLIER_FIELDS
        if not d.get(f)
    ]


def supplier_route_findings(
    cat: Catalog, cell_id: str, suppliers: Mapping[str, Mapping[str, Any]]
) -> list[str]:
    """Where a supplier would store this cell's data outside the cell's legal zone (the same rule a Regional tenant gets)."""
    r = Residency("-", "SRP-1", cell_id, (), (), (), "PLATFORM_MANAGED", "-", 1, 1)
    return [
        f"{name}: {finding}"
        for name, d in sorted(suppliers.items())
        for finding in dependency_findings(cat, r, list(d.get("storage_locations") or ()))
    ]


def cell_state(o: Offers, cell_id: str, *, environment: str) -> str:
    """READY, SANDBOX (local and test only) or HELD. Anything unknown is HELD."""
    r = o.readiness.get(cell_id)
    if r is None:
        return "HELD"
    if r.status == "READY":
        return "READY"
    if r.status == "SANDBOX" and environment in SANDBOX_ENVIRONMENTS:
        return "SANDBOX"
    return "HELD"


def offer_for_profile(o: Offers, profile: str) -> Offer:
    for offer in o.offers.values():
        if offer.profile == profile:
            return offer
    raise ResidencyError("OFFER_INVALID", f"no offer for {profile}")


def require_cell_ready(o: Offers, profile: str, cell_id: str, *, environment: str) -> str:
    """Placing a tenant on a regional or higher profile needs a ready home cell. Fails closed, never downgrades."""
    offer = offer_for_profile(o, profile)
    if not offer.sensitive_data:
        return "NOT_APPLICABLE"
    state = cell_state(o, cell_id, environment=environment)
    if state == "HELD":
        raise ResidencyError(
            "CELL_NOT_READY",
            f"{cell_id} has not passed its readiness gate, so the {offer.code.title()} offer is on hold there.",
        )
    return state


def public_view(o: Offers, *, environment: str) -> list[dict[str, Any]]:
    """What may be said about each offer, and where Regional can be taken now. No gate references."""
    out: list[dict[str, Any]] = []
    for offer in o.offers.values():
        row: dict[str, Any] = {
            "offer": offer.code,
            "availability": offer.availability,
            "customer_promise": offer.customer_promise,
        }
        if offer.availability == "STANDARD_AFTER_GATE":
            row["regions"] = [
                {
                    "region": h.region,
                    "serves": h.serves,
                    "state": cell_state(o, h.cell_id, environment=environment),
                }
                for h in o.home_regions.values()
            ]
        out.append(row)
    return out
