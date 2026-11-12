"""Issuer and registry trust graph, accreditation and recognition (ZID-GAP-001 v2.0 GID-038, GID-045). Pure domain.

An issuer is a school, regulator, employer, insurer or vendor. What this module refuses to do:
- guess: a name that is only SIMILAR to a known issuer is a lookalike for a person to look at, never a match;
- forget: an issuer's earlier names still resolve (a renamed school is the same school), but only to the issuer that held them;
- trust a status it was not given: an unknown issuer, a revoked one, one outside its coverage, or one whose official authority
  is unrecorded is not trusted for a claim;
- treat a cryptographically valid signature as authority (that is the existing issuer-trust rule; this adds registry facts);
- infer accreditation from the issuer's existence: a program is accredited only if a recorded accreditation covered it at the
  time of the award;
- infer recognition from accreditation or equivalence from recognition: a recognition rule must exist for the exact pair of
  countries and profession, otherwise the answer is REVIEW.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from difflib import SequenceMatcher
from enum import StrEnum
from typing import Any


class TrustGraphError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class IssuerKind(StrEnum):
    UNIVERSITY = "UNIVERSITY"
    REGULATOR = "REGULATOR"
    EMPLOYER = "EMPLOYER"
    INSURER = "INSURER"
    VENDOR = "VENDOR"
    ACCREDITOR = "ACCREDITOR"


class Authority(StrEnum):
    OFFICIAL = "OFFICIAL"  # a recorded legal or regulatory basis for issuing this kind of claim
    UNRECORDED = "UNRECORDED"


@dataclass(frozen=True, slots=True)
class Accreditation:
    body_issuer_id: str
    program: str | None  # None = the whole institution
    valid_from: date
    valid_until: date | None = None

    def covers(self, program: str, on: date) -> bool:
        if self.program is not None and _norm(self.program) != _norm(program):
            return False
        return self.valid_from <= on and (self.valid_until is None or on <= self.valid_until)


@dataclass(frozen=True, slots=True)
class SealSigner:
    """A certificate an issuer seals its credentials with: pinned by the SHA-256 of the certificate (hex), and optionally by the
    organization identifier it carries (for example an EU legal-person identifier). Recorded by the owners, never inferred."""

    sha256: str | None
    organization_identifier: str | None = None
    valid_from: date | None = None
    valid_until: date | None = None

    def covers(self, on: date) -> bool:
        return (self.valid_from is None or self.valid_from <= on) and (
            self.valid_until is None or on <= self.valid_until
        )


@dataclass(frozen=True, slots=True)
class NameUse:
    name: str
    from_: date | None = None
    until: date | None = None


@dataclass(frozen=True, slots=True)
class Issuer:
    issuer_id: str
    kind: IssuerKind
    names: tuple[NameUse, ...]  # the current name has no `until`
    countries: frozenset[str]
    claim_types: frozenset[str]
    authority: Authority = Authority.UNRECORDED
    accreditations: tuple[Accreditation, ...] = ()
    revoked_on: date | None = None
    successor_of: str | None = None
    outreach_contacts: tuple[
        str, ...
    ] = ()  # registered out of band; the only addresses outreach may use
    seal_signers: tuple[SealSigner, ...] = ()  # the certificates its e-seals come from (GID-051)


@dataclass(frozen=True, slots=True)
class RecognitionRule:
    rule_id: str
    award_country: str
    target_country: str
    profession_code: str
    valid_from: date
    valid_until: date | None = None
    requires_review: bool = (
        True  # the legal body decides; a rule can only say "this pair is recognizable"
    )


_STRIP = re.compile(r"[^\w\s]", re.UNICODE)


def _norm(value: str) -> str:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", value) if not unicodedata.combining(c)
    )
    return " ".join(_STRIP.sub(" ", folded.casefold()).split())


class Match(StrEnum):
    RESOLVED = "RESOLVED"
    LOOKALIKE = "LOOKALIKE"  # similar to a known issuer but not the same: a person decides
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class Resolution:
    match: Match
    issuer_id: str | None = None
    via_former_name: bool = False
    candidates: tuple[str, ...] = ()


LOOKALIKE_THRESHOLD = 0.82


@dataclass(frozen=True, slots=True)
class Registry:
    version: str
    issuers: Mapping[str, Issuer] = field(default_factory=dict)
    recognition: Sequence[RecognitionRule] = ()


def resolve(
    registry: Registry, name: str, *, country: str | None = None, on: date | None = None
) -> Resolution:
    """Map a name on a document to a registered issuer. Exact (after case, accent and punctuation folding) is RESOLVED; a
    near miss is LOOKALIKE and lists the candidates; anything else is UNKNOWN. Never resolves across countries when one is given."""
    target = _norm(name)
    if not target:
        return Resolution(Match.UNKNOWN)
    exact: list[tuple[str, bool]] = []
    near: set[str] = set()
    for issuer in registry.issuers.values():
        if country and issuer.countries and country.upper() not in issuer.countries:
            continue
        for use in issuer.names:
            cand = _norm(use.name)
            current = use.until is None
            in_period = on is None or (
                (use.from_ is None or use.from_ <= on) and (use.until is None or on <= use.until)
            )
            if cand == target:
                if in_period or current:
                    exact.append((issuer.issuer_id, not current))
            elif SequenceMatcher(None, cand, target).ratio() >= LOOKALIKE_THRESHOLD:
                near.add(issuer.issuer_id)
    ids = {i for i, _ in exact}
    if len(ids) == 1:
        issuer_id = next(iter(ids))
        return Resolution(
            Match.RESOLVED, issuer_id, via_former_name=all(f for i, f in exact if i == issuer_id)
        )
    if len(ids) > 1:  # two issuers answer to one name: ambiguous, never picked
        return Resolution(Match.LOOKALIKE, None, candidates=tuple(sorted(ids)))
    if near:
        return Resolution(Match.LOOKALIKE, None, candidates=tuple(sorted(near)))
    return Resolution(Match.UNKNOWN)


def trusted_for(
    registry: Registry, issuer_id: str | None, *, claim_type: str, country: str | None, on: date
) -> None:
    """Raise `ISSUER_NOT_TRUSTED` (with a reason) unless the registry vouches for this issuer for this claim. An unknown
    issuer is untrusted even if its signature verifies."""
    issuer = registry.issuers.get(issuer_id or "")
    if issuer is None:
        raise TrustGraphError("ISSUER_NOT_TRUSTED", "unknown issuer")
    if issuer.revoked_on is not None and on >= issuer.revoked_on:
        raise TrustGraphError("ISSUER_NOT_TRUSTED", "revoked")
    if issuer.authority is not Authority.OFFICIAL:
        raise TrustGraphError("ISSUER_NOT_TRUSTED", "authority not recorded")
    if claim_type not in issuer.claim_types:
        raise TrustGraphError("ISSUER_NOT_TRUSTED", "not trusted for this claim type")
    if country and issuer.countries and country.upper() not in issuer.countries:
        raise TrustGraphError("ISSUER_NOT_TRUSTED", "not trusted for this country")


class Accredited(StrEnum):
    ACCREDITED = "ACCREDITED"
    NOT_ACCREDITED = (
        "NOT_ACCREDITED"  # a record exists but did not cover the program at the award date
    )
    UNKNOWN = "UNKNOWN"  # no accreditation record at all: neither a pass nor a failure


def accreditation_at_award(
    issuer: Issuer, program: str, awarded_on: date
) -> tuple[Accredited, tuple[str, ...]]:
    """Accreditation is judged at the AWARD date, not today (an institution accredited now may not have been then)."""
    if not issuer.accreditations:
        return Accredited.UNKNOWN, ("NO_ACCREDITATION_RECORD",)
    covering = [a for a in issuer.accreditations if a.covers(program, awarded_on)]
    if covering:
        return Accredited.ACCREDITED, ()
    return Accredited.NOT_ACCREDITED, ("NOT_ACCREDITED_AT_AWARD_DATE",)


class Recognition(StrEnum):
    NOT_REQUIRED = "NOT_REQUIRED"  # the award is used where it was earned
    REVIEW = "REVIEW"  # a rule makes the pair recognizable; a competent body still decides
    UNRECOGNIZED = "UNRECOGNIZED"  # no rule for this pair: never inferred


def recognition(
    registry: Registry, *, award_country: str, target_country: str, profession: str, on: date
) -> tuple[Recognition, str | None]:
    """Recognition of a foreign award for a profession. Never AUTO-equivalent: the best answer is REVIEW."""
    if award_country.upper() == target_country.upper():
        return Recognition.NOT_REQUIRED, None
    for rule in registry.recognition:
        if (
            rule.award_country == award_country.upper()
            and rule.target_country == target_country.upper()
            and rule.profession_code == profession
            and rule.valid_from <= on
            and (rule.valid_until is None or on <= rule.valid_until)
        ):
            return Recognition.REVIEW, rule.rule_id
    return Recognition.UNRECOGNIZED, None


class Binding(StrEnum):
    BOUND = "BOUND"  # the seal's certificate is one the resolved issuer is recorded as sealing with
    NOT_BOUND = "NOT_BOUND"  # the issuer is known, but this certificate is not one of its recorded seal signers
    ISSUER_UNKNOWN = "ISSUER_UNKNOWN"  # no issuer of that name is recorded
    AMBIGUOUS = (
        "AMBIGUOUS"  # a lookalike, or two issuers answer to the name: a person decides, never bound
    )
    ISSUER_REVOKED = (
        "ISSUER_REVOKED"  # the issuer was withdrawn from the registry before the date asked
    )


@dataclass(frozen=True, slots=True)
class SealBinding:
    result: Binding
    issuer_id: str | None = None
    candidates: tuple[str, ...] = ()


def bind_seal(
    registry: Registry,
    awarding_body: str | None,
    signer_sha256: str | None,
    signer_organization_identifier: str | None,
    *,
    country: str | None = None,
    on: date,
) -> SealBinding:
    """Is the sealer the awarding body the credential names? The NAME is resolved through the registry like any other issuer name
    (exact after folding, former names by period, lookalikes refused); the BINDING is then a match on a pinned certificate
    fingerprint or organization identifier the owners recorded for that issuer. A similar-looking name never binds."""
    if not awarding_body:
        return SealBinding(Binding.ISSUER_UNKNOWN)
    res = resolve(registry, awarding_body, country=country, on=on)
    if res.match is Match.UNKNOWN or res.issuer_id is None and not res.candidates:
        return SealBinding(Binding.ISSUER_UNKNOWN)
    if res.issuer_id is None:
        return SealBinding(Binding.AMBIGUOUS, None, res.candidates)
    issuer = registry.issuers[res.issuer_id]
    if issuer.revoked_on is not None and issuer.revoked_on <= on:
        return SealBinding(Binding.ISSUER_REVOKED, issuer.issuer_id)
    for ss in issuer.seal_signers:
        if not ss.covers(on):
            continue
        if ss.sha256 and signer_sha256 and ss.sha256 == signer_sha256.lower():
            return SealBinding(Binding.BOUND, issuer.issuer_id)
        if (
            ss.organization_identifier
            and signer_organization_identifier
            and ss.organization_identifier == signer_organization_identifier
        ):
            return SealBinding(Binding.BOUND, issuer.issuer_id)
    return SealBinding(Binding.NOT_BOUND, issuer.issuer_id)


def parse(doc: Mapping[str, Any]) -> Registry:
    """Build a registry from a loaded config document; malformed rows are refused, not skipped."""
    issuers: dict[str, Issuer] = {}
    try:
        for raw in doc.get("issuers") or []:
            names = tuple(
                NameUse(n["name"], _d(n.get("from")), _d(n.get("until")))
                for n in raw.get("names") or []
            )
            if not names:
                raise TrustGraphError("TRUST_GRAPH_INVALID", str(raw.get("issuer_id")))
            issuer = Issuer(
                issuer_id=raw["issuer_id"],
                kind=IssuerKind(raw["kind"]),
                names=names,
                countries=frozenset(c.upper() for c in raw.get("countries") or ()),
                claim_types=frozenset(raw.get("claim_types") or ()),
                authority=Authority(raw.get("authority", "UNRECORDED")),
                accreditations=tuple(
                    Accreditation(
                        a["body"], a.get("program"), _d(a["from"]) or date.min, _d(a.get("until"))
                    )
                    for a in raw.get("accreditations") or []
                ),
                revoked_on=_d(raw.get("revoked_on")),
                successor_of=raw.get("successor_of"),
                outreach_contacts=tuple(raw.get("outreach_contacts") or ()),
                seal_signers=tuple(
                    SealSigner(
                        (s.get("sha256") or "").lower() or None,
                        s.get("organization_identifier"),
                        _d(s.get("from")),
                        _d(s.get("until")),
                    )
                    for s in raw.get("seal_signers") or []
                ),
            )
            for ss in issuer.seal_signers:
                if not ss.sha256 and not ss.organization_identifier:
                    raise TrustGraphError(
                        "TRUST_GRAPH_INVALID", f"{issuer.issuer_id}: empty seal signer"
                    )
            if issuer.issuer_id in issuers:
                raise TrustGraphError("TRUST_GRAPH_INVALID", f"duplicate {issuer.issuer_id}")
            issuers[issuer.issuer_id] = issuer
        rules = tuple(
            RecognitionRule(
                r["rule_id"],
                r["award_country"].upper(),
                r["target_country"].upper(),
                r["profession_code"],
                _d(r["from"]) or date.min,
                _d(r.get("until")),
            )
            for r in doc.get("recognition") or []
        )
    except (KeyError, ValueError, TypeError) as e:
        if isinstance(e, TrustGraphError):
            raise
        raise TrustGraphError("TRUST_GRAPH_INVALID", str(e)[:80]) from e
    return Registry(str(doc.get("version", "0")), issuers, rules)


def _d(value: Any) -> date | None:
    if value is None:
        return None
    return value if isinstance(value, date) else date.fromisoformat(str(value))
