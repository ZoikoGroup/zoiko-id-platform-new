"""Company identifiers by market, and names in their own script (WF02 K01, K02; acceptance T01, T30, T31).

A registration number means something only with its register: a UK number is a Companies House number, a German one needs its court, a U.S. one needs its state, and no country is asked for
another's. A name is kept exactly as the applicant wrote it, in its own script and with its diacritics; the comparison form is separate and traceable."""

from __future__ import annotations

import pytest
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    KybError,
    canonical_authority,
    check_applicant,
    check_registration_number,
    entity_id,
    name_similarity,
    normalize_name,
)


def code(**kw) -> str:
    with pytest.raises(KybError) as e:
        check_applicant(Applicant(**kw))
    return e.value.code


def test_a_uk_company_needs_a_companies_house_number_and_keeps_it_as_written() -> None:  # T01
    assert (
        check_registration_number("GB", "00123456") == "00123456"
    )  # leading zeros are part of the number
    assert check_registration_number("GB", "sc123456") == "SC123456"
    for bad in ("123456", "ABCDEFGH", "0012345", "00123456X", ""):
        with pytest.raises(KybError, match="IDENTIFIER_INVALID"):
            check_registration_number("GB", bad)


def test_a_us_company_is_identified_by_its_state_file_number_never_by_a_national_one() -> (
    None
):  # T30
    ok = check_applicant(Applicant("US", "0123456", "Acme Inc", register_id="US-DE"))
    assert (
        ok.registration_number == "0123456" and ok.register_id == "US-DE"
    )  # leading zero kept, state named
    assert (
        code(jurisdiction="US", registration_number="0123456", legal_name="Acme Inc")
        == "REGISTER_REQUIRED"
    )  # a U.S. file number means nothing outside its state
    assert (
        code(
            jurisdiction="US",
            registration_number="0123456",
            legal_name="Acme Inc",
            register_id="DE",
        )
        == "REGISTER_REQUIRED"
    )
    assert (
        check_registration_number("US", "0123456") == "0123456"
    )  # no UK-style number is demanded of a U.S. company


def test_the_same_number_in_two_registers_is_two_companies() -> None:
    a = entity_id("US", canonical_authority("US", "US-DE"), "0123456")
    b = entity_id("US", canonical_authority("US", "US-NY"), "0123456")
    assert a != b and a == entity_id("US", canonical_authority("US", "US-DE"), "0123456")


def test_a_german_number_needs_its_court() -> None:
    assert (
        code(jurisdiction="DE", registration_number="HRB 12345", legal_name="Mueller GmbH")
        == "REGISTER_REQUIRED"
    )
    ok = check_applicant(
        Applicant("DE", "HRB 12345", "Mueller GmbH", register_id="Amtsgericht Muenchen")
    )
    assert ok.register_id == "Amtsgericht Muenchen"


def test_a_name_is_kept_exactly_in_its_own_script_and_the_comparison_form_is_separate() -> (
    None
):  # T31
    for name in (
        "株式会社アクメ",
        "Müller & Söhne GmbH",
        "Ελληνική Εταιρεία Α.Ε.",
        "شركة المثال المحدودة",
    ):
        assert (
            check_applicant(Applicant("JP", "1234567890123", f"  {name}  ")).legal_name == name
        )  # trimmed, never rewritten
    assert normalize_name("Müller & Söhne GmbH") == normalize_name(
        "Muller Sohne GmbH"
    )  # comparison drops diacritics, the record does not
    assert name_similarity("株式会社アクメ", "株式会社アクメ") == 1.0


def test_a_country_code_must_be_a_country_code() -> None:
    for bad in ("", "G", "GBR", "g1", "12"):
        with pytest.raises(KybError, match="JURISDICTION_INVALID"):
            check_registration_number(bad, "00123456")
