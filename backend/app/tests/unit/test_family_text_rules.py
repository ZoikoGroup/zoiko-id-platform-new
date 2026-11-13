"""Family wording revisions (ZID-COM-WF-2026-11 WF-I01, G01, INV-11): wording only, never a price or a status claim, and never structure."""

from __future__ import annotations

import pytest
from zid_platform.modules.commercial.domain import family_text as ft
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

REG = family_registry()


def test_good_wording_is_trimmed_and_accepted() -> None:
    t = ft.check_text(
        "  Identity verification  ",
        "Verification case",
        "Biographical and document proof; decision state",
    )
    assert t == ft.Text(
        "Identity verification",
        "Verification case",
        "Biographical and document proof; decision state",
    )
    assert ft.check_text("Business verification", "Legal entity check", "").sales_qualifier == ""


@pytest.mark.parametrize(
    ("args", "code"),
    [
        (("A", "Unit", ""), "NAME_LENGTH"),
        (("x" * 81, "Unit", ""), "NAME_LENGTH"),
        (("Name", "U", ""), "UNIT_LENGTH"),
        (("Name", "Unit", "q" * 301), "QUALIFIER_LENGTH"),
        (("Identity checks from $2", "Check", ""), "PRICE_IN_TEXT"),
        (("Identity", "Check, 5 EUR each", ""), "PRICE_IN_TEXT"),
        (("Identity", "Check", "Free trial included"), "PRICE_IN_TEXT"),
        (("Identity", "Check", "billed per month"), "PRICE_IN_TEXT"),
        (("Identity", "Check", "10% off"), "PRICE_IN_TEXT"),
        (("Qualified signatures", "Envelope", ""), "CLAIM_IN_LABEL"),
        (("Signatures", "Certified envelope", ""), "CLAIM_IN_LABEL"),
        (("Globally available identity", "Case", ""), "CLAIM_IN_LABEL"),
        (("Accredited screening", "Subject", ""), "CLAIM_IN_LABEL"),
        (("Identity", "Guaranteed check", ""), "CLAIM_IN_LABEL"),
    ],
)
def test_wording_cannot_become_a_price_or_a_status_claim(
    args: tuple[str, str, str], code: str
) -> None:
    with pytest.raises(ft.TextError) as e:
        ft.check_text(*args)
    assert e.value.code == code


def test_a_condition_may_mention_a_status_in_the_sales_qualifier_but_a_label_may_not() -> None:
    ft.check_text(
        "Electronic signatures",
        "Signature envelope",
        "Qualified-class availability only through authorised local partners",
    )
    with pytest.raises(ft.TextError):
        ft.check_text("Qualified electronic signatures", "Signature envelope", "")


def test_applying_wording_changes_three_fields_and_never_the_structure() -> None:
    new = ft.apply_text(
        REG,
        {"SIGN": ft.Text("E-signatures", "Signature envelope", "Needs a verified identity first")},
    )
    before, after = REG.families["SIGN"], new.families["SIGN"]
    assert (after.name, after.unit, after.sales_qualifier) == (
        "E-signatures",
        "Signature envelope",
        "Needs a verified identity first",
    )
    for structural in ("code", "depends_on", "regulated", "needs_provider", "meter"):
        assert getattr(after, structural) == getattr(before, structural), structural
    assert new.version == REG.version and new.status == REG.status
    assert {c: f for c, f in new.families.items() if c != "SIGN"} == {
        c: f for c, f in REG.families.items() if c != "SIGN"
    }
    assert REG.families["SIGN"].name != "E-signatures", "the loaded file is not mutated"


def test_wording_for_an_unknown_family_is_ignored_and_no_wording_is_the_file() -> None:
    assert ft.apply_text(REG, {"GHOST": ft.Text("Ghost", "Unit", "")}).families == REG.families
    assert ft.apply_text(REG, {}).families == REG.families
