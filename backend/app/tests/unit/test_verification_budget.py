"""A company's monthly verification spend budget as pure rules (ZID-DTV-WF-01 WF01-014; CR-107)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from zid_platform.modules.verification.domain import budget as bg
from zid_platform.modules.verification.domain.verification import VerificationError


def test_a_budget_is_a_decimal_at_or_above_zero_in_a_three_letter_currency() -> None:
    assert bg.parse_budget("1500.25", "EUR") == bg.Budget(Decimal("1500.25"), "EUR")
    assert bg.parse_budget(0, "GBP").monthly_limit == 0  # none this month is a real budget
    assert bg.parse_budget("0.000001", "USD").monthly_limit == Decimal("0.000001")
    for limit in (
        -1,
        "-0.01",
        "abc",
        "",
        "1e3x",
        "NaN",
        "Infinity",
        1.5,
        True,
        None,
        [],
        "0.0000001",
        "1" + "0" * 20,
    ):
        with pytest.raises(VerificationError) as e:
            bg.parse_budget(limit, "EUR")
        assert e.value.code == "BUDGET_INVALID", limit
    for currency in ("eur", "EU", "EURO", "", None, 3):
        with pytest.raises(VerificationError):
            bg.parse_budget("10", currency)


def test_no_budget_means_no_check_and_no_default_amount() -> None:
    bg.require_within_budget(None, {"EUR": Decimal(999999)})


def test_spend_at_or_over_the_limit_stops_new_verifications_and_under_it_does_not() -> None:
    b = bg.Budget(Decimal(100), "EUR")
    bg.require_within_budget(b, {})
    bg.require_within_budget(b, {"EUR": Decimal("99.999999")})
    for spent in ("100", "100.000001", "5000"):
        with pytest.raises(VerificationError) as e:
            bg.require_within_budget(b, {"EUR": Decimal(spent)})
        assert e.value.code == "VERIFICATION_BUDGET_EXCEEDED"
    with pytest.raises(VerificationError):
        bg.require_within_budget(bg.Budget(Decimal(0), "EUR"), {})  # a zero budget starts nothing


def test_only_the_budgets_own_currency_counts_and_the_rest_is_reported_never_converted() -> None:
    b = bg.Budget(Decimal(100), "EUR")
    spent = {"EUR": Decimal(10), "USD": Decimal(9999), "GBP": Decimal(0)}
    bg.require_within_budget(b, spent)  # no exchange rate is invented
    assert bg.spent_in(b, spent) == 10
    assert bg.other_currencies(b, spent) == ["USD"]  # a zero amount is not reported


def test_the_month_starts_at_midnight_utc_on_the_first() -> None:
    assert bg.month_start(datetime(2026, 11, 17, 23, 59, 59, 5, tzinfo=UTC)) == datetime(
        2026, 11, 1, tzinfo=UTC
    )
    assert bg.month_start(datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)) == datetime(
        2026, 12, 1, tzinfo=UTC
    )
