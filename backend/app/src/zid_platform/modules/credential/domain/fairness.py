"""Accuracy and fairness: a disaggregated disparity check that holds the automated path for a cohort (ZID-RCG-003 section 12 T44; CR-092). Pure domain.

The one fairness-sensitive automation in a name match is the ADVERSE one: "these are different people" (NOT_MATCHED) decided without a person. Outcomes
are counted per COHORT, the writing system of the name (never the name, never a person). A cohort whose automated adverse rate is materially above the
lowest cohort's is HELD: its NOT_MATCHED answers go to a person (AMBIGUOUS) instead of being final. A hold only ever adds a human; it never turns a
non-match into a match.

The threshold is owner content, not code: a maximum gap in rate and a minimum cohort size, each with the approval that makes it count. Without an
approved threshold nothing can be said to be fair, so the check FAILS CLOSED: every cohort with outcomes is held for human review until the owners
approve a threshold. No number here is a legal or policy value; the shipped file holds none.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

COHORTS = ("LATIN", "CYRILLIC", "GREEK", "ARABIC", "CJK", "INDIC", "HEBREW", "OTHER")
_BY_SCRIPT = {
    "LATIN": "LATIN",
    "MODIFIER": "LATIN",
    "CYRILLIC": "CYRILLIC",
    "GREEK": "GREEK",
    "ARABIC": "ARABIC",
    "CJK": "CJK",
    "HIRAGANA": "CJK",
    "KATAKANA": "CJK",
    "HANGUL": "CJK",
    "BOPOMOFO": "CJK",
    "DEVANAGARI": "INDIC",
    "BENGALI": "INDIC",
    "GURMUKHI": "INDIC",
    "GUJARATI": "INDIC",
    "ORIYA": "INDIC",
    "TAMIL": "INDIC",
    "TELUGU": "INDIC",
    "KANNADA": "INDIC",
    "MALAYALAM": "INDIC",
    "SINHALA": "INDIC",
    "HEBREW": "HEBREW",
}


def name_cohort(name: str) -> str:
    """The writing system of a name: the one all its letters share, or OTHER for a mix or a script not listed. Letters only; nothing of the name is kept."""
    found = {
        _BY_SCRIPT.get(unicodedata.name(ch, "UNKNOWN").split(" ", 1)[0], "OTHER")
        for ch in name
        if ch.isalpha()
    }
    if not found:
        return "OTHER"
    return found.pop() if len(found) == 1 else "OTHER"


class Reason(StrEnum):
    NO_APPROVED_THRESHOLD = "NO_APPROVED_THRESHOLD"
    MATERIAL_DISPARITY = "MATERIAL_DISPARITY"


@dataclass(frozen=True, slots=True)
class Counts:
    total: int  # name matches made automatically in the window
    adverse: int  # of them, answered NOT_MATCHED by the automation


@dataclass(frozen=True, slots=True)
class Threshold:
    max_rate_gap: float  # a cohort this far above the reference rate (absolute, 0..1) is held
    min_cohort_size: int  # a cohort with fewer outcomes cannot be judged: reported, not held
    approved_by: str
    approval_ref: str


@dataclass(frozen=True, slots=True)
class Finding:
    cohort: str
    held: bool
    reason: Reason | None
    total: int
    adverse: int
    rate: float
    reference_rate: float | None
    judged: bool  # False: too few outcomes to compare


def parse_threshold(doc: Mapping[str, object]) -> Threshold | None:
    """The approved threshold, or None. Anything short of a complete, approved, sensible one is None (and so fails closed)."""
    if str(doc.get("status", "")).upper() != "APPROVED":
        return None
    approval = doc.get("approval")
    if not isinstance(approval, Mapping):
        return None
    by, ref = (
        str(approval.get("approved_by") or "").strip(),
        str(approval.get("reference") or "").strip(),
    )
    gap, size = doc.get("max_rate_gap"), doc.get("min_cohort_size")
    if not by or not ref or isinstance(gap, bool) or isinstance(size, bool):
        return None
    if not isinstance(gap, int | float) or not isinstance(size, int):
        return None
    if not 0 < float(gap) <= 1 or size < 1:
        return None
    return Threshold(float(gap), size, by, ref)


def evaluate(cohorts: Mapping[str, Counts], threshold: Threshold | None) -> list[Finding]:
    """One finding per cohort that has outcomes, ordered by cohort."""
    seen = {c: n for c, n in cohorts.items() if n.total > 0}
    if (
        threshold is None
    ):  # nothing can be called fair without an approved threshold: hold, a person decides
        return [
            Finding(
                c,
                True,
                Reason.NO_APPROVED_THRESHOLD,
                n.total,
                n.adverse,
                n.adverse / n.total,
                None,
                False,
            )
            for c, n in sorted(seen.items())
        ]
    judged = {c: n for c, n in seen.items() if n.total >= threshold.min_cohort_size}
    reference = min((n.adverse / n.total for n in judged.values()), default=None)
    out: list[Finding] = []
    for c, n in sorted(seen.items()):
        rate = n.adverse / n.total
        is_judged = c in judged
        held = is_judged and reference is not None and rate - reference >= threshold.max_rate_gap
        out.append(
            Finding(
                c,
                held,
                Reason.MATERIAL_DISPARITY if held else None,
                n.total,
                n.adverse,
                rate,
                reference if is_judged else None,
                is_judged,
            )
        )
    return out
