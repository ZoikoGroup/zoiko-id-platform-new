"""The market matrix rules (ZID-COM-WF-2026-11 v3.0 WF-I01, WF-I05): a row is complete for its family, and a recorded row supports exactly the facts it names."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from zid_platform.modules.commercial.domain import market_availability as av
from zid_platform.modules.commercial.domain import market_matrix as mm
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

REG = family_registry()
TODAY = date(2026, 11, 13)


def row(family: str = "API", country: str = "DE", **over) -> mm.Row:
    base: dict[str, object] = dict(  # noqa: C408
        family=family, country=country, seller_entity="Zoiko Tech GmbH", merchant_route="stripe-eu",
        currency="EUR", tax_class="B2B-SERVICES", data_region="eu-central", offer_ref="offer:2026-11/api-de",
        legal_basis_ref="legal:memo-114", release_evidence_ref="release:G6-2026-11-14", payment_configured=True,
        tax_configured=True, provider_rights_ref=None, regulated_status_ref=None, stage="PRODUCTION",
    )  # fmt: skip
    base.update(over)
    return mm.Row(**base)


def test_a_complete_row_for_an_ordinary_family_has_no_problem() -> None:
    assert mm.row_problems(row(), REG) == []
    mm.check_row(row(), REG)


def test_an_unknown_family_is_refused() -> None:
    assert mm.row_problems(row(family="GHOST"), REG) == ["FAMILY_UNKNOWN"]


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"country": "Germany"}, "COUNTRY_INVALID"),
        ({"currency": "euro"}, "CURRENCY_INVALID"),
        ({"seller_entity": "  "}, "SELLER_ENTITY_MISSING"),
        ({"merchant_route": ""}, "MERCHANT_ROUTE_MISSING"),
        ({"tax_class": ""}, "TAX_CLASS_MISSING"),
        ({"data_region": " "}, "DATA_REGION_MISSING"),
        ({"offer_ref": ""}, "OFFER_REF_MISSING"),
        ({"legal_basis_ref": ""}, "LEGAL_BASIS_REF_MISSING"),
        ({"release_evidence_ref": ""}, "RELEASE_EVIDENCE_REF_MISSING"),
    ],
)
def test_each_blank_or_malformed_field_is_named(change, problem) -> None:
    assert problem in mm.row_problems(row(**change), REG)


def test_a_family_that_needs_a_provider_needs_provider_rights_and_a_regulated_one_needs_its_status() -> (
    None
):
    assert "PROVIDER_RIGHTS_REF_MISSING" in mm.row_problems(row(family="IDV"), REG)
    assert "REGULATED_STATUS_REF_MISSING" in mm.row_problems(
        row(family="SIGN", provider_rights_ref="p"), REG
    )
    full = row(
        family="SIGN",
        provider_rights_ref="rights:qtsp-de",
        regulated_status_ref="eutl:DE-QTSP-7",
        provider_rights_expires=date(2027, 6, 30),
        regulated_status_expires=date(2027, 6, 30),
    )
    assert mm.row_problems(full, REG) == []


def test_check_row_raises_the_first_problem_with_all_of_them_in_the_detail() -> None:
    with pytest.raises(mm.MatrixError) as e:
        mm.check_row(row(family="SIGN", seller_entity=""), REG)
    assert e.value.code == "SELLER_ENTITY_MISSING"
    assert (
        "PROVIDER_RIGHTS_REF_MISSING" in e.value.detail
        and "REGULATED_STATUS_REF_MISSING" in e.value.detail
    )


def test_no_row_supports_nothing_every_fact_stays_unrecorded() -> None:
    assert mm.facts_for_row(None, "API", REG, TODAY) == av.Facts()
    assert mm.facts_for_row(row(), "GHOST", REG, TODAY) == av.Facts()


def test_a_complete_row_supports_a_sale_of_an_ordinary_family() -> None:
    verdict = av.decide(REG.families["API"], mm.facts_by_dimension(
        {("API", "DE"): row()}, REG, "API", subject_country="DE", payer_country="DE", operating_region="eu-central", on=TODAY))  # fmt: skip
    assert verdict.sellable is True and verdict.refusals == ()


def test_a_regulated_family_is_not_sellable_without_its_references() -> None:
    thin = row(family="SIGN", provider_rights_ref="rights", regulated_status_ref=None)
    verdict = av.decide(REG.families["SIGN"], mm.facts_by_dimension(
        {("SIGN", "DE"): thin}, REG, "SIGN", subject_country="DE", payer_country="DE", operating_region="eu-central", on=TODAY))  # fmt: skip
    assert verdict.sellable is False
    assert "REGULATED_STATUS_NOT_SATISFIED" in {r.code for r in verdict.refusals}


def test_each_dimension_is_judged_against_its_own_country_row() -> None:
    rows = {
        ("API", "DE"): row(),
        ("API", "US"): row(country="US", seller_entity="", payment_configured=False),
    }
    facts = mm.facts_by_dimension(
        rows,
        REG,
        "API",
        subject_country="DE",
        payer_country="US",
        operating_region="eu-central",
        on=TODAY,
    )
    v = av.decide(REG.families["API"], facts)
    assert v.sellable is False
    assert {r.dimension for r in v.refusals} == {"payer"}


def test_a_country_with_no_row_is_a_refusal_not_a_pass() -> None:
    facts = mm.facts_by_dimension(
        {("API", "DE"): row()},
        REG,
        "API",
        subject_country="DE",
        payer_country="FR",
        operating_region="eu-central",
        on=TODAY,
    )
    v = av.decide(REG.families["API"], facts)
    assert v.sellable is False and {r.dimension for r in v.refusals} == {"payer"}
    assert "OFFER_EFFECTIVE_UNKNOWN" in {r.code for r in v.refusals}


def test_an_operating_region_with_no_row_recorded_for_it_is_refused() -> None:
    facts = mm.facts_by_dimension(
        {("API", "DE"): row()},
        REG,
        "API",
        subject_country="DE",
        payer_country="DE",
        operating_region="us-east",
        on=TODAY,
    )
    v = av.decide(REG.families["API"], facts)
    assert v.sellable is False and {r.dimension for r in v.refusals} == {"operating"}


def test_a_dimension_nobody_named_is_not_evaluated_and_blocks_the_sale() -> None:
    facts = mm.facts_by_dimension(
        {("API", "DE"): row()},
        REG,
        "API",
        subject_country="DE",
        payer_country=None,
        operating_region=None,
        on=TODAY,
    )
    v = av.decide(REG.families["API"], facts)
    assert v.sellable is False and {"PAYER_NOT_EVALUATED", "OPERATING_NOT_EVALUATED"} <= {
        r.code for r in v.refusals
    }


def test_a_row_whose_payment_or_tax_is_not_configured_is_not_sellable() -> None:
    for change in ({"payment_configured": False}, {"tax_configured": False}):
        facts = mm.facts_by_dimension(
            {("API", "DE"): replace(row(), **change)},
            REG,
            "API",
            subject_country="DE",
            payer_country="DE",
            operating_region="eu-central",
            on=TODAY,
        )
        assert av.decide(REG.families["API"], facts).sellable is False


def test_the_roles_still_missing_are_listed_in_the_documents_order() -> None:
    assert mm.missing_approvals([]) == mm.ROLES
    assert mm.missing_approvals(["FINANCE", "PRODUCT"]) == ("LEGAL_PRIVACY", "SECURITY_QA")
    assert mm.missing_approvals(list(mm.ROLES)) == ()


def test_a_reference_needs_its_expiry_and_a_right_past_its_expiry_supports_no_sale() -> None:
    no_expiry = row(family="SIGN", provider_rights_ref="rights", regulated_status_ref="eutl")
    assert {"PROVIDER_RIGHTS_EXPIRY_MISSING", "REGULATED_STATUS_EXPIRY_MISSING"} <= set(
        mm.row_problems(no_expiry, REG)
    )

    def sold(on: date, expires: date) -> bool:
        r = row(
            family="SIGN",
            provider_rights_ref="rights",
            regulated_status_ref="eutl",
            provider_rights_expires=expires,
            regulated_status_expires=expires,
        )
        facts = mm.facts_by_dimension(
            {("SIGN", "DE"): r},
            REG,
            "SIGN",
            subject_country="DE",
            payer_country="DE",
            operating_region="eu-central",
            on=on,
        )
        return av.decide(REG.families["SIGN"], facts).sellable

    assert sold(TODAY, date(2026, 11, 13)) is True, "valid through its last day"
    assert sold(TODAY, date(2026, 11, 12)) is False, "the day after, it is denied, not rerouted"
    assert mm.facts_for_row(no_expiry, "SIGN", REG, TODAY).provider_entitled is False, (
        "an expiry never recorded supports nothing"
    )


def test_rights_about_to_expire_or_already_expired_are_listed_for_renewal() -> None:
    rows = [
        row(
            family="SIGN",
            country="DE",
            provider_rights_ref="p",
            regulated_status_ref="q",
            provider_rights_expires=date(2026, 11, 20),
            regulated_status_expires=date(2027, 1, 1),
        ),
        row(
            family="SIGN",
            country="FR",
            provider_rights_ref="p",
            regulated_status_ref="q",
            provider_rights_expires=date(2026, 11, 1),
            regulated_status_expires=date(2027, 1, 1),
        ),
        row(),
    ]
    alerts = mm.rights_alerts(rows, TODAY)
    assert [(a["country"], a["kind"], a["state"]) for a in alerts] == [
        ("DE", "PROVIDER_RIGHTS", "EXPIRING"),
        ("FR", "PROVIDER_RIGHTS", "EXPIRED"),
    ]
