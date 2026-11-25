"""Evidence and certification semantics (ZID-CP-WF12 v2.0, WF-12.02 and WF-12.10). Pure domain, no I/O.

An artifact proves only what its assessed scope covers. A report is not a certificate, a supplier's certificate is not ours, and an expired or suspended one is not active. A FedRAMP statement needs an official program record.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class EvidenceType(StrEnum):
    REPORT = "report"  # an auditor's report, e.g. SOC 2
    ATTESTATION = "attestation"
    CERTIFICATE = "certificate"  # issued by a registrar or scheme body, e.g. ISO 27001
    ASSESSMENT = "assessment"
    REGISTRATION = "registration"
    SELF_ASSESSMENT = "self_assessment"
    DECLARATION = "declaration"
    CONTRACT_COMMITMENT = "contract_commitment"
    PROGRAM_LISTING = "program_listing"  # an official listing, e.g. the FedRAMP Marketplace


class Lifecycle(StrEnum):
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    EXPIRED = "EXPIRED"
    SUSPENDED = "SUSPENDED"
    WITHDRAWN = "WITHDRAWN"


# a scheme's word and the one evidence type that may carry it: SOC 2 is a report, never a certification
SCHEME_TYPES: dict[str, frozenset[EvidenceType]] = {
    "soc 2": frozenset({EvidenceType.REPORT, EvidenceType.ATTESTATION}),
    "iso 27001": frozenset({EvidenceType.CERTIFICATE}),
    "fedramp": frozenset({EvidenceType.PROGRAM_LISTING}),
}


@dataclass(frozen=True)
class Assessed:
    """What the evidence actually covers."""

    legal_entity: str
    products: frozenset[str]
    regions: frozenset[str]  # empty means not stated, which covers nothing
    period_start: date
    period_end: date
    exclusions: tuple[str, ...] = ()


@dataclass(frozen=True)
class Artifact:
    evidence_id: str
    evidence_type: EvidenceType
    scheme: str
    issuer: str
    assessor: str
    assessed: Assessed
    digest: str
    issuer_confirmed: bool  # the issuer or auditor's own record was checked, not a screenshot
    holder: str  # who the certificate was issued to: "zoiko" or a supplier's name
    lifecycle: Lifecycle = Lifecycle.ACTIVE
    restricted: bool = False  # not public: a request room grant is needed


def lifecycle_on(a: Artifact, today: date) -> Lifecycle:
    """Expiry is computed, not remembered: an artifact past its period is EXPIRED whatever its stored state says."""
    if a.lifecycle != Lifecycle.ACTIVE:
        return a.lifecycle
    return Lifecycle.EXPIRED if a.assessed.period_end < today else Lifecycle.ACTIVE


def is_current(a: Artifact, today: date) -> bool:
    return (
        a.issuer_confirmed
        and lifecycle_on(a, today) == Lifecycle.ACTIVE
        and a.assessed.period_start <= today
    )


@dataclass(frozen=True)
class Subject:
    """What a claim is about."""

    legal_entity: str
    product: str
    region: str
    on: date


def covers(a: Artifact, s: Subject) -> list[str]:
    """Why this artifact does not support a claim about this subject. Empty means it does. A subset cannot support a universal claim."""
    why: list[str] = []
    if a.assessed.legal_entity != s.legal_entity:
        why.append(f"It was issued for {a.assessed.legal_entity}, not {s.legal_entity}.")
    if s.product not in a.assessed.products:
        why.append(f"It does not cover {s.product}.")
    if not a.assessed.regions or s.region not in a.assessed.regions:
        why.append(f"It does not cover {s.region}.")
    if not (a.assessed.period_start <= s.on <= a.assessed.period_end):
        why.append("Its period does not include the date the claim is about.")
    if s.product in a.assessed.exclusions:
        why.append(f"{s.product} is expressly excluded.")
    return why


def scheme_type_error(scheme: str, evidence_type: EvidenceType) -> str | None:
    """SOC 2 labelled a certificate, or ISO 27001 supported by a self-assessment, is refused."""
    allowed = SCHEME_TYPES.get(scheme.lower())
    if allowed is not None and evidence_type not in allowed:
        return f"{scheme} is evidenced by {', '.join(sorted(str(t) for t in allowed))}, not by a {evidence_type}."
    return None


def supports_own_certification(a: Artifact, today: date) -> list[str]:
    """May this artifact stand behind 'Zoiko iD holds ...'? Not a supplier's, not unconfirmed, not out of date."""
    why: list[str] = []
    if a.holder != "zoiko":
        why.append(
            f"It was issued to {a.holder}, not to Zoiko iD. Show it as a supplier's, with that attribution."
        )
    if not a.issuer_confirmed:
        why.append(
            "The issuer or auditor has not confirmed it: a screenshot or an upload alone proves nothing."
        )
    if lifecycle_on(a, today) != Lifecycle.ACTIVE:
        why.append(f"It is {lifecycle_on(a, today).value.lower()}.")
    if (e := scheme_type_error(a.scheme, a.evidence_type)) is not None:
        why.append(e)
    return why


def fedramp_blockers(artifacts: Sequence[Artifact], *, offering: str, today: date) -> list[str]:
    """A FedRAMP statement needs an official program listing for this exact offering, current. Nothing else will do."""
    listings = [
        a
        for a in artifacts
        if a.scheme.lower() == "fedramp"
        and a.evidence_type == EvidenceType.PROGRAM_LISTING
        and offering in a.assessed.products
        and is_current(a, today)
    ]
    return (
        []
        if listings
        else [f"No current official FedRAMP program record for {offering}: the claim is blocked."]
    )


@dataclass(frozen=True)
class Quantity:
    """A figure someone wants to publish: 6B+, 150+, 50ms, 99.99%. It needs a definition, a reproducible query, a source and a window; a management representation is not a source."""

    label: str
    definition: str
    query_ref: str
    source_system: str
    window_start: date | None
    window_end: date | None
    reconciled: bool


def quantity_blockers(q: Quantity) -> list[str]:
    out: list[str] = []
    for field_, value in (
        ("definition", q.definition),
        ("reproducible query", q.query_ref),
        ("source system", q.source_system),
    ):
        if not value.strip():
            out.append(f"{q.label}: no {field_}.")
    if q.window_start is None or q.window_end is None:
        out.append(f"{q.label}: no counting window.")
    if not q.reconciled:
        out.append(f"{q.label}: not reconciled to the source system.")
    return out


@dataclass(frozen=True)
class Endorsement:
    customer: str
    permission_ref: str | None
    permission_date: date | None
    permission_expires: date | None
    may_use_name: bool
    may_use_logo: bool
    results_evidence_ref: str | None


def endorsement_blockers(
    e: Endorsement, *, today: date, shows_name: bool, shows_logo: bool, shows_results: bool
) -> list[str]:
    """A named story needs a signed, current permission for what it shows and evidence for any result."""
    out: list[str] = []
    if not e.permission_ref or e.permission_date is None:
        out.append(f"No signed permission from {e.customer}.")
    if e.permission_expires is not None and e.permission_expires < today:
        out.append(f"The permission from {e.customer} has expired.")
    if shows_name and not e.may_use_name:
        out.append(f"{e.customer} has not allowed use of its name.")
    if shows_logo and not e.may_use_logo:
        out.append(f"{e.customer} has not allowed use of its logo.")
    if shows_results and not e.results_evidence_ref:
        out.append(f"The results shown for {e.customer} have no evidence.")
    return out
