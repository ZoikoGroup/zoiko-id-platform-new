"""Address and proof of address (ZID-GAP-001 v2.0 DTV-17; CR-100). Pure domain.

What is real and checkable without a data provider: the shape of an address, a tolerant comparison of two addresses, and whether a document offered as proof of
address is of an accepted type, recent enough, issued by someone, dated sensibly and about the address and person claimed. What needs a provider is NOT here
and not pretended: whether anyone lives at an address, an electoral roll, a credit-header match. `AddressProvider` is the interface an adapter will fill;
until one exists every provider answer is UNAVAILABLE.

The rules that hold whatever the policy says:
- a proof document SUPPORTS a claimed address; it never VERIFIES it. The strongest answer is SUPPORTS, with the ceiling DOCUMENT_PRESENTED: a person presented a
  document that says this. Whether the document is genuine is a separate question (document validation), and this module says it was not asked;
- the accepted document types and how old they may be are POLICY, data the compliance officer owns. The shipped policy is a SAMPLE, marked so, and nobody has
  approved it;
- uncertainty goes to a person, never to a refusal: a partly matching address or name is REVIEW, and only a clear mismatch is DOES_NOT_SUPPORT (still evidence
  for a person, never a decision to decline someone);
- nothing numeric decides: there is no score, only named findings;
- a postcode is checked for shape only for the countries whose shape is known; elsewhere the answer says POSTCODE_NOT_CHECKED instead of guessing.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any, Protocol

COUNTRY = re.compile(r"^[A-Z]{2}$")
MAX_FIELD = 120
POSTCODE_SHAPES: Mapping[str, re.Pattern[str]] = {
    "GB": re.compile(r"^[A-Z]{1,2}[0-9][A-Z0-9]? ?[0-9][A-Z]{2}$"),
    "US": re.compile(r"^[0-9]{5}(-[0-9]{4})?$"),
    "IE": re.compile(r"^[A-Z][0-9]{2} ?[A-Z0-9]{4}$|^[A-Z]{3} ?[A-Z0-9]{4}$"),
    "DE": re.compile(r"^[0-9]{5}$"),
    "FR": re.compile(r"^[0-9]{5}$"),
    "ES": re.compile(r"^[0-9]{5}$"),
}
# short forms that mean the same thing in an address; applied to whole words only
ABBREVIATIONS: Mapping[str, str] = {
    "st": "street", "rd": "road", "ave": "avenue", "av": "avenue", "ln": "lane", "dr": "drive", "ct": "court", "pl": "place", "sq": "square",
    "apt": "apartment", "flat": "flat", "fl": "floor", "bldg": "building", "str": "strasse",
}  # fmt: skip


class AddressError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Address:
    line1: str
    city: str
    country: str
    postal_code: str = ""
    line2: str = ""
    region: str = ""


def _clean(value: Any, field_name: str, *, required: bool) -> str:
    text = "" if value is None else str(value).strip()
    if required and not text:
        raise AddressError("ADDRESS_FIELD_MISSING", field_name)
    if len(text) > MAX_FIELD:
        raise AddressError("ADDRESS_FIELD_TOO_LONG", field_name)
    return text


def parse_address(raw: Mapping[str, Any]) -> Address:
    """An address from a mapping. Required: line1, city and an ISO country. Anything else is optional. Unknown keys are refused."""
    unknown = set(raw) - {"line1", "line2", "city", "region", "postal_code", "country"}
    if unknown:
        raise AddressError("ADDRESS_KEY_UNKNOWN", ",".join(sorted(unknown)))
    country = _clean(raw.get("country"), "country", required=True).upper()
    if not COUNTRY.match(country):
        raise AddressError("COUNTRY_INVALID", country)
    return Address(
        _clean(raw.get("line1"), "line1", required=True),
        _clean(raw.get("city"), "city", required=True),
        country,
        _clean(raw.get("postal_code"), "postal_code", required=False),
        _clean(raw.get("line2"), "line2", required=False),
        _clean(raw.get("region"), "region", required=False),
    )


def _fold(text: str) -> str:
    """Case, accents and punctuation are not differences between two spellings of one address."""
    base = (
        unicodedata.normalize("NFKD", text.replace("ß", "ss"))
        .encode("ascii", "ignore")
        .decode("ascii")
        .lower()
    )
    words = re.sub(r"[^a-z0-9 ]+", " ", base).split()
    out = []
    for w in words:
        w = ABBREVIATIONS.get(w, w)
        if len(w) > 5 and w.endswith(
            "str"
        ):  # a German street name carries its abbreviation: Hauptstr is Hauptstrasse
            w += "asse"
        out.append(w)
    return " ".join(out)


def normalized_postcode(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def postcode_check(address: Address) -> str:
    """OK, BAD_SHAPE, or NOT_CHECKED (no shape is known for this country: say so, never guess)."""
    shape = POSTCODE_SHAPES.get(address.country)
    if shape is None:
        return "NOT_CHECKED"
    return (
        "OK"
        if address.postal_code and shape.match(address.postal_code.upper().strip())
        else "BAD_SHAPE"
    )


class Match(StrEnum):
    SAME = "SAME"  # equal after folding case, accents, punctuation and common abbreviations
    PARTIAL = "PARTIAL"  # the same place described differently, or one part differs: a person looks
    DIFFERENT = "DIFFERENT"


def compare(a: Address, b: Address) -> Match:
    """Tolerant comparison. A different country is DIFFERENT. The same postcode and the same street line is SAME even if the city is spelled another way;
    everything in between is PARTIAL, never SAME and never a quiet DIFFERENT."""
    if a.country != b.country:
        return Match.DIFFERENT
    line_a, line_b = _fold(f"{a.line1} {a.line2}"), _fold(f"{b.line1} {b.line2}")
    city_a, city_b = _fold(a.city), _fold(b.city)
    post_a, post_b = normalized_postcode(a.postal_code), normalized_postcode(b.postal_code)
    postcode_known = bool(post_a and post_b)
    same_post = postcode_known and post_a == post_b
    if line_a == line_b and (city_a == city_b or same_post) and (not postcode_known or same_post):
        return Match.SAME
    if postcode_known and not same_post:
        return Match.DIFFERENT if line_a != line_b and city_a != city_b else Match.PARTIAL
    ta, tb = set(line_a.split()), set(line_b.split())
    overlap = len(ta & tb) / max(1, min(len(ta), len(tb)))
    if city_a == city_b and overlap >= 0.6:
        return Match.PARTIAL
    if same_post and overlap >= 0.34:
        return Match.PARTIAL
    return Match.DIFFERENT


# ---------------------------------------------------------------------------------------------------------------------- proof of address


class Support(StrEnum):
    SUPPORTS = "SUPPORTS"
    REVIEW = "REVIEW"
    DOES_NOT_SUPPORT = "DOES_NOT_SUPPORT"


@dataclass(frozen=True, slots=True)
class ProofPolicy:
    """The compliance officer's rule: which document types are accepted for which countries, and how old each may be. Data, never code."""

    version: str
    status: str  # SAMPLE | APPROVED
    max_age_days: Mapping[str, int]  # accepted document type -> how old it may be
    approved_by: str | None = None
    legal_basis_ref: str | None = None


