"""Country capability registry (ZID-EDR-001 Global Launch Geography). Pure domain, no I/O.

One global product, enabled country by country. A country is discoverable before it is eligible for
registration, identity verification, business verification, credential issuance or enterprise use, and
each of those is gated on its own. Rules:
- the default is DISABLED: a country or capability the registry does not name is not available;
- ENABLED needs all six go/no-go approvals recorded and an effective date that has begun (EDR-001 §4); only
  DISCOVERY (information, documentation, enquiry) is open without them;
- SANDBOX is a development stand-in and counts only in local/test, never in a deployed environment;
- an unavailable capability never yields "verified": the caller is refused with a truthful reason;
- the country is the ISO 3166-1 alpha-2 code the caller declares; it is never inferred from IP or phone.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

ISO_ALPHA2 = re.compile(r"^[A-Z]{2}$")
SANDBOX_ENVIRONMENTS = frozenset({"local", "test"})
APPROVALS = (
    "legal_assessment",
    "supplier_coverage",
    "privacy_and_data",
    "security_and_resilience",
    "local_support",
    "signoff",
)


class Capability(StrEnum):
    DISCOVERY = "DISCOVERY"
    REGISTRATION = "REGISTRATION"
    INDIVIDUAL_VERIFICATION = "INDIVIDUAL_VERIFICATION"
    BUSINESS_VERIFICATION = "BUSINESS_VERIFICATION"
    CREDENTIAL_ISSUANCE = "CREDENTIAL_ISSUANCE"
    ENTERPRISE_USE = "ENTERPRISE_USE"


class Availability(StrEnum):
    DISABLED = "DISABLED"
    SANDBOX = "SANDBOX"
    ENABLED = "ENABLED"


class CountryError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class CapabilityEntry:
    availability: Availability = Availability.DISABLED
    provider: str | None = None
    legal_status: str | None = None
    effective_from: datetime | None = None
    approvals: Mapping[str, str] = field(default_factory=dict)  # approval -> recorded reference


@dataclass(frozen=True)
class Country:
    code: str
    name: str
    wave: int | None
    capabilities: Mapping[Capability, CapabilityEntry]
    contact: str | None = None  # where to go when proofing is unavailable


@dataclass(frozen=True)
class Registry:
    version: str
    countries: Mapping[str, Country]


def validate(registry: Registry) -> None:
    """A registry that claims production availability without its approvals is refused at load."""
    for code, country in registry.countries.items():
        if not ISO_ALPHA2.match(code) or country.code != code:
            raise CountryError("COUNTRY_CODE_INVALID", code)
        for cap, entry in country.capabilities.items():
            if entry.availability is Availability.ENABLED and cap is not Capability.DISCOVERY:
                missing = [a for a in APPROVALS if not entry.approvals.get(a)]
                if missing or entry.effective_from is None:
                    raise CountryError(
                        "COUNTRY_APPROVALS_MISSING",
                        f"{code}/{cap.value}: needs {missing or ['effective_from']}",
                    )


def availability(
    registry: Registry, country: str, capability: Capability, *, now: datetime, environment: str
) -> Availability:
    """What a caller may rely on right now. Anything unknown, unapproved or not yet effective is DISABLED."""
    c = registry.countries.get(country.upper()) if ISO_ALPHA2.match(country.upper()) else None
    entry = c.capabilities.get(capability) if c else None
    if entry is None:
        return Availability.DISABLED
    if entry.availability is Availability.ENABLED:
        if entry.effective_from is None or entry.effective_from > now:
            return Availability.DISABLED
        return Availability.ENABLED
    if entry.availability is Availability.SANDBOX and environment in SANDBOX_ENVIRONMENTS:
        return Availability.SANDBOX
    return Availability.DISABLED


def require(
    registry: Registry, country: str, capability: Capability, *, now: datetime, environment: str
) -> Availability:
    """Fail closed: raise unless the capability is usable here."""
    state = availability(registry, country, capability, now=now, environment=environment)
    if state is Availability.DISABLED:
        raise CountryError(
            "COUNTRY_CAPABILITY_UNAVAILABLE", f"{capability.value} is not available for {country}."
        )
    return state


def public_view(registry: Registry, *, now: datetime, environment: str) -> list[dict[str, Any]]:
    """The truthful availability list for the country selector: nothing about providers or approvals."""
    out = []
    for code in sorted(registry.countries):
        c = registry.countries[code]
        out.append(
            {
                "country": code,
                "name": c.name,
                "wave": c.wave,
                "contact": c.contact,
                "capabilities": {
                    cap.value: availability(
                        registry, code, cap, now=now, environment=environment
                    ).value
                    for cap in Capability
                },
            }
        )
    return out


# ------------------------------------------------------------------------------------ openings (CR-127)
# An operator opens (or closes) one regulated capability for a country that the registry file lists. The decision is data, recorded with its approvals; the
# file stays the list of countries and the source of everything else. Pure: the store and the API live elsewhere.


@dataclass(frozen=True)
class Opening:
    country: str
    version: int
    status: str  # OPEN | CLOSED
    effective_from: datetime | None = None
    approvals: Mapping[str, str] = field(default_factory=dict)
    home_cells: tuple[str, ...] = ()
    reason: str | None = None
    recorded_by: str = ""
    recorded_at: datetime | None = None
    capability: str = "REGISTRATION"


# Every regulated capability is its own go/no-go. DISCOVERY is open without approvals, so it is never opened here.
OPENABLE = (
    Capability.REGISTRATION,
    Capability.INDIVIDUAL_VERIFICATION,
    Capability.BUSINESS_VERIFICATION,
    Capability.CREDENTIAL_ISSUANCE,
    Capability.ENTERPRISE_USE,
)
OpeningKey = tuple[str, str]  # (country, capability)


_REFERENCE = re.compile(r"^[^\x00-\x1f]{3,200}$")
_CELL = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")


def check_opening(
    registry: Registry,
    country: str,
    *,
    capability: str = "REGISTRATION",
    effective_from: datetime | None,
    approvals: Mapping[str, str],
    home_cells: list[str],
) -> tuple[Mapping[str, str], tuple[str, ...]]:
    """Refuse an opening that is not complete. Returns the cleaned approvals and cells. Nothing here decides *whether* a country should open:
    that is what the six references stand for, and they are the operator's record of owners' decisions."""
    if country not in registry.countries:
        raise CountryError("COUNTRY_NOT_LISTED", f"{country} is not in the country registry.")
    if capability not in {c.value for c in OPENABLE}:
        raise CountryError("CAPABILITY_NOT_OPENABLE", f"{capability} cannot be opened here.")
    if effective_from is None:
        raise CountryError("COUNTRY_OPENING_INCOMPLETE", "an effective date is required")
    unknown = sorted(set(approvals) - set(APPROVALS))
    if unknown:
        raise CountryError("COUNTRY_OPENING_INCOMPLETE", f"unknown approvals: {unknown}")
    cleaned = {a: str(approvals.get(a, "")).strip() for a in APPROVALS}
    missing = [a for a, ref in cleaned.items() if not _REFERENCE.match(ref)]
    if missing:
        raise CountryError(
            "COUNTRY_OPENING_INCOMPLETE", f"each approval needs its reference (3 to 200 characters): {missing}"
        )
    if capability != Capability.REGISTRATION.value:
        return cleaned, ()  # only sign-up decides which cell holds an account
    cells = tuple(dict.fromkeys(c.strip() for c in home_cells if c.strip()))
    if not cells or not all(_CELL.match(c) for c in cells):
        raise CountryError("COUNTRY_OPENING_INCOMPLETE", "name the cell that holds this country's accounts")
    return cleaned, cells


def apply_openings(registry: Registry, openings: Mapping[OpeningKey, Opening]) -> Registry:
    """The registry with each (country, capability)'s newest recorded decision applied. An OPEN opening makes the capability ENABLED with its
    approvals and date (so it counts in a deployed environment); a CLOSED one makes it DISABLED, even where the file says SANDBOX. A country the
    file does not list, or a capability that cannot be opened here, is ignored. Everything else is left exactly as the file has it."""
    if not openings:
        return registry
    countries = dict(registry.countries)
    openable = {c.value: c for c in OPENABLE}
    for (code, name), opening in openings.items():
        base = countries.get(code)
        cap = openable.get(name)
        if base is None or cap is None:
            continue
        caps = dict(base.capabilities)
        current = caps.get(cap, CapabilityEntry())
        if opening.status == "OPEN":
            caps[cap] = CapabilityEntry(
                availability=Availability.ENABLED,
                provider=current.provider,
                legal_status=current.legal_status,
                effective_from=opening.effective_from,
                approvals=dict(opening.approvals),
            )
        else:
            caps[cap] = CapabilityEntry(
                availability=Availability.DISABLED,
                provider=current.provider,
                legal_status=current.legal_status,
            )
        countries[code] = Country(
            code=base.code, name=base.name, wave=base.wave, capabilities=caps, contact=base.contact
        )
    merged = Registry(version=registry.version, countries=countries)
    validate(merged)
    return merged
