"""DTV-16: MRZ reading with ICAO 9303 check digits. The specimen lines are the ones printed in ICAO Doc 9303.

A line that holds together proves consistency, never authenticity: the result always says so.
"""

from __future__ import annotations

from datetime import date

import pytest
from zid_platform.modules.evidence.domain import mrz

TODAY = date(2026, 11, 12)
TD3 = [
    "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<",
    "L898902C36UTO7408122F1204159ZE184226B<<<<<10",
]
TD2 = [
    "I<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<",
    "D231458907UTO7408122F1204159<<<<<<<6",
]
TD1 = [
    "I<UTOD231458907<<<<<<<<<<<<<<<",
    "7408122F1204159UTO<<<<<<<<<<<6",
    "ERIKSSON<<ANNA<MARIA<<<<<<<<<<",
]


def test_the_check_digit_matches_the_icao_specimen_values() -> None:
    assert mrz.check_digit("L898902C3") == 6
    assert mrz.check_digit("740812") == 2
    assert mrz.check_digit("120415") == 9
    assert mrz.check_digit("ZE184226B<<<<<") == 1


@pytest.mark.parametrize(("lines", "form"), [(TD3, "TD3"), (TD2, "TD2"), (TD1, "TD1")])
def test_each_specimen_reads_and_every_check_digit_holds(lines: list[str], form: str) -> None:
    r = mrz.parse(lines, today=TODAY)
    assert r.form == form and r.all_checks_hold and r.reason_codes == ()
    assert (r.surname, r.given_names) == ("ERIKSSON", "ANNA MARIA")
    assert r.document_number.value in {"L898902C3", "D23145890"} and r.document_number.check_ok
    assert r.issuing_state == "UTO" and r.nationality == "UTO" and r.sex == "F"
    assert r.birth_date_iso == "1974-08-12" and r.expiry_date_iso == "2012-04-15"


def test_a_changed_character_fails_the_check_digit_it_belongs_to_and_is_not_repaired() -> None:
    bad = [TD3[0], TD3[1].replace("L898902C3", "L898902C4")]
    r = mrz.parse(bad, today=TODAY)
    assert "DOCUMENT_NUMBER_CHECK_DIGIT_FAILED" in r.reason_codes and not r.all_checks_hold
    assert r.document_number.value == "L898902C4", "what was read is reported as read"
    assert "COMPOSITE_CHECK_DIGIT_FAILED" in r.reason_codes  # the composite protects the number too


def test_each_field_has_its_own_check_digit() -> None:
    dob = [TD3[0], TD3[1].replace("7408122", "7408132")]  # the day changed, the digit did not
    assert "BIRTH_DATE_CHECK_DIGIT_FAILED" in mrz.parse(dob, today=TODAY).reason_codes
    exp = [TD3[0], TD3[1].replace("1204159", "1204169")]
    assert "EXPIRY_DATE_CHECK_DIGIT_FAILED" in mrz.parse(exp, today=TODAY).reason_codes


def test_a_date_that_does_not_exist_is_reported_even_when_its_check_digit_holds() -> None:
    # 741332 is not a calendar date; give it its correct check digit so only the calendar rule can catch it
    digit = mrz.check_digit("741332")
    line2 = "L898902C36UTO741332" + str(digit) + "F1204159ZE184226B<<<<<10"
    r = mrz.parse([TD3[0], line2], today=TODAY)
    assert r.birth_date.check_ok is True and "BIRTH_DATE_NOT_A_DATE" in r.reason_codes
    assert r.birth_date_iso is None


def test_the_century_of_a_birth_date_is_never_in_the_future() -> None:
    assert mrz.parse(TD3, today=date(2026, 11, 12)).birth_date_iso == "1974-08-12"
    # 2 digit year 30 with today in 2026: born in 1930, not 2030
    r = mrz._dated("300101", future_ok=False, today=date(2026, 11, 12))
    assert r == "1930-01-01"


def test_wrong_shapes_and_characters_are_refused_not_guessed() -> None:
    with pytest.raises(mrz.MrzError) as e:
        mrz.parse([TD3[0]], today=TODAY)
    assert e.value.code == "MRZ_FORMAT_UNKNOWN"
    with pytest.raises(mrz.MrzError) as e2:
        mrz.parse([TD3[0], TD3[1][:-1] + "é"], today=TODAY)
    assert e2.value.code == "MRZ_CHARACTER_INVALID"
    with pytest.raises(mrz.MrzError):
        mrz.check_digit("ab")


def test_spaces_and_case_in_a_pasted_mrz_are_tolerated() -> None:
    pasted = [TD3[0].lower() + "  ", " " + TD3[1].lower()]
    assert mrz.parse(pasted, today=TODAY).all_checks_hold
