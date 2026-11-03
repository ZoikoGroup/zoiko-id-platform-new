"""What the official register says about directors and persons with significant control, set beside what the customer declared, and the
fairness rules around appeals (ZID-ED-04 sections 04 and 06; EDR04-04 and EDR04-06)."""

from __future__ import annotations

import json

import pytest
from test_screening import POLICY, all_lists, clean_inputs, entry, person
from zid_platform.modules.screening.domain.decision import (
    RegisterEvidence,
    RegisterPerson,
    Role,
    ScreeningError,
    ScreeningStatus,
    Subject,
    check_appeal,
    check_appeal_reviewer,
    decide,
    evidence_object,
    reconcile_register,
    safe_view,
    tasks_for,
)
from zid_platform.modules.screening.domain.lists import ListId, PartyKind
from zid_platform.modules.screening.domain.matching import Party


def reg(*people: RegisterPerson, status: str = "OK") -> RegisterEvidence:
    return RegisterEvidence(status, tuple(people), "c" * 64, 1)


def psc(ref: str, name: str, *, ceased: bool = False) -> RegisterPerson:
    return RegisterPerson(
        ref,
        "PSC",
        Party(ref, PartyKind.PERSON, name),
        ("ownership-of-shares-25-to-50-percent",),
        ceased,
    )


def officer(ref: str, name: str) -> RegisterPerson:
    return RegisterPerson(ref, "OFFICER", Party(ref, PartyKind.PERSON, name), ("director",))


def test_a_person_the_register_lists_and_the_customer_left_out_is_a_task_not_a_pass_and_not_a_review() -> (
    None
):
    d = decide(clean_inputs(register=reg(psc("psc1", "Olivia Owner"), psc("psc2", "Peter Hidden"))))
    assert d.status is ScreeningStatus.GATHERING
    assert d.reasons == ("REGISTER_PSC_UNDECLARED:psc2",) and d.expiry_at is None
    [task] = tasks_for(d.reasons)
    assert task["code"] == "DECLARE_REGISTER_PSC" and task["count"] == 1
    assert "Peter" not in json.dumps(task) and "Hidden" not in json.dumps(task), (
        "the task says how many, never who"
    )


def test_a_declared_person_matches_the_register_whatever_the_word_order_accents_or_case() -> None:
    same = reg(
        psc("psc1", "OWNER, Olivia"), psc("psc2", "alice smith")
    )  # as Companies House writes, and the representative
    assert decide(clean_inputs(register=same)).status is ScreeningStatus.VERIFIED
    assert (
        decide(clean_inputs(register=None)).status is ScreeningStatus.VERIFIED
    )  # a country with no source changes nothing
    assert (
        decide(clean_inputs(register=reg(status="NOT_APPLICABLE"))).status
        is ScreeningStatus.VERIFIED
    )


def test_a_person_who_has_ceased_to_be_significant_is_not_asked_for() -> None:
    assert (
        decide(
            clean_inputs(
                register=reg(psc("psc1", "Olivia Owner"), psc("psc9", "Peter Gone", ceased=True))
            )
        ).status
        is ScreeningStatus.VERIFIED
    )


def test_everyone_the_register_lists_is_screened_even_if_the_customer_did_not_declare_them() -> (
    None
):
    lists = all_lists(UN_SC=entry(ListId.UN_SC, primary_name="Peter Hidden"))
    d = decide(
        clean_inputs(
            snapshots=lists, register=reg(psc("psc1", "Olivia Owner"), psc("psc2", "Peter Hidden"))
        )
    )
    assert d.status is ScreeningStatus.REVIEW_REQUIRED, "an alert goes to a person before any task"
    assert "SCREENING_ALERT" in d.reasons and any(m.party_ref == "reg:psc2" for m in d.matches)
    assert "REGISTER_PSC_UNDECLARED:psc2" in d.reasons


def test_a_register_that_could_not_be_read_is_a_review_never_a_clear() -> None:
    d = decide(clean_inputs(register=reg(status="UNAVAILABLE")))
    assert (
        d.status is ScreeningStatus.REVIEW_REQUIRED
        and "REGISTER_OWNERSHIP_UNAVAILABLE" in d.reasons
    )
    assert d.expiry_at is None


def test_a_protected_entry_the_register_does_not_name_goes_to_a_person() -> None:
    d = decide(clean_inputs(register=reg(psc("psc1", "Olivia Owner"), psc("psc2", ""))))
    assert (
        d.status is ScreeningStatus.REVIEW_REQUIRED and "REGISTER_PSC_PROTECTED:psc2" in d.reasons
    )
    assert not any(r.startswith("REGISTER_PSC_UNDECLARED") for r in d.reasons)


def test_a_declared_director_who_is_not_a_current_officer_is_a_review() -> None:
    subjects = (
        *clean_inputs().subjects,
        Subject(Role.DIRECTOR, person("dir1", "Dora Director")),
    )
    ok = decide(
        clean_inputs(
            subjects=subjects,
            register=reg(psc("psc1", "Olivia Owner"), officer("off1", "Dora Director")),
        )
    )
    assert ok.status is ScreeningStatus.VERIFIED
    bad = decide(
        clean_inputs(
            subjects=subjects,
            register=reg(psc("psc1", "Olivia Owner"), officer("off1", "Someone Else")),
        )
    )
    assert (
        bad.status is ScreeningStatus.REVIEW_REQUIRED
        and "DECLARED_DIRECTOR_NOT_ON_REGISTER:dir1" in bad.reasons
    )
    assert reconcile_register(
        clean_inputs(subjects=subjects, register=reg(psc("psc1", "Olivia Owner")))
    ) == ["DECLARED_DIRECTOR_NOT_ON_REGISTER:dir1"]


