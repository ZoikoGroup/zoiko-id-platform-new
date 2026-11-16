"""DTV-30 verification workflows as data: structure, the static safety rules checked before publish, simulation and the publish gates. Synthetic only."""

from __future__ import annotations

import copy

import pytest
import yaml
from zid_platform.modules.verification.domain import workflow as wf
from zid_platform.modules.verification.infrastructure.workflow_registry import (
    CONFIG,
    DIRECTORY,
    workflow_definitions,
)

GOOD = {
    "id": "onboarding",
    "version": "1.0.0",
    "start": "identity",
    "required_checks": ["IDENTITY_DOCUMENT", "LIVENESS"],
    "steps": {
        "identity": {"type": "CHECK", "check": "IDENTITY_DOCUMENT", "next": "liveness"},
        "liveness": {"type": "CHECK", "check": "LIVENESS", "next": "decide"},
        "decide": {
            "type": "DECISION",
            "branches": [
                {
                    "when": {
                        "all_of": [
                            {"check": "IDENTITY_DOCUMENT", "outcome_in": ["VERIFIED"]},
                            {"check": "LIVENESS", "outcome_in": ["VERIFIED"]},
                        ]
                    },
                    "next": "approved",
                }
            ],
            "else": "look",
        },
        "look": {"type": "REVIEW", "role": "verification_reviewer", "next": "refer"},
        "refer": {"type": "END", "outcome": "REFER_TO_PERSON"},
        "approved": {"type": "END", "outcome": "APPROVED"},
    },
}


def make(mutate=None, **top):
    doc = copy.deepcopy(GOOD)
    doc.update(top)
    if mutate:
        mutate(doc)
    return wf.parse(doc, author="author-1")


def problems(w: wf.Workflow, **kw) -> set[str]:
    return {p.code for p in wf.analyze(w, **kw)}


# --------------------------------------------------------------------------------------------------------------------- structure


def test_a_well_formed_safe_workflow_has_no_problems_and_a_stable_hash():
    w = make()
    assert wf.analyze(w) == ()
    assert w.content_hash == make().content_hash
    assert make(version="1.0.1").content_hash != w.content_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(id="Bad Id"),
        lambda d: d.update(version=""),
        lambda d: d["steps"].clear(),
        lambda d: d["steps"]["identity"].update(type="SCRIPT"),
        lambda d: d["steps"]["identity"].update(check="TELEPATHY"),
        lambda d: d["steps"]["identity"].update(
            code="import os"
        ),  # an unknown key: nothing executable is ever accepted
        lambda d: d["steps"]["identity"].pop("next"),
        lambda d: d["steps"]["decide"].pop("else"),
        lambda d: d["steps"]["decide"].update(branches=[]),
        lambda d: d["steps"]["decide"]["branches"][0].update(when={"eval": "1==1"}),
        lambda d: d["steps"]["decide"]["branches"][0].update(
            when={"check": "LIVENESS", "outcome_in": ["PASS"]}
        ),
        lambda d: d["steps"]["decide"]["branches"][0].update(
            when={"check": "LIVENESS", "outcome_in": []}
        ),
        lambda d: d["steps"]["refer"].update(outcome="DECLINED"),  # only a person declines
        lambda d: d["steps"]["refer"].update(outcome="REJECTED"),
        lambda d: d["steps"]["look"].pop("role"),
    ],
)
def test_a_malformed_definition_is_refused_whole(mutate):
    with pytest.raises(wf.WorkflowError):
        make(mutate)


def test_a_condition_cannot_be_nested_without_limit():
    deep = {"check": "LIVENESS", "outcome_in": ["VERIFIED"]}
    for _ in range(wf.MAX_COND_DEPTH + 1):
        deep = {"all_of": [deep]}
    with pytest.raises(wf.WorkflowError, match="CONDITION_INVALID"):
        make(lambda d: d["steps"]["decide"]["branches"][0].update(when=deep))


def test_a_workflow_cannot_have_more_steps_than_the_limit():
    steps = {
        f"s{i}": {"type": "NOTIFY", "template": "t", "next": f"s{i + 1}"}
        for i in range(wf.MAX_STEPS)
    }
    steps[f"s{wf.MAX_STEPS}"] = {"type": "END", "outcome": "COMPLETED_NO_DECISION"}
    with pytest.raises(wf.WorkflowError, match="WORKFLOW_INVALID"):
        wf.parse({"id": "big", "version": "1", "start": "s0", "steps": steps})


# --------------------------------------------------------------------------------------------------------------------- reachability and ends


def test_a_branch_to_a_missing_step_a_missing_start_and_an_unreachable_step_are_each_named():
    assert "TARGET_MISSING" in problems(
        make(lambda d: d["steps"]["identity"].update(next="nowhere"))
    )
    assert "START_MISSING" in problems(make(start="ghost"))
    orphan = make(
        lambda d: d["steps"].update(island={"type": "END", "outcome": "COMPLETED_NO_DECISION"})
    )
    assert ("STEP_UNREACHABLE", "island") in {(p.code, p.where) for p in wf.analyze(orphan)}


