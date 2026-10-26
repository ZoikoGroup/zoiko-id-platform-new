"""Budgets, included-quota warnings and projection (ZID-COM-001 §7; CR-029). Pure rules."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import budgets as bg

D = Decimal
START = datetime(2026, 10, 1, tzinfo=UTC)
END = datetime(2026, 11, 1, tzinfo=UTC)


def test_thresholds_are_checked_sorted_and_deduplicated() -> None:
    assert bg.check_thresholds([100, 50, 80, 50]) == (50, 80, 100)
    for bad in ([], [0], [201], [10, 20, 30, 40, 50, 60]):
        with pytest.raises(bg.BudgetError):
            bg.check_thresholds(bad)


def test_crossings_are_inclusive_and_a_zero_limit_trips_at_once() -> None:
    assert bg.crossed(D(49), D(100), (50, 80, 100)) == []
    assert bg.crossed(D(50), D(100), (50, 80, 100)) == [50]
    assert bg.crossed(D(100), D(100), (50, 80, 100)) == [50, 80, 100]
    assert bg.crossed(D(1), D(0), (50, 80, 100)) == [50, 80, 100]
    assert bg.crossed(D(0), D(0), (50, 80, 100)) == []


def test_quota_warnings_skip_negotiated_and_absent_allowances() -> None:
    out = bg.quota_crossings(
        {"MAH": D(1500), "AUTH_EVENT": D(10), "VC_ISSUED": D(9)},
        {"MAH": D(2500), "AUTH_EVENT": D(25_000), "POLICY_DECISION": None},
    )
    assert out == {
        "MAH": [50]
    }  # 60% of the allowance; VC meters have no allowance, POLICY is negotiated
    out = bg.quota_crossings({"MAH": D(2000)}, {"MAH": D(2500)})
    assert out == {"MAH": [50, 80]}


def test_the_projection_waits_six_hours_and_is_low_confidence_for_three_days() -> None:
    assert bg.projection_status(START, START + timedelta(hours=5)) == "NONE"
    assert bg.projection_status(START, START + timedelta(hours=7)) == "LOW"
    assert bg.projection_status(START, START + timedelta(days=4)) == "NORMAL"


def test_projection_scales_usage_by_the_elapsed_share_and_rounds_up() -> None:
    half = bg.elapsed_fraction(START, END, START + (END - START) / 2)
    assert half == D("0.5")
    assert bg.project_usage({"MAH": D(1001), "AUTH_EVENT": D(0)}, half) == {
        "MAH": D(2002),
        "AUTH_EVENT": D(0),
    }
    third = bg.elapsed_fraction(START, END, START + (END - START) / 3)
    assert bg.project_usage({"MAH": D(10)}, third) == {"MAH": D(30)}
    assert bg.elapsed_fraction(START, END, START - timedelta(days=1)) == 0
    assert bg.elapsed_fraction(START, END, END + timedelta(days=1)) == 1
