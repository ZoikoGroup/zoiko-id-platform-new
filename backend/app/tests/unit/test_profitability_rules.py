"""Profitability, run-rate and retention (ZID-COM-WF-2026-11 CW-13): same-currency margins only, inferred costs flagged, no growth assumed."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from zid_platform.modules.commercial.domain import profitability as pf

D = Decimal


def test_margin_is_revenue_less_cost_in_the_same_currency() -> None:
    out = pf.margin(
        {("IDV", "EUR"): D(1000)},
        [
            pf.Cost("IDV", "EUR", D(250), "SIGNED_TARIFF"),
            pf.Cost("IDV", "EUR", D(50), "SIGNED_TARIFF"),
        ],
    )
    assert out["lines"] == [
        {
            "sku": "IDV",
            "currency": "EUR",
            "revenue": "1000.00",
            "cost": "300.00",
            "margin": "700.00",
            "margin_pct": "70.00",
            "cost_is_inferred": False,
        }
    ]
    assert out["cost_without_revenue_in_that_currency"] == []


def test_a_quoted_or_estimated_cost_is_flagged_as_inferred_never_as_actual() -> None:
    for confidence in ("QUOTED", "ESTIMATE", None):
        line = pf.margin({("IDV", "EUR"): D(100)}, [pf.Cost("IDV", "EUR", D(10), confidence)])[
            "lines"
        ][0]
        assert line["cost_is_inferred"] is True
    mixed = pf.margin(
        {("IDV", "EUR"): D(100)},
        [pf.Cost("IDV", "EUR", D(10), "SIGNED_TARIFF"), pf.Cost("IDV", "EUR", D(5), "ESTIMATE")],
    )["lines"][0]
    assert mixed["cost_is_inferred"] is True and mixed["cost"] == "15.00"


def test_a_cost_in_another_currency_is_listed_never_converted_and_unknown_margin_is_not_100_percent() -> (
    None
):
    out = pf.margin({("IDV", "EUR"): D(100)}, [pf.Cost("IDV", "USD", D(40), "SIGNED_TARIFF")])
    line = out["lines"][0]  # type: ignore[index]
    assert line["cost"] is None and line["margin"] is None and line["margin_pct"] is None
    assert out["cost_without_revenue_in_that_currency"] == [
        {"sku": "IDV", "currency": "USD", "cost": "40.00", "inferred": False}
    ]


def test_a_loss_is_a_negative_margin_and_zero_revenue_has_no_percentage() -> None:
    loss = pf.margin({("DOC", "EUR"): D(100)}, [pf.Cost("DOC", "EUR", D(130), "SIGNED_TARIFF")])[
        "lines"
    ][0]
    assert loss["margin"] == "-30.00" and loss["margin_pct"] == "-30.00"
    zero = pf.margin({("DOC", "EUR"): D(0)}, [pf.Cost("DOC", "EUR", D(5), "SIGNED_TARIFF")])[
        "lines"
    ][0]
    assert zero["margin"] == "-5.00" and zero["margin_pct"] is None


def series(*pairs: tuple[int, int, int]) -> dict[date, Decimal]:
    return {date(y, m, 1): D(v) for y, m, v in pairs}


def test_run_rate_is_the_average_of_the_last_complete_months_times_twelve() -> None:
    s = series((2026, 7, 100), (2026, 8, 200), (2026, 9, 300), (2026, 10, 600), (2026, 11, 9999))
    out = pf.run_rate(s, as_of=date(2026, 11, 13))  # the current month is partial and is left out
    assert out == {
        "months_used": 3,
        "monthly_average": "366.67",
        "annualised": "4400.00",
        "complete_window": True,
    }


def test_run_rate_says_when_it_has_too_little_history_and_assumes_no_growth() -> None:
    assert pf.run_rate({}, as_of=date(2026, 11, 13)) == {
        "months_used": 0,
        "monthly_average": None,
        "annualised": None,
        "complete_window": False,
    }
    one = pf.run_rate(series((2026, 10, 100)), as_of=date(2026, 11, 13))
    assert (
        one["months_used"] == 1
        and one["complete_window"] is False
        and one["annualised"] == "1200.00"
    )


def test_retention_splits_churn_contraction_expansion_and_new() -> None:
    prev = {"a": D(100), "b": D(100), "c": D(100), "d": D(100)}
    cur = {"a": D(0), "b": D(60), "c": D(150), "d": D(100), "e": D(80)}
    out = pf.retention(prev, cur)
    assert out["churned"] == {"companies": 1, "revenue": "100.00"}
    assert out["contraction"] == "40.00" and out["expansion"] == "50.00"
    assert out["new"] == {"companies": 1, "revenue": "80.00"}
    assert out["gross_retention_pct"] == "65.00" and out["net_retention_pct"] == "77.50"


def test_retention_with_no_prior_revenue_is_unknown_not_100_percent() -> None:
    out = pf.retention({}, {"a": D(10)})
    assert out["gross_retention_pct"] is None and out["net_retention_pct"] is None
    assert out["new"] == {"companies": 1, "revenue": "10.00"}
