"""Family rates and the public estimate (ZID-UX-PRICE-004 sections 06.1, 06.2): exact decimals, a minimum that is either a floor or a fee and never both, no mixing of currencies, and a hash of exactly
what was priced. The amounts below are test fixtures, not product prices."""

from __future__ import annotations

from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import family_rates as fr

D = Decimal


def rate(**over: object) -> fr.Rate:
    base: dict[str, object] = {
        "family": "API", "country": "DE", "period": "MONTHLY", "currency": "EUR", "charge_unit": "completed check", "included_units": 100,
        "unit_amount": D("0.50"), "minimum_amount": None, "minimum_model": None,
    }  # fmt: skip
    return fr.Rate(**{**base, **over})  # type: ignore[arg-type]


def test_usage_above_the_included_units_is_charged_at_the_unit_amount() -> None:
    line = fr.charge(rate(), 1000)
    assert (line.billable_units, line.usage, line.amount) == (900, D("450.00"), D("450.00"))
    assert fr.charge(rate(), 100).amount == D("0.00") and fr.charge(rate(), 101).amount == D(
        "0.50"
    ), "the allowance boundary is exact"
    assert fr.charge(rate(), 0).amount == D("0.00")


def test_a_floor_is_the_greater_of_the_minimum_and_usage_never_the_sum() -> None:
    floor = rate(minimum_amount=D("49"), minimum_model="FLOOR")
    assert fr.charge(floor, 110).amount == D("49.00"), "usage of 5.00 is below the 49.00 floor"
    assert fr.charge(floor, 1000).amount == D("450.00"), (
        "usage above the floor is just usage, not floor plus usage"
    )
    assert "the greater of the EUR 49.00 minimum spend" in fr.charge(floor, 110).formula


def test_a_platform_fee_is_billed_in_addition_to_usage_and_says_so() -> None:
    fee = rate(minimum_amount=D("49"), minimum_model="PLATFORM_FEE")
    assert fr.charge(fee, 100).amount == D("49.00")
    assert fr.charge(fee, 1000).amount == D("499.00")
    assert "platform fee plus usage" in fr.charge(fee, 1000).formula


def test_a_zero_decimal_currency_is_exact_and_an_undeclared_currency_is_refused_not_guessed() -> (
    None
):
    assert fr.charge(rate(currency="JPY", unit_amount=D("2.5"), included_units=0), 3).amount == D(
        "8"
    ), "7.5 yen rounds half to even, once, on the line"
    with pytest.raises(fr.RateError) as e:
        fr.charge(rate(currency="XYZ"), 10)
    assert e.value.code == "CURRENCY_UNSUPPORTED"


@pytest.mark.parametrize("volume", [-1, 1_000_000_001, True, 1.5, "10"])
def test_a_volume_is_a_whole_number_in_bounds(volume: object) -> None:
    with pytest.raises(fr.RateError) as e:
        fr.charge(rate(), volume)  # type: ignore[arg-type]
    assert e.value.code == "VOLUME_INVALID"


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"period": "WEEKLY"}, "PERIOD_INVALID"),
        ({"currency": "ZZZ"}, "CURRENCY_UNSUPPORTED"),
        ({"included_units": -1}, "INCLUDED_NEGATIVE"),
        ({"unit_amount": D("-0.1")}, "UNIT_AMOUNT_NEGATIVE"),
        ({"minimum_amount": D("49")}, "MINIMUM_MODEL_REQUIRED"),
        ({"minimum_model": "FLOOR"}, "MINIMUM_AMOUNT_REQUIRED"),
        ({"minimum_amount": D("0"), "minimum_model": "FLOOR"}, "MINIMUM_NOT_POSITIVE"),
        ({"minimum_amount": D("5"), "minimum_model": "BOTH"}, "MINIMUM_MODEL_INVALID"),
        ({"unit_amount": D("0")}, "NO_PRICE"),
        ({"charge_unit": " x "}, "CHARGE_UNIT_MISSING"),
    ],
)
def test_a_rate_line_is_refused_for_the_named_reason(
    change: dict[str, object], problem: str
) -> None:
    assert problem in fr.rate_problems(rate(**change))


def test_a_rate_with_only_a_minimum_is_a_valid_flat_price() -> None:
    flat = rate(unit_amount=D("0"), minimum_amount=D("99"), minimum_model="PLATFORM_FEE")
    assert fr.rate_problems(flat) == [] and fr.charge(flat, 5000).amount == D("99.00")


def test_totals_never_add_across_currencies() -> None:
    lines = [
        fr.charge(rate(), 1000),
        fr.charge(rate(family="DOC", currency="USD", unit_amount=D("1")), 200),
        fr.charge(rate(family="IDV"), 200),
    ]
    assert fr.totals(lines) == {"EUR": D("500.00"), "USD": D("100.00")}


def test_an_annual_saving_is_claimed_only_when_two_rates_in_one_currency_compute_it() -> None:
    monthly = rate()
    annual = rate(period="ANNUAL", included_units=1200, unit_amount=D("0.40"))
    saving = fr.annual_comparison(monthly, annual, 1000)
    assert saving == {
        "currency": "EUR",
        "twelve_months": "5400.00",
        "annual": "4320.00",
        "saving": "1080.00",
    }
    assert fr.annual_comparison(monthly, rate(period="ANNUAL", currency="USD"), 1000) is None, (
        "different currencies are never compared"
    )
    dearer = rate(period="ANNUAL", included_units=1200, unit_amount=D("0.60"))
    assert fr.annual_comparison(monthly, dearer, 1000) is None, (
        "no saving is claimed when the annual rate is not cheaper"
    )


def test_the_quote_hash_is_stable_for_the_same_inputs_and_changes_with_any_input() -> None:
    scope = {"families": {"API": 1000}, "period": "MONTHLY", "payer_country": "DE"}
    lines = [fr.charge(rate(), 1000)]
    base = fr.quote_hash(scope, 3, lines)
    assert len(base) == 64 and base == fr.quote_hash(dict(reversed(list(scope.items()))), 3, lines)
    assert base != fr.quote_hash(scope, 4, lines), "another card version"
    assert base != fr.quote_hash({**scope, "payer_country": "FR"}, 3, lines), "another market"
    assert base != fr.quote_hash(scope, 3, [fr.charge(rate(), 1001)]), "another volume"
