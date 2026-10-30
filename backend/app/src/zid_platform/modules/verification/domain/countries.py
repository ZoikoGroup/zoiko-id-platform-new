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
