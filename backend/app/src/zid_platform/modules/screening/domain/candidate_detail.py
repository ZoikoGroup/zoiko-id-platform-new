"""What a licensed PEP or media candidate's detail means, and what an update to it does (ZID-DTV-WF-06 section 10 and 11; QA-023, QA-025, QA-026; CR-118). Pure: mappings in, verdicts out.

Three rules, all in the direction of a person deciding, never the platform:

- **A former PEP is shown as former, with dates and the relationship, and is never lowered in risk automatically.** The office held, when it ended and, for a relative or close associate, who they are related to and how, come
  from the provider's detail. `FORMER` is a fact about dates. Whether it matters less after some months is a policy per jurisdiction: with no lookback set the note says the risk is not reduced, and with one set it
  says only that the candidate is outside the lookback and still goes to a reviewer. Nothing here closes a case.
- **An article that shares only a name with the subject is not linked.** A media hit attaches to a subject only when at least two independent attributes agree (date of birth, country, an identifier, an employer
  or occupation) and none contradicts. A name alone is `NAME_ONLY`; a contradicting date of birth or country is `CONTRADICTED`. The candidate is still shown for a person to judge, flagged as unlinked.
- **A retraction or a correction reopens the case.** When the provider retracts or corrects an article a case relied on, the case goes back to a person with a stated reason. A retraction never clears the
  subject and a correction never escalates one by itself.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Any

RELATIONSHIPS = frozenset({"SELF", "FAMILY_MEMBER", "CLOSE_ASSOCIATE"})
CORROBORATING = ("birth", "country", "identifier", "employer")
UPDATE_REASONS = {
    "ARTICLE_RETRACTED": "ARTICLE_RETRACTED",
    "ARTICLE_CORRECTED": "ARTICLE_CORRECTED",
    "ENTRY_REMOVED": "ENTRY_DELISTED_OR_CHANGED",
}


class PepStatus(StrEnum):
    CURRENT = "CURRENT"
    FORMER = "FORMER"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class PepPolicy:
    lookback_months: int | None = (
        None  # None: no jurisdiction has set one, so being former never lowers anything
    )


@dataclass(frozen=True, slots=True)
class PepView:
    status: PepStatus
    office: str | None
    ended: date | None
    relationship: str | None
    related_to: str | None  # the PEP a relative or associate is tied to, for a reviewer
    note: str  # CURRENT | FORMER_RISK_NOT_REDUCED | FORMER_OUTSIDE_LOOKBACK_REVIEW | FORMER_WITHIN_LOOKBACK | DATES_MISSING | RELATIONSHIP_MISSING
    needs_person: bool  # always True: no PEP detail closes a case


def _date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _months_between(a: date, b: date) -> int:
    return (b.year - a.year) * 12 + (b.month - a.month) - (1 if b.day < a.day else 0)


NO_LOOKBACK = PepPolicy()


def pep_view(detail: Mapping[str, Any], *, today: date, policy: PepPolicy = NO_LOOKBACK) -> PepView:
    office = str(detail["office"]) if detail.get("office") else None
    ended = _date(detail.get("ended")) if detail.get("ended") else None
    relationship = str(detail.get("relationship", "SELF")).upper()
    related_to = str(detail["related_to"]) if detail.get("related_to") else None
    if relationship not in RELATIONSHIPS or (relationship != "SELF" and not related_to):
        return PepView(
            PepStatus.UNKNOWN, office, ended, None, related_to, "RELATIONSHIP_MISSING", True
        )  # a relative with no named PEP cannot be judged
    held_flag = detail.get("current")
    if ended is None and held_flag is True:
        return PepView(PepStatus.CURRENT, office, None, relationship, related_to, "CURRENT", True)
    if ended is None:
        return PepView(
            PepStatus.UNKNOWN, office, None, relationship, related_to, "DATES_MISSING", True
        )  # no end date and not stated as current: unknown, never "former"
    if ended > today:
        return PepView(PepStatus.CURRENT, office, ended, relationship, related_to, "CURRENT", True)
    if policy.lookback_months is None:
        note = "FORMER_RISK_NOT_REDUCED"
    elif _months_between(ended, today) >= policy.lookback_months:
        note = "FORMER_OUTSIDE_LOOKBACK_REVIEW"
    else:
        note = "FORMER_WITHIN_LOOKBACK"
    return PepView(PepStatus.FORMER, office, ended, relationship, related_to, note, True)


class Link(StrEnum):
    LINKED = "LINKED"
    NAME_ONLY = "NAME_ONLY"
    CONTRADICTED = "CONTRADICTED"


@dataclass(frozen=True, slots=True)
class ArticleLink:
    result: Link
    agreeing: tuple[str, ...]
    contradicting: tuple[str, ...]

    @property
    def attach(self) -> bool:
        """Only a corroborated, uncontradicted article is attached to the subject's case as theirs."""
        return self.result is Link.LINKED


def _norm(value: Any) -> str:
    return " ".join(str(value).casefold().split())


def link_article(subject: Mapping[str, Any], article: Mapping[str, Any]) -> ArticleLink:
    """Agree on two independent attributes or do not link. Missing on either side is neither agreement nor contradiction."""
    agree: list[str] = []
    against: list[str] = []
    for key in CORROBORATING:
        a, b = subject.get(key), article.get(key)
        if a in (None, "", [], ()) or b in (None, "", [], ()):
            continue
        left = {_norm(x) for x in (a if isinstance(a, (list, tuple, set)) else [a])}
        right = {_norm(x) for x in (b if isinstance(b, (list, tuple, set)) else [b])}
        (agree if left & right else against).append(key)
    if against:
        return ArticleLink(Link.CONTRADICTED, tuple(agree), tuple(against))
    if len(agree) >= 2:
        return ArticleLink(Link.LINKED, tuple(agree), ())
    return ArticleLink(Link.NAME_ONLY, tuple(agree), ())


def update_reason(kind: str) -> str | None:
    """The reason a case is reopened for a provider's update, or None for a kind this module does not act on (an unknown kind never reopens or clears anything)."""
    return UPDATE_REASONS.get(kind)
