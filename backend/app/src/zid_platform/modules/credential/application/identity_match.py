"""Matching a credential holder to a source record (ZID-GAP-001 v2.0 GID-052). Pure computation over the screening name scorer.

Names are compared the way the sanctions matcher compares them (case, accents, word order, and Cyrillic, Greek and Arabic written
in Latin first), so a diacritic or a transliteration difference is never by itself a rejection (GLB-24). Rules:
- a mismatch is never auto-rejected on a middling score: it is AMBIGUOUS and a person decides; only a very low score with no
  other tie is NOT_MATCHED;
- a name from an older document counts only with recorded evidence of the change (a former-name record); without it the answer is
  AMBIGUOUS, which opens the evidence-based human path (GLB-08);
- a conflicting birth year turns a good name match into AMBIGUOUS, never into a match and never into an adverse result (GLB-22);
- a name in a script the scorer cannot compare (Chinese, Korean, Hindi, Hebrew and others) is NEVER "a different person": a low score
  there means "we could not compare", so it goes to a person (SCRIPT_NOT_COMPARABLE). Treating it as a mismatch would reject people
  automatically because of how their name is written (anti-discrimination, section 11);
- letters that Unicode does not decompose (the stroke in Ł and Ø, Đ, ß, Æ ...) are folded to their usual Latin form first, so a
  Polish or Danish name is not penalized for being spelled with its own letters;
- the reasons name what was compared, not the names themselves.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from zid_platform.modules.credential.domain.claims import SubjectMatch
from zid_platform.modules.screening.ports import name_score

MATCH_AT = 0.9
POSSIBLE_AT = 0.6

# Unicode does not decompose these, so accent-stripping leaves them different from their usual Latin form
_FOLD = str.maketrans(
    {
        "ø": "o", "Ø": "O", "ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th",
        "ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ħ": "h", "Ħ": "H", "ı": "i", "İ": "I", "ŧ": "t", "Ŧ": "T",
    }
)  # fmt: skip
# the scripts the name scorer can compare: it folds accents and writes Cyrillic, Greek and Arabic in Latin first
_COMPARABLE_SCRIPTS = frozenset({"LATIN", "CYRILLIC", "GREEK", "ARABIC", "MODIFIER"})


def fold(name: str) -> str:
    return name.translate(_FOLD)


def comparable(name: str) -> bool:
    """Can the scorer compare this name at all? False when any letter is in a script it cannot handle."""
    for ch in name:
        if (
            ch.isalpha()
            and unicodedata.name(ch, "UNKNOWN").split(" ", 1)[0] not in _COMPARABLE_SCRIPTS
        ):
            return False
    return True


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
    names = [holder.legal_name, source_name, *holder.former_names]
    can_compare = all(comparable(n) for n in names)
    source_name = fold(source_name)
    current = name_score(fold(holder.legal_name), source_name)
    former = max((name_score(fold(n), source_name) for n in holder.former_names), default=0.0)
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
    elif not can_compare:
        return SubjectMatch.AMBIGUOUS, (
            "SCRIPT_NOT_COMPARABLE",
        )  # never "a different person" on a script we cannot read
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
