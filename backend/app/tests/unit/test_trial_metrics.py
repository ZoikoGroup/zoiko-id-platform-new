"""Raw biometric trial results to the measures and intervals the model gate reads (Document 04 PR-15, PR-16)."""

from __future__ import annotations

import pytest
from zid_platform.modules.privacy.domain import models
from zid_platform.modules.privacy.domain import trial_metrics as tm

# ------------------------------------------------------------------------------------------------- the interval itself


@pytest.mark.parametrize(
    ("events", "trials", "low", "high"),
    [
        (5, 10, 0.187086, 0.812914),  # the textbook exact 95% interval for 5 of 10
        (0, 10, 0.0, 0.308490),  # one minus the 40th root... (0.025 ** 0.1)
        (10, 10, 0.691510, 1.0),
        (1, 20, 0.001266, 0.248733),
        (3, 100, 0.006229, 0.085168),
    ],
)
def test_the_interval_matches_known_exact_binomial_values(events, trials, low, high) -> None:
    got_low, got_high = tm.clopper_pearson(events, trials)
    assert got_low == pytest.approx(low, abs=2e-5)
    assert got_high == pytest.approx(high, abs=2e-5)


def test_the_interval_always_contains_the_observed_rate_and_widens_as_the_sample_shrinks() -> None:
    for events, trials in [(0, 5), (2, 7), (7, 7), (50, 1000)]:
        low, high = tm.clopper_pearson(events, trials)
        assert 0.0 <= low <= events / trials <= high <= 1.0
    small, large = tm.clopper_pearson(1, 10), tm.clopper_pearson(100, 1000)
    assert (small[1] - small[0]) > (large[1] - large[0])


def test_a_higher_confidence_gives_a_wider_interval() -> None:
    narrow, wide = tm.clopper_pearson(5, 50, 0.90), tm.clopper_pearson(5, 50, 0.99)
    assert wide[0] < narrow[0] and wide[1] > narrow[1]


@pytest.mark.parametrize("args", [(-1, 10), (11, 10), (0, 0), (1, -3)])
def test_impossible_counts_are_refused(args) -> None:
    with pytest.raises(tm.TrialError):
        tm.clopper_pearson(*args)


def test_a_confidence_that_is_not_a_confidence_is_refused() -> None:
    for bad in (0.5, 1.0, 0.0, 1.5):
        with pytest.raises(tm.TrialError):
            tm.clopper_pearson(1, 10, bad)


# ------------------------------------------------------------------------------------------------------------ PAD


def pad(attack, accepted, species=None, acquired=True, cohort=None):
    return {
        "attack": attack,
        "accepted": accepted,
        "species": species,
        "acquired": acquired,
        "cohort": cohort,
    }


def sample_pad() -> list[dict]:
    out = []
    out += [pad(True, i < 2, "print") for i in range(100)]  # 2 of 100 accepted
    out += [pad(True, i < 7, "replay") for i in range(100)]  # 7 of 100 accepted: the worst
    out += [pad(False, i >= 3) for i in range(200)]  # 3 of 200 bona fide rejected
    out += [pad(False, True, acquired=False) for _ in range(10)]  # nothing acquired
    return out


def test_apcer_is_reported_per_species_with_the_worst_as_the_headline_never_averaged() -> None:
    m = tm.pad_measures(sample_pad())
    assert set(m["apcer_by_species"]) == {"print", "replay"}
    assert m["apcer"]["species"] == "replay" and m["apcer"]["value"] == pytest.approx(0.07)
    assert m["apcer_by_species"]["print"]["value"] == pytest.approx(0.02)
    assert m["apcer"]["value"] > (0.02 + 0.07) / 2, (
        "an average would have hidden the weaker defence"
    )


def test_bpcer_and_failure_to_acquire_are_separate_and_unacquired_trials_do_not_dilute_the_errors() -> (
    None
):
    m = tm.pad_measures(sample_pad())
    assert m["bpcer"]["events"] == 3 and m["bpcer"]["trials"] == 200
    assert m["failure_to_acquire"]["events"] == 10 and m["failure_to_acquire"]["trials"] == 410
    assert m["apcer"]["trials"] == 100, "only acquired presentations count in an error rate"


def test_the_pad_output_is_exactly_what_the_model_gate_accepts() -> None:
    assert models.evaluation_problems("pad", tm.pad_measures(sample_pad())) == []


