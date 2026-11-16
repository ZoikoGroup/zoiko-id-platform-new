"""DTV-17 address and proof of address: shape, tolerant comparison, the policy, and the rules about what a document can and cannot say. Synthetic only."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import yaml
from zid_platform.modules.verification.domain import address as ad
from zid_platform.modules.verification.infrastructure.address_policy_registry import (
    CONFIG,
    FILE,
    proof_policy,
)

TODAY = date(2026, 11, 16)
HOME = ad.parse_address(
    {"line1": "12 Station Road", "city": "Leeds", "postal_code": "LS1 4AB", "country": "GB"}
)
POLICY = ad.parse_policy(
    {
        "version": "t",
        "status": "SAMPLE",
        "max_age_days": {"UTILITY_BILL": 90, "TENANCY_AGREEMENT": 365},
    }
)


def doc(**over):
    base = {
        "doc_type": "UTILITY_BILL", "issuer": "Northern Power", "issued_on": TODAY - timedelta(days=20),
        "name_on_document": "Dana Lee", "address": HOME,
    }  # fmt: skip
    base.update(over)
    return ad.ProofDocument(**base)


def assess(d=None, **kw):
    return ad.assess_proof(
        d or doc(), HOME, kw.get("name", "Dana Lee"), kw.get("policy", POLICY), today=TODAY
    )


def codes(a: ad.Assessment) -> set[str]:
    return {f.code for f in a.findings}


# ------------------------------------------------------------------------------------------------------------------- shape


def test_an_address_needs_line_city_and_a_two_letter_country_and_refuses_unknown_keys():
    assert ad.parse_address({"line1": "1 A St", "city": "X", "country": "gb"}).country == "GB"
    for bad in (
        {"city": "X", "country": "GB"},
        {"line1": "1 A St", "country": "GB"},
        {"line1": "1 A St", "city": "X", "country": "GBR"},
        {"line1": "1 A St", "city": "X", "country": "GB", "planet": "earth"},
        {"line1": "x" * 121, "city": "X", "country": "GB"},
    ):
        with pytest.raises(ad.AddressError):
            ad.parse_address(bad)


@pytest.mark.parametrize(
    ("country", "code", "expected"),
    [
        ("GB", "LS1 4AB", "OK"),
        ("GB", "LS14AB", "OK"),
        ("GB", "ZZZ", "BAD_SHAPE"),
        ("US", "94105-1234", "OK"),
        ("US", "9410", "BAD_SHAPE"),
        ("DE", "10115", "OK"),
        ("JP", "100-0001", "NOT_CHECKED"),
        ("GB", "", "BAD_SHAPE"),
    ],
)
def test_a_postcode_is_checked_only_where_its_shape_is_known(country, code, expected):
    a = ad.parse_address({"line1": "1 A St", "city": "X", "postal_code": code, "country": country})
    assert ad.postcode_check(a) == expected


# ------------------------------------------------------------------------------------------------------------------- comparison


def a(**over):
    base = {"line1": "12 Station Road", "city": "Leeds", "postal_code": "LS1 4AB", "country": "GB"}
    base.update(over)
    return ad.parse_address(base)


def test_case_accents_punctuation_and_abbreviations_are_not_differences():
    assert ad.compare(a(), a(line1="12, STATION RD.")) is ad.Match.SAME
    assert (
        ad.compare(
            a(city="Köln", country="DE", postal_code="50667", line1="1 Hauptstr"),
            a(city="Koln", country="DE", postal_code="50667", line1="1 hauptstrasse"),
        )
        is ad.Match.SAME
    )
    assert ad.compare(a(postal_code="LS14AB"), a(postal_code="ls1 4ab")) is ad.Match.SAME


def test_the_same_street_and_postcode_with_another_city_spelling_is_the_same_place():
    assert ad.compare(a(city="Leeds"), a(city="City of Leeds")) is ad.Match.SAME


def test_a_different_country_is_different_and_a_different_house_is_not_quietly_the_same():
    assert ad.compare(a(), a(country="IE", postal_code="D02 X285")) is ad.Match.DIFFERENT
    assert (
        ad.compare(a(), a(line1="99 Other Lane", city="Bristol", postal_code="BS1 5TR"))
        is ad.Match.DIFFERENT
    )
    assert (
        ad.compare(a(), a(line1="14 Station Road")) is not ad.Match.SAME
    )  # another house on the same road


def test_a_flat_number_difference_in_the_same_building_goes_to_a_person_not_to_a_pass():
    assert (
        ad.compare(a(line1="Flat 2, 12 Station Road"), a(line1="Flat 3, 12 Station Road"))
        is ad.Match.PARTIAL
    )


def test_the_comparison_is_symmetric():
    pairs = [
        (a(), a(line1="12, STATION RD.")),
        (a(), a(line1="Flat 2, 12 Station Road")),
        (a(), a(country="IE", postal_code="D02 X285")),
    ]
    for x, y in pairs:
        assert ad.compare(x, y) is ad.compare(y, x)


# ------------------------------------------------------------------------------------------------------------------- the policy


def test_a_policy_is_data_with_types_and_ages_and_refuses_nonsense():
    assert ad.accepted_types(POLICY) == ["TENANCY_AGREEMENT", "UTILITY_BILL"]
    for bad in (
        {"status": "LIVE", "max_age_days": {"UTILITY_BILL": 90}},
        {"max_age_days": {}},
        {"max_age_days": {"utility bill": 90}},
        {"max_age_days": {"UTILITY_BILL": 0}},
        {"max_age_days": {"UTILITY_BILL": 99999}},
        {"max_age_days": {"UTILITY_BILL": "90"}},
        {"max_age_days": {"UTILITY_BILL": True}},
        {
            "status": "APPROVED",
            "max_age_days": {"UTILITY_BILL": 90},
        },  # approval needs a named approver and a legal basis
    ):
        with pytest.raises(ad.AddressError):
            ad.parse_policy(bad)
    ok = ad.parse_policy(
        {
            "status": "APPROVED",
            "approved_by": "compliance",
            "legal_basis_ref": "L-1",
            "max_age_days": {"UTILITY_BILL": 90},
        }
    )
    assert ok.status == "APPROVED"


def test_the_shipped_policy_is_a_sample_nobody_approved_and_says_so():
    p = proof_policy()
    assert p.status == "SAMPLE" and p.approved_by is None
    raw = yaml.safe_load((CONFIG / FILE).read_text(encoding="utf-8"))
    assert raw["status"] == "SAMPLE" and "approved_by" not in raw
    assert assess(policy=p).policy_status == "SAMPLE"


# ------------------------------------------------------------------------------------------------------------------- proof of address


def test_a_recent_accepted_document_about_this_person_and_address_supports_it_and_nothing_more():
    r = assess()
    assert r.support is ad.Support.SUPPORTS and r.ceiling == "DOCUMENT_PRESENTED"
    assert any("does not verify" in n for n in r.notes) and any("genuine" in n for n in r.notes)
    assert not hasattr(r, "score")


@pytest.mark.parametrize(
    ("mutate", "code", "support"),
    [
        (
            lambda: doc(doc_type="MOBILE_BILL"),
            "DOCUMENT_TYPE_NOT_ACCEPTED",
            ad.Support.DOES_NOT_SUPPORT,
        ),
        (
            lambda: doc(issued_on=TODAY - timedelta(days=91)),
            "DOCUMENT_TOO_OLD",
            ad.Support.DOES_NOT_SUPPORT,
        ),
        (
            lambda: doc(issued_on=TODAY + timedelta(days=1)),
            "DOCUMENT_DATED_IN_THE_FUTURE",
            ad.Support.DOES_NOT_SUPPORT,
        ),
        (lambda: doc(issuer="  "), "ISSUER_MISSING", ad.Support.REVIEW),
        (
            lambda: doc(name_on_document="Someone Else"),
            "NAME_MISMATCH",
            ad.Support.DOES_NOT_SUPPORT,
        ),
        (lambda: doc(name_on_document="Dana Marie Lee"), "NAME_PARTIAL_MATCH", ad.Support.REVIEW),
        (
            lambda: doc(address=a(line1="99 Other Lane", city="Bristol", postal_code="BS1 5TR")),
            "ADDRESS_MISMATCH",
            ad.Support.DOES_NOT_SUPPORT,
        ),
        (
            lambda: doc(address=a(line1="Flat 2, 12 Station Road")),
            "ADDRESS_PARTIAL_MATCH",
            ad.Support.REVIEW,
        ),
    ],
)
def test_each_problem_is_named_and_uncertainty_goes_to_a_person(mutate, code, support):
    r = assess(mutate())
    assert code in codes(r) and r.support is support


def test_a_document_exactly_at_its_age_limit_is_still_accepted_and_one_day_over_is_not():
    assert assess(doc(issued_on=TODAY - timedelta(days=90))).support is ad.Support.SUPPORTS
    assert "DOCUMENT_TOO_OLD" in codes(assess(doc(issued_on=TODAY - timedelta(days=91))))


def test_a_different_type_may_be_older_because_the_policy_says_so():
    assert (
        assess(doc(doc_type="TENANCY_AGREEMENT", issued_on=TODAY - timedelta(days=300))).support
        is ad.Support.SUPPORTS
    )


def test_a_postcode_of_unknown_shape_is_noted_not_guessed():
    jp = ad.parse_address(
        {"line1": "1-1 Chiyoda", "city": "Tokyo", "postal_code": "100-0001", "country": "JP"}
    )
    r = ad.assess_proof(doc(address=jp), jp, "Dana Lee", POLICY, today=TODAY)
    assert "POSTCODE_NOT_CHECKED" in codes(r) and r.support is ad.Support.SUPPORTS


def test_the_same_document_gives_the_same_answer_every_time():
    assert assess() == assess()


# ------------------------------------------------------------------------------------------------------------------- the provider


def test_no_provider_ships_and_a_provider_that_fails_is_unavailable():
    assert ad.provider_answer(None, "Dana Lee", HOME) == ad.ProviderAnswer(
        False, None, ("NO_PROVIDER_CONFIGURED",), ""
    )

    class Broken:
        def check(self, subject_name, address):
            raise ConnectionError("down")

    assert ad.provider_answer(Broken(), "Dana Lee", HOME).reasons == ("PROVIDER_FAILED",)

    class Fine:
        def check(self, subject_name, address):
            return ad.ProviderAnswer(True, "LISTED", ("ON_REGISTER",), "roll")

    assert ad.provider_answer(Fine(), "Dana Lee", HOME).match == "LISTED"


def test_the_two_kinds_of_evidence_are_shown_together_and_never_merged_into_a_pass():
    result = ad.combine(assess(), ad.provider_answer(None, "Dana Lee", HOME))
    assert result.recommendation == "REVIEW"
    assert result.document is not None and result.document.support is ad.Support.SUPPORTS
    assert not result.provider.available
