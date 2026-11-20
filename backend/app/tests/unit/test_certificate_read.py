"""Reading an uploaded company certificate and checking it against the claim (CR-131): consistent, contradicting, or not enough to say; never a verification."""

from __future__ import annotations

from datetime import date

from zid_platform.modules.organization.domain import certificate_read as c

CERT = """Certificate of Incorporation
of a Public Limited Company
Company Number 00445790
The Registrar of Companies for England and Wales hereby certifies that
TESCO PLC
is this day incorporated under the Companies Act 2006 as a public company, and that the company is limited by shares.
Given at Companies House, on 27th November 1947
"""


def test_the_name_number_and_date_are_read_from_a_certificate() -> None:
    got = c.extract(CERT)
    assert got.document == "CERTIFICATE_OF_INCORPORATION"
    assert got.company_number == "00445790" and got.company_name == "TESCO PLC"
    assert got.incorporated_on == date(1947, 11, 27)


def test_a_certificate_for_the_claimed_company_is_consistent() -> None:
    r = c.compare(CERT, claimed_name="Tesco PLC", claimed_number="00445790")
    assert r["verdict"] == c.CONSISTENT and r["number"] == c.MATCH and r["name"] == c.MATCH


def test_another_companys_certificate_contradicts_the_claim() -> None:
    assert c.compare(CERT, claimed_name="Tesco PLC", claimed_number="00123456")["verdict"] == c.CONTRADICTS
    assert c.compare(CERT, claimed_name="Acme Widgets Limited", claimed_number="00445790")["verdict"] == c.CONTRADICTS


def test_a_leading_zero_lost_is_a_different_number() -> None:
    assert c.compare(CERT, claimed_name="Tesco PLC", claimed_number="445790")["number"] == c.MISMATCH


def test_a_poor_reading_is_not_enough_and_never_held_against_the_applicant() -> None:
    assert c.compare("xx", claimed_name="Tesco PLC", claimed_number="00445790")["verdict"] == c.NOT_ENOUGH
    noisy = "Certificate of Incorporation of a company. Registrar of Companies England. Some words here that do not carry the details at all, only noise."
    r = c.compare(noisy, claimed_name="Tesco PLC", claimed_number="00445790")
    assert r["verdict"] == c.NOT_ENOUGH and r["number"] == c.NOT_READ


def test_a_noisy_name_still_matches_and_a_garbled_one_is_left_undecided() -> None:
    near = CERT.replace("TESCO PLC", "TESC0 PLC")
    assert c.compare(near, claimed_name="Tesco PLC", claimed_number="00445790")["verdict"] == c.CONSISTENT


def test_the_answer_carries_codes_and_a_date_never_the_text() -> None:
    r = c.compare(CERT, claimed_name="Tesco PLC", claimed_number="00445790")
    assert set(r) == {"verdict", "reading", "document", "number", "name", "incorporated_on"}
    assert "TESCO" not in str(r) and r["incorporated_on"] == "1947-11-27"
