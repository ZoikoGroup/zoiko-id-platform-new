"""DTV-19 age assurance: a threshold answer, never a birth date, and uncertainty is never a pass or a failure."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain import age

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
D = NOW.date()


def test_a_verified_adult_is_at_least_18_for_good():
    a = age.from_birth_date(date(1990, 5, 1), 18, on=D, verified=True)
    assert (a.result, a.method, a.valid_until) == (
        age.Result.AT_LEAST,
        age.Method.VERIFIED_BIRTH_DATE,
        None,
    )
    assert age.still_valid(a, NOW + timedelta(days=36500))  # age only goes up


def test_the_birthday_itself_counts_and_the_day_before_does_not():
    born = date(2008, 11, 11)
    assert (
        age.from_birth_date(born, 18, on=date(2026, 11, 11), verified=True).result
        is age.Result.AT_LEAST
    )
    under = age.from_birth_date(born, 18, on=date(2026, 11, 10), verified=True)
    assert under.result is age.Result.UNDER
    assert under.valid_until == datetime(2026, 11, 11, tzinfo=UTC)  # lapses the day they turn 18
    assert age.still_valid(under, datetime(2026, 11, 10, 23, tzinfo=UTC)) and not age.still_valid(
        under, datetime(2026, 11, 11, tzinfo=UTC)
    )


def test_a_leap_day_birthday_is_taken_as_1_march_in_a_common_year():
    born = date(2008, 2, 29)
    assert age.birthday(born, 18) == date(2026, 3, 1)
    assert (
        age.from_birth_date(born, 18, on=date(2026, 2, 28), verified=True).result
        is age.Result.UNDER
    )
    assert (
        age.from_birth_date(born, 18, on=date(2026, 3, 1), verified=True).result
        is age.Result.AT_LEAST
    )


def test_a_self_declared_date_never_gives_a_pass_and_offers_a_documented_check():
    a = age.from_birth_date(date(1970, 1, 1), 18, on=D, verified=False)
    assert a.result is age.Result.UNDETERMINED and a.method is age.Method.SELF_DECLARED
    assert a.fallback_offered and a.reason_codes == ("BIRTH_DATE_NOT_VERIFIED",)


def test_only_policy_defined_thresholds_are_allowed_and_a_future_birth_date_is_refused():
    for t in (0, 17, 19, 99):
        with pytest.raises(age.AgeError) as e:
            age.from_birth_date(date(1990, 1, 1), t, on=D, verified=True)
        assert e.value.code == "AGE_THRESHOLD_NOT_ALLOWED"
    with pytest.raises(age.AgeError) as e:
        age.from_birth_date(date(2030, 1, 1), 18, on=D, verified=True)
    assert e.value.code == "BIRTH_DATE_IN_THE_FUTURE"


@pytest.mark.parametrize(
    ("estimate", "margin", "threshold", "result"),
    [
        (35.0, 3.0, 18, age.Result.AT_LEAST),  # 32 clears 18 + the 2 year buffer
        (22.0, 3.0, 18, age.Result.UNDETERMINED),  # 19 is inside the buffer
        (18.0, 2.0, 18, age.Result.UNDETERMINED),  # right on the line
        (15.0, 2.0, 16, age.Result.UNDETERMINED),  # 17 is not clearly under 16 and not clearly over
        (9.0, 2.0, 16, age.Result.UNDER),  # 11 is clearly under 16
    ],
)
def test_an_estimate_decides_only_when_its_whole_margin_and_the_buffer_clear_the_line(
    estimate, margin, threshold, result
):
    a = age.from_estimate(estimate, margin, threshold, now=NOW)
    assert a.result is result
    assert a.method is age.Method.ESTIMATION
    assert a.valid_until == NOW + age.ESTIMATE_TTL  # an estimate lapses within a day
    assert a.fallback_offered is (result is age.Result.UNDETERMINED)


def test_an_estimate_near_the_line_is_not_a_failure_it_offers_a_documented_check():
    a = age.from_estimate(19.0, 1.5, 18, now=NOW)
    assert (
        a.result is age.Result.UNDETERMINED
        and a.reason_codes == ("ESTIMATE_TOO_CLOSE_TO_THE_LINE",)
        and a.fallback_offered
    )


def test_a_bad_estimate_is_refused():
    for est, margin in ((-1, 1), (20, -1)):
        with pytest.raises(age.AgeError) as e:
            age.from_estimate(est, margin, 18, now=NOW)
        assert e.value.code == "ESTIMATE_INVALID"


def test_a_verified_birth_date_beats_an_estimate_and_disagreement_is_never_a_pass():
    exact = age.from_birth_date(date(2010, 1, 1), 18, on=D, verified=True)  # clearly under
    guess = age.from_estimate(40, 2, 18, now=NOW)  # says adult
    assert age.combine([guess, exact]).result is age.Result.UNDER
    a = age.from_estimate(40, 2, 18, now=NOW)
    b = age.from_estimate(5, 1, 18, now=NOW)
    clash = age.combine([a, b])
    assert clash.result is age.Result.UNDETERMINED and clash.reason_codes == ("EVIDENCE_CONFLICTS",)
    assert age.combine([a, a]).result is age.Result.AT_LEAST
    near = age.from_estimate(19, 1, 18, now=NOW)
    only_unclear = age.combine([near, age.from_estimate(20, 1, 18, now=NOW)])
    assert only_unclear.result is age.Result.UNDETERMINED and only_unclear.reason_codes == (
        "NO_DECISIVE_EVIDENCE",
    )


def test_combine_refuses_nothing_and_mixed_thresholds():
    with pytest.raises(age.AgeError) as e:
        age.combine([])
    assert e.value.code == "NO_EVIDENCE"
    with pytest.raises(age.AgeError) as e:
        age.combine([age.from_estimate(40, 1, 18, now=NOW), age.from_estimate(40, 1, 21, now=NOW)])
    assert e.value.code == "MIXED_THRESHOLDS"


def test_the_contract_never_carries_a_birth_date_an_age_or_an_estimate():
    for a in (
        age.from_birth_date(date(1990, 5, 1), 18, on=D, verified=True),
        age.from_birth_date(date(2012, 5, 1), 18, on=D, verified=True),
        age.from_estimate(37.4, 2.1, 18, now=NOW),
    ):
        flat = str(a.to_contract())
        for secret in ("1990", "2012", "37.4", "2.1", "birth", "dob"):
            assert secret not in flat.replace("2026", ""), secret
        assert set(a.to_contract()) == {
            "threshold",
            "result",
            "method",
            "valid_until",
            "reason_codes",
            "fallback_offered",
        }
