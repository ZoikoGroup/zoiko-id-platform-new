"""Company verification grades K0 to K4 and what each may do (ZID-DTV-WF-02 section 4; K08). Pure domain, no I/O.

A grade is a description of which claims are proven about a company. It is not a privilege: what a company may DO is decided at the moment of the action from the claims
then proven (`permitted_actions`), never read off a badge.

- K0 UNVERIFIED     the applicant's own data, or an uploaded file, and nothing independent.
- K1 DOCUMENTS_REVIEWED   two independent reviewers accepted current documents. No authoritative match. It is never shown as a "verified company".
- K2 REGISTRY_MATCHED     an approved register matched the exact registration number, the source reports the company active, and the check is fresh.
- K3 KYB_COMPLETED  K2, and the ownership and control are assessed as complete, the relevant natural people are verified, and screening (where required) is clear.
- K4 AUTHORITY_VERIFIED_FOR_ACTION  K3, and a person whose identity is proven holds a mandate that is in force and covers the action.

Anything that is not proven lowers the grade; nothing here ever raises it from a document, a name, an email address or a role. A company the source reports dissolved is K0 whatever
else is true. A missing, stale or unavailable source is the absence of a claim, never a finding that the company does not exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum


class Grade(StrEnum):
    K0 = "K0"
    K1 = "K1"
    K2 = "K2"
    K3 = "K3"
    K4 = "K4"


LABEL = {
    Grade.K0: "Unverified",
    Grade.K1: "Documents reviewed",
    Grade.K2: "Registry matched",
    Grade.K3: "KYB completed",
    Grade.K4: "Authority verified for action",
}
_ORDER = (Grade.K0, Grade.K1, Grade.K2, Grade.K3, Grade.K4)

# What each grade may do at most. A binding act also needs the mandate, checked separately and per action.
ACTIONS_BY_GRADE: dict[Grade, frozenset[str]] = {
    Grade.K0: frozenset(),
    Grade.K1: frozenset({"ORG_PROFILE_DRAFT", "ORG_PROFILE_MAINTENANCE"}),
    Grade.K2: frozenset({"ORG_PROFILE_DRAFT", "ORG_PROFILE_MAINTENANCE", "ORG_ROUTINE_FUNCTIONS"}),
    Grade.K3: frozenset(
        {
            "ORG_PROFILE_DRAFT",
            "ORG_PROFILE_MAINTENANCE",
            "ORG_ROUTINE_FUNCTIONS",
            "RISK_APPROVED_COMMERCIAL_WORKFLOWS",
        }
    ),
    Grade.K4: frozenset(
        {
            "ORG_PROFILE_DRAFT",
            "ORG_PROFILE_MAINTENANCE",
            "ORG_ROUTINE_FUNCTIONS",
            "RISK_APPROVED_COMMERCIAL_WORKFLOWS",
        }
    ),
}
# Acts that bind the company. They need K4 and a mandate that lists the act: never a grade alone, never a role.
BINDING_ACTIONS = frozenset(
    {
        "BIND_ENTITY",
        "SIGN_AGREEMENT",
        "AUTHORIZE_PAYMENT",
        "TRANSFER_ACCOUNT_OWNERSHIP",
        "DELEGATE_AUTHORITY",
    }
)

# Provisional recheck ceilings for low-risk, non-regulated uses (the wireframe's D-06). Policy replaces them; they are never longer than this.
DEFAULT_RECHECK_DAYS = {Grade.K1: 180, Grade.K2: 365}


@dataclass(frozen=True, slots=True)
class RegistryFact:
    """What an approved authoritative register said. `matched` is an exact identifier match, never a name match."""

    matched: bool
    status_reported: str  # ACTIVE | DISSOLVED | UNKNOWN | SOURCE_UNAVAILABLE
    checked_at: datetime
    max_age: timedelta  # how old the check may be for the market and purpose


@dataclass(frozen=True, slots=True)
class DocumentFact:
    """Document-based review. `reviewers` are the distinct people who accepted the evidence; `current` is the market's freshness rule applied to the documents."""

    reviewers: tuple[str, ...]
    current: bool
    reviewed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class MandateFact:
    """A representative's authority: who, what, when. `person_verified` is a current identity assertion for exactly this person, not a name."""

    person: str
    person_verified: bool
    powers: frozenset[str]
    valid_from: datetime
    valid_to: datetime | None
    revoked: bool = False
    second_signatory_required: bool = False
    second_signatory_present: bool = False


