"""Company verification (ZID-ED-03 Global Company Verification). Pure domain, no I/O.

Zoiko iD owns the outcome; a registry, an LEI record or a supplier only supplies facts. Rules:
- the identity of a company is jurisdiction + registry authority + registration number, as written (leading zeros
  kept); a name or address that looks alike is supporting evidence, never proof;
- VERIFIED needs an exact identifier match from an official register, or from a supplier whose coverage for that
  country has been proven; a missing record, an outage, an expired check or a claim the applicant made alone never is;
- an ambiguous, conflicting, inactive or incomplete record goes to a reviewer (NEEDS_REVIEW), not to REJECTED;
- no source is ever substituted silently: a supplier fact counts only where the routing says its coverage is proven;
- every decision becomes an evidence object with its sources, hashes, policy version, reasons and an expiry.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any


class KybError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class KybStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    VERIFIED = "VERIFIED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    REJECTED = "REJECTED"
    UNAVAILABLE = "UNAVAILABLE"
    EXPIRED = "EXPIRED"


class SourceKind(StrEnum):
    OFFICIAL_REGISTER = "OFFICIAL_REGISTER"
    LEI_REFERENCE = "LEI_REFERENCE"
    SUPPLIER = "SUPPLIER"


class SourceState(StrEnum):
    FOUND = "FOUND"
    NOT_FOUND = "NOT_FOUND"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class Capabilities:
    """What an adapter says it can do (ED-03 §06): where, for which entity types and under which data rights."""

    countries: frozenset[str]
    entity_types: frozenset[str] = frozenset()  # empty: every type the source holds
    data_rights: frozenset[str] = (
        frozenset()
    )  # e.g. EMBED, CACHE, RESELL: only what the contract grants


@dataclass(frozen=True, slots=True)
class Applicant:
    """What the applicant declared; none of it is proof."""

    jurisdiction: str
    registration_number: str
    legal_name: str
    entity_form: str | None = None
    lei: str | None = None
    register_id: str | None = None  # the register court (DE) or state (US) the number belongs to


@dataclass(frozen=True, slots=True)
class Candidate:
    adapter_id: str
    jurisdiction: str
    registry_authority: str
    registration_number: str
    legal_name: str
    registration_status: str
    source_uri: str
    confidence: float  # ranking only: a candidate is never chosen for the applicant


@dataclass(frozen=True, slots=True)
class CompanyFacts:
    """Normalized source facts (ED-03 §06 field list). Raw responses are not kept; their hash is."""

    adapter_id: str
    source_kind: SourceKind
    jurisdiction: str
    registry_authority: str
    registration_number: str
    legal_name: str
    legal_form: str | None
    registration_status: str  # source-native
    active: bool
    registered_address: str | None
    source_uri: str
    observed_at: datetime
    response_hash: str
    lei: str | None = None
    duns: str | None = None
    supplier_entity_id: str | None = None


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    adapter_id: str
    kind: SourceKind
    state: SourceState
    facts: CompanyFacts | None = None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Policy:
    version: str
    validity: timedelta  # how long a VERIFIED outcome stands before a review is due
    name_threshold: float = 0.85  # supporting evidence only


@dataclass(frozen=True, slots=True)
class Decision:
    status: KybStatus
    reasons: tuple[str, ...]
    review_reason: str | None
    match_confidence: float | None
    sources: tuple[SourceOutcome, ...]
    primary: CompanyFacts | None
    expiry_at: datetime | None


# Identifier formats per jurisdiction (engineering defaults; the registry owners confirm each one).
# A national number is only unique inside its own jurisdiction and register, and is never coerced to an integer.
_FORMATS: Mapping[str, re.Pattern[str]] = {
    "GB": re.compile(r"^(?:[0-9]{8}|[A-Z]{2}[0-9]{6})$"),
    "IE": re.compile(r"^[0-9]{1,12}$"),  # CRO numbers are all digits with no fixed length
    "DE": re.compile(r"^(?:HRA|HRB|GnR|PR|VR)\s?[0-9]{1,7}(?:\s?[A-Z])?$"),
    "FR": re.compile(r"^[0-9]{9}$"),  # SIREN, with the Luhn check below
    "US": re.compile(
        r"^[A-Za-z0-9][A-Za-z0-9 ./-]{0,31}$"
    ),  # a state file number; the state is the authority
}
ISO_ALPHA2 = re.compile(r"^[A-Z]{2}$")


def _luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n = n * 2 - 9 if n * 2 > 9 else n * 2
        total += n
    return total % 10 == 0


def check_registration_number(jurisdiction: str, number: str) -> str:
    """The number as the register writes it (case and leading zeros kept), or KybError."""
    if not ISO_ALPHA2.fullmatch(jurisdiction or ""):
        raise KybError("JURISDICTION_INVALID", "a two-letter country code is required")
    value = (number or "").strip()
    pattern = _FORMATS.get(jurisdiction)
    if not value or (
        pattern is not None
        and not pattern.fullmatch(value.upper() if jurisdiction == "GB" else value)
    ):
        raise KybError("IDENTIFIER_INVALID", f"not a valid registration number for {jurisdiction}")
    if jurisdiction == "FR" and not _luhn_ok(value):
        raise KybError("IDENTIFIER_INVALID", "the SIREN check digit does not match")
    if jurisdiction not in _FORMATS and not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9 ./-]{0,31}", value
    ):
        raise KybError("IDENTIFIER_INVALID", "unsupported identifier characters")
    return value.upper() if jurisdiction == "GB" else value


# The register an identifier belongs to, whichever source reported it. A German Handelsregister number means nothing
# without its register court, and a U.S. file number only inside its state, so those two must name theirs.
_FIXED_AUTHORITY = {"GB": "companies-house", "FR": "sirene-rne", "IE": "cro"}
_REGISTER_ID = {
    "DE": re.compile(r"^\S.{1,79}$"),  # the Amtsgericht, e.g. "Amtsgericht Charlottenburg"
    "US": re.compile(r"^US-[A-Z]{2}$"),  # the state register, e.g. "US-DE"
}


def canonical_authority(jurisdiction: str, register_id: str | None) -> str:
    fixed = _FIXED_AUTHORITY.get(jurisdiction)
    if fixed:
        return fixed
    return (register_id or f"{jurisdiction}-register").strip()


def check_applicant(applicant: Applicant) -> Applicant:
    """The applicant's identifier in the form the register writes it, with the register it belongs to."""
    number = check_registration_number(applicant.jurisdiction, applicant.registration_number)
    register_id = (applicant.register_id or "").strip() or None
    pattern = _REGISTER_ID.get(applicant.jurisdiction)
    if pattern is not None and not (register_id and pattern.fullmatch(register_id)):
        raise KybError(
            "REGISTER_REQUIRED",
            f"{applicant.jurisdiction} needs the register the number belongs to",
        )
    return Applicant(
        applicant.jurisdiction,
        number,
        applicant.legal_name.strip(),
        applicant.entity_form,
        applicant.lei,
        register_id,
    )