def test_a_loop_is_refused_because_it_would_never_end():
    looped = make(lambda d: d["steps"]["look"].update(next="identity"))
    assert "CYCLE" in problems(looped)


def test_a_workflow_with_no_end_is_refused():
    only = {
        "id": "x",
        "version": "1",
        "start": "a",
        "steps": {"a": {"type": "NOTIFY", "template": "t", "next": "a"}},
    }
    assert problems(wf.parse(only)) & {"CYCLE", "NO_END"}


# --------------------------------------------------------------------------------------------------------------------- the approval guard


def test_an_approval_that_skips_a_required_check_is_refused_at_publish():
    def skip(d):
        d["steps"]["identity"]["next"] = "decide"  # liveness is never run

    got = {p.code for p in wf.analyze(make(skip))}
    assert "APPROVAL_SKIPS_A_REQUIRED_CHECK" in got


def test_an_approval_not_guarded_by_the_check_result_is_refused():
    def unguarded(d):
        d["steps"]["decide"]["branches"][0]["when"] = {
            "check": "IDENTITY_DOCUMENT",
            "outcome_in": ["VERIFIED"],
        }  # liveness not tested

    assert "APPROVAL_NOT_GUARDED" in problems(make(unguarded))

    def straight(d):
        d["steps"]["liveness"]["next"] = "approved"  # approve with no decision at all

    assert "APPROVAL_NOT_GUARDED" in problems(make(straight))


def test_a_guard_that_lets_an_adverse_or_unknown_result_through_is_refused():
    def leaky(d):
        d["steps"]["decide"]["branches"][0]["when"] = {
            "all_of": [
                {"check": "IDENTITY_DOCUMENT", "outcome_in": ["VERIFIED", "INDETERMINATE"]},
                {"check": "LIVENESS", "outcome_in": ["VERIFIED"]},
            ]
        }

    assert "APPROVAL_NOT_GUARDED" in problems(make(leaky))

    def either(
        d,
    ):  # any_of guarantees only what every alternative shares: here nothing about liveness
        d["steps"]["decide"]["branches"][0]["when"] = {
            "any_of": [
                {"check": "IDENTITY_DOCUMENT", "outcome_in": ["VERIFIED"]},
                {"check": "LIVENESS", "outcome_in": ["VERIFIED"]},
            ]
        }

    assert "APPROVAL_NOT_GUARDED" in problems(make(either))


def test_verified_limited_counts_only_when_the_workflow_says_so():
    def limited(d):
        for c in d["steps"]["decide"]["branches"][0]["when"]["all_of"]:
            c["outcome_in"] = ["VERIFIED", "VERIFIED_LIMITED"]

    assert "APPROVAL_NOT_GUARDED" in problems(make(limited))
    assert wf.analyze(make(limited, allow_limited=True)) == ()


def test_the_else_branch_is_never_a_route_to_approval():
    def else_approves(d):
        d["steps"]["decide"]["else"] = "approved"

    assert "APPROVAL_NOT_GUARDED" in problems(make(else_approves))


# --------------------------------------------------------------------------------------------------------------------- regulated checks


REGULATED = {
    "id": "regulated",
    "version": "1",
    "start": "screen",
    "required_checks": ["AML_SCREENING"],
    "steps": {
        "screen": {"type": "CHECK", "check": "AML_SCREENING", "next": "decide"},
        "decide": {
            "type": "DECISION",
            "branches": [
                {"when": {"check": "AML_SCREENING", "outcome_in": ["VERIFIED"]}, "next": "approved"}
            ],
            "else": "refer",
        },
        "refer": {"type": "END", "outcome": "REFER_TO_PERSON"},
        "approved": {"type": "END", "outcome": "APPROVED"},
    },
}


def test_a_regulated_check_never_auto_approves_a_person_must_look_after_it():
    w = wf.parse(REGULATED)
    assert "REGULATED_CHECK_AUTO_APPROVES" in problems(w)
    fixed = copy.deepcopy(REGULATED)
    fixed["steps"]["decide"]["branches"][0]["next"] = "look"
    fixed["steps"]["look"] = {"type": "REVIEW", "role": "compliance_reviewer", "next": "approved"}
    assert wf.analyze(wf.parse(fixed)) == ()


def test_a_review_before_the_regulated_check_does_not_count():
    early = copy.deepcopy(REGULATED)
    early["start"] = "look"
    early["steps"]["look"] = {"type": "REVIEW", "role": "compliance_reviewer", "next": "screen"}
    assert "REGULATED_CHECK_AUTO_APPROVES" in problems(wf.parse(early))


