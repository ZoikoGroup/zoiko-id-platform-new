"""How a person is named (CR-061): "Name (M-0007)", and what counts as a usable name."""

from __future__ import annotations

import pytest
from zid_platform.modules.tenant.application.people import (
    MAX_NAME,
    NameError_,
    PersonRef,
    clean_name,
    member_code,
)

ID = "01a1a5db-ae65-7b7a-873c-7019b54dc16e"


def test_a_member_code_is_a_zero_padded_company_number() -> None:
    assert member_code(7) == "M-0007"
    assert member_code(12345) == "M-12345"  # past four digits it simply grows
    assert member_code(None) is None


def test_a_person_is_shown_as_name_and_number_or_number_alone() -> None:
    assert PersonRef(ID, 3, "Anil Kumar").label == "Anil Kumar (M-0003)"
    assert PersonRef(ID, 3, None).label == "M-0003"
    assert (
        PersonRef(ID, None, None).label == ID[:8]
    )  # even the number unknown: a short id, never an empty cell


def test_the_view_carries_everything_a_screen_needs() -> None:
    v = PersonRef(ID, 2, "Priya Nair").view()
    assert v == {
        "principal_id": ID,
        "member_no": 2,
        "member_code": "M-0002",
        "name": "Priya Nair",
        "label": "Priya Nair (M-0002)",
    }


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("  Priya   Nair ", "Priya Nair"),
        ("Zoë Müller", "Zoë Müller"),
        ("名前 テスト", "名前 テスト"),
        ("", None),
        ("   ", None),
        (None, None),
        ("line\nbreak", "line break"),  # a pasted newline or tab is a space, not an error
        ("tab\there", "tab here"),
    ],
)
def test_a_name_is_trimmed_single_spaced_and_blank_clears_it(
    raw: str | None, clean: str | None
) -> None:
    assert clean_name(raw) == clean


@pytest.mark.parametrize("raw", ["bad\x00name", "bell\x07 inside", "x" * (MAX_NAME + 1), "\x07"])
def test_a_name_with_control_characters_or_too_long_is_refused(raw: str) -> None:
    with pytest.raises(NameError_):
        clean_name(raw)