def parse_policy(doc: Mapping[str, Any]) -> ProofPolicy:
    status = str(doc.get("status", "SAMPLE"))
    if status not in {"SAMPLE", "APPROVED"}:
        raise AddressError("POLICY_INVALID", "status")
    ages = doc.get("max_age_days")
    if not isinstance(ages, Mapping) or not ages:
        raise AddressError("POLICY_INVALID", "max_age_days")
    parsed: dict[str, int] = {}
    for kind, days in ages.items():
        if (
            not re.fullmatch(r"[A-Z][A-Z_]{2,40}", str(kind))
            or not isinstance(days, int)
            or isinstance(days, bool)
            or not 1 <= days <= 3650
        ):
            raise AddressError("POLICY_INVALID", f"{kind}")
        parsed[str(kind)] = days
    approved_by = str(doc["approved_by"]) if doc.get("approved_by") else None
    legal = str(doc["legal_basis_ref"]) if doc.get("legal_basis_ref") else None
    if status == "APPROVED" and not (approved_by and legal):
        raise AddressError(
            "POLICY_APPROVAL_INCOMPLETE", "APPROVED needs approved_by and legal_basis_ref"
        )
    return ProofPolicy(str(doc.get("version", "")), status, parsed, approved_by, legal)


@dataclass(frozen=True, slots=True)
class ProofDocument:
    """What a document says about the person and address, as READ from it (by OCR or a person). Nothing here is trusted as genuine."""

    doc_type: str
    issuer: str
    issued_on: date
    name_on_document: str
    address: Address


@dataclass(frozen=True, slots=True)
class Finding:
    code: str