def test_a_pad_evaluation_with_nothing_to_measure_is_refused_not_defaulted() -> None:
    with pytest.raises(tm.TrialError) as e:
        tm.pad_measures([])
    assert e.value.code == "NO_TRIALS"
    with pytest.raises(tm.TrialError) as e:
        tm.pad_measures([pad(False, True) for _ in range(5)])
    assert e.value.code == "NO_ATTACK_PRESENTATIONS"
    with pytest.raises(tm.TrialError) as e:
        tm.pad_measures([pad(True, False, "print") for _ in range(5)])
    assert e.value.code == "NO_BONA_FIDE_PRESENTATIONS"


def test_an_attack_with_no_species_is_refused_so_a_weak_species_cannot_hide() -> None:
    rows = [pad(True, False, None), pad(False, True)]
    with pytest.raises(tm.TrialError) as e:
        tm.pad_measures(rows)
    assert e.value.code == "ATTACK_SPECIES_MISSING"


def test_a_trial_flag_that_is_not_a_real_boolean_is_refused() -> None:
    rows = [{"attack": "yes", "accepted": 1, "acquired": True, "species": "print"}]
    with pytest.raises(tm.TrialError) as e:
        tm.pad_measures(rows)
    assert e.value.code == "TRIAL_FIELD_NOT_BOOLEAN"


# ------------------------------------------------------------------------------------------------------ face match


def comparison(genuine, accepted, acquired=True, cohort=None):
    return {"genuine": genuine, "accepted": accepted, "acquired": acquired, "cohort": cohort}


def test_face_match_measures_are_fmr_fnmr_and_failure_to_acquire_and_pass_the_gate_shape() -> None:
    rows = [comparison(False, i < 1) for i in range(1000)]  # 1 of 1000 impostors accepted
    rows += [comparison(True, i >= 20) for i in range(500)]  # 20 of 500 genuine rejected
    rows += [comparison(True, False, acquired=False) for _ in range(5)]
    m = tm.face_match_measures(rows)
    assert m["fmr"]["events"] == 1 and m["fmr"]["trials"] == 1000
    assert m["fnmr"]["events"] == 20 and m["fnmr"]["trials"] == 500
    assert m["failure_to_acquire"]["events"] == 5
    assert models.evaluation_problems("face_match", m) == []


def test_face_match_needs_both_kinds_of_comparison() -> None:
    with pytest.raises(tm.TrialError):
        tm.face_match_measures([comparison(True, True)])
    with pytest.raises(tm.TrialError):
        tm.face_match_measures([comparison(False, False)])


# -------------------------------------------------------------------------------------------------------- cohorts


def test_cohort_differences_say_distinguishable_only_when_the_intervals_do_not_overlap() -> None:
    rows = [pad(False, i >= 1, cohort="A") for i in range(500)]  # BPCER 0.2%
    rows += [pad(False, i >= 60, cohort="B") for i in range(500)]  # BPCER 12%
    d = tm.pad_cohort_differences(
        rows
        + [pad(True, False, "print", cohort="A")] * 50
        + [pad(True, False, "print", cohort="B")] * 50
    )
    (pair,) = d["bpcer"]["pairs"]
    assert pair["cohorts"] == ["A", "B"] and pair["distinguishable"] is True
    assert pair["difference"] == pytest.approx(0.118)
    assert d["bpcer"]["smallest_cohort_trials"] == 500


def test_a_small_sample_is_never_reported_as_no_difference() -> None:
    rows = [pad(False, True, cohort="A")] * 9 + [pad(False, False, cohort="A")]
    rows += [pad(False, True, cohort="B")] * 5 + [pad(False, False, cohort="B")] * 5
    rows += [pad(True, False, "print", cohort="A"), pad(True, False, "print", cohort="B")]
    d = tm.pad_cohort_differences(rows)
    (pair,) = d["bpcer"]["pairs"]
    assert pair["difference"] == pytest.approx(0.4) and pair["distinguishable"] is False
    assert d["bpcer"]["smallest_cohort_trials"] == 10, "the sample size travels with the verdict"


def test_cohort_review_needs_a_cohort_on_every_trial_and_at_least_two_cohorts() -> None:
    with pytest.raises(tm.TrialError) as e:
        tm.pad_cohort_differences([pad(False, True, cohort=None), pad(False, True, cohort="A")])
    assert e.value.code == "COHORT_MISSING"
    with pytest.raises(tm.TrialError) as e:
        tm.pad_cohort_differences([pad(False, True, cohort="A"), pad(True, False, "p", cohort="A")])
    assert e.value.code == "NEED_AT_LEAST_TWO_COHORTS"


def test_no_threshold_or_verdict_is_produced_anywhere() -> None:
    m = tm.pad_measures(sample_pad())
    flat = repr(m).lower()
    for word in ("pass", "fail_", "acceptable", "compliant", "threshold"):
        assert word not in flat
