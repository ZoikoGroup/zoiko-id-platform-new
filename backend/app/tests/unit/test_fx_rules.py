"""FX for reporting (ZID-COM-WF-2026-11 section 05.2): no default rates, nothing converted without one, rounded once on the total."""

from __future__ import annotations

from decimal import Decimal

from zid_platform.modules.commercial.domain import fx

D = Decimal


def test_with_no_rate_set_only_dollars_count_and_everything_else_is_named_not_dropped() -> None:
    out = fx.to_usd({"USD": D(100), "EUR": D(50), "JPY": D(1000)}, None)
    assert out == {
        "usd": "100.00",
        "complete": False,
        "rates_used": [],
        "not_converted": ["EUR", "JPY"],
    }


def test_a_listed_currency_is_converted_at_the_set_rate_and_the_rate_used_is_shown() -> None:
    out = fx.to_usd({"USD": D(10), "EUR": D(100)}, {"EUR": D("1.0850")})
    assert out["usd"] == "118.50" and out["complete"] is True
    assert (
        out["rates_used"] == [{"currency": "EUR", "usd_per_unit": "1.0850"}]
        and out["not_converted"] == []
    )


def test_a_partial_set_converts_what_it_lists_and_says_what_it_does_not() -> None:
    out = fx.to_usd({"EUR": D(100), "JPY": D(5000)}, {"EUR": D("1.1")})
    assert out["usd"] == "110.00" and out["complete"] is False and out["not_converted"] == ["JPY"]


def test_rounding_is_half_to_even_once_on_the_total_not_per_line() -> None:
    # 0.005 + 0.005 per line would each round to 0.00 or 0.01; on the total it is 0.01
    out = fx.to_usd({"AAA": D("0.5"), "BBB": D("0.5")}, {"AAA": D("0.01"), "BBB": D("0.01")})
    assert out["usd"] == "0.01"
    assert fx.to_usd({"AAA": D("0.5")}, {"AAA": D("0.01")})["usd"] == "0.00", (
        "0.005 rounds to the even cent"
    )
