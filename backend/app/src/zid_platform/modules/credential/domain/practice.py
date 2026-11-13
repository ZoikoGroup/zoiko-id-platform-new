"""Practice restrictions and insurance adequacy (ZID-RCG-003 RCG-G05; sections 06, 10.1; CR-076). Pure domain, no I/O.

`eligibility.py` says whether a person holds a live licence in the right place for the service, and `standing.py` whether cover names the person, the
territory, the dates and the work. Neither says what a licence's CONDITIONS or the cover's LIMITS mean for one service. This module does, as a rule
the owners publish for one policy. What it refuses to do:
- guess: a licence condition the rule has not classified is REVIEW, never permitted; a condition the rule says blocks the service is NOT_ESTABLISHED;
- infer: a qualification (a genuine degree, a licence) is never practice eligibility. The result carries both, separately: QUALIFICATION_VERIFIED
  with PRACTICE_ELIGIBILITY_NOT_ESTABLISHED, never one composite VERIFIED (document 10.1, mandatory counterexample);
- convert: cover in another currency does not meet a limit in this one; a missing limit, class or basis is a failure to report, not a pass;
- lean on today's wording: a claims-made policy needs its retroactive date on or before the start of the work;
- loosen: applying a rule can only keep an engagement result or make it stricter, never upgrade one.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain import standing as st
from zid_platform.modules.credential.domain.eligibility import Engagement, EngagementOutcome


class PracticeError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Practice(StrEnum):
    ESTABLISHED = "ESTABLISHED"
    REVIEW = "REVIEW"
    NOT_ESTABLISHED = "NOT_ESTABLISHED"


class Qualification(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"


@dataclass(frozen=True, slots=True)
class InsuranceRequirement:
    min_limit: Decimal
    currency: str
    practice_classes: frozenset[str] = frozenset()  # empty = any class counts
    claims_made_needs_retroactive: bool = True


@dataclass(frozen=True, slots=True)
class PracticeRule:
    """The published rule for one eligibility policy. Conditions are matched after folding case, spaces and punctuation."""

    rule_id: str
    version: int
    policy_id: str
    permitted: frozenset[str] = frozenset()  # conditions that do not limit this service
    blocking: frozenset[str] = frozenset()  # conditions that make the service not permitted
    review: frozenset[str] = frozenset()  # conditions a person must look at
    insurance: InsuranceRequirement | None = None


@dataclass(frozen=True, slots=True)
class PracticeDecision:
    qualification: Qualification
    practice: Practice
    reasons: tuple[str, ...]
    conditions: Mapping[str, tuple[str, ...]] = field(
        default_factory=dict
    )  # reason code -> the conditions behind it


_FOLD = re.compile(r"[^A-Z0-9]+")


def fold(value: Any) -> str:
    """A condition or class in one comparable form: SUPERVISED PRACTICE ONLY, supervised-practice-only and Supervised_Practice_Only agree."""
    return _FOLD.sub("_", str(value or "").upper()).strip("_")


def restriction_result(
    rule: PracticeRule, conditions: Sequence[str]
) -> tuple[Practice, tuple[str, ...], dict[str, tuple[str, ...]]]:
    """What a licence's conditions mean for this service. Worst result wins: a blocking condition, then any condition needing review or not
    classified, then ESTABLISHED. No conditions at all is ESTABLISHED for this check only."""
    blocking, review, unknown = [], [], []
    for raw in conditions:
        c = fold(raw)
        if not c:
            continue
        if c in rule.blocking:
            blocking.append(c)
        elif c in rule.review:
            review.append(c)
        elif c not in rule.permitted:
            unknown.append(c)
    reasons: list[str] = []
    detail: dict[str, tuple[str, ...]] = {}
    if blocking:
        reasons.append("CONDITION_BLOCKS_SERVICE")
        detail["CONDITION_BLOCKS_SERVICE"] = tuple(sorted(set(blocking)))
    if review:
        reasons.append("CONDITION_NEEDS_REVIEW")
        detail["CONDITION_NEEDS_REVIEW"] = tuple(sorted(set(review)))
    if unknown:
        reasons.append("CONDITION_NOT_CLASSIFIED")
        detail["CONDITION_NOT_CLASSIFIED"] = tuple(sorted(set(unknown)))
    if blocking:
        return Practice.NOT_ESTABLISHED, tuple(reasons), detail
    if review or unknown:
        return Practice.REVIEW, tuple(reasons), detail
    return Practice.ESTABLISHED, (), {}


def _amount(value: Any) -> Decimal | None:
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and d >= 0 else None


def _on(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def insurance_adequacy(
    requirement: InsuranceRequirement, facts: Mapping[str, Any], need: st.InsuranceNeed
) -> tuple[bool, tuple[str, ...]]:
    """(adequate, reasons). The basic checks of `standing.insurance_result` (named insured, territory, dates, work, exclusions) plus limit, currency,
    practice class and claims-made retroactive date. Every failure is reported so a person can fix it."""
    _, basic = st.insurance_result(facts, need)
    reasons = list(basic)
    limit = _amount(facts.get("coverage_limit"))
    if limit is None:
        reasons.append("COVERAGE_LIMIT_NOT_REPORTED")
    elif fold(facts.get("currency")) != fold(requirement.currency):
        reasons.append("COVERAGE_CURRENCY_MISMATCH")  # never converted
    elif limit < requirement.min_limit:
        reasons.append("COVERAGE_LIMIT_BELOW_MINIMUM")
    if requirement.practice_classes:
        classes = {fold(c) for c in _as_list(facts.get("practice_class"))}
        if not classes & requirement.practice_classes:
            reasons.append("PRACTICE_CLASS_NOT_COVERED")
    basis = fold(facts.get("cover_basis"))
    if basis == "CLAIMS_MADE" and requirement.claims_made_needs_retroactive:
        retro = _on(facts.get("retroactive_date"))
        if retro is None:
            reasons.append("RETROACTIVE_DATE_MISSING")
        elif retro > need.from_:
            reasons.append("RETROACTIVE_DATE_AFTER_WORK_START")
    elif basis not in ("CLAIMS_MADE", "OCCURRENCE"):
        reasons.append("COVER_BASIS_NOT_REPORTED")
    return (not reasons, tuple(reasons))


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return list(value) if isinstance(value, list | tuple | set | frozenset) else [value]


def decide(
    rule: PracticeRule,
    *,
    qualification_verified: bool,
    eligible: bool,
    conditions: Sequence[str],
) -> PracticeDecision:
    """Qualification and practice, kept apart. Practice is ESTABLISHED only when the policy's eligibility says ELIGIBLE AND the licence's conditions
    leave the service open. Eligibility that is not ELIGIBLE is NOT_ESTABLISHED here whatever the conditions say."""
    qualification = Qualification.VERIFIED if qualification_verified else Qualification.NOT_VERIFIED
    if not eligible:
        reasons = ["PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"]
        if qualification_verified:
            reasons.insert(0, "QUALIFICATION_VERIFIED")
        return PracticeDecision(qualification, Practice.NOT_ESTABLISHED, tuple(reasons))
    practice, why, detail = restriction_result(rule, conditions)
    reasons = list(why)
    if practice is not Practice.ESTABLISHED:
        if practice is Practice.NOT_ESTABLISHED:
            reasons.append("PRACTICE_ELIGIBILITY_NOT_ESTABLISHED")
        if qualification_verified:
            reasons.insert(0, "QUALIFICATION_VERIFIED")
    return PracticeDecision(qualification, practice, tuple(reasons), detail)


_RANK = {Engagement.READY: 0, Engagement.HOLD: 1, Engagement.BLOCKED: 2}


def apply_to_engagement(
    outcome: EngagementOutcome, decision: PracticeDecision
) -> EngagementOutcome:
    """Fold the practice decision into an engagement result. It can keep the result or make it stricter; it never upgrades one."""
    if decision.practice is Practice.ESTABLISHED:
        return outcome
    wanted = (
        Engagement.BLOCKED if decision.practice is Practice.NOT_ESTABLISHED else Engagement.HOLD
    )
    engagement = outcome.engagement if _RANK[outcome.engagement] >= _RANK[wanted] else wanted
    reasons = tuple(dict.fromkeys((*outcome.reasons, *decision.reasons)))
    return replace(outcome, engagement=engagement, reasons=reasons)


def parse(doc: Mapping[str, Any]) -> list[PracticeRule]:
    """Build rules from a loaded config document; a malformed row is refused whole, not skipped. A condition may not be in two classes."""
    rules: list[PracticeRule] = []
    try:
        for raw in doc.get("rules") or []:
            cond = raw.get("conditions") or {}
            classes = {
                k: frozenset(fold(c) for c in cond.get(k) or ())
                for k in ("permitted", "blocking", "review")
            }
            names = list(classes.values())
            if sum(len(s) for s in names) != len(frozenset().union(*names)):
                raise PracticeError(
                    "PRACTICE_RULE_INVALID", f"{raw.get('rule_id')}: a condition is in two classes"
                )
            ins = raw.get("insurance")
            requirement = (
                InsuranceRequirement(
                    min_limit=Decimal(str(ins["min_limit"])),
                    currency=str(ins["currency"]).upper(),
                    practice_classes=frozenset(fold(c) for c in ins.get("practice_classes") or ()),
                    claims_made_needs_retroactive=bool(
                        ins.get("claims_made_needs_retroactive", True)
                    ),
                )
                if ins
                else None
            )
            if requirement is not None and requirement.min_limit <= 0:
                raise PracticeError(
                    "PRACTICE_RULE_INVALID",
                    f"{raw.get('rule_id')}: the minimum limit must be positive",
                )
            rules.append(
                PracticeRule(
                    rule_id=str(raw["rule_id"]),
                    version=int(raw["version"]),
                    policy_id=str(raw["policy_id"]),
                    permitted=classes["permitted"],
                    blocking=classes["blocking"],
                    review=classes["review"],
                    insurance=requirement,
                )
            )
    except PracticeError:
        raise
    except (KeyError, ValueError, TypeError, InvalidOperation) as e:
        raise PracticeError("PRACTICE_RULE_INVALID", str(e)[:80]) from e
    keys = [(r.policy_id, r.version) for r in rules]
    if len(keys) != len(set(keys)):
        raise PracticeError("PRACTICE_RULE_INVALID", "duplicate rule version")
    return rules
