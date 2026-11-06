"""The built-in tax rate table: off until approved, exact to the penny, and never a guess for a country it does not list."""

from __future__ import annotations

import asyncio
from decimal import Decimal
from pathlib import Path

import pytest
from zid_platform.modules.commercial.ports import FallbackTax, NoTaxConfigured, RateTableTax
from zid_platform.modules.commercial.application.adapters import TaxableLine, TaxLine

SHIPPED = Path(__file__).resolve().parents[3] / "config" / "commercial" / "tax-rate-table.yaml"
LINES = [TaxableLine(1, "SUBSCRIPTION", None, Decimal("100.00")), TaxableLine(2, "USAGE", None, Decimal("33.33")), TaxableLine(3, "CREDIT", None, Decimal("-5"))]


def table(tmp_path: Path, *, approved: bool, extra: str = "") -> RateTableTax:
    f = tmp_path / "t.yaml"
    f.write_text(
        f"version: '1'\napproved: {str(approved).lower()}\napproved_by: finance@example.com\napproved_on: '2026-11-05'\nexempt: [CREDIT]\nrates:\n  GB: {{tax: VAT, rate: '0.20'}}\n{extra}",
        encoding="utf-8",
    )
    return RateTableTax.from_file(f)


def ask(engine: object, country: str | None) -> dict[int, TaxLine] | None:
    return asyncio.run(engine.lines_for(currency="GBP", region="uk-1", country=country, lines=LINES))  # type: ignore[attr-defined]


def test_the_shipped_table_is_off_so_nothing_changes_until_finance_approves() -> None:
    shipped = RateTableTax.from_file(SHIPPED)
    assert shipped.approved is False and shipped.rates == {}
    assert ask(shipped, "GB") is None


def test_an_approved_table_taxes_each_line_to_the_penny_and_skips_what_is_exempt(tmp_path: Path) -> None:
    got = ask(table(tmp_path, approved=True), "gb")
    assert got is not None
    assert got[1] == TaxLine("TABLE_VAT_20", Decimal("20.00"))
    assert got[2].amount == Decimal("6.67")  # 33.33 x 20% = 6.666, rounded half up
    assert got[3].amount == Decimal("0.00") and got[3].code == "TABLE_VAT_0"  # a credit is not taxed


def test_an_unapproved_table_or_an_unlisted_country_answers_none_never_a_guess(tmp_path: Path) -> None:
    assert ask(table(tmp_path, approved=False), "GB") is None
    assert ask(table(tmp_path, approved=True), "FR") is None
    assert ask(table(tmp_path, approved=True), None) is None


def test_approval_needs_who_and_when(tmp_path: Path) -> None:
    f = tmp_path / "t.yaml"
    f.write_text("approved: true\nrates:\n  GB: {tax: VAT, rate: '0.20'}\n", encoding="utf-8")
    assert RateTableTax.from_file(f).approved is False


def test_a_rate_that_is_not_a_fraction_is_refused_at_load(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        table(tmp_path, approved=True, extra="  XX: {tax: VAT, rate: '20'}\n")


def test_the_primary_engine_wins_and_the_table_only_fills_the_gap(tmp_path: Path) -> None:
    class Primary:
        def __init__(self, answer: dict[int, TaxLine] | None) -> None:
            self.answer = answer

        async def lines_for(self, **_: object) -> dict[int, TaxLine] | None:
            return self.answer

    t = table(tmp_path, approved=True)
    stripe = {1: TaxLine("VAT_20", Decimal("1.00"))}
    assert ask(FallbackTax(Primary(stripe), t), "GB") == stripe
    filled = ask(FallbackTax(Primary(None), t), "GB")
    assert filled is not None and filled[1].code.startswith("TABLE_")
    assert ask(FallbackTax(NoTaxConfigured(), table(tmp_path, approved=False)), "GB") is None