@dataclass(frozen=True, slots=True)
class KybFacts:
    registry: RegistryFact | None = None
    documents: DocumentFact | None = None
    ownership_complete: bool = False  # the ownership and control graph is assessed complete under the market's signed rules
    persons_verified: bool = False  # every relevant natural person (owners, controllers) is verified through person proofing
    screening: str = "NOT_RUN"  # SCREENING_CLEAR | POTENTIAL_MATCH | UNAVAILABLE | NOT_RUN
    screening_required: bool = True
    mandates: tuple[MandateFact, ...] = ()
    extra: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Assessment:
    grade: Grade
    label: str
    reasons: tuple[str, ...]  # why it is not higher, in stable codes
    claims: tuple[str, ...]  # the claims actually proven
    recheck_due: datetime | None


def _registry_current(r: RegistryFact | None, now: datetime) -> bool:
    return (
        r is not None
        and r.matched
        and r.status_reported == "ACTIVE"
        and now - r.checked_at <= r.max_age
    )


def assess(
    facts: KybFacts, *, now: datetime | None = None, recheck_days: dict[Grade, int] | None = None
) -> Assessment:
    """The grade the proven facts support, and why not higher. Deterministic: the same facts give the same answer, and a missing fact only ever lowers it."""
    now = now or datetime.now(UTC)
    days = {**DEFAULT_RECHECK_DAYS, **(recheck_days or {})}
    reasons: list[str] = []
    claims: list[str] = []
    reg = facts.registry

    if reg is not None and reg.matched and reg.status_reported == "DISSOLVED":
        return Assessment(
            Grade.K0, LABEL[Grade.K0], ("COMPANY_DISSOLVED_SOURCE_REPORTED",), (), None
        )

    grade = Grade.K0
    registry_ok = _registry_current(reg, now)
    docs = facts.documents
    docs_ok = docs is not None and docs.current and len(set(docs.reviewers)) >= 2

    if registry_ok:
        grade = Grade.K2
        claims += ["REGISTER_FOUND", "COMPANY_ACTIVE_SOURCE_REPORTED"]
    else:
        if reg is None:
            reasons.append("NO_REGISTRY_CHECK")
        elif not reg.matched:
            reasons.append(
                "REGISTRY_NO_EXACT_MATCH"
            )  # not a finding that the company does not exist
        elif reg.status_reported in ("SOURCE_UNAVAILABLE", "UNKNOWN"):
            reasons.append("REGISTRY_STATUS_NOT_ESTABLISHED")
        elif now - reg.checked_at > reg.max_age:
            reasons.append("REGISTRY_CHECK_STALE")
        if docs_ok:
            grade = Grade.K1
            claims.append("DOCUMENTS_REVIEWED")
        elif docs is None:
            reasons.append("NO_DOCUMENT_REVIEW")
        elif not docs.current:
            reasons.append("DOCUMENTS_NOT_CURRENT")
        elif len(set(docs.reviewers)) < 2:
            reasons.append("TWO_INDEPENDENT_REVIEWERS_REQUIRED")

    if grade == Grade.K2:
        if not facts.ownership_complete:
            reasons.append("OWNERSHIP_INCOMPLETE")
        if not facts.persons_verified:
            reasons.append("PERSONS_NOT_VERIFIED")
        if facts.screening_required and facts.screening != "SCREENING_CLEAR":
            reasons.append(f"SCREENING_{facts.screening}")
        if not any(r.startswith(("OWNERSHIP", "PERSONS", "SCREENING")) for r in reasons):
            grade = Grade.K3
            claims += ["OWNERSHIP_ASSESSED", "PERSONS_VERIFIED"] + (
                ["SCREENING_CLEAR"] if facts.screening_required else []
            )
        if grade == Grade.K3:
            live = [m for m in facts.mandates if _mandate_in_force(m, now)]
            if live:
                grade = Grade.K4
                claims.append("AUTHORITY_VERIFIED")
            else:
                reasons.append("NO_MANDATE_IN_FORCE")

    ceiling = days.get(grade)
    due = None
    if ceiling is not None:
        anchor = (
            reg.checked_at
            if registry_ok and reg
            else (docs.reviewed_at if docs and docs.reviewed_at else None)
        )
        due = anchor + timedelta(days=ceiling) if anchor else None
    elif grade in (Grade.K3, Grade.K4) and reg is not None:
        due = reg.checked_at + timedelta(
            days=days[Grade.K2]
        )  # K3 and K4 recheck on the registry's clock, never later
    return Assessment(grade, LABEL[grade], tuple(reasons), tuple(claims), due)


def _mandate_in_force(m: MandateFact, now: datetime) -> bool:
    return (
        m.person_verified
        and not m.revoked
        and m.valid_from <= now
        and (m.valid_to is None or now < m.valid_to)
    )


