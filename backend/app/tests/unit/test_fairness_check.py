"""T44: the disaggregated disparity check. A material gap in the automated adverse rate between name cohorts holds the affected cohort for a person;
without an approved threshold the check fails closed; a hold only ever adds a person."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.credential.application.fairness import apply_hold
from zid_platform.modules.credential.domain import fairness as fa
from zid_platform.modules.credential.domain.claims import SubjectMatch
from zid_platform.modules.credential.infrastructure.registries import fairness_threshold

APPROVED: dict[str, Any] = {
    "status": "APPROVED",
    "max_rate_gap": 0.2,
    "min_cohort_size": 10,
    "approval": {"approved_by": "owner", "reference": "DEC-1"},
}


def test_a_name_is_placed_in_the_cohort_of_its_writing_system() -> None:
    assert fa.name_cohort("Jane Doe") == "LATIN"
    assert fa.name_cohort("José Álvarez") == "LATIN"
    assert fa.name_cohort("Сергей Иванов") == "CYRILLIC"
    assert fa.name_cohort("Νίκος") == "GREEK"
    assert fa.name_cohort("محمد علي") == "ARABIC"
    assert fa.name_cohort("王伟") == "CJK" and fa.name_cohort("김민준") == "CJK"
    assert fa.name_cohort("प्रिया शर्मा") == "INDIC" and fa.name_cohort("יעל כהן") == "HEBREW"
    assert fa.name_cohort("Ivan Иванов") == "OTHER" and fa.name_cohort("1234") == "OTHER"


def test_the_shipped_threshold_is_owner_content_and_is_not_approved() -> None:
    assert fairness_threshold() is None


@pytest.mark.parametrize(
    "change",
    [
        {"status": "PENDING_OWNER_APPROVAL"},
        {"approval": None},
        {"approval": {"approved_by": "", "reference": "DEC-1"}},
        {"approval": {"approved_by": "owner", "reference": ""}},
        {"max_rate_gap": None},
        {"max_rate_gap": 0},
        {"max_rate_gap": 1.5},
        {"max_rate_gap": True},
        {"min_cohort_size": 0},
        {"min_cohort_size": 2.5},
    ],
)
def test_anything_short_of_a_complete_approved_threshold_is_not_a_threshold(
    change: dict[str, Any],
) -> None:
    assert fa.parse_threshold({**APPROVED, **change}) is None
    assert fa.parse_threshold(APPROVED) == fa.Threshold(0.2, 10, "owner", "DEC-1")


def test_without_an_approved_threshold_every_cohort_with_outcomes_is_held() -> None:
    found = fa.evaluate(
        {"LATIN": fa.Counts(50, 1), "CJK": fa.Counts(3, 0), "GREEK": fa.Counts(0, 0)}, None
    )
    assert [(f.cohort, f.held, f.reason) for f in found] == [
        ("CJK", True, fa.Reason.NO_APPROVED_THRESHOLD),
        ("LATIN", True, fa.Reason.NO_APPROVED_THRESHOLD),
    ]  # a cohort with no outcomes has nothing to hold


def test_a_material_disparity_holds_the_affected_cohort_and_only_that_one() -> None:
    threshold = fa.parse_threshold(APPROVED)
    found = {
        f.cohort: f
        for f in fa.evaluate(
            {
                "LATIN": fa.Counts(100, 2),  # 2%: the reference
                "CYRILLIC": fa.Counts(100, 10),  # 10%: a gap of 8 points, under 20
                "ARABIC": fa.Counts(100, 30),  # 30%: a gap of 28 points
                "GREEK": fa.Counts(4, 4),  # 100% but only 4 outcomes: cannot be judged
            },
            threshold,
        )
    }
    assert found["ARABIC"].held and found["ARABIC"].reason is fa.Reason.MATERIAL_DISPARITY
    assert not found["CYRILLIC"].held and not found["LATIN"].held
    assert (
        not found["GREEK"].held and not found["GREEK"].judged
    )  # reported as too small, not cleared
    assert found["ARABIC"].reference_rate == pytest.approx(0.02)


def test_a_gap_exactly_at_the_threshold_is_held() -> None:
    found = fa.evaluate(
        {"LATIN": fa.Counts(100, 0), "ARABIC": fa.Counts(100, 20)}, fa.parse_threshold(APPROVED)
    )
    assert [f.held for f in found] == [True, False]  # ARABIC, LATIN


def test_a_hold_turns_only_an_automated_non_match_into_a_person() -> None:
    held = frozenset({"CYRILLIC"})
    used, record = apply_hold(SubjectMatch.NOT_MATCHED, "Сергей Иванов", held)
    assert used is SubjectMatch.AMBIGUOUS
    assert record == {"cohort": "CYRILLIC", "automated": "NOT_MATCHED", "held": True}
    assert apply_hold(SubjectMatch.NOT_MATCHED, "Jane Doe", held)[0] is SubjectMatch.NOT_MATCHED
    for other in (
        SubjectMatch.MATCHED,
        SubjectMatch.AMBIGUOUS,
    ):  # nothing else changes: a hold cannot add a pass
        assert apply_hold(other, "Сергей Иванов", held)[0] is other
    assert "Сергей" not in str(record)  # the cohort, never the name
