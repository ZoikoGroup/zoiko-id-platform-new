"""Choosing a face-match threshold from measurements (CR-130)."""

from __future__ import annotations

import random

import pytest
from zid_platform.modules.verification.domain import face_calibration as fc


def pairs(same: list[float], different: list[float]) -> list[fc.Pair]:
    return [fc.Pair(s, True) for s in same] + [fc.Pair(s, False) for s in different]


def test_rates_count_a_score_at_the_threshold_as_a_match():
    fmr, fnmr = fc.rates([0.5, 0.6], [0.1, 0.5], 0.5)
    assert fmr == 0.5 and fnmr == 0.0


def test_the_threshold_for_a_target_lets_through_no_more_than_the_target():
    rng = random.Random(7)
    different = [rng.gauss(0.05, 0.1) for _ in range(2000)]
    for target in (0.1, 0.01, 0.001):
        t = fc.threshold_for_fmr(different, target)
        assert sum(1 for s in different if s >= t) <= int(target * len(different))


def test_a_target_of_one_in_ten_on_ten_scores_lets_one_through():
    different = [0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]
    t = fc.threshold_for_fmr(different, 0.1)
    assert sum(1 for s in different if s >= t) == 1


def test_well_separated_scores_report_as_separable_with_no_errors_at_the_chosen_point():
    rng = random.Random(1)
    same = [rng.gauss(0.7, 0.05) for _ in range(300)]
    different = [rng.gauss(0.05, 0.05) for _ in range(5000)]
    r = fc.report(pairs(same, different))
    assert r["separable"] is True
    point = {row["target_fmr"]: row for row in r["operating_points"]}[0.001]  # type: ignore[union-attr, index]
    assert point["fnmr"] == 0.0 and point["enough_pairs"] is True


def test_overlapping_scores_cost_false_non_matches_and_say_so():
    rng = random.Random(3)
    same = [rng.gauss(0.40, 0.12) for _ in range(500)]
    different = [rng.gauss(0.10, 0.10) for _ in range(4000)]
    r = fc.report(pairs(same, different))
    assert r["separable"] is False
    strict = {row["target_fmr"]: row for row in r["operating_points"]}[0.001]  # type: ignore[union-attr, index]
    loose = {row["target_fmr"]: row for row in r["operating_points"]}[0.1]  # type: ignore[union-attr, index]
    assert (
        strict["threshold"] > loose["threshold"] and strict["fnmr"] > loose["fnmr"]
    )  # a stricter target turns more right people away


def test_a_few_pairs_are_not_enough_to_trust_a_strict_target():
    r = fc.report(pairs([0.6, 0.7, 0.65], [0.1, 0.05, 0.12, 0.2, 0.0]))
    strict = {row["target_fmr"]: row for row in r["operating_points"]}[0.001]  # type: ignore[union-attr, index]
    assert strict["enough_pairs"] is False  # 5 strangers cannot show a one-in-a-thousand rate


def test_no_strangers_let_through_is_still_only_bounded_by_about_three_over_n():
    assert fc.wilson_upper(0, 1000) == pytest.approx(0.0038, abs=0.0005)
    assert fc.wilson_upper(0, 0) == 1.0


@pytest.mark.parametrize(
    ("same", "different", "code"),
    [
        ([0.5], [0.1, 0.2], "TOO_FEW_SAME_PERSON_PAIRS"),
        ([0.5, 0.6], [0.1], "TOO_FEW_DIFFERENT_PERSON_PAIRS"),
        ([0.5, float("nan")], [0.1, 0.2], "SCORE_NOT_A_NUMBER"),
    ],
)
def test_a_set_that_cannot_be_measured_is_refused_with_a_reason(
    same: list[float], different: list[float], code: str
):
    with pytest.raises(fc.CalibrationError) as e:
        fc.report(pairs(same, different))
    assert e.value.code == code


def test_a_target_outside_zero_and_one_is_refused():
    with pytest.raises(fc.CalibrationError):
        fc.threshold_for_fmr([0.1, 0.2], 0)
