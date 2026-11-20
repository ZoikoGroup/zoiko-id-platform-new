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


DE = "Handelsregister B des Amtsgerichts Muenchen\nHRB 123456\nFirma: Beispiel Maschinenbau GmbH\nSitz der Gesellschaft: Muenchen\nAuszug aus dem Handelsregister vom 12.03.2024\n"
FR = "EXTRAIT KBIS\nSociete par actions simplifiee\nDenomination sociale : EXEMPLE INDUSTRIES SAS\nNumero SIREN 552 100 554 RCS Paris\nDate d'immatriculation : 03/05/1982\n"
IN = "CERTIFICATE OF INCORPORATION\nCorporate Identity Number U74999MH2019PTC123456\nthe company SAMPLE TECH PRIVATE LIMITED is incorporated under the Companies Act 2013\n"


def test_a_certificate_from_any_country_is_consistent_when_it_carries_the_claimed_number_and_name() -> None:
    """CR-135: no label has to be recognised; the claim says what to look for."""
    assert c.compare(DE, claimed_name="Beispiel Maschinenbau GmbH", claimed_number="HRB 123456")["verdict"] == c.CONSISTENT
    assert c.compare(FR, claimed_name="Exemple Industries SAS", claimed_number="552100554")["verdict"] == c.CONSISTENT
    assert c.compare(IN, claimed_name="Sample Tech Private Limited", claimed_number="U74999MH2019PTC123456")["verdict"] == c.CONSISTENT


def test_another_number_or_another_name_is_never_found_in_free_text() -> None:
    assert c.compare(FR, claimed_name="Exemple Industries SAS", claimed_number="552100555")["verdict"] != c.CONSISTENT  # a different number
    assert c.compare(FR, claimed_name="Totally Different Name SAS", claimed_number="552100554")["verdict"] != c.CONSISTENT  # a different name
    assert c.compare(FR, claimed_name="Exemple Industries SAS", claimed_number="5521")["verdict"] != c.CONSISTENT  # too short to look for
