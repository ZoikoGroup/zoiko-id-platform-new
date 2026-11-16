"""DTV-30 the workflow executor: a state machine that waits for a check or a person, agrees with the simulator, and can only be moved by the event it awaits."""

from __future__ import annotations

import copy
import itertools

import pytest
from test_workflow import GOOD, REGULATED
from zid_platform.modules.verification.domain import workflow as wf
from zid_platform.modules.verification.domain import workflow_run as run

W = wf.parse(GOOD)


def to_end(w, results, *, reviewer="rev-1", applicant="app-1"):
    state = run.begin(w)
    for _ in range(40):
        if state.status is run.Status.ENDED:
            return state
        if state.status is run.Status.WAITING_FOR_CHECK:
            state = run.report_check(
                w, state, state.awaiting_check, results.get(state.awaiting_check, "UNAVAILABLE")
            )
        else:
            state = run.complete_review(w, state, reviewer=reviewer, applicant=applicant)
    raise AssertionError("did not end")


def test_a_new_run_waits_for_the_first_check_and_says_which():
    s = run.begin(W)
    assert (
        s.status is run.Status.WAITING_FOR_CHECK
        and s.awaiting_check == "IDENTITY_DOCUMENT"
        and s.outcome is None
    )
    assert s.workflow_hash == W.content_hash and [e.kind for e in s.events] == ["ENTERED"]


def test_results_move_the_run_check_by_check_to_an_approval_only_when_both_are_verified():
    s = run.report_check(W, run.begin(W), "IDENTITY_DOCUMENT", "VERIFIED")
    assert s.awaiting_check == "LIVENESS" and s.outcome is None
    s = run.report_check(W, s, "LIVENESS", "VERIFIED")
    assert s.status is run.Status.ENDED and s.outcome == "APPROVED"
    assert [e.kind for e in s.events][-1] == "ENDED"


def test_a_result_that_is_not_verified_ends_with_a_person_after_a_review_and_never_an_approval():
    s = run.report_check(W, run.begin(W), "IDENTITY_DOCUMENT", "VERIFIED")
    s = run.report_check(W, s, "LIVENESS", "NOT_VERIFIED")
    assert s.status is run.Status.WAITING_FOR_REVIEW and s.awaiting_role == "verification_reviewer"
    done = run.complete_review(W, s, reviewer="rev-1", applicant="app-1")
    assert done.status is run.Status.ENDED and done.outcome == "REFER_TO_PERSON"


def test_the_applicant_cannot_review_their_own_run_and_a_reviewer_is_required():
    s = run.report_check(W, run.begin(W), "IDENTITY_DOCUMENT", "NOT_VERIFIED")
    s = run.report_check(W, s, "LIVENESS", "VERIFIED")
    for who in ("app-1", ""):
        with pytest.raises(run.RunError, match="REVIEWER_NOT_INDEPENDENT"):
            run.complete_review(W, s, reviewer=who, applicant="app-1")


def test_only_the_awaited_event_is_accepted():
    s = run.begin(W)
    with pytest.raises(run.RunError, match="CHECK_NOT_AWAITED"):
        run.report_check(W, s, "LIVENESS", "VERIFIED")  # not asked for yet
    with pytest.raises(run.RunError, match="REVIEW_NOT_AWAITED"):
        run.complete_review(W, s, reviewer="r", applicant="a")
    with pytest.raises(run.RunError, match="OUTCOME_UNKNOWN"):
        run.report_check(W, s, "IDENTITY_DOCUMENT", "PASS")
    after = run.report_check(W, s, "IDENTITY_DOCUMENT", "VERIFIED")
    with pytest.raises(run.RunError, match="CHECK_NOT_AWAITED"):
        run.report_check(
            W, after, "IDENTITY_DOCUMENT", "NOT_VERIFIED"
        )  # a second answer for the same check changes nothing


