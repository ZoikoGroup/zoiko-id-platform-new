"""Standing rules per claim class (ZID-GAP-001 v2.0 GID-040, 042, 043, 044, 046, 047). Pure domain.

`claims.decide` says whether a claim is verified; these functions say what the source's facts MEAN for it. Each is a function
of recorded facts and a date; none looks at the holder's own words.
- license (040): a status is derived from what the regulator reports and the dates, never defaulted to ACTIVE; a lapsed,
  suspended or not-yet-effective license cannot be ACTIVE, and conditions are carried as restrictions, not dropped;
- certificate (042): past its renewal date it is EXPIRED; a program the vendor retired no longer renews but a past award stays true;
- CPD (043): only a source-confirmed record from an approved provider that meets the required credits is SATISFIED; anything
  else is INDETERMINATE with its reasons (never a fraud finding, never a silent pass);
- employment (044): only the fields the employer is permitted to disclose are returned; an unreachable or vanished employer is
  not a false claim;
- disciplinary (046): a name-only match is nothing; an adverse finding needs a matched, source-confirmed, adjudicated record,
  and one under appeal says so; no record is not a clearance of anything beyond that source;
- insurance (047): cover must name the person (or the person's own entity), the territory, the dates and the work, and must not
  exclude it; a firm's cover is not a person's.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain.claims import (
    CredentialStatus,
    EvidenceTier,
    SubjectMatch,
)

# ------------------------------------------------------------------------------------------------------ license (040)


@dataclass(frozen=True, slots=True)
class LicenseStanding:
    status: CredentialStatus
    restrictions: tuple[str, ...]
    reasons: tuple[str, ...]


def license_standing(fields: Mapping[str, Any], *, on: date) -> LicenseStanding:
    """Derive the status of a license from what the regulator confirmed. Unknown stays UNKNOWN."""
    restrictions = tuple(str(c) for c in (fields.get("conditions") or ()))
    start, end = _date(fields.get("effective_from")), _date(fields.get("effective_until"))
    standing = fields.get("good_standing", "UNKNOWN")
    if start and on < start:
        return LicenseStanding(
            CredentialStatus.UNKNOWN, restrictions, ("LICENSE_NOT_YET_EFFECTIVE",)
        )
    if end and on > end:
        return LicenseStanding(CredentialStatus.EXPIRED, restrictions, ("LICENSE_PERIOD_ENDED",))
    if standing == "NOT_GOOD":
        return LicenseStanding(CredentialStatus.SUSPENDED, restrictions, ("NOT_IN_GOOD_STANDING",))
    if standing == "CONDITIONS" and not restrictions:
        return LicenseStanding(
            CredentialStatus.UNKNOWN, (), ("CONDITIONS_UNSPECIFIED",)
        )  # a condition we cannot name is not a pass
    if standing in ("GOOD", "CONDITIONS"):
        return LicenseStanding(CredentialStatus.ACTIVE, restrictions, ())
    return LicenseStanding(CredentialStatus.UNKNOWN, restrictions, ("STANDING_NOT_REPORTED",))


# ------------------------------------------------------------------------------------------------- certificate (042)


def certificate_status(
    fields: Mapping[str, Any], *, on: date, program_retired: bool = False
) -> LicenseStanding:
    due = _date(fields.get("renewal_due"))
    if due is not None and on > due:
        return LicenseStanding(CredentialStatus.EXPIRED, (), ("RENEWAL_OVERDUE",))
    reasons = ("PROGRAM_RETIRED_NO_RENEWAL",) if program_retired else ()
    return LicenseStanding(CredentialStatus.ACTIVE, (), reasons)


# ------------------------------------------------------------------------------------------------------------ CPD (043)


class CpdResult(StrEnum):
    SATISFIED = "SATISFIED"
    INDETERMINATE = "INDETERMINATE"


def cpd_standing(
    fields: Mapping[str, Any],
    *,
    required_credits: float,
    approved_providers: Collection[str],
    evidence: EvidenceTier,
) -> tuple[CpdResult, tuple[str, ...]]:
    reasons: list[str] = []
    if evidence < EvidenceTier.PRIMARY_SOURCE_CONFIRMED:
        reasons.append(
            "EVIDENCE_NOT_SOURCE_CONFIRMED"
        )  # a forged or unverified CE certificate lands here
    if str(fields.get("evidence_source")) not in set(approved_providers):
        reasons.append(
            "PROVIDER_NOT_APPROVED"
        )  # recorded as a receipt, does not satisfy the regulator's rule
    try:
        credits = float(fields.get("credits", 0))
    except (TypeError, ValueError):
        credits = 0.0
        reasons.append("CREDITS_UNREADABLE")
    if credits < required_credits:
        reasons.append("CREDITS_BELOW_REQUIREMENT")
    return (CpdResult.INDETERMINATE, tuple(reasons)) if reasons else (CpdResult.SATISFIED, ())


# ----------------------------------------------------------------------------------------------------- employment (044)


def employment_disclosure(
    source_fields: Mapping[str, Any], *, permitted_fields: Collection[str]
) -> dict[str, Any]:
    """Only what the employer permits to be shared; the rest is dropped before anything is stored or returned."""
    allowed = set(permitted_fields)
    out = {k: v for k, v in source_fields.items() if k in allowed}
    start, end = _date(out.get("start_date")), _date(out.get("end_date"))
    if start and end and end < start:
        out["end_date"] = None  # an end before the start is a data fault, not a fact to publish
        out["date_fault"] = True
    return out


# ------------------------------------------------------------------------------------------------- disciplinary (046)


class Disciplinary(StrEnum):
    ADVERSE_FINDING = "ADVERSE_FINDING"
    ADVERSE_UNDER_APPEAL = "ADVERSE_UNDER_APPEAL"
    NO_ADVERSE_FINDING = (
        "NO_ADVERSE_FINDING"  # this source holds none; says nothing about other regulators
    )
    REVIEW = "REVIEW"
    NO_MATCH = "NO_MATCH"


def disciplinary_result(
    *,
    subject: SubjectMatch,
    evidence: EvidenceTier,
    record_found: bool,
    final: bool,
    appeal_pending: bool,
) -> tuple[Disciplinary, tuple[str, ...]]:
    if subject is SubjectMatch.NOT_MATCHED:
        return Disciplinary.NO_MATCH, ("SUBJECT_NOT_MATCHED",)
    if subject is SubjectMatch.AMBIGUOUS:
        return Disciplinary.REVIEW, (
            "SUBJECT_MATCH_AMBIGUOUS",
        )  # a same-name hit is never an adverse decision
    if not record_found:
        return Disciplinary.NO_ADVERSE_FINDING, ("NO_RECORD_AT_THIS_SOURCE",)
    if evidence < EvidenceTier.PRIMARY_SOURCE_CONFIRMED:
        return Disciplinary.REVIEW, ("FINDING_NOT_SOURCE_CONFIRMED",)
    if appeal_pending:
        return Disciplinary.ADVERSE_UNDER_APPEAL, ("APPEAL_PENDING",)
    if not final:
        return Disciplinary.REVIEW, ("FINDING_NOT_FINAL",)
    return Disciplinary.ADVERSE_FINDING, ()


# ------------------------------------------------------------------------------------------------------ insurance (047)


@dataclass(frozen=True, slots=True)
class InsuranceNeed:
    insured_entity: str  # the person (or the person's own legal entity) who must be named
    region: str
    work: str  # the activity the cover must include
    from_: date
    to: date


def insurance_result(
    fields: Mapping[str, Any], need: InsuranceNeed
) -> tuple[bool, tuple[str, ...]]:
    """(satisfied, reasons). Every requirement is checked and every failure is reported, so a person can fix it."""
    reasons: list[str] = []
    if _norm(fields.get("insured_entity")) != _norm(need.insured_entity):
        reasons.append("INSURED_ENTITY_MISMATCH")  # a firm's policy is not the individual's
    regions = {_norm(r) for r in _listify(fields.get("region"))}
    if _norm(need.region) not in regions:
        reasons.append("TERRITORY_NOT_COVERED")
    expires = _date(fields.get("expires_on"))
    if expires is None or expires < need.to:
        reasons.append("COVER_ENDS_BEFORE_PERIOD")
    activities = {_norm(a) for a in _listify(fields.get("activities"))}
    if activities and _norm(need.work) not in activities:
        reasons.append("WORK_NOT_COVERED")
    if _norm(need.work) in {_norm(x) for x in _listify(fields.get("excluded_work"))}:
        reasons.append("WORK_EXCLUDED")
    return (not reasons, tuple(reasons))


# ---------------------------------------------------------------------------------------------------------------- helpers


def _norm(value: Any) -> str:
    return " ".join(str(value or "").casefold().split())


def _listify(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple | set | frozenset) else [value]


def _date(value: Any) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None
