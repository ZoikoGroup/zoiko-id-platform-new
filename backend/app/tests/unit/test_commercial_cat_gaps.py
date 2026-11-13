"""Acceptance tests of ZID-COM-WF-2026-11 section 15 that had no test: CAT-07 (zero-decimal currency) and CAT-36 (a paid, denied outcome stays denied).

These are engineering evidence, not independent execution: gate G5 still needs Independent QA to run them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from zid_platform.modules.commercial.application.adapters import from_minor_units, to_minor_units
from zid_platform.modules.commercial.domain import chargeability as ch
from zid_platform.modules.commercial.domain import vendor as v
from zid_platform.modules.commercial.domain.rating import (
    PlanTerms,
    PriceLine,
    Segment,
    rate,
)

D = Decimal
START, END = datetime(2026, 10, 1, tzinfo=UTC), datetime(2026, 11, 1, tzinfo=UTC)
MID = datetime(2026, 10, 16, 12, tzinfo=UTC)


def jpy_segment(start: datetime = START, end: datetime = END) -> Segment:
    plan = PlanTerms("BUSINESS", D(150000), False, {"MAH": D(1000), "AUTH_EVENT": D(100000)}, 0)
    prices = {
        "ZID-BASE": PriceLine(D(150000)),
        "ZID-HUM": PriceLine(D("2.5"), 1),
        "ZID-AUTH": PriceLine(D("40"), 1000),
    }
    return Segment(plan, start, end, prices)


def test_cat07_a_zero_decimal_currency_rates_in_whole_yen_and_the_quote_and_invoice_amounts_are_identical() -> (
    None
):
    usage = [{"MAH": D(1337), "AUTH_EVENT": D(100_333)}]
    out = rate(
        currency="JPY",
        period_start=START,
        period_end=END,
        segments=[jpy_segment()],
        usage=usage,
        connections={},
    )
    # every line is a whole number of yen; nothing carries a fraction a processor would have to round
    assert all(ln.net == ln.net.to_integral_value() for ln in out.lines)
    assert (
        out.subtotal == sum((ln.net for ln in out.lines), D(0)) == D("150855")
    )  # 150000 + 337 x 2.5 = 842.5 -> 842 (half-even) + 333/1000 x 40 = 13.32 -> 13
    # what the quote shows, what the invoice carries and what the processor is asked to charge are the same whole number
    assert to_minor_units(out.subtotal, "JPY") == int(out.subtotal)
    assert from_minor_units(to_minor_units(out.subtotal, "JPY"), "JPY") == out.subtotal
    again = rate(
        currency="JPY",
        period_start=START,
        period_end=END,
        segments=[jpy_segment()],
        usage=usage,
        connections={},
    )
    assert again.subtotal == out.subtotal, "the same inputs always give the same amount"


def test_cat07_half_a_yen_rounds_to_even_once_per_line_and_a_fraction_is_never_sent_to_the_processor() -> (
    None
):
    out = rate(
        currency="JPY",
        period_start=START,
        period_end=END,
        segments=[jpy_segment()],
        usage=[{"MAH": D(1003)}],
        connections={},
    )
    overage = next(ln for ln in out.lines if ln.meter == "MAH")
    assert overage.net == D(8), "3 x 2.5 = 7.5 yen rounds half-to-even to 8, once, on the line"
    with pytest.raises(ValueError):
        to_minor_units(D("7.5"), "JPY")  # a fraction of a yen is refused, never silently rounded


def test_cat07_a_mid_month_change_prorates_to_whole_yen() -> None:
    half = rate(
        currency="JPY",
        period_start=START,
        period_end=END,
        segments=[jpy_segment(START, MID), jpy_segment(MID, END)],
        usage=[{}, {}],
        connections={},
    )
    assert half.subtotal == D(150000) and all(
        ln.net == ln.net.to_integral_value() for ln in half.lines
    )


def test_cat36_a_denied_outcome_is_billed_as_the_check_that_was_run_and_is_never_turned_into_a_success() -> (
    None
):
    # the billing outcome follows the provider's work, not the verdict, and nothing in billing can change the verdict
    for status in ("VERIFIED", "NOT_VERIFIED", "FAILED"):
        assert v.classify(status, []) == "COMPLETED"
    paid = v.price_attempt(
        v.Rate(D(10), D(5), "EUR", "SIGNED_TARIFF"), v.classify("NOT_VERIFIED", []), 1
    )
    assert paid.chargeable is True and paid.outcome == "COMPLETED"
    # a provider fault is never chargeable even when the verdict was denied: the customer does not pay for our failure
    assert (
        v.price_attempt(
            v.Rate(D(10), D(5), "EUR", "SIGNED_TARIFF"), v.classify("FAILED", ["PROVIDER_ERROR"]), 1
        ).chargeable
        is False
    )
    # the billing records carry no field that could change the verdict
    assert not any(
        hasattr(paid, name) for name in ("decision", "verdict", "decision_status", "verified")
    )


def test_cat36_the_chargeability_decision_has_no_payment_input_so_payment_cannot_flip_an_outcome() -> (
    None
):
    scenario_fields = set(ch.SkuPolicy.__dataclass_fields__)
    assert not {n for n in scenario_fields if "paid" in n or "payment" in n or "verified" in n}, (
        scenario_fields
    )
    out = ch.decide(
        ch.Scenario.IDENTITY_COMPLETED, environment="LIVE", policy=ch.SkuPolicy(unit_disclosed=True)
    )
    assert out.billable is True and out.treatment == "CHARGE", (
        "completed counts under the disclosed policy whether verified or rejected"
    )
    assert (
        ch.decide(
            ch.Scenario.IDENTITY_COMPLETED,
            environment="SANDBOX",
            policy=ch.SkuPolicy(unit_disclosed=True),
        ).billable
        is False
    )
