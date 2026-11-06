"""A plan's request allowance: the multipliers are validated, a bigger plan never gets less, and 0 means no limit."""

from __future__ import annotations

import pytest

from zid_platform.modules.commercial.domain.throughput import ThroughputError, check, request_limit
from zid_platform.modules.commercial.infrastructure.bands import plan_throughput
from zid_platform.modules.metering.domain.usage import RateLimiter

OK = {"DEVELOPER": 1, "LAUNCH": 2, "BUSINESS": 5, "SCALE": 20, "ENTERPRISE": 0}


def test_the_shipped_multipliers_are_valid_and_start_at_the_base() -> None:
    m = plan_throughput()
    assert m["DEVELOPER"] == 1 and m["ENTERPRISE"] == 0
    assert [m[p] for p in ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE")] == sorted(m[p] for p in ("DEVELOPER", "LAUNCH", "BUSINESS", "SCALE"))


@pytest.mark.parametrize(
    "bad",
    [
        {**OK, "LAUNCH": 0},  # a bigger plan with less
        {**OK, "BUSINESS": 1, "LAUNCH": 2},  # out of order
        {**OK, "DEVELOPER": 0},  # no base
        {**OK, "SCALE": 2.5},  # not whole
        {k: v for k, v in OK.items() if k != "SCALE"},  # a plan missing
    ],
)
def test_bad_multipliers_are_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ThroughputError):
        check(bad)


def test_the_limit_is_the_base_times_the_plan_and_zero_is_unlimited() -> None:
    assert [request_limit(p, 600, OK) for p in OK] == [600, 1200, 3000, 12000, 0]
    assert request_limit("UNKNOWN", 600, OK) == 600  # no plan: the base allowance
    assert request_limit("SCALE", 0, OK) == 0  # a base of 0 turns the limit off for everyone


def test_the_limiter_can_count_against_a_company_s_own_limit() -> None:
    lim = RateLimiter(2)
    assert [lim.check("a", 0.0).allowed for _ in range(3)] == [True, True, False]  # the base limit
    assert [lim.check("b", 0.0, 4).allowed for _ in range(5)] == [True, True, True, True, False]  # its own, larger limit
    assert lim.check("c", 0.0, 0).allowed and lim.peek("c", 0.0, 0).limit == 0  # its own limit: none


def test_the_numbers_are_reported_as_an_engineering_default_until_someone_signs_them_off(monkeypatch, tmp_path) -> None:  # type: ignore[no-untyped-def]
    from zid_platform.modules.commercial.infrastructure import bands

    assert bands.plan_throughput_status() == "ENGINEERING_DEFAULT"  # the shipped file is not signed off
    bands.plan_throughput_status.cache_clear()
    monkeypatch.setattr(bands, "_yaml", lambda name: {"status": "CONFIRMED"} if name == "plan-throughput.yaml" else {})
    assert bands.plan_throughput_status() == "ENGINEERING_DEFAULT"  # CONFIRMED needs who and when too
    bands.plan_throughput_status.cache_clear()
    monkeypatch.setattr(bands, "_yaml", lambda name: {"status": "CONFIRMED", "confirmed_by": "finance", "confirmed_on": "2026-11-05"})
    assert bands.plan_throughput_status() == "CONFIRMED"
    bands.plan_throughput_status.cache_clear()