def entity_id(jurisdiction: str, registry_authority: str, registration_number: str) -> str:
    """A stable id for the legal entity: the same triple always gives the same id, and no other does."""
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_URL,
            f"zid:entity:{jurisdiction}:{registry_authority}:{registration_number}",
        )
    )


_SYNONYMS = {"ltd": "limited", "inc": "incorporated", "corp": "corporation", "co": "company"}


def normalize_name(name: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFKD", name) if not unicodedata.combining(c))
    words = re.sub(r"[^\w\s]", " ", plain.casefold()).split()
    return " ".join(_SYNONYMS.get(w, w) for w in words)


def name_similarity(a: str, b: str) -> float:
    """0..1, deterministic. Supporting evidence for a person to read, never a reason to verify."""
    na, nb = normalize_name(a), normalize_name(b)
    return 0.0 if not na or not nb else round(SequenceMatcher(None, na, nb).ratio(), 4)


def _incomplete(f: CompanyFacts) -> bool:
    return (
        not f.legal_name.strip()
        or not f.registration_status.strip()
        or not f.registration_number.strip()
    )


def reconcile(
    applicant: Applicant,
    outcomes: Sequence[SourceOutcome],
    *,
    accepted_suppliers: frozenset[str],
    policy: Policy,
    now: datetime,
) -> Decision:
    """Deterministic reconciliation (ED-03 §04 step 04). Legal id and jurisdiction decide; the rest supports."""
    reasons: list[str] = []
    usable: list[SourceOutcome] = []
    for o in outcomes:
        if o.kind is SourceKind.SUPPLIER and o.adapter_id not in accepted_suppliers:
            reasons.append(
                "SUPPLIER_COVERAGE_UNPROVEN"
            )  # never a silent substitute for the register
            continue
        usable.append(o)
    primary = [o for o in usable if o.kind is not SourceKind.LEI_REFERENCE]
    lei = [o for o in usable if o.kind is SourceKind.LEI_REFERENCE]
    sources = tuple(outcomes)

    def result(
        status: KybStatus,
        review: str | None = None,
        *,
        extra: Sequence[str] = (),
        confidence: float | None = None,
        used: CompanyFacts | None = None,
    ) -> Decision:
        expiry = now + policy.validity if status is KybStatus.VERIFIED else None
        return Decision(
            status,
            tuple(dict.fromkeys([*reasons, *extra])),
            review,
            confidence,
            sources,
            used,
            expiry,
        )

    if not primary:
        return result(KybStatus.UNAVAILABLE, "NO_COVERAGE", extra=["NO_COVERAGE"])

    found = [o for o in primary if o.state is SourceState.FOUND and o.facts is not None]
    matching = [
        o.facts
        for o in found
        if o.facts is not None
        and o.facts.jurisdiction == applicant.jurisdiction
        and o.facts.registration_number == applicant.registration_number
    ]
    if (
        found and not matching
    ):  # a source answered, but for a different company: a conflict, not a match
        return result(KybStatus.NEEDS_REVIEW, "IDENTIFIER_MISMATCH", extra=["IDENTIFIER_MISMATCH"])
    if not matching:
        if any(o.state is SourceState.UNAVAILABLE for o in primary):
            return result(KybStatus.UNAVAILABLE, "SOURCE_UNAVAILABLE", extra=["SOURCE_UNAVAILABLE"])
        return result(
            KybStatus.NEEDS_REVIEW, "NOT_FOUND", extra=["NOT_FOUND"]
        )  # never "the company does not exist"
    if any(o.state is SourceState.UNAVAILABLE for o in primary):
        reasons.append(
            "PARTIAL_SOURCES"
        )  # another source is down; the answer rests on the ones that replied

    best = next((f for f in matching if f.source_kind is SourceKind.OFFICIAL_REGISTER), matching[0])
    confidence = name_similarity(applicant.legal_name, best.legal_name)

    if any(_incomplete(f) for f in matching):
        return result(
            KybStatus.NEEDS_REVIEW,
            "INCOMPLETE_RECORD",
            extra=["INCOMPLETE_RECORD"],
            confidence=confidence,
            used=best,
        )
    for other in matching:
        if other is best:
            continue
        if (
            name_similarity(best.legal_name, other.legal_name) < policy.name_threshold
            or best.active != other.active
            or (
                best.legal_form
                and other.legal_form
                and normalize_name(best.legal_form) != normalize_name(other.legal_form)
            )
        ):
            return result(
                KybStatus.NEEDS_REVIEW,
                "SOURCES_CONFLICT",
                extra=["SOURCES_CONFLICT"],
                confidence=confidence,
                used=best,
            )
    if not all(f.active for f in matching):
        return result(
            KybStatus.NEEDS_REVIEW,
            "ENTITY_INACTIVE",
            extra=["ENTITY_INACTIVE"],
            confidence=confidence,
            used=best,
        )
    if confidence < policy.name_threshold:
        return result(
            KybStatus.NEEDS_REVIEW,
            "NAME_MISMATCH",
            extra=["NAME_MISMATCH"],
            confidence=confidence,
            used=best,
        )
    if (
        applicant.entity_form
        and best.legal_form
        and normalize_name(applicant.entity_form) != normalize_name(best.legal_form)
    ):
        return result(
            KybStatus.NEEDS_REVIEW,
            "ENTITY_FORM_MISMATCH",
            extra=["ENTITY_FORM_MISMATCH"],
            confidence=confidence,
            used=best,
        )

    if applicant.lei:
        confirmed = [o.facts for o in lei if o.state is SourceState.FOUND and o.facts is not None]
        if not confirmed:
            reasons.append(
                "LEI_NOT_CONFIRMED"
            )  # an LEI the applicant gave and no source could confirm is not recorded
        else:
            ref = confirmed[0]
            if (
                ref.jurisdiction != applicant.jurisdiction
                or name_similarity(ref.legal_name, best.legal_name) < policy.name_threshold
            ):
                return result(
                    KybStatus.NEEDS_REVIEW,
                    "LEI_CONFLICT",
                    extra=["LEI_CONFLICT"],
                    confidence=confidence,
                    used=best,
                )
            reasons.append("LEI_CONFIRMED")
    reasons.append("REGISTRY_MATCH_EXACT")
    return result(KybStatus.VERIFIED, None, confidence=confidence, used=best)


def effective_status(status: KybStatus, expiry_at: datetime | None, now: datetime) -> KybStatus:
    """A VERIFIED outcome past its expiry is EXPIRED: stale evidence is never verified (ED-03 §08)."""
    if status is KybStatus.VERIFIED and (expiry_at is None or expiry_at <= now):
        return KybStatus.EXPIRED
    return status


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode()


def evidence_object(
    *,
    tenant_id: str,
    applicant: Applicant,
    decision: Decision,
    policy: Policy,
    version: int,
    now: datetime,
    carry: Mapping[str, Any] | None = None,
    reviewed: bool = False,
) -> dict[str, Any]:
    """The decision as signed evidence: source, jurisdiction, time, policy, reasons, hashes and expiry (ED-03 §04 step 05)."""
    p = decision.primary
    authority = canonical_authority(applicant.jurisdiction, applicant.register_id)
    body: dict[str, Any] = {
        "tenant_id": tenant_id,
        "entity_id": entity_id(applicant.jurisdiction, authority, applicant.registration_number),
        "jurisdiction": applicant.jurisdiction,
        "registry_authority": authority,
        "source_authority": p.registry_authority if p else None,
        "registration_number": applicant.registration_number,
        "legal_name": p.legal_name if p else None,
        "legal_form": p.legal_form if p else None,
        "registration_status": p.registration_status if p else None,
        "registered_address": p.registered_address if p else None,
        "lei": next(
            (
                o.facts.lei
                for o in decision.sources
                if o.facts and o.facts.lei and "LEI_CONFIRMED" in decision.reasons
            ),
            None,
        ),
        "duns": p.duns if p else None,
        "supplier_entity_id": p.supplier_entity_id if p else None,
        "source_uri": p.source_uri if p else None,
        "observed_at": (p.observed_at if p else now).isoformat(),
        "policy_version": policy.version,
        "match_confidence": decision.match_confidence,
        "expiry_at": decision.expiry_at.isoformat() if decision.expiry_at else None,
        "decision_status": decision.status.value,
        "review_reason": decision.review_reason,
        "reasons": list(decision.reasons),
        "version": version,
        "decided_at": now.isoformat(),
        "sources": [
            {
                "adapter_id": o.adapter_id,
                "kind": o.kind.value,
                "state": o.state.value,
                "response_hash": o.facts.response_hash if o.facts else None,
                "observed_at": o.facts.observed_at.isoformat() if o.facts else None,
            }
            for o in decision.sources
        ],
    }
    if carry:  # a reviewer's ruling keeps the facts and sources of the decision it answers
        for key in CARRIED:
            if key in carry:
                body[key] = carry[key]
    body["reviewed"] = reviewed
    body["evidence_hash"] = hashlib.sha256(_canonical(body)).hexdigest()
    return body


CARRIED = (
    "source_authority",
    "legal_name",
    "legal_form",
    "registration_status",
    "registered_address",
    "lei",
    "duns",
    "supplier_entity_id",
    "source_uri",
    "observed_at",
    "sources",
)


def response_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@dataclass(frozen=True, slots=True)
class Review:
    """A reviewer's ruling on a case in review; it needs a recorded rationale and never edits history."""

    reviewer_id: str
    outcome: KybStatus
    rationale: str
    reasons: tuple[str, ...] = field(default_factory=tuple)


REVIEW_OUTCOMES = frozenset({KybStatus.VERIFIED, KybStatus.REJECTED, KybStatus.NEEDS_REVIEW})


def check_review(current: KybStatus, review: Review, *, requester_id: str | None) -> None:
    if current is not KybStatus.NEEDS_REVIEW:
        raise KybError("CASE_NOT_IN_REVIEW", f"the case is {current.value}")
    if review.outcome not in REVIEW_OUTCOMES:
        raise KybError("REVIEW_OUTCOME_INVALID", review.outcome.value)
    if len(review.rationale.strip()) < 10:
        raise KybError("RATIONALE_REQUIRED", "a reviewer's decision needs a recorded reason")
    if requester_id is not None and review.reviewer_id == requester_id:
        raise KybError("REVIEWER_IS_REQUESTER", "the person who asked cannot decide the case")


@dataclass(frozen=True, slots=True)
class Route:
    """Which sources a country uses (ED-03 section 03): tried first, the LEI reference, and the exception suppliers."""

    first_pass: tuple[str, ...]
    lei: str | None = None
    exception: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Routing:
    version: str
    policy: Policy
    routes: Mapping[str, Route]
    adapter_status: Mapping[str, str]
    supplier_coverage: Mapping[str, frozenset[str]]  # country -> suppliers whose coverage is proven

    def route(self, country: str) -> Route | None:
        return self.routes.get(country)

    def accepted_suppliers(self, country: str) -> frozenset[str]:
        return self.supplier_coverage.get(country, frozenset())


# A supplier is called only where the register route is absent or inadequate (ED-03 section 04 step 03); an outage is not that.
EXCEPTION_TRIGGERS = frozenset(
    {"NOT_FOUND", "SOURCES_CONFLICT", "NAME_MISMATCH", "IDENTIFIER_MISMATCH", "INCOMPLETE_RECORD"}
)
