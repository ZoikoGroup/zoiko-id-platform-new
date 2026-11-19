"""Typed details against the platform's own reading of a document photograph (CR-129): a poor reading is never held against a person, a good reading that lacks the detail is."""

from datetime import date

from zid_platform.modules.verification.domain import document_details as d

AADHAAR = (
    "Government of India\nANILKUMAR NAKULURI\nDOB: 14/03/1994\nMale\n7421 5530 9187\nMy Aadhaar, My Identity\nVID 9123 4567 8901 2345"
)


def test_a_card_that_carries_the_details_matches():
    r = d.compare(AADHAAR, name="Anilkumar Nakuluri", number="7421 5530 9187", birth_date=date(1994, 3, 14))
    assert r == {"name": "MATCH", "number": "MATCH", "birth_date": "MATCH"}


def test_a_noisy_reading_of_a_name_still_matches():
    noisy = AADHAAR.replace("ANILKUMAR", "ANILKUMAA").replace("NAKULURI", "NAKULUR1")
    assert d.compare(noisy, name="Anilkumar Nakuluri", number=None, birth_date=None) == {"name": "MATCH"}


def test_a_covered_number_matches_on_its_last_four_digits():
    covered = AADHAAR.replace("7421 5530", "XXXX XXXX")
    assert d.compare(covered, name=None, number="7421 5530 9187", birth_date=None) == {"number": "MATCH"}


def test_a_detail_the_document_lacks_is_a_mismatch_when_the_reading_is_good():
    r = d.compare(AADHAAR, name="Someone Else", number="1111 2222 3333", birth_date=date(1990, 1, 1))
    assert r == {"name": "MISMATCH", "number": "MISMATCH", "birth_date": "MISMATCH"}
    assert d.contradicted(r) == ["birth_date", "name", "number"]


def test_a_poor_reading_is_held_against_nobody():
    r = d.compare("xx", name="Anilkumar", number="7421 5530 9187", birth_date=date(1994, 3, 14))
    assert r == {"name": "NOT_READ", "number": "NOT_READ", "birth_date": "NOT_READ"}
    assert d.contradicted(r) == []


def test_only_the_year_printed_matches_a_year_of_birth_card():
    text = AADHAAR.replace("DOB: 14/03/1994", "Year of Birth: 1994")
    assert d.compare(text, name=None, number=None, birth_date=date(1994, 3, 14)) == {"birth_date": "MATCH"}


def test_an_untyped_detail_is_absent():
    assert d.compare(AADHAAR, name="", number=None, birth_date=None) == {}
