"""The price of a Verify API usage fact and the indicative cost of a month (CR-161; decision D12)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from zid_platform.modules.verify_api.domain import pricing as p
from zid_platform.modules.verify_api.domain.model import PartnerError

MINOR = {"USD": 2, "EUR": 2, "GBP": 2, "JPY": 0}
TODAY = date(2026, 11, 25)


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(PartnerError) as e:
        fn(*a, **k)
    return e.value.code


GOOD = {
    "meter": "VERIFY_CHECK_COMPLETE",
    "currency": "USD",
    "unit_rate": "1.25",
    "effective_from": "2026-12-01",
}


def test_a_proposal_is_cleaned_and_starts_on_a_month_that_has_not_begun() -> None:
    out = p.check_price(GOOD, today=TODAY, minor_units=MINOR)
    assert out == {
        "meter": "VERIFY_CHECK_COMPLETE",
        "currency": "USD",
        "unit_rate": Decimal("1.25"),
        "effective_from": date(2026, 12, 1),
    }
    assert p.first_of_next_month(date(2026, 12, 31)) == date(2027, 1, 1)
    # this month, or any earlier month, is refused: nothing already used is repriced
    for early in ("2026-11-01", "2026-10-01", "2020-01-01"):
        assert (
            code(p.check_price, {**GOOD, "effective_from": early}, today=TODAY, minor_units=MINOR)
            == "PRICE_NOT_IN_FUTURE"
        )
    assert (
        code(
            p.check_price, {**GOOD, "effective_from": "2026-12-15"}, today=TODAY, minor_units=MINOR
        )
        == "VALIDATION_FAILED"
    )
    assert (
        code(p.check_price, {**GOOD, "effective_from": "soon"}, today=TODAY, minor_units=MINOR)
        == "VALIDATION_FAILED"
    )


def test_a_price_is_a_decimal_string_in_a_known_currency_for_a_known_usage_fact() -> None:
    for bad in (
        {**GOOD, "meter": "SOMETHING_ELSE"},
        {**GOOD, "currency": "XYZ"},
        {**GOOD, "currency": "usd"},
        {**GOOD, "unit_rate": 1.25},  # a float is refused outright
        {**GOOD, "unit_rate": None},
        {**GOOD, "unit_rate": "abc"},
        {**GOOD, "unit_rate": "-1"},
        {**GOOD, "unit_rate": "NaN"},
        {**GOOD, "unit_rate": "100000.01"},
        {**GOOD, "unit_rate": "0.0000001"},
    ):
        assert code(p.check_price, bad, today=TODAY, minor_units=MINOR) == "VALIDATION_FAILED", bad
    assert p.check_price({**GOOD, "unit_rate": "0"}, today=TODAY, minor_units=MINOR)[
        "unit_rate"
    ] == Decimal(0)
    assert p.check_price({**GOOD, "unit_rate": 2}, today=TODAY, minor_units=MINOR)[
        "unit_rate"
    ] == Decimal(2)


def test_the_price_in_force_is_the_latest_one_that_started_and_never_a_later_one() -> None:
    first = p.Price("VERIFY_CHECK_COMPLETE", "USD", Decimal("1.00"), date(2026, 12, 1))
    second = p.Price("VERIFY_CHECK_COMPLETE", "USD", Decimal("1.50"), date(2027, 2, 1))
    other = p.Price("VERIFY_CHECK_COMPLETE", "EUR", Decimal("0.90"), date(2026, 12, 1))
    prices = [first, second, other]
    assert p.price_on(prices, "VERIFY_CHECK_COMPLETE", "USD", date(2026, 11, 1)) is None
    assert p.price_on(prices, "VERIFY_CHECK_COMPLETE", "USD", date(2026, 12, 1)) is first
    assert p.price_on(prices, "VERIFY_CHECK_COMPLETE", "USD", date(2027, 1, 1)) is first
    assert p.price_on(prices, "VERIFY_CHECK_COMPLETE", "USD", date(2027, 2, 1)) is second
    assert p.price_on(prices, "VERIFY_SESSION_START", "USD", date(2027, 2, 1)) is None


def test_the_cost_is_exact_decimal_rounded_once_per_line_and_says_when_it_is_incomplete() -> None:
    prices = [
        p.Price("VERIFY_CHECK_COMPLETE", "USD", Decimal("1.255"), date(2026, 12, 1)),
        p.Price("VERIFY_SESSION_START", "USD", Decimal("0.10"), date(2026, 12, 1)),
        p.Price("VERIFY_CHECK_COMPLETE", "JPY", Decimal(150), date(2026, 12, 1)),
    ]
    out = p.indicative_cost(
        prices,
        {"VERIFY_SESSION_START": 7, "VERIFY_CHECK_COMPLETE": 3},
        month_start=date(2026, 12, 1),
        minor_units=MINOR,
    )
    usd = next(c for c in out if c["currency"] == "USD")
    assert (
        usd["complete"] is True and usd["total"] == "4.46"
    )  # 0.70 + round(3 x 1.255 = 3.765 -> 3.76, half to even)
    assert [(ln["meter"], ln["units"], ln["unit_rate"], ln["amount"]) for ln in usd["lines"]] == [
        ("VERIFY_SESSION_START", 7, "0.1", "0.70"),
        ("VERIFY_CHECK_COMPLETE", 3, "1.255", "3.76"),
    ]
    jpy = next(c for c in out if c["currency"] == "JPY")
    # sessions started have no JPY price but there is usage: the total is not the whole amount, and it says so
    assert jpy["complete"] is False and jpy["total"] == "450"
    # no usage for an unpriced meter is not a gap
    quiet = p.indicative_cost(
        prices, {"VERIFY_CHECK_COMPLETE": 2}, month_start=date(2026, 12, 1), minor_units=MINOR
    )
    assert next(c for c in quiet if c["currency"] == "JPY")["complete"] is True
    assert all(
        isinstance(ln["amount"], str) for c in out for ln in c["lines"]
    )  # money leaves as text, never a float


def test_a_month_before_any_price_has_no_cost_and_a_note_is_bounded() -> None:
    prices = [p.Price("VERIFY_CHECK_COMPLETE", "USD", Decimal(1), date(2026, 12, 1))]
    assert (
        p.indicative_cost(
            prices, {"VERIFY_CHECK_COMPLETE": 5}, month_start=date(2026, 11, 1), minor_units=MINOR
        )
        == []
    )
    assert code(p.check_note, " ", required=True) == "NOTE_REQUIRED"
    assert p.check_note(None, required=False) == ""
    assert code(p.check_note, "x" * 1001, required=False) == "VALIDATION_FAILED"