def test_an_ended_run_accepts_nothing():
    done = to_end(W, {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED"})
    with pytest.raises(run.RunError, match="RUN_ENDED"):
        run.report_check(W, done, "LIVENESS", "VERIFIED")
    with pytest.raises(run.RunError, match="RUN_ENDED"):
        run.complete_review(W, done, reviewer="r", applicant="a")


def test_a_run_stays_on_the_version_it_started_on():
    s = run.begin(W)
    other = wf.parse({**copy.deepcopy(GOOD), "version": "1.0.1"})
    with pytest.raises(run.RunError, match="WORKFLOW_CHANGED"):
        run.report_check(other, s, "IDENTITY_DOCUMENT", "VERIFIED")


def test_a_run_never_mutates_the_state_it_was_given():
    s = run.begin(W)
    before = (s.results, s.events, s.step)
    run.report_check(W, s, "IDENTITY_DOCUMENT", "VERIFIED")
    assert (s.results, s.events, s.step) == before


def test_the_trace_holds_check_names_outcome_words_and_roles_never_a_person():
    done = to_end(
        W,
        {"IDENTITY_DOCUMENT": "NOT_VERIFIED", "LIVENESS": "VERIFIED"},
        reviewer="rev-secret",
        applicant="app-secret",
    )
    text = repr(done.events)
    assert "rev-secret" not in text and "app-secret" not in text
    assert "IDENTITY_DOCUMENT:NOT_VERIFIED" in text and "verification_reviewer" in text


# ----------------------------------------------------------------------------------------------------------------- notify steps


def with_notify():
    doc = copy.deepcopy(GOOD)
    doc["steps"]["liveness"]["next"] = "tell"
    doc["steps"]["tell"] = {"type": "NOTIFY", "template": "ZID-EM-777", "next": "decide"}
    return wf.parse(doc)


def test_a_notify_step_is_recorded_for_the_caller_to_send_and_the_machine_moves_on():
    w = with_notify()
    s = run.report_check(
        w,
        run.report_check(w, run.begin(w), "IDENTITY_DOCUMENT", "VERIFIED"),
        "LIVENESS",
        "VERIFIED",
    )
    assert (
        s.notifications == ("ZID-EM-777",) and s.outcome == "APPROVED"
    )  # it queued the template and did not send it


# ----------------------------------------------------------------------------------------------------------------- agreement with the simulator


@pytest.mark.parametrize("doc", [GOOD, REGULATED])
def test_the_executor_ends_exactly_where_the_simulator_says_it_would_for_every_combination_of_results(
    doc,
):
    w = wf.parse(doc)
    checks = sorted({s.check.value for s in w.steps.values() if s.check})
    for combo in itertools.product(sorted(wf.OUTCOMES), repeat=len(checks)):
        results = dict(zip(checks, combo, strict=True))
        sim = wf.simulate(w, results)
        ran = to_end(w, results)
        assert ran.outcome == sim.outcome, results
        assert sim.outcome in wf.END_OUTCOMES


def test_the_simulator_reveals_a_result_only_when_its_step_is_reached():
    """A decision that looks at a check not yet run sees UNAVAILABLE, in the simulator as in a real run."""
    doc = copy.deepcopy(GOOD)
    doc["steps"]["identity"]["next"] = "early"
    doc["steps"]["early"] = {
        "type": "DECISION",
        "branches": [
            {"when": {"check": "LIVENESS", "outcome_in": ["VERIFIED"]}, "next": "approved"}
        ],
        "else": "liveness",
    }
    w = wf.parse(doc)
    assert wf.simulate(w, {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED"}).steps[:3] == (
        "identity",
        "early",
        "liveness",
    )
    ended = to_end(w, {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED"})
    assert [e.step for e in ended.events if e.kind == "ENTERED"] == ["identity", "liveness"]


def test_no_run_of_a_workflow_that_passes_analysis_can_end_approved_unless_both_checks_were_verified():
    assert wf.analyze(W) == ()
    for a, b in itertools.product(sorted(wf.OUTCOMES), repeat=2):
        ended = to_end(W, {"IDENTITY_DOCUMENT": a, "LIVENESS": b})
        assert (ended.outcome == "APPROVED") == (a == "VERIFIED" and b == "VERIFIED")
