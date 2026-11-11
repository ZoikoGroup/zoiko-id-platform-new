"""Matching a credential holder to a source record (ZID-GAP-001 v2.0 GID-052). Pure computation over the screening name scorer.

Names are compared the way the sanctions matcher compares them (case, accents, word order, and Cyrillic, Greek and Arabic written
in Latin first), so a diacritic or a transliteration difference is never by itself a rejection (GLB-24). Rules:
- a mismatch is never auto-rejected on a middling score: it is AMBIGUOUS and a person decides; only a very low score with no
  other tie is NOT_MATCHED;
- a name from an older document counts only with recorded evidence of the change (a former-name record); without it the answer is
  AMBIGUOUS, which opens the evidence-based human path (GLB-08);
- a conflicting birth year turns a good name match into AMBIGUOUS, never into a match and never into an adverse result (GLB-22);
- the reasons name what was compared, not the names themselves.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from zid_platform.modules.credential.domain.claims import SubjectMatch
from zid_platform.modules.screening.ports import name_score

MATCH_AT = 0.9
POSSIBLE_AT = 0.6


@dataclass(frozen=True, slots=True)
class Holder:
    legal_name: str
    former_names: Sequence[str] = ()
    name_change_evidence: bool = (
        False  # a recorded legal-name-change record exists for the former names
    )
    birth_year: int | None = None


def match_holder(
    holder: Holder, source_name: str, *, source_birth_year: int | None = None
) -> tuple[SubjectMatch, tuple[str, ...]]:
    current = name_score(holder.legal_name, source_name)
    former = max((name_score(n, source_name) for n in holder.former_names), default=0.0)
    reasons: list[str] = []
    if current >= MATCH_AT:
        decision = SubjectMatch.MATCHED
    elif former >= MATCH_AT:
        if holder.name_change_evidence:
            decision = SubjectMatch.MATCHED
            reasons.append("MATCHED_VIA_FORMER_NAME_WITH_EVIDENCE")
        else:
            decision = SubjectMatch.AMBIGUOUS
            reasons.append("FORMER_NAME_WITHOUT_CHANGE_EVIDENCE")
    elif max(current, former) >= POSSIBLE_AT:
        decision = SubjectMatch.AMBIGUOUS
        reasons.append("NAME_PARTIAL")
    else:
        return SubjectMatch.NOT_MATCHED, ("NAME_DIFFERENT",)
    if (
        decision is SubjectMatch.MATCHED
        and holder.birth_year is not None
        and source_birth_year is not None
        and holder.birth_year != source_birth_year
    ):
        return SubjectMatch.AMBIGUOUS, (*reasons, "BIRTH_YEAR_CONFLICT")
    return decision, tuple(reasons)