def permitted_actions(
    facts: KybFacts,
    assessment: Assessment,
    *,
    person: str | None = None,
    now: datetime | None = None,
) -> frozenset[str]:
    """What may be done now. Grades give the routine ceiling; a binding act additionally needs a verified person whose mandate is in force and names that act, and where the mandate
    requires a second signatory, that signatory. Resolved at the time of the action, never cached from a badge."""
    now = now or datetime.now(UTC)
    allowed = set(ACTIONS_BY_GRADE[assessment.grade])
    if assessment.grade == Grade.K4 and person is not None:
        for m in facts.mandates:
            if m.person != person or not _mandate_in_force(m, now):
                continue
            if m.second_signatory_required and not m.second_signatory_present:
                continue
            allowed |= m.powers & BINDING_ACTIONS
    return frozenset(allowed)


def rank(grade: Grade) -> int:
    return _ORDER.index(grade)


def event_invalidates(event: str) -> bool:
    """Events that make a recheck due immediately, whatever the time limit says (K10)."""
    return event in {
        "REGISTRY_STATUS_CHANGED",
        "COMPANY_DISSOLVED",
        "OWNERSHIP_CHANGED",
        "OWNERSHIP_DISPUTED",
        "MANDATE_REVOKED",
        "MANDATE_EXPIRED",
        "REPRESENTATIVE_REMOVED",
        "SANCTIONS_ALERT",
        "FRAUD_SIGNAL",
    }


# ------------------------------------------------------------------------------------------------------------- storable form


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d is not None else None


def _when(v: object) -> datetime | None:
    return datetime.fromisoformat(str(v)) if v else None


def facts_to_dict(f: KybFacts) -> dict[str, object]:
    """The facts as plain JSON, so the grade can be worked out again later (at the time of an action) from exactly what was proven. Ids and codes only: no names, no documents."""
    r, d = f.registry, f.documents
    return {
        "registry": None
        if r is None
        else {
            "matched": r.matched,
            "status_reported": r.status_reported,
            "checked_at": _iso(r.checked_at),
            "max_age_s": int(r.max_age.total_seconds()),
        },
        "documents": None
        if d is None
        else {
            "reviewers": list(d.reviewers),
            "current": d.current,
            "reviewed_at": _iso(d.reviewed_at),
        },
        "ownership_complete": f.ownership_complete,
        "persons_verified": f.persons_verified,
        "screening": f.screening,
        "screening_required": f.screening_required,
        "mandates": [
            {
                "person": m.person,
                "person_verified": m.person_verified,
                "powers": sorted(m.powers),
                "valid_from": _iso(m.valid_from),
                "valid_to": _iso(m.valid_to),
                "revoked": m.revoked,
                "second_signatory_required": m.second_signatory_required,
                "second_signatory_present": m.second_signatory_present,
            }
            for m in f.mandates
        ],
    }


def facts_from_dict(d: dict[str, object]) -> KybFacts:
    """The inverse. A malformed record raises ValueError: a stored fact that cannot be read is never treated as a fact."""
    try:
        reg = d.get("registry")
        doc = d.get("documents")
        return KybFacts(
            registry=None
            if not reg
            else RegistryFact(
                bool(reg["matched"]),
                str(reg["status_reported"]),
                _when(reg["checked_at"]) or datetime.min.replace(tzinfo=UTC),
                timedelta(seconds=int(reg["max_age_s"])),  # type: ignore[index, call-overload]
            ),
            documents=None
            if not doc
            else DocumentFact(
                tuple(str(x) for x in doc["reviewers"]),
                bool(doc["current"]),
                _when(doc.get("reviewed_at")),
            ),  # type: ignore[index, attr-defined]
            ownership_complete=bool(d.get("ownership_complete")),
            persons_verified=bool(d.get("persons_verified")),
            screening=str(d.get("screening", "NOT_RUN")),
            screening_required=bool(d.get("screening_required", True)),
            mandates=tuple(
                MandateFact(
                    str(m["person"]),
                    bool(m["person_verified"]),
                    frozenset(str(p) for p in m["powers"]),
                    _when(m["valid_from"]) or datetime.min.replace(tzinfo=UTC),
                    _when(m.get("valid_to")),
                    bool(m.get("revoked")),
                    bool(m.get("second_signatory_required")),
                    bool(m.get("second_signatory_present")),
                )
                for m in (d.get("mandates") or [])  # type: ignore[union-attr]
            ),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise ValueError("KYB_FACTS_UNREADABLE") from e
