"""Registry source facts and root establishment rules (ZID-ROOT-001 §3-6, §19; G-W1).

Pure rules, no I/O. A source adapter (Companies House, GLEIF) returns a `RegistryEntity`: the
source-native status, dates, officers and revision, never a conclusion. These rules then decide:

- the entity must be ACTIVE in its source to originate authority (§6.1 dissolution/strike-off);
- only a *current* officer in an official representative role can hold an OFFICIAL_ROLE root; a
  PSC/owner or a company secretary never does (§8, ROOT-07);
- the representative is bound to the officer only by an exact match of a *verified* identity claim
  against a normalized rendering of the registered name (never fuzzy, never self-asserted);
- a registry root is REGISTRY / AR-3 with ENTITY_EXISTENCE + OFFICIAL_ROLE and a freshness bound;
  it carries no SIGNING_POWER scope: a role alone authorizes nothing beyond what grants carve out.
GLEIF proves the entity (LEI), not the role, so it can corroborate but never hold a root alone.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum

from zid_platform.modules.organization.domain.roots import (
    Assurance,
    Fact,
    RootAssertion,
    RootStatus,
    RootType,
)


class RegistryError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


class SourceType(StrEnum):
    NATIONAL_REGISTER = "NATIONAL_REGISTER"
    GLEIF = "GLEIF"


# Companies House officer_role values that represent the company (§6.1); secretaries and PSCs don't.
OFFICIAL_ROLES = frozenset(
    {"director", "llp-designated-member", "llp-member", "corporate-director"}
)
REGISTRY_FRESHNESS = timedelta(days=1)  # re-checked by the worker before it lapses (§4 freshness)
CH_NUMBER = re.compile(r"^(?:[0-9]{8}|[A-Z]{2}[0-9]{6})$")
LEI = re.compile(r"^[A-Z0-9]{18}[0-9]{2}$")
# EUID (Business Registers Interconnection System): <country><register id>.<national number>
EUID = re.compile(r"^[A-Z]{2}[A-Z0-9]{1,12}\.[A-Z0-9][A-Z0-9_-]{0,29}$")
# Namespaces whose identifiers may be linked to one canonical organization (ROOT-01). LEI and EUID
# are global namespaces; a national number only means something inside its own jurisdiction.
GLOBAL_NAMESPACES = frozenset({"LEI", "EUID"})
LINKABLE_NAMESPACES = frozenset({"GB-COH", "LEI", "EUID"})


@dataclass(frozen=True, slots=True)
class RegistryOfficer:
    name: str  # as registered (Companies House: "SURNAME, Given Names")
    role: str
    appointed_on: date | None
    resigned_on: date | None

    def current(self, today: date) -> bool:
        started = self.appointed_on is None or self.appointed_on <= today
        return started and (self.resigned_on is None or self.resigned_on > today)


@dataclass(frozen=True, slots=True)
class RegistryEntity:
    source_id: str  # e.g. "companies-house", "gleif"
    source_type: SourceType
    namespace: str  # registration namespace, e.g. "GB-COH", "LEI"
    identifier: str
    jurisdiction: str  # ISO 3166-1 alpha-2
    legal_name: str
    source_status: str  # source-native, kept as given
    active: bool
    incorporated_on: date | None
    officers: tuple[RegistryOfficer, ...]
    source_revision: str  # ETag / etag-like revision for idempotent refresh
    observed_at: datetime


def check_identifier(namespace: str, identifier: str) -> str:
    value = identifier.strip().upper()
    if namespace == "GB-COH" and CH_NUMBER.fullmatch(value):
        return value
    if namespace == "LEI" and LEI.fullmatch(value) and lei_checksum_ok(value):
        return value
    if namespace == "EUID" and EUID.fullmatch(value):
        return value
    raise RegistryError("ORGANIZATION_IDENTIFIER_INVALID", f"not a valid {namespace} identifier")


def lei_checksum_ok(lei: str) -> bool:
    """ISO 17442: the two check digits make the ISO 7064 MOD 97-10 remainder 1."""
    digits = "".join(str(int(c, 36)) for c in lei)
    return int(digits) % 97 == 1


def _fold(s: str) -> str:
    decomposed = unicodedata.normalize("NFKD", s)
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s'-]", " ", plain)).strip()


def name_renderings(registered: str) -> tuple[str, ...]:
    """The exact strings a verified legal-name claim may equal for this registered name.

    "DOE, Jane Mary" -> "Jane Mary Doe", "JANE MARY DOE", "jane mary doe", "Jane Mary DOE" ...
    A finite, deterministic list: matching is equality against a verified claim, never fuzzy."""
    if "," in registered:
        surname, given = (p.strip() for p in registered.split(",", 1))
    else:
        surname, given = registered.strip(), ""
    surname, given = _fold(surname), _fold(given)
    if not surname:
        return ()
    natural = f"{given} {surname}".strip()
    forms = {
        natural,
        natural.upper(),
        natural.lower(),
        natural.title(),
        f"{given.title()} {surname.upper()}".strip(),
        f"{given.title()} {surname.title()}".strip(),
    }
    return tuple(sorted(f for f in forms if f))


def representative_officers(entity: RegistryEntity, today: date) -> list[RegistryOfficer]:
    return [o for o in entity.officers if o.role in OFFICIAL_ROLES and o.current(today)]


def check_can_originate(entity: RegistryEntity) -> None:
    if entity.source_type is not SourceType.NATIONAL_REGISTER:
        raise RegistryError(
            "ORGANIZATION_ROLE_UNVERIFIED",
            "An entity identifier proves the organization, not a representative role.",
        )
    if not entity.active:
        raise RegistryError(
            "ORGANIZATION_INACTIVE", f"The register reports status {entity.source_status!r}."
        )


def registry_root(
    entity: RegistryEntity,
    *,
    root_id: str,
    organization_id: str,
    holder_principal_id: str,
    now: datetime,
) -> RootAssertion:
    check_can_originate(entity)
    return RootAssertion(
        root_id=root_id,
        organization_id=organization_id,
        holder_principal_id=holder_principal_id,
        root_type=RootType.REGISTRY,
        facts=frozenset({Fact.ENTITY_EXISTENCE, Fact.OFFICIAL_ROLE}),
        assurance=Assurance.AR3,
        status=RootStatus.ACTIVE,
        jurisdiction=entity.jurisdiction,
        valid_from=now,
        valid_to=None,
        fresh_until=now + REGISTRY_FRESHNESS,
        source_revision=entity.source_revision,
    )


class RefreshOutcome(StrEnum):
    FRESH = "FRESH"  # still true: freshness extended
    OFFICER_CEASED = "OFFICER_CEASED"
    DISSOLVED = "DISSOLVED"


def officer_key(registered: str) -> str:
    """A commitment to a registered officer name (no name is stored; refresh re-derives it)."""
    return hashlib.sha256(_fold(registered).casefold().encode()).hexdigest()


def refresh_outcome(entity: RegistryEntity, officer: str, today: date) -> RefreshOutcome:
    """What a re-check of the source means for an existing registry root (§6.1, §18).

    `officer` is the `officer_key` of the registered officer the holder was bound to."""
    if not entity.active:
        return RefreshOutcome.DISSOLVED
    current = {officer_key(o.name) for o in representative_officers(entity, today)}
    return RefreshOutcome.FRESH if officer in current else RefreshOutcome.OFFICER_CEASED
