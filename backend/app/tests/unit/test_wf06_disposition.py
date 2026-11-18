"""WF06 decisions, holds and appeals (CR-113): maker-checker, legal-only acts, stale revisions, append-only history (QA-020, QA-031, QA-039, QA-049, QA-052)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import disposition as d
from zid_platform.modules.screening.domain.coverage import Disposition as D

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
LONG = "The date of birth and the country on the list entry both differ from the subject."


def proposal(
    disposition=D.CLEARED_FOR_PURPOSE,
    action=d.Action.RELEASE,
    maker="a1",
    role=d.Role.ANALYST,
    **kw,
):
    base = {
        "case_id": "scr_1",
        "expected_revision": 0,
        "disposition": disposition,
        "action": action,
        "maker": maker,
        "maker_role": role,
        "rationale": LONG,
        "reason_codes": ("CONFLICTING_DOB",),
        "evidence_refs": ("ev_1",),
        "policy_version": "v1",
        "review_by": NOW + timedelta(days=90),
    }
    base.update(kw)
    return d.Proposal(**base)


def code(fn) -> str:
    with pytest.raises(d.DispositionError) as e:
        fn()
    return e.value.code


def test_a_false_positive_needs_a_rationale_codes_and_cited_evidence() -> None:
    assert (
        code(lambda: d.check_proposal(proposal(rationale="no"), current_revision=0))
        == "RATIONALE_REQUIRED"
    )  # QA-020
    assert (
        code(lambda: d.check_proposal(proposal(reason_codes=()), current_revision=0))
        == "REASON_CODES_REQUIRED"
    )
    assert (
        code(lambda: d.check_proposal(proposal(evidence_refs=()), current_revision=0))
        == "EVIDENCE_REQUIRED"
    )
    d.check_proposal(proposal(), current_revision=0)


def test_clearing_a_case_needs_a_second_person_who_is_not_the_maker_and_not_an_analyst() -> None:
    p = proposal()
    assert d.needs_checker(p)
    assert (
        code(lambda: d.append([], p, checker=None, checker_role=None, at=NOW)) == "CHECKER_REQUIRED"
    )
    assert (
        code(lambda: d.append([], p, checker="a1", checker_role=d.Role.SENIOR, at=NOW))
        == "CHECKER_IS_THE_MAKER"
    )  # QA-039
    assert (
        code(lambda: d.append([], p, checker="a2", checker_role=d.Role.ANALYST, at=NOW))
        == "CHECKER_NEEDS_SENIOR_ROLE"
    )
    h = d.append([], p, checker="s1", checker_role=d.Role.SENIOR, at=NOW)
    assert h[0].revision == 1 and h[0].checker == "s1" and h[0].case_hash


def test_a_hold_or_an_edd_request_needs_no_second_person() -> None:
    p = proposal(D.HOLD_PENDING_REVIEW, d.Action.HOLD, evidence_refs=())
    assert not d.needs_checker(p)
    assert len(d.append([], p, checker=None, checker_role=None, at=NOW)) == 1
    edd = proposal(D.EDD_REQUIRED, d.Action.EDD, evidence_refs=())
    assert len(d.append([], edd, checker=None, checker_role=None, at=NOW)) == 1


def test_a_customer_administrator_can_only_ask_for_a_review() -> None:
    admin = proposal(role=d.Role.CUSTOMER_ADMIN, maker="c1")
    assert (
        code(lambda: d.check_proposal(admin, current_revision=0)) == "ROLE_CANNOT_DECIDE"
    )  # QA-052
    r = d.request_review(
        "scr_1", requested_by="c1", role=d.Role.CUSTOMER_ADMIN, note="Please look at this again"
    )
    assert r.case_id == "scr_1"
    assert (
        code(
            lambda: d.request_review(
                "scr_1", requested_by="c1", role=d.Role.CUSTOMER_ADMIN, note="no"
            )
        )
        == "STATEMENT_INVALID"
    )
    assert (
        code(
            lambda: d.append(
                [], proposal(), checker="c2", checker_role=d.Role.CUSTOMER_ADMIN, at=NOW
            )
        )
        == "ROLE_CANNOT_DECIDE"
    )


@pytest.mark.parametrize("action", [d.Action.FREEZE, d.Action.BLOCK, d.Action.REPORT])
def test_freeze_block_and_report_are_legal_only_and_need_a_citation(action) -> None:
    p = proposal(D.PROHIBITED, action, legal_citation="Reg 2014/833 Art 5")
    assert code(lambda: d.check_proposal(p, current_revision=0)) == "LEGAL_ROLE_REQUIRED"
    legal = replace(p, maker="l1", maker_role=d.Role.LEGAL)
    d.check_proposal(legal, current_revision=0)
    assert (
        code(lambda: d.check_proposal(replace(legal, legal_citation=None), current_revision=0))
        == "LEGAL_CITATION_REQUIRED"
    )
    assert (
        code(lambda: d.append([], legal, checker="s1", checker_role=d.Role.SENIOR, at=NOW))
        == "LEGAL_ROLE_REQUIRED"
    )
    assert len(d.append([], legal, checker="l2", checker_role=d.Role.LEGAL, at=NOW)) == 1


def test_prohibited_is_legal_only_even_with_a_harmless_action() -> None:
    p = proposal(D.PROHIBITED, d.Action.REJECT, legal_citation="x")
    assert code(lambda: d.check_proposal(p, current_revision=0)) == "LEGAL_ROLE_REQUIRED"


def test_an_action_must_fit_the_disposition() -> None:
    assert (
        code(
            lambda: d.check_proposal(
                proposal(D.CLEARED_FOR_PURPOSE, d.Action.REJECT), current_revision=0
            )
        )
        == "ACTION_NOT_FOR_DISPOSITION"
    )
    assert (
        code(
            lambda: d.check_proposal(
                proposal(D.HOLD_PENDING_REVIEW, d.Action.RELEASE, evidence_refs=()),
                current_revision=0,
            )
        )
        == "ACTION_NOT_FOR_DISPOSITION"
    )


def test_a_restriction_needs_a_review_date_so_it_cannot_run_for_ever() -> None:
    p = proposal(D.RESTRICTED, d.Action.HOLD, review_by=None)
    assert code(lambda: d.check_proposal(p, current_revision=0)) == "REVIEW_DATE_REQUIRED"


def test_a_decision_made_against_a_stale_revision_is_refused_with_409() -> None:
    first = d.append(
        [],
        proposal(D.HOLD_PENDING_REVIEW, d.Action.HOLD, evidence_refs=()),
        checker=None,
        checker_role=None,
        at=NOW,
    )
    stale = proposal(D.CLEARED_FOR_PURPOSE, expected_revision=0)
    with pytest.raises(d.DispositionError) as e:
        d.append(first, stale, checker="s1", checker_role=d.Role.SENIOR, at=NOW)
    assert e.value.code == "REVISION_CONFLICT" and e.value.http_status == 409  # QA-049
    fresh = replace(stale, expected_revision=1)
    assert (
        d.append(first, fresh, checker="s1", checker_role=d.Role.SENIOR, at=NOW)[-1].revision == 2
    )


def test_a_screened_party_cannot_decide_their_own_case() -> None:
    assert (
        code(
            lambda: d.check_proposal(proposal(maker="p9"), current_revision=0, subject_refs=["p9"])
        )
        == "MAKER_IS_A_SUBJECT"
    )


def test_history_is_append_only_and_a_chain_an_auditor_can_verify() -> None:
    h = d.append(
        [],
        proposal(D.HOLD_PENDING_REVIEW, d.Action.HOLD, evidence_refs=()),
        checker=None,
        checker_role=None,
        at=NOW,
    )
    h = d.append(
        h,
        proposal(expected_revision=1),
        checker="s1",
        checker_role=d.Role.SENIOR,
        at=NOW + timedelta(hours=1),
    )
    assert [r.revision for r in h] == [1, 2] and h[1].supersedes == 1
    assert d.verify_chain(h) and d.current(h).disposition is D.CLEARED_FOR_PURPOSE
    edited = [replace(h[0], rationale="changed after the fact"), h[1]]
    assert not d.verify_chain(edited)
    assert not d.verify_chain([h[1]])  # a removed revision breaks the chain
    assert d.verify_chain([])


def test_an_appeal_adds_a_revision_by_someone_new_and_keeps_the_original() -> None:
    h = d.append(
        [], proposal(D.RESTRICTED, d.Action.HOLD), checker="s1", checker_role=d.Role.SENIOR, at=NOW
    )
    args = {
        "case_id": "scr_1",
        "appellant_note": "The registration number on the list entry is a different company.",
        "new_disposition": D.CLEARED_FOR_PURPOSE,
        "action": d.Action.RELEASE,
        "rationale": LONG,
        "reason_codes": ("DIFFERENT_REGISTRATION_NUMBER",),
        "evidence_refs": ("ev_9",),
        "policy_version": "v1",
        "at": NOW + timedelta(days=1),
    }
    for same in ("a1", "s1"):  # QA-031: the maker and the checker cannot hear the appeal
        assert (
            code(
                lambda same=same: d.open_appeal(
                    h,
                    reviewer=same,
                    reviewer_role=d.Role.SENIOR,
                    checker="s2",
                    checker_role=d.Role.SENIOR,
                    **args,
                )
            )
            == "REVIEWER_CONFLICT"
        )
    out = d.open_appeal(
        h,
        reviewer="s5",
        reviewer_role=d.Role.SENIOR,
        checker="s6",
        checker_role=d.Role.SENIOR,
        **args,
    )
    assert [r.kind for r in out] == ["DECISION", "APPEAL"] and out[0] == h[
        0
    ]  # the original is untouched
    assert out[1].supersedes == 1 and d.verify_chain(out)
    assert (
        code(
            lambda: d.open_appeal(
                h,
                reviewer="s5",
                reviewer_role=d.Role.SENIOR,
                checker="s6",
                checker_role=d.Role.SENIOR,
                **{**args, "appellant_note": "no"},
            )
        )
        == "STATEMENT_INVALID"
    )


def test_the_downstream_event_id_is_stable_so_a_retry_cannot_act_twice() -> None:
    assert d.event_id("scr_1", 2, d.Action.HOLD) == d.event_id("scr_1", 2, d.Action.HOLD)
    assert (
        len(
            {
                d.event_id("scr_1", 2, d.Action.HOLD),
                d.event_id("scr_1", 3, d.Action.HOLD),
                d.event_id("scr_1", 2, d.Action.RELEASE),
                d.event_id("scr_2", 2, d.Action.HOLD),
            }
        )
        == 4
    )
