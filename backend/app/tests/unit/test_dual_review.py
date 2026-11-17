"""Two independent reviewers for a company approval no authoritative source backs (WF02 K07; acceptance T10, T11)."""

from __future__ import annotations

import pytest
from zid_platform.modules.organization.domain import dual_review as d
from zid_platform.modules.organization.domain.kyb import KybError, KybStatus

V, R, N = KybStatus.VERIFIED, KybStatus.REJECTED, KybStatus.NEEDS_REVIEW
SYSTEM = d.Row(None, N, ("NAME_MISMATCH",))


def step(history, reviewer, outcome, *, dual=True, escalation=False):
    return d.next_step(
        history,
        reviewer=reviewer,
        outcome=outcome,
        dual_required=dual,
        escalation_authorized=escalation,
    )


def after(history, reviewer, outcome, **kw):
    s = step(history, reviewer, outcome, **kw)
    return [*history, d.Row(reviewer, s.outcome, s.reasons)]


def test_a_case_with_an_authoritative_record_keeps_the_single_reviewer_path() -> None:
    assert step([SYSTEM], "a", V, dual=False) == d.Step(V, (), True)
    assert step([SYSTEM], "a", R, dual=False) == d.Step(R, (), True)
    assert step([SYSTEM], "a", N, dual=False) == d.Step(N, (), False)


def test_the_first_approval_is_a_first_finding_and_the_case_stays_in_review() -> None:
    s = step([SYSTEM], "a", V)
    assert s == d.Step(N, (d.FIRST_ACCEPTED,), False)


def test_a_second_different_reviewer_who_agrees_approves() -> None:
    h = after([SYSTEM], "a", V)
    s = step(h, "b", V)
    assert s == d.Step(V, (d.AGREED,), True)


def test_the_same_reviewer_cannot_give_the_second_opinion() -> None:  # T10
    h = after([SYSTEM], "a", V)
    with pytest.raises(KybError) as e:
        step(h, "a", V)
    assert e.value.code == "REVIEWER_NOT_INDEPENDENT"


def test_a_material_disagreement_escalates_and_is_never_last_write_wins() -> None:  # T11
    h = after([SYSTEM], "a", V)
    s = step(h, "b", R)
    assert s == d.Step(N, (d.ESCALATED, d.DISAGREEMENT), False)
    assert not s.final
    h2 = [*h, d.Row("b", s.outcome, s.reasons)]
    assert d.is_escalated(h2)


def test_an_escalated_case_needs_the_escalation_permission_and_a_third_person() -> None:
    h = after(after([SYSTEM], "a", V), "b", R)
    with pytest.raises(KybError) as e:
        step(h, "c", V)  # no permission
    assert e.value.code == "ESCALATION_NEEDS_COMPLIANCE"
    for earlier in ("a", "b"):
        with pytest.raises(KybError) as e2:
            step(h, earlier, V, escalation=True)
        assert e2.value.code == "REVIEWER_NOT_INDEPENDENT", earlier
    assert step(h, "c", V, escalation=True) == d.Step(V, (d.COMPLIANCE,), True)
    assert step(h, "c", R, escalation=True) == d.Step(R, (d.COMPLIANCE,), True)
    assert step(h, "c", N, escalation=True).final is False


def test_a_rejection_protects_so_one_reviewer_is_enough() -> None:
    assert step([SYSTEM], "a", R) == d.Step(R, (), True)


def test_a_request_for_more_evidence_is_one_reviewer_and_restarts_the_round() -> None:
    h = after([SYSTEM], "a", V)
    s = step(h, "b", N)
    assert s == d.Step(N, (d.EVIDENCE_REQUESTED,), False)
    h2 = [*h, d.Row("b", s.outcome, s.reasons)]
    # nobody has accepted in the new position, so an approval is again a first finding
    assert step([SYSTEM, *h2[-1:]], "c", V) == d.Step(N, (d.FIRST_ACCEPTED,), False)


def test_a_system_decision_starts_a_new_round_so_earlier_reviewers_may_look_again() -> None:
    h = after([SYSTEM], "a", V)
    refreshed = [
        *h,
        d.Row(None, N, ("SOURCES_CONFLICT",)),
    ]  # a refresh re-read the sources: new facts, new round
    assert d.reviewers_so_far(refreshed) == frozenset()
    assert step(refreshed, "a", V) == d.Step(N, (d.FIRST_ACCEPTED,), False)


def test_requires_dual_only_when_no_authoritative_record_backs_the_case() -> None:
    assert d.requires_dual({"source_authority": None, "registration_status": None})
    assert not d.requires_dual(
        {"source_authority": "COMPANIES_HOUSE", "registration_status": "active"}
    )
    assert not d.requires_dual(
        {"source_authority": None, "registration_status": "active"}
    )  # a status came from somewhere
    assert d.requires_dual({})


def test_position_markers_are_never_inherited_between_rulings() -> None:
    assert {
        d.FIRST_ACCEPTED,
        d.ESCALATED,
        d.AGREED,
        d.COMPLIANCE,
        d.DISAGREEMENT,
        d.EVIDENCE_REQUESTED,
    } <= d.POSITION_MARKERS
    assert "NAME_MISMATCH" not in d.POSITION_MARKERS


def test_the_position_of_a_case_is_a_stable_code_for_a_screen() -> None:
    assert d.position(N, (d.FIRST_ACCEPTED,)) == "AWAITING_SECOND_REVIEWER"
    assert d.position(N, (d.ESCALATED, d.DISAGREEMENT)) == "ESCALATED_TO_COMPLIANCE"
    assert d.position(V, (d.AGREED,)) == "APPROVED_BY_TWO_REVIEWERS"
    assert (
        d.position(V, (d.COMPLIANCE,)) == "DECIDED_BY_COMPLIANCE"
        and d.position(R, (d.COMPLIANCE,)) == "DECIDED_BY_COMPLIANCE"
    )
    assert d.position(V, ()) == "DECIDED" and d.position(R, ()) == "DECIDED"
    assert d.position(N, ()) == "IN_REVIEW"
