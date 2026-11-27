"""The automatic browser route's rules, pure (CR-126)."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain import idv_decision as d
from zid_platform.modules.verification.domain import idv_evidence as ev
from zid_platform.modules.verification.domain import idv_policy

POLICY = d.Policy(
    "v1", chip=d.Requirement.NOT_USED, face=d.Requirement.NOT_USED, capture_integrity=d.Requirement.NOT_USED, expired_document="REFUSE",
    alternates=(d.AlternateRoute("WEB_AUTO", True, 0, True),),
)  # fmt: skip


def sig(**kw: object) -> d.Signals:
    base = {
        "optical": d.Optical.PASS,
        "route": "WEB_AUTO",
        "web_only_capture": True,
        "mrz_check_digits_ok": True,
        "document_expired": False,
        "declared_matches_document": "MATCH",
    }
    return d.Signals(**{**base, **kw})  # type: ignore[arg-type]


def test_everything_holding_is_approved_with_no_reviewer() -> None:
    assert d.decide(sig(), POLICY).decision is d.Decision.APPROVED


@pytest.mark.parametrize(
    ("change", "decision", "reason"),
    [
        (
            {"declared_matches_document": "MISMATCH"},
            d.Decision.REJECTED,
            "DECLARED_DATA_DIFFERS_FROM_DOCUMENT",
        ),
        (
            {"declared_matches_document": "UNKNOWN"},
            d.Decision.INDETERMINATE,
            "DECLARED_DATA_NOT_COMPARED",
        ),
        ({"mrz_check_digits_ok": None}, d.Decision.INDETERMINATE, "DOCUMENT_READING_NOT_RELIABLE"),
        ({"mrz_check_digits_ok": False}, d.Decision.INDETERMINATE, "MRZ_CHECK_DIGITS_FAILED"),
        ({"document_expired": True}, d.Decision.REJECTED, "DOCUMENT_EXPIRED"),
        ({"document_expired": None}, d.Decision.INDETERMINATE, "DOCUMENT_DATE_NOT_ESTABLISHED"),
        ({"optical": d.Optical.FAIL}, d.Decision.REJECTED, "DOCUMENT_MANIPULATION_INDICATED"),
    ],
)
def test_anything_short_is_never_approved(
    change: dict[str, object], decision: d.Decision, reason: str
) -> None:
    r = d.decide(sig(**change), POLICY)
    assert r.decision is decision and reason in r.reasons


def test_the_two_reviewer_floor_still_holds_for_every_other_alternate() -> None:
    with pytest.raises(idv_policy.PolicyError):
        idv_policy.parse(
            {
                "markets": {
                    "GB": {
                        "policy_version": "v",
                        "purpose": "KYC",
                        "alternates": [
                            {"route_id": "X", "may_approve": True, "reviewers_needed": 1}
                        ],
                    }
                }
            }
        )


def test_an_automatic_route_must_say_may_approve_and_name_no_reviewers() -> None:
    for alt in (
        {"route_id": "A", "automatic": True},
        {"route_id": "A", "automatic": True, "may_approve": True, "reviewers_needed": 2},
        {"route_id": "A", "automatic": "yes", "may_approve": True},
    ):
        with pytest.raises(idv_policy.PolicyError):
            idv_policy.parse(
                {"markets": {"GB": {"policy_version": "v", "purpose": "KYC", "alternates": [alt]}}}
            )


def test_comparison_needs_all_four_fields_and_forgives_only_spelling_marks() -> None:
    printed = {
        "surname": "MUELLER",
        "given_names": "ERIKA MARIA",
        "document_number": "C01X00T47",
        "birth_date": "1985-03-12",
    }
    ok = {
        "surname": "Mueller-",
        "given_names": "Erika Maria",
        "document_number": "c01x00t47",
        "birth_date": "1985-03-12",
    }
    assert ev.compare_declared(printed, ok)[0] == "MATCH"
    assert ev.compare_declared(printed, {**ok, "birth_date": "1985-03-13"}) == (
        "MISMATCH",
        ("birth_date",),
    )
    assert (
        ev.compare_declared(printed, {k: v for k, v in ok.items() if k != "given_names"})[0]
        == "UNKNOWN"
    )
    assert ev.compare_declared({}, ok)[0] == "UNKNOWN"