def test_a_check_whose_product_line_is_not_available_cannot_be_published():
    sof = copy.deepcopy(REGULATED)
    sof["required_checks"] = ["SOURCE_OF_FUNDS"]
    sof["steps"]["screen"]["check"] = "SOURCE_OF_FUNDS"
    sof["steps"]["decide"]["branches"][0]["when"]["check"] = "SOURCE_OF_FUNDS"
    sof["steps"]["decide"]["branches"][0]["next"] = "look"
    sof["steps"]["look"] = {"type": "REVIEW", "role": "r", "next": "approved"}
    w = wf.parse(sof)
    assert "PRODUCT_LINE_NOT_AVAILABLE" in problems(w, available_lines=frozenset())
    assert "PRODUCT_LINE_NOT_AVAILABLE" not in problems(
        w, available_lines=frozenset({"source_of_funds"})
    )


def test_a_workflow_with_too_many_paths_is_refused_not_guessed_at():
    steps: dict = {}
    for i in range(12):  # twelve decisions each with two ways on: 2^12 = 4096 paths
        steps[f"d{i}"] = {
            "type": "DECISION",
            "branches": [
                {"when": {"check": "LIVENESS", "outcome_in": ["VERIFIED"]}, "next": f"d{i + 1}"}
            ],
            "else": f"d{i + 1}",
        }
    steps["d12"] = {"type": "END", "outcome": "COMPLETED_NO_DECISION"}
    assert "TOO_COMPLEX" in problems(
        wf.parse({"id": "wide", "version": "1", "start": "d0", "steps": steps})
    )


# --------------------------------------------------------------------------------------------------------------------- simulation


def test_simulation_follows_the_results_and_has_no_side_effects():
    w = make()
    ok = wf.simulate(w, {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED"})
    assert (
        ok.outcome == "APPROVED"
        and ok.steps == ("identity", "liveness", "decide", "approved")
        and ok.needs_person == ()
    )
    bad = wf.simulate(w, {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "NOT_VERIFIED"})
    assert bad.outcome == "REFER_TO_PERSON" and bad.needs_person == (
        "verification_reviewer",
    )  # an adverse result goes to a person


def test_a_check_with_no_result_is_unavailable_never_assumed_fine():
    out = wf.simulate(make(), {"IDENTITY_DOCUMENT": "VERIFIED"})
    assert out.outcome == "REFER_TO_PERSON"
    assert wf.simulate(make(), {}).outcome == "REFER_TO_PERSON"


@pytest.mark.parametrize(
    "result",
    [
        "INDETERMINATE",
        "PENDING",
        "UNAVAILABLE",
        "REVOKED",
        "EXPIRED",
        "NOT_VERIFIED",
        "VERIFIED_LIMITED",
    ],
)
def test_every_result_other_than_verified_ends_with_a_person_never_an_approval(result):
    for which in ("IDENTITY_DOCUMENT", "LIVENESS"):
        results = {"IDENTITY_DOCUMENT": "VERIFIED", "LIVENESS": "VERIFIED", which: result}
        assert wf.simulate(make(), results).outcome != "APPROVED"


def test_an_unknown_result_word_is_an_error():
    with pytest.raises(wf.WorkflowError, match="OUTCOME_UNKNOWN"):
        wf.simulate(make(), {"LIVENESS": "PASS"})


def test_no_workflow_that_passes_analysis_can_approve_on_any_combination_of_results():
    """The property behind the guard, checked by brute force: for every assignment of results, APPROVED only when both required checks were VERIFIED."""
    import itertools

    w = make()
    assert wf.analyze(w) == ()
    for a, b in itertools.product(sorted(wf.OUTCOMES), repeat=2):
        outcome = wf.simulate(w, {"IDENTITY_DOCUMENT": a, "LIVENESS": b}).outcome
        assert (outcome == "APPROVED") == (a == "VERIFIED" and b == "VERIFIED")


# --------------------------------------------------------------------------------------------------------------------- publishing


def test_the_author_cannot_approve_their_own_version_and_an_approver_is_required():
    w = make()
    assert wf.may_publish(w, approver="someone-else", available_lines=frozenset()) == ()
    assert {
        p.code for p in wf.may_publish(w, approver="author-1", available_lines=frozenset())
    } == {"APPROVER_IS_AUTHOR"}
    assert {p.code for p in wf.may_publish(w, approver="", available_lines=frozenset())} == {
        "APPROVER_MISSING"
    }
    unsafe = make(lambda d: d["steps"]["liveness"].update(next="approved"))
    assert "APPROVAL_NOT_GUARDED" in {
        p.code for p in wf.may_publish(unsafe, approver="someone-else", available_lines=frozenset())
    }


def test_the_shipped_sample_loads_passes_the_rules_and_is_not_claimed_to_be_approved():
    defs = workflow_definitions()
    sample = defs[("standard_onboarding_sample", "0.1.0-SAMPLE")]
    assert wf.analyze(sample) == ()
    text = (CONFIG / DIRECTORY / "standard-onboarding-SAMPLE.yaml").read_text(encoding="utf-8")
    assert "SAMPLE" in text and "nobody has approved it" in text
    assert "approved_by" not in yaml.safe_load(text)
