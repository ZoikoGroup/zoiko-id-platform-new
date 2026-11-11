"""Source and contract registry, cross-border routing, sensitive-source gating, claims readiness
(ZID-GAP-001 v2.0 GID-055, GID-056, GID-057, GID-065). Pure domain.

A provider having a product is not a licence to use its data. Before any source is asked, `may_query` checks, in order, and
refuses on the first failure with its own code (so each case has its own test and its own audit reason):
  contract in force -> claim type covered -> purpose permitted -> processing region allowed -> transfer lawful
  -> sensitive category entitled (consent or legal basis, a named authorized user, and a reviewer).
Nothing is queried, cached or reused when a check fails (GLB-15, GLB-16, GLB-18).

`coverage` turns a registry into the only public statement of reach: a country with no permitted primary source is
`PARTNER_ONLY` or `NO_COVERAGE`, and a claim of coverage that the registry cannot back is refused (GLB-34).
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any


class SourceError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Sensitivity(StrEnum):
    STANDARD = "STANDARD"
    CRIMINAL = "CRIMINAL"  # criminal record or similar
    DISCIPLINARY = "DISCIPLINARY"  # regulator sanctions
    SANCTIONS = "SANCTIONS"


@dataclass(frozen=True, slots=True)
class SourceContract:
    source_id: str
    claim_types: frozenset[str]
    countries: frozenset[str]  # whose subjects this source may be asked about
    purposes: frozenset[
        str
    ]  # permissible purposes, e.g. EMPLOYMENT_SCREENING, PROFESSIONAL_ENGAGEMENT
    processing_regions: frozenset[str]  # where the data may be processed and held
    valid_from: date
    valid_until: date | None = None
    sensitivity: Sensitivity = Sensitivity.STANDARD
    lawful_transfer: frozenset[str] = (
        frozenset()
    )  # regions it is lawful to move data to (with the mechanism on file)
    refresh_sla_hours: int | None = None
    price_book_ref: str | None = None
    primary: bool = True  # the issuer or regulator itself, not an aggregator
    simulated: bool = False  # a local/test stand-in: usable in a sandbox, never counted as coverage


@dataclass(frozen=True, slots=True)
class Request:
    claim_type: str
    country: str
    purpose: str
    processing_region: str  # where Zoiko would process the answer
    subject_region: str | None = None  # where the subject's data must stay, if restricted
    on: date = date.min
    # sensitive categories only:
    consent_ref: str | None = None
    legal_basis_ref: str | None = None
    authorized_user: bool = False
    reviewer_assigned: bool = False


def may_query(contract: SourceContract | None, req: Request) -> None:
    """Raise `SourceError` unless this source may be asked this question, here, now."""
    if contract is None:
        raise SourceError("SOURCE_NO_CONTRACT")
    if req.on < contract.valid_from or (
        contract.valid_until is not None and req.on > contract.valid_until
    ):
        raise SourceError("SOURCE_CONTRACT_NOT_IN_FORCE", contract.source_id)
    if req.claim_type not in contract.claim_types or req.country.upper() not in contract.countries:
        raise SourceError("SOURCE_NOT_COVERED", contract.source_id)
    if req.purpose not in contract.purposes:
        raise SourceError("SOURCE_PURPOSE_NOT_PERMITTED", req.purpose)
    if req.processing_region not in contract.processing_regions:
        raise SourceError("SOURCE_REGION_BLOCKED", req.processing_region)
    if (
        req.subject_region
        and req.processing_region != req.subject_region
        and req.processing_region not in contract.lawful_transfer
    ):
        # data bound to a region may leave it only to a region the contract lists as lawfully transferable
        raise SourceError(
            "SOURCE_TRANSFER_NOT_LAWFUL", f"{req.subject_region}->{req.processing_region}"
        )
    if contract.sensitivity is not Sensitivity.STANDARD:
        if not (req.consent_ref or req.legal_basis_ref):
            raise SourceError("SENSITIVE_NO_BASIS", contract.sensitivity.value)
        if not req.authorized_user:
            raise SourceError("SENSITIVE_USER_NOT_AUTHORIZED", contract.sensitivity.value)
        if not req.reviewer_assigned:
            raise SourceError("SENSITIVE_NO_REVIEWER", contract.sensitivity.value)


def choose(
    contracts: Sequence[SourceContract], req: Request
) -> tuple[SourceContract | None, Mapping[str, str]]:
    """The first contract that may be used, preferring a primary source; and why each other one was refused. Never
    falls back to a source that was refused: no contract that passes means no query."""
    refused: dict[str, str] = {}
    ordered = sorted(contracts, key=lambda c: (not c.primary, c.source_id))
    for c in ordered:
        try:
            may_query(c, req)
        except SourceError as e:
            refused[c.source_id] = e.code
            continue
        return c, refused
    return None, refused


class Coverage(StrEnum):
    PRIMARY_SOURCE = "PRIMARY_SOURCE"
    PARTNER_ONLY = "PARTNER_ONLY"  # only through an aggregator/partner, no primary source
    NO_COVERAGE = "NO_COVERAGE"


def coverage(
    contracts: Collection[SourceContract],
    *,
    country: str,
    claim_type: str,
    on: date,
    include_simulated: bool = False,
) -> Coverage:
    """What reach can be stated for a country and claim type. A SIMULATED source never counts, so a statement of coverage
    cannot rest on a stand-in; only a local or test caller may ask to include one."""
    live = [
        c
        for c in contracts
        if (include_simulated or not c.simulated)
        and c.sensitivity is Sensitivity.STANDARD
        and country.upper() in c.countries
        and claim_type in c.claim_types
        and c.valid_from <= on
        and (c.valid_until is None or on <= c.valid_until)
    ]
    if any(c.primary for c in live):
        return Coverage.PRIMARY_SOURCE
    return Coverage.PARTNER_ONLY if live else Coverage.NO_COVERAGE


@dataclass(frozen=True, slots=True)
class CoverageClaim:
    """A statement a marketing page or an API description wants to make, e.g. academic checks in 200 countries."""

    claim_type: str
    countries: frozenset[str]
    wording: str = ""


def refuse_unbacked_claim(
    claim: CoverageClaim, contracts: Collection[SourceContract], *, on: date
) -> None:
    """Block publication of a coverage claim the registry cannot back (GLB-34). Lists the countries that fail."""
    unbacked = sorted(
        c
        for c in claim.countries
        if coverage(contracts, country=c, claim_type=claim.claim_type, on=on)
        is not Coverage.PRIMARY_SOURCE
    )
    if unbacked:
        raise SourceError("COVERAGE_CLAIM_UNBACKED", ",".join(unbacked[:20]))


def parse(doc: Mapping[str, Any]) -> list[SourceContract]:
    out: list[SourceContract] = []
    seen: set[str] = set()
    try:
        for raw in doc.get("sources") or []:
            c = SourceContract(
                source_id=raw["source_id"],
                claim_types=frozenset(raw["claim_types"]),
                countries=frozenset(x.upper() for x in raw["countries"]),
                purposes=frozenset(raw["purposes"]),
                processing_regions=frozenset(raw["processing_regions"]),
                valid_from=date.fromisoformat(str(raw["valid_from"])),
                valid_until=date.fromisoformat(str(raw["valid_until"]))
                if raw.get("valid_until")
                else None,
                sensitivity=Sensitivity(raw.get("sensitivity", "STANDARD")),
                lawful_transfer=frozenset(raw.get("lawful_transfer") or ()),
                refresh_sla_hours=raw.get("refresh_sla_hours"),
                price_book_ref=raw.get("price_book_ref"),
                primary=bool(raw.get("primary", True)),
                simulated=bool(raw.get("simulated", False)),
            )
            if c.source_id in seen:
                raise SourceError("SOURCE_REGISTRY_INVALID", f"duplicate {c.source_id}")
            seen.add(c.source_id)
            out.append(c)
    except (KeyError, ValueError, TypeError) as e:
        if isinstance(e, SourceError):
            raise
        raise SourceError("SOURCE_REGISTRY_INVALID", str(e)[:80]) from e
    return out
