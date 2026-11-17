"""What may be recorded as a representative's mandate (WF02 K05; acceptance T15, T16, T22)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.organization.domain import kyb_mandate as m

NOW = datetime(2026, 11, 17, tzinfo=UTC)
OK = {
    "person": "bob",
    "recorder": "rita",
    "role": "DIRECTOR",
    "source": "BOARD_MINUTE",
    "powers": ["SIGN_AGREEMENT"],
    "valid_from": NOW - timedelta(days=1),
    "valid_to": NOW + timedelta(days=100),
    "evidence_ref": "board-minute-1",
    "second_signatory_required": False,
    "now": NOW,
}


def check(**over):
    return m.validate(**{**OK, **over})


def code(**over) -> str:
    with pytest.raises(m.MandateError) as e:
        check(**over)
    return e.value.code


def test_a_good_mandate_is_accepted_with_sorted_unique_powers() -> None:
    c = check(powers=["SIGN_AGREEMENT", "BIND_ENTITY", "SIGN_AGREEMENT"])
    assert c.powers == ("BIND_ENTITY", "SIGN_AGREEMENT") and c.second_signatory_required is False


def test_a_representative_cannot_record_their_own_mandate() -> None:
    assert code(recorder="bob") == "MANDATE_SELF_RECORDED"


def test_evidence_roles_and_sources_are_required_and_closed_lists() -> None:
    assert code(evidence_ref="  ") == "MANDATE_EVIDENCE_REQUIRED"
    assert code(evidence_ref="x" * 513) == "MANDATE_EVIDENCE_REQUIRED"
    assert (
        code(role="OWNER") == "MANDATE_ROLE_INVALID"
    )  # an account Owner is not a representative by that fact
    assert code(role="PLATFORM_ADMIN") == "MANDATE_ROLE_INVALID"
    assert code(source="EMAIL") == "MANDATE_SOURCE_INVALID"


def test_powers_are_the_binding_acts_only_and_never_empty() -> None:
    assert code(powers=[]) == "MANDATE_POWERS_EMPTY"
    assert code(powers=["READ_FILES"]) == "MANDATE_POWERS_INVALID"
    assert code(powers=["SIGN_AGREEMENT", "ORG_PROFILE_DRAFT"]) == "MANDATE_POWERS_INVALID"


def test_money_ownership_and_delegation_always_need_a_second_signatory() -> None:
    for p in m.ALWAYS_SECOND_SIGNATORY:
        assert check(powers=[p]).second_signatory_required is True, p
    assert (
        check(powers=["SIGN_AGREEMENT"], second_signatory_required=True).second_signatory_required
        is True
    )


def test_the_window_must_make_sense() -> None:
    assert code(valid_to=OK["valid_from"]) == "MANDATE_WINDOW_INVALID"
    assert (
        code(valid_to=OK["valid_from"] + m.MAX_TERM + timedelta(days=1)) == "MANDATE_TERM_TOO_LONG"
    )
    assert (
        code(valid_from=NOW - timedelta(days=30), valid_to=NOW - timedelta(days=1))
        == "MANDATE_ALREADY_ENDED"
    )
    assert check(
        valid_to=None
    ).powers  # an open-ended mandate is allowed: it is revoked, not left to run silently? the grade still re-checks the person each time


def test_the_second_signatory_is_a_third_person() -> None:
    assert (
        m.cosign_problem(person="bob", recorder="rita", cosigner="bob")
        == "COSIGNER_IS_REPRESENTATIVE"
    )
    assert (
        m.cosign_problem(person="bob", recorder="rita", cosigner="rita") == "COSIGNER_IS_RECORDER"
    )
    assert m.cosign_problem(person="bob", recorder="rita", cosigner="cy") is None
