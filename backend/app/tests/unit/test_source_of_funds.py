"""DTV-28 source of funds and wealth: the rules that hold whatever questionnaire a compliance officer writes. Synthetic data only."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
import yaml
from zid_platform.modules.verification.domain import source_of_funds as sof
from zid_platform.modules.verification.infrastructure.questionnaire_registry import (
    CONFIG,
    DIRECTORY,
    current_questionnaire,
    source_of_funds_questionnaires,
)

NOW = datetime(2026, 11, 16, 12, tzinfo=UTC)
DOC = {
    "code": "source_of_funds",
    "version": "1.0.0",
    "status": "SAMPLE",
    "countries": ["GB", "ie"],
    "questions": [
        {
            "id": "Q-1",
            "text": "Main source?",
            "answer": "CHOICE",
            "choices": ["EMPLOYMENT", "GIFT"],
            "evidence_required_for": {"EMPLOYMENT": ["PAYSLIP"], "GIFT": ["GIFT_LETTER"]},
        },
        {"id": "Q-2", "text": "How much?", "answer": "AMOUNT"},
        {"id": "Q-3", "text": "When?", "answer": "DATE"},
        {"id": "Q-4", "text": "Borrowed?", "answer": "YES_NO"},
        {"id": "Q-5", "text": "Explain", "answer": "TEXT", "required": False},
    ],
}
GOOD = {
    "Q-1": "EMPLOYMENT",
    "Q-2": {"value": "25000.00", "currency": "GBP"},
    "Q-3": "2020-01-31",
    "Q-4": False,
}
EVIDENCE = [{"question_id": "Q-1", "type": "PAYSLIP", "upload_id": "u-1"}]


def q(**over):
    doc = copy.deepcopy(DOC)
    doc.update(over)
    return sof.parse(doc)


def test_a_questionnaire_is_identified_by_a_content_hash_and_any_change_changes_it():
    base = q()
    assert base.content_hash == q().content_hash and base.countries == ("GB", "IE")
    assert q(version="1.0.1").content_hash != base.content_hash
    reworded = copy.deepcopy(DOC)
    reworded["questions"][0]["text"] = "Main source of the funds?"
    assert sof.parse(reworded).content_hash != base.content_hash


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.update(code="other"),
        lambda d: d.update(countries=[]),
        lambda d: d.update(countries=["GBR"]),
        lambda d: d.update(status="LIVE"),
        lambda d: d.update(status="APPROVED"),  # approval needs a named approver and a legal basis
        lambda d: d.update(questions=[]),
        lambda d: d["questions"].append(dict(d["questions"][0])),  # a repeated id
        lambda d: d["questions"][1].update(choices=["A"]),  # choices on a non-CHOICE question
        lambda d: d["questions"][0].pop("choices"),
        lambda d: d["questions"][0].update(evidence_required_for={"EMPLOYMENT": ["NOT_A_TYPE"]}),
        lambda d: d["questions"][0].update(evidence_required_for={"RETIRED": ["PAYSLIP"]}),
        lambda d: d["questions"][1].update(evidence_required_for={"x": ["PAYSLIP"]}),
        lambda d: d["questions"][2].update(answer="NUMBER"),
        lambda d: d["questions"][2].update(text=" "),
    ],
)
def test_a_malformed_questionnaire_is_refused_whole(mutate):
    doc = copy.deepcopy(DOC)
    mutate(doc)
    with pytest.raises(sof.SofError):
        sof.parse(doc)


def test_approval_needs_a_named_approver_and_a_legal_basis_and_only_then_may_a_line_be_live():
    approved = q(status="APPROVED", approved_by="compliance.officer", legal_basis_ref="LEG-1")
    assert approved.usable_when_live
    assert not q().usable_when_live  # SAMPLE
    assert sof.questionnaire_for_case(approved, "gb", line_live=True) is approved
    with pytest.raises(sof.SofError, match="QUESTIONNAIRE_NOT_APPROVED"):
        sof.questionnaire_for_case(q(), "GB", line_live=True)
    assert sof.questionnaire_for_case(q(), "GB", line_live=False)  # fine while the line is not live
    with pytest.raises(sof.SofError, match="QUESTIONNAIRE_NOT_FOR_COUNTRY"):
        sof.questionnaire_for_case(approved, "US", line_live=False)


def test_complete_answers_with_the_evidence_the_answer_asks_for_are_complete():
    a = sof.assess(q(), GOOD, EVIDENCE)
    assert a.complete and a.problems == () and sof.after_submission(a) is sof.CaseState.NEEDS_REVIEW


def test_every_gap_is_named_so_the_person_can_fix_it():
    a = sof.assess(q(), {"Q-1": "GIFT", "Q-2": {"value": "10", "currency": "GBP"}}, [])
    assert not a.complete and sof.after_submission(a) is sof.CaseState.INCOMPLETE
    assert "ANSWER_MISSING:Q-3" in a.problems and "ANSWER_MISSING:Q-4" in a.problems
    assert "EVIDENCE_MISSING:Q-1:GIFT_LETTER" in a.problems
    assert ("Q-1", "GIFT_LETTER") in a.needed_evidence
    assert "ANSWER_MISSING:Q-5" not in a.problems  # optional


def test_evidence_must_match_the_answer_given_not_just_exist():
    wrong = [{"question_id": "Q-1", "type": "GIFT_LETTER", "upload_id": "u-2"}]
    a = sof.assess(q(), GOOD, wrong)
    assert "EVIDENCE_MISSING:Q-1:PAYSLIP" in a.problems


def test_an_evidence_reference_without_an_upload_does_not_count():
    a = sof.assess(q(), GOOD, [{"question_id": "Q-1", "type": "PAYSLIP", "upload_id": ""}])
    assert "EVIDENCE_MISSING:Q-1:PAYSLIP" in a.problems


@pytest.mark.parametrize(
    ("question", "bad"),
    [
        ("Q-1", "RETIRED"),
        ("Q-2", {"value": 25000, "currency": "GBP"}),  # a number, not a decimal string
        ("Q-2", {"value": "-1", "currency": "GBP"}),
        ("Q-2", {"value": "NaN", "currency": "GBP"}),
        ("Q-2", {"value": "5", "currency": "gbp"}),
        ("Q-2", {"value": "5", "currency": "GBP", "extra": 1}),
        ("Q-3", "31/01/2020"),
        ("Q-3", "2999-01-01"),  # in the future
        ("Q-4", "no"),
        ("Q-5", "x" * (sof.MAX_TEXT + 1)),
    ],
)
def test_a_wrongly_shaped_answer_is_invalid_not_coerced(question, bad):
    answers = {**GOOD, question: bad}
    a = sof.assess(q(), answers, EVIDENCE)
    assert f"ANSWER_INVALID:{question}" in a.problems and not a.complete


def test_an_answer_to_a_question_that_does_not_exist_is_refused():
    a = sof.assess(q(), {**GOOD, "Q-9": "x"}, EVIDENCE)
    assert "ANSWER_UNKNOWN_QUESTION:Q-9" in a.problems


def test_the_system_never_clears_anyone_by_itself():
    """The only automatic outcomes are INCOMPLETE and NEEDS_REVIEW; there is no path from an assessment to CLEARED."""
    a = sof.assess(q(), GOOD, EVIDENCE)
    assert sof.after_submission(a) not in {sof.CaseState.CLEARED, sof.CaseState.NOT_CLEARED_FINAL}
    assert not hasattr(sof, "score") and not hasattr(sof, "risk_score")


def decide(decision, **over):
    args = {
        "state": sof.CaseState.NEEDS_REVIEW,
        "reviewer": "rev-1",
        "submitted_by": "sub-1",
        "first_reviewer": None,
        "reasons": ["FUNDS_EXPLAINED_AND_EVIDENCED"],
        "now": NOW,
    }
    args.update(over)
    return sof.decide(args.pop("state"), decision, **args)


def test_a_clearance_is_a_human_decision_and_it_expires():
    out = decide(sof.Decision.CLEAR)
    assert out.state is sof.CaseState.CLEARED and out.valid_until == NOW + timedelta(
        days=sof.CLEARANCE_VALID_DAYS
    )
    assert sof.is_clear(out.state, out.valid_until, NOW + timedelta(days=364))
    assert not sof.is_clear(out.state, out.valid_until, NOW + timedelta(days=366))
    assert not sof.is_clear(sof.CaseState.NEEDS_REVIEW, None, NOW)


def test_the_person_who_submitted_cannot_decide_their_own_case():
    with pytest.raises(sof.SofError, match="REVIEWER_NOT_INDEPENDENT"):
        decide(sof.Decision.CLEAR, reviewer="sub-1")
    with pytest.raises(sof.SofError, match="REVIEWER_NOT_INDEPENDENT"):
        decide(sof.Decision.CLEAR, reviewer="")


@pytest.mark.parametrize(
    "reasons", [[], ["NOT_A_CODE"], ["FUNDS_EXPLAINED_AND_EVIDENCED", "MADE_UP"]]
)
def test_a_decision_needs_reason_codes_from_the_fixed_list(reasons):
    with pytest.raises(sof.SofError, match="REASON_CODE_REQUIRED"):
        decide(sof.Decision.CLEAR, reasons=reasons)


@pytest.mark.parametrize(
    "state",
    [s for s in sof.CaseState if s not in {sof.CaseState.NEEDS_REVIEW, sof.CaseState.APPEALED}],
)
def test_only_a_case_waiting_for_a_decision_can_be_decided(state):
    with pytest.raises(sof.SofError, match="CASE_NOT_AWAITING_DECISION"):
        decide(sof.Decision.CLEAR, state=state)


def test_an_adverse_first_decision_is_never_final_and_can_be_appealed():
    out = decide(sof.Decision.NOT_CLEAR, reasons=["SOURCE_NOT_EXPLAINED"])
    assert out.state is sof.CaseState.NOT_CLEARED_PENDING_APPEAL and out.valid_until is None
    assert (
        sof.appeal(out.state, by="subject-1", subject="subject-1", submitted_by="sub-1")
        is sof.CaseState.APPEALED
    )
    assert (
        sof.appeal(out.state, by="sub-1", subject="subject-1", submitted_by="sub-1")
        is sof.CaseState.APPEALED
    )
    with pytest.raises(sof.SofError, match="APPEAL_NOT_BY_THE_PERSON"):
        sof.appeal(out.state, by="stranger", subject="subject-1", submitted_by="sub-1")
    with pytest.raises(sof.SofError, match="CASE_NOT_APPEALABLE"):
        sof.appeal(sof.CaseState.CLEARED, by="subject-1", subject="subject-1", submitted_by="sub-1")


def test_an_appeal_is_decided_by_someone_who_did_not_decide_first_and_only_then_is_it_final():
    first = decide(sof.Decision.NOT_CLEAR, reasons=["EVIDENCE_INSUFFICIENT"])
    assert first.state is sof.CaseState.NOT_CLEARED_PENDING_APPEAL
    with pytest.raises(sof.SofError, match="APPEAL_NEEDS_A_DIFFERENT_REVIEWER"):
        decide(
            sof.Decision.NOT_CLEAR, state=sof.CaseState.APPEALED, reviewer="rev-1", first_reviewer="rev-1",
            reasons=["APPEAL_NO_NEW_INFORMATION"],
        )  # fmt: skip
    final = decide(
        sof.Decision.NOT_CLEAR, state=sof.CaseState.APPEALED, reviewer="rev-2", first_reviewer="rev-1",
        reasons=["APPEAL_NO_NEW_INFORMATION"],
    )  # fmt: skip
    assert final.state is sof.CaseState.NOT_CLEARED_FINAL
    won = decide(
        sof.Decision.CLEAR, state=sof.CaseState.APPEALED, reviewer="rev-2", first_reviewer="rev-1",
        reasons=["APPEAL_NEW_EVIDENCE"],
    )  # fmt: skip
    assert won.state is sof.CaseState.CLEARED and won.valid_until


def test_asking_for_more_is_not_an_adverse_outcome_and_an_appeal_cannot_ask_for_more():
    more = decide(sof.Decision.ASK_FOR_MORE, reasons=["EVIDENCE_INSUFFICIENT"])
    assert more.state is sof.CaseState.INCOMPLETE and more.valid_until is None
    with pytest.raises(sof.SofError, match="APPEAL_CANNOT_ASK_FOR_MORE"):
        decide(
            sof.Decision.ASK_FOR_MORE, state=sof.CaseState.APPEALED, reviewer="rev-2", first_reviewer="rev-1",
            reasons=["EVIDENCE_INSUFFICIENT"],
        )  # fmt: skip


def test_the_shipped_questionnaire_parses_is_sample_and_cannot_be_used_on_a_live_line():
    all_q = source_of_funds_questionnaires()
    assert "0.1.0-SAMPLE" in all_q
    current = current_questionnaire()
    assert current.status is sof.Status.SAMPLE and not current.usable_when_live
    raw = yaml.safe_load((CONFIG / DIRECTORY / "0.1.0-SAMPLE.yaml").read_text(encoding="utf-8"))
    assert (
        raw["status"] == "SAMPLE" and "approved_by" not in raw
    )  # nobody has approved it, and the file does not pretend otherwise
    with pytest.raises(sof.SofError, match="QUESTIONNAIRE_NOT_APPROVED"):
        sof.questionnaire_for_case(current, "GB", line_live=True)


def test_the_newest_approved_questionnaire_wins_over_a_newer_sample():
    old = q(version="1.0.0", status="APPROVED", approved_by="co", legal_basis_ref="L")
    newer_sample = q(version="2.0.0")
    chosen = current_questionnaire({old.version: old, newer_sample.version: newer_sample})
    assert chosen.version == "1.0.0"
    newest = q(version="1.10.0", status="APPROVED", approved_by="co", legal_basis_ref="L")
    assert current_questionnaire({old.version: old, newest.version: newest}).version == "1.10.0"
    with pytest.raises(sof.SofError, match="NO_QUESTIONNAIRE"):
        current_questionnaire({})