@dataclass(frozen=True, slots=True)
class Assessment:
    support: Support
    findings: tuple[Finding, ...]
    address_match: Match | None
    postcode: str
    ceiling: str = "DOCUMENT_PRESENTED"
    notes: tuple[str, ...] = (
        "A document can support an address; it does not verify it.",
        "Whether the document is genuine was not asked here: that is document validation.",
        "A policy marked SAMPLE has not been approved by anyone.",
    )
    policy_status: str = "SAMPLE"


def _same_name(a: str, b: str) -> Match:
    ta, tb = set(_fold(a).split()), set(_fold(b).split())
    if not ta or not tb:
        return Match.DIFFERENT
    if ta == tb:
        return Match.SAME
    return Match.PARTIAL if (ta <= tb or tb <= ta or len(ta & tb) >= 2) else Match.DIFFERENT


def assess_proof(
    doc: ProofDocument, claimed: Address, subject_name: str, policy: ProofPolicy, *, today: date
) -> Assessment:
    """Whether a document SUPPORTS the claimed address for this person. Findings are named codes; there is no score."""
    findings: list[str] = []
    hard = False  # a clear mismatch the person can see
    soft = False  # something a person should look at

    limit = policy.max_age_days.get(doc.doc_type)
    if limit is None:
        findings.append("DOCUMENT_TYPE_NOT_ACCEPTED")
        hard = True
    if not doc.issuer.strip():
        findings.append("ISSUER_MISSING")
        soft = True
    if doc.issued_on > today:
        findings.append("DOCUMENT_DATED_IN_THE_FUTURE")
        hard = True
    elif limit is not None and today - doc.issued_on > timedelta(days=limit):
        findings.append("DOCUMENT_TOO_OLD")
        hard = True

    match = compare(claimed, doc.address)
    if match is Match.DIFFERENT:
        findings.append("ADDRESS_MISMATCH")
        hard = True
    elif match is Match.PARTIAL:
        findings.append("ADDRESS_PARTIAL_MATCH")
        soft = True

    name = _same_name(subject_name, doc.name_on_document)
    if name is Match.DIFFERENT:
        findings.append("NAME_MISMATCH")
        hard = True
    elif name is Match.PARTIAL:
        findings.append("NAME_PARTIAL_MATCH")
        soft = True

    postcode = postcode_check(doc.address)
    if postcode == "BAD_SHAPE":
        findings.append("POSTCODE_BAD_SHAPE")
        soft = True
    elif postcode == "NOT_CHECKED":
        findings.append("POSTCODE_NOT_CHECKED")

    support = Support.DOES_NOT_SUPPORT if hard else Support.REVIEW if soft else Support.SUPPORTS
    return Assessment(
        support, tuple(Finding(c) for c in findings), match, postcode, policy_status=policy.status
    )


# ---------------------------------------------------------------------------------------------------------------------- the provider interface


@dataclass(frozen=True, slots=True)
class ProviderAnswer:
    available: bool
    match: str | None = (
        None  # what the provider states about the person at the address, passed through; never invented here
    )
    reasons: tuple[str, ...] = ()
    provider: str = ""


class AddressProvider(Protocol):
    """What an address data provider (electoral roll, credit header, postal register) adapter implements. It is the only place a network call may happen
    and must fail by returning `ProviderAnswer(available=False)`, never by raising into a decision."""

    def check(self, subject_name: str, address: Address) -> ProviderAnswer: ...


class NoProvider:
    def check(self, subject_name: str, address: Address) -> ProviderAnswer:
        return ProviderAnswer(False, None, ("NO_PROVIDER_CONFIGURED",), "")


def provider_answer(
    provider: AddressProvider | None, subject_name: str, address: Address
) -> ProviderAnswer:
    try:
        return (provider or NoProvider()).check(subject_name, address)
    except Exception:  # noqa: BLE001 - a provider that fails is unavailable, never a pass and never a fail
        return ProviderAnswer(False, None, ("PROVIDER_FAILED",), "")


@dataclass(frozen=True, slots=True)
class AddressResult:
    document: Assessment | None
    provider: ProviderAnswer = field(
        default_factory=lambda: ProviderAnswer(False, None, ("NO_PROVIDER_CONFIGURED",), "")
    )
    recommendation: str = "REVIEW"  # a person decides; there is no other value


def combine(document: Assessment | None, provider: ProviderAnswer) -> AddressResult:
    """Both pieces of evidence side by side. They are never merged into a pass: a person sees each."""
    return AddressResult(document, provider)


def accepted_types(policy: ProofPolicy) -> Sequence[str]:
    return sorted(policy.max_age_days)