def test_the_signed_evidence_names_the_register_snapshot_and_holds_no_name() -> None:
    inp = clean_inputs(register=reg(psc("psc1", "Olivia Owner"), psc("psc2", "Peter Hidden")))
    body = evidence_object(tenant_id="t", case_id="c", inp=inp, decision=decide(inp), version=1)
    assert body["register"] == {
        "status": "OK", "seq": 1, "content_hash": "c" * 64,
        "people": [{"ref": "psc1", "kind": "PSC", "ceased": False}, {"ref": "psc2", "kind": "PSC", "ceased": False}],
    }  # fmt: skip
    text = json.dumps(body)
    for name in ("Olivia", "Owner", "Peter", "Hidden", "Alice", "Smith"):
        assert name not in text, name


def test_every_task_is_words_for_the_customer_with_no_name_or_list_content() -> None:
    reasons = [
        "REGISTER_PSC_UNDECLARED:psc1",
        "REGISTER_PSC_UNDECLARED:psc2",
        "MISSING_PARTY:ENTITY",
        "MISSING_OWNERSHIP",
        "OWNER_NOT_SCREENED:o1",
        "ALERT_STRONG",
        "OFAC_50_PERCENT_AGGREGATE",
    ]
    tasks = tasks_for(reasons)
    assert {t["code"] for t in tasks} == {
        "DECLARE_REGISTER_PSC",
        "ADD_PARTIES",
        "DECLARE_OWNERSHIP",
        "ADD_IDENTIFIED_OWNERS",
    }
    assert [t["count"] for t in tasks if t["code"] == "DECLARE_REGISTER_PSC"] == [2]
    assert all("OFAC" not in t["text"] and "alert" not in t["text"].lower() for t in tasks)
    assert safe_view(ScreeningStatus.GATHERING, reasons)["tasks"] == tasks


# ---- appeals: a fair second look


def test_an_appeal_is_for_a_rejection_and_a_correction_for_a_case_that_is_not_clear() -> None:
    sentence = "The birth year on the list is not mine; my passport shows 1975."
    check_appeal(ScreeningStatus.REJECTED, "APPEAL", sentence, open_appeal=False)
    for status in (
        ScreeningStatus.GATHERING,
        ScreeningStatus.REVIEW_REQUIRED,
        ScreeningStatus.REJECTED,
    ):
        check_appeal(status, "CORRECTION", sentence, open_appeal=False)
    for status in (
        ScreeningStatus.VERIFIED,
        ScreeningStatus.REVIEW_REQUIRED,
        ScreeningStatus.UNAVAILABLE,
    ):
        with pytest.raises(ScreeningError) as e:
            check_appeal(status, "APPEAL", sentence, open_appeal=False)
        assert e.value.code == "APPEAL_NOT_AVAILABLE"
    for status in (ScreeningStatus.VERIFIED, ScreeningStatus.UNAVAILABLE, ScreeningStatus.EXPIRED):
        with pytest.raises(ScreeningError):
            check_appeal(status, "CORRECTION", sentence, open_appeal=False)


def test_one_request_is_open_at_a_time_and_the_statement_must_be_a_sentence() -> None:
    with pytest.raises(ScreeningError) as e:
        check_appeal(
            ScreeningStatus.REJECTED,
            "APPEAL",
            "A long enough statement of the case.",
            open_appeal=True,
        )
    assert e.value.code == "APPEAL_ALREADY_OPEN"
    for bad in ("", "no", "x" * 19, "x" * 2001):
        with pytest.raises(ScreeningError) as e:
            check_appeal(ScreeningStatus.REJECTED, "APPEAL", bad, open_appeal=False)
        assert e.value.code == "STATEMENT_INVALID"
    with pytest.raises(ScreeningError) as e:
        check_appeal(
            ScreeningStatus.REJECTED,
            "COMPLAINT",
            "A long enough statement of the case.",
            open_appeal=False,
        )
    assert e.value.code == "APPEAL_KIND_INVALID"


def test_an_appeal_is_decided_by_someone_who_did_not_decide_before() -> None:
    check_appeal_reviewer("reviewer-b", ["reviewer-a"])
    with pytest.raises(ScreeningError) as e:
        check_appeal_reviewer("reviewer-a", ["reviewer-a", "reviewer-c"])
    assert e.value.code == "REVIEWER_CONFLICT"
    check_appeal_reviewer("anyone", [])


def test_the_policy_still_requires_the_five_list_families_the_register_changes_nothing_about() -> (
    None
):
    assert len(POLICY.required_lists) == 4
    missing = all_lists()
    del missing[ListId.UN_SC]
    assert (
        decide(clean_inputs(snapshots=missing, register=reg(psc("psc1", "Olivia Owner")))).status
        is ScreeningStatus.UNAVAILABLE
    )
