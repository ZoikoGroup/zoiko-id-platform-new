"""A company's screening rolls up its owners' screens and never reads absence as clear (WF06 QA-029 to QA-033; CR-120)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.screening.domain import ownership_rollup as r

NOW = datetime(2026, 11, 18, 12, tzinfo=UTC)
AGE = timedelta(days=30)


def owner(ref: str, status: str = "CONFIRMED") -> r.Owner:
    return r.Owner(ref, status)


def screen(ref: str, state: str, days_ago: int = 1) -> r.OwnerScreen:
    return r.OwnerScreen(ref, state, NOW - timedelta(days=days_ago))


def run(owners, screens):
    return r.rollup(owners, screens, now=NOW, max_age=AGE)


def test_no_owner_identified_is_incomplete_never_clear() -> None:
    out = run([], [])
    assert out.status is r.Rollup.INCOMPLETE and out.reasons == ("NO_OWNERS_IDENTIFIED",)


def test_every_owner_clear_and_current_makes_the_company_complete_clear() -> None:
    out = run(
        [owner("a"), owner("b")],
        [screen("a", "CLOSED_CLEAR_FOR_SCOPE"), screen("b", "NO_CANDIDATE")],
    )
    assert (
        out.status is r.Rollup.COMPLETE_CLEAR
        and out.reasons == ()
        and out.designated == frozenset()
    )


def test_an_owner_with_no_screen_or_no_evidenced_identity_holds_the_company_incomplete() -> None:
    assert run([owner("a"), owner("b")], [screen("a", "CLOSED_CLEAR_FOR_SCOPE")]).reasons == (
        "OWNER_NOT_SCREENED",
    )
    out = run([owner("a", "NEEDS_EVIDENCE")], [screen("a", "CLOSED_CLEAR_FOR_SCOPE")])
    assert out.status is r.Rollup.INCOMPLETE and out.reasons == (
        "OWNER_NOT_EVIDENCED",
    )  # a screen cannot stand in for evidence of who they are


def test_a_stale_or_unresolved_screen_is_incomplete() -> None:
    assert run([owner("a")], [screen("a", "CLOSED_CLEAR_FOR_SCOPE", days_ago=45)]).reasons == (
        "OWNER_CHECK_STALE",
    )
    assert run([owner("a")], [screen("a", "CLOSED_UNRESOLVED")]).reasons == (
        "OWNER_CASE_UNRESOLVED",
    )


def test_an_owner_waiting_for_a_person_puts_the_company_in_review() -> None:
    out = run(
        [owner("a"), owner("b")],
        [screen("a", "REVIEW_REQUIRED"), screen("b", "CLOSED_CLEAR_FOR_SCOPE")],
    )
    assert out.status is r.Rollup.IN_REVIEW and out.designated == frozenset()


def test_a_restricted_owner_restricts_the_company_and_is_designated_for_the_ownership_rules() -> (
    None
):
    out = run(
        [owner("a"), owner("b"), owner("c")],
        [
            screen("a", "CLOSED_RESTRICTED"),
            screen("b", "REVIEW_REQUIRED"),
            screen("c", "CLOSED_CLEAR_FOR_SCOPE"),
        ],
    )
    assert out.status is r.Rollup.RESTRICTED and out.designated == frozenset(
        {"a"}
    )  # in review beats nothing, restricted beats review


def test_a_possible_match_still_waiting_is_never_a_designation() -> None:
    out = run([owner("a")], [screen("a", "REVIEW_REQUIRED")])
    assert out.designated == frozenset()


def test_the_latest_screen_of_a_person_is_the_one_that_counts() -> None:
    out = run(
        [owner("a")],
        [
            screen("a", "CLOSED_RESTRICTED", days_ago=10),
            screen("a", "CLOSED_CLEAR_FOR_SCOPE", days_ago=2),
        ],
    )
    assert out.status is r.Rollup.COMPLETE_CLEAR
    out = run(
        [owner("a")],
        [
            screen("a", "CLOSED_CLEAR_FOR_SCOPE", days_ago=10),
            screen("a", "CLOSED_RESTRICTED", days_ago=2),
        ],
    )
    assert out.status is r.Rollup.RESTRICTED


def test_only_owners_with_no_screen_or_a_stale_one_get_a_new_case_and_unevidenced_owners_are_left_for_their_evidence() -> (
    None
):
    owners = [
        owner("fresh"),
        owner("none"),
        owner("old"),
        owner("waiting"),
        owner("evidence", "NEEDS_EVIDENCE"),
    ]
    screens = [
        screen("fresh", "CLOSED_CLEAR_FOR_SCOPE"),
        screen("old", "CLOSED_CLEAR_FOR_SCOPE", days_ago=60),
        screen("waiting", "REVIEW_REQUIRED"),
    ]
    assert r.needs_screening(owners, screens, now=NOW, max_age=AGE) == ("none", "old")


def test_what_the_company_is_told_never_says_an_owner_is_restricted_and_carries_no_designation() -> (
    None
):
    out = run(
        [owner("a"), owner("b")],
        [screen("a", "CLOSED_RESTRICTED"), screen("b", "CLOSED_CLEAR_FOR_SCOPE")],
    )
    assert out.status is r.Rollup.RESTRICTED and out.designated == frozenset(
        {"a"}
    )  # the platform knows
    seen = r.customer_view(out)
    assert seen["status"] == "IN_REVIEW"  # the company is told only that it is being looked at
    assert {o["person_ref"]: o["reason"] for o in seen["owners"]} == {
        "a": "OWNER_CASE_IN_REVIEW",
        "b": "OWNER_CLEAR_FOR_SCOPE",
    }
    assert "RESTRICTED" not in repr(seen) and "designated" not in seen
