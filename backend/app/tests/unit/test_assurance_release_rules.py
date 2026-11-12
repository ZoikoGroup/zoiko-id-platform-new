"""The rules of independent assurance (ZID-UAT-005; CR-074 slice S1). Every Document 05 release-blocking assertion is a test here."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.assurance.domain import rules as r
from zid_platform.modules.assurance.domain.rules import Candidate, Case, Signature, Status

NOW = datetime(2026, 11, 12, tzinfo=UTC)


def manifest(**over) -> dict:
    m = {
        "run_id": "run_1", "case_id": "DOC-02", "requirement_ids": ["DTV-02"], "build_sha256": "b" * 64, "infra_ref": "env-1",
        "tester_principal": "qa_1", "tenant_id": "synthetic_a", "country": "DE", "policy_pack": "DE-QES@1", "fixture_hashes": ["sha256:f"],
        "oracle_version": "o1", "result": "PASS", "captured_at": "2026-11-12T00:00:00Z", "evidence_signing_key_id": "k1",
        "manifest_signature": "sig", "trace_refs": ["t1"],
    }  # fmt: skip
    return {**m, **over}


# ---------------------------------------------------------------------------------------------------------------- statuses and manifests


def test_a_pass_needs_a_complete_manifest_and_a_screenshot_only_pass_is_inconclusive() -> None:
    assert r.verdict_from(manifest()) is Status.PASS
    assert r.verdict_from(None) is Status.INCONCLUSIVE
    assert r.verdict_from(manifest(trace_refs=[])) is Status.INCONCLUSIVE, (
        "no trace, receipt or snapshot: screenshot-only"
    )
    assert r.verdict_from(manifest(manifest_signature="")) is Status.INCONCLUSIVE
    assert r.verdict_from(manifest(result="PASS-ish")) is Status.INCONCLUSIVE
    assert "NO_TRACE_RECEIPT_OR_SOURCE_SNAPSHOT" in r.manifest_problems(manifest(trace_refs=[]))
    assert r.verdict_from(manifest(trace_refs=[], provider_receipt_refs=["p1"])) is Status.PASS


def test_a_failure_is_never_weakened_by_a_thin_manifest() -> None:
    assert r.verdict_from({"result": "FAIL"}) is Status.FAIL


def test_the_state_machine_allows_only_the_documented_moves() -> None:
    assert r.move(Status.NOT_RUN, Status.PASS, p0=True, manifest=manifest()) is Status.PASS
    with pytest.raises(r.RuleError, match="PASS_NEEDS_A_COMPLETE_MANIFEST"):
        r.move(Status.NOT_RUN, Status.PASS, p0=True, manifest=manifest(trace_refs=[]))
    with pytest.raises(r.RuleError, match="FAIL_NEEDS_A_DEFECT"):
        r.move(Status.NOT_RUN, Status.FAIL, p0=True)
    assert r.move(Status.NOT_RUN, Status.FAIL, p0=True, defect_id="DEF-1") is Status.FAIL
    for a, b in (
        (Status.PASS, Status.NOT_RUN),
        (Status.FAIL, Status.PASS),
        (Status.WAIVED, Status.PASS),
        (Status.NOT_RUN, Status.EXPIRED),
    ):
        with pytest.raises(r.RuleError, match="TRANSITION_NOT_ALLOWED"):
            r.move(a, b, p0=False, manifest=manifest(), defect_id="D")
    for s in Status:
        assert r.NEXT[s] <= set(Status) - {s}


def test_a_waiver_is_for_a_non_p0_case_with_an_independent_approver_and_an_expiry() -> None:
    ok = {
        "approver": "a",
        "requested_by": "b",
        "reason": "cosmetic",
        "expires_at": NOW + timedelta(days=10),
    }
    assert r.move(Status.FAIL, Status.WAIVED, p0=False, waiver=ok, now=NOW) is Status.WAIVED
    with pytest.raises(r.RuleError, match="P0_CANNOT_BE_WAIVED"):
        r.move(Status.FAIL, Status.WAIVED, p0=True, waiver=ok, now=NOW)
    for bad, code in (
        ({**ok, "approver": "b"}, "WAIVER_APPROVER_IS_THE_REQUESTER"),
        ({**ok, "expires_at": NOW - timedelta(days=1)}, "WAIVER_MUST_EXPIRE_IN_THE_FUTURE"),
        ({**ok, "expires_at": None}, "WAIVER_MUST_EXPIRE_IN_THE_FUTURE"),
        ({**ok, "reason": ""}, "WAIVER_NEEDS_APPROVER_REQUESTER_AND_REASON"),
    ):
        with pytest.raises(r.RuleError, match=code):
            r.move(Status.FAIL, Status.WAIVED, p0=False, waiver=bad, now=NOW)
    with pytest.raises(r.RuleError, match="WAIVER_LONGER_THAN_POLICY_ALLOWS"):
        r.check_waiver(p0=False, waiver=ok, now=NOW, max_days=5)


def test_a_pass_expires_when_anything_it_was_pinned_to_changes() -> None:
    pinned = {
        "build_sha256": "a",
        "policy_pack": "DE@1",
        "provider_certification": "cert-1",
        "trust_list_snapshot": "tl-9",
    }
    assert r.age(Status.PASS, pinned, dict(pinned)) == (Status.PASS, [])
    for key, new in (
        ("build_sha256", "b"),
        ("policy_pack", "DE@2"),
        ("provider_certification", "cert-2"),
        ("trust_list_snapshot", "tl-10"),
    ):
        status, why = r.age(Status.PASS, pinned, {**pinned, key: new})
        assert status is Status.EXPIRED and why == [key]
    assert (
        r.age(
            Status.PASS, pinned, {k: v for k, v in pinned.items() if k != "provider_certification"}
        )[0]
        is Status.EXPIRED
    ), "a vanished input counts as changed"
    assert r.age(Status.FAIL, pinned, {**pinned, "build_sha256": "z"}) == (Status.FAIL, []), (
        "a FAIL does not heal by itself"
    )
    assert r.age(Status.PASS, pinned, {**pinned, "unwatched": "x"})[0] is Status.PASS


# ------------------------------------------------------------------------------------------------------------------------------ fixtures


def test_a_fixture_needs_a_second_reviewer_and_is_locked_once_approved() -> None:
    assert (
        r.review_fixture(author="a", reviewer="b", approve=True, rationale="oracle checked")
        == "APPROVED"
    )
    assert (
        r.review_fixture(author="a", reviewer="b", approve=False, rationale="wrong vector")
        == "REJECTED"
    )
    with pytest.raises(r.RuleError, match="REVIEWER_IS_THE_AUTHOR"):
        r.review_fixture(author="a", reviewer="a", approve=True, rationale="fine")
    with pytest.raises(r.RuleError, match="RATIONALE_REQUIRED"):
        r.review_fixture(author="a", reviewer="b", approve=True, rationale=" ")
    assert r.amend_fixture(state="APPROVED", old_hash="h1", new_hash="h2") == "NEW_DRAFT_REVISION"
    assert r.amend_fixture(state="APPROVED", old_hash="h1", new_hash="h1") == "UNCHANGED"
    assert r.amend_fixture(state="DRAFT", old_hash="h1", new_hash="h2") == "EDIT_IN_PLACE"


def test_changing_a_golden_outcome_never_rewrites_recorded_results() -> None:
    assert r.can_rewrite_history(recorded_results=0) is True
    assert r.can_rewrite_history(recorded_results=1) is False


# ---------------------------------------------------------------------------------------------------------------------- release decision


def green() -> Candidate:
    cases = [
        Case(f"{s}-{i:02d}", s, "P0" if i == 1 else "P1", Status.PASS)
        for s in ("GOV", "IDV", "DOC", "PRO", "RNT", "INT", "VC", "OPS")
        for i in (1, 2)
    ]
    cases += [Case("PRI-07", "PRI", "P0", Status.PASS), Case("PRI-08", "PRI", "P0", Status.PASS)]
    return Candidate(
        cases=cases, unresolved_requirement_links=0, official_source_status_in_date=True, critical_false_pass_open=False,
        open_critical_or_high_security=0, privacy_release_ready=True, thresholds_policy_signed=True,
        signatures=[Signature("QA_LEAD", "qa", "APPROVE"), Signature("CTO", "cto", "APPROVE"), Signature("SECURITY_GRC", "sec", "APPROVE"),
                    Signature("PRIVACY", "dpo", "APPROVE"), Signature("COUNTRY_LEGAL", "lawyer", "APPROVE")],
        engineering_owner="eng", commit_authors={"eng", "dev2"}, flag_documented=True, post_launch_owner="sre_lead",
        no_go_facts={k: False for k in r.NO_GO},
    )  # fmt: skip


def test_a_candidate_with_every_input_and_every_gate_is_the_only_thing_that_is_go() -> None:
    d = r.evaluate(green())
    assert d.status == "GO" and d.reason == "ALL_GATES_PASSED" and all(g.passed for g in d.gates)
    assert d.p0_pass == d.p0_required > 0 and d.missing == ()


def test_nothing_run_and_nothing_supplied_is_no_go_and_never_green() -> None:
    d = r.evaluate(Candidate(cases=[Case("GOV-01", "GOV", "P0", Status.NOT_RUN)]))
    assert d.status == "NO_GO" and d.reason in ("NO_GO_CONDITION", "NOT_RUN")
    assert not any(g.passed for g in d.gates if g.gate in ("UAT-G0", "UAT-G1", "UAT-G7"))
    assert r.evaluate(Candidate(cases=[])).status == "NO_GO"


@pytest.mark.parametrize(
    "bad",
    [
        Status.NOT_RUN,
        Status.BLOCKED,
        Status.FAIL,
        Status.INCONCLUSIVE,
        Status.EXPIRED,
        Status.WAIVED,
    ],
)
def test_g0_one_p0_case_that_is_anything_but_an_independent_pass_blocks_the_release(
    bad: Status,
) -> None:
    c = green()
    cases = list(c.cases)
    cases[0] = replace(cases[0], status=bad)
    d = r.evaluate(replace(c, cases=cases))
    assert (
        d.status == "NO_GO"
        and not d.gates[0].passed
        and f"GOV-01_{bad.value}" in d.gates[0].reasons
    )


def test_g0_a_proposed_p0_is_not_a_confirmed_one_and_unresolved_links_block() -> None:
    c = green()
    cases = [replace(x, risk="PROPOSED_P0") if x.risk == "P0" else x for x in c.cases]
    assert "RISK_NOT_CONFIRMED_BY_QA_LEAD" in r.g0(replace(c, cases=cases)).reasons
    assert (
        "3_REQUIREMENT_LINKS_UNRESOLVED" in r.g0(replace(c, unresolved_requirement_links=3)).reasons
    )
    assert (
        "REQUIREMENT_LINKS_NOT_CHECKED"
        in r.g0(replace(c, unresolved_requirement_links=None)).reasons
    )


def test_a_case_that_is_not_applicable_is_left_out_not_counted_as_a_pass() -> None:
    c = green()
    cases = list(c.cases) + [Case("RNT-04", "RNT", "P0", Status.NOT_RUN, applicable=False)]
    assert r.evaluate(replace(c, cases=cases)).status == "GO"


def test_g1_to_g3_missing_legal_security_or_privacy_input_blocks() -> None:
    c = green()
    assert not r.g1(replace(c, official_source_status_in_date=None)).passed
    assert not r.g1(replace(c, official_source_status_in_date=False)).passed
    assert (
        not r.g2(replace(c, critical_false_pass_open=None)).passed
        and not r.g2(replace(c, critical_false_pass_open=True)).passed
    )
    assert (
        not r.g3(replace(c, open_critical_or_high_security=None)).passed
        and not r.g3(replace(c, open_critical_or_high_security=1)).passed
    )
    assert (
        not r.g3(replace(c, privacy_release_ready=None)).passed
        and not r.g3(replace(c, privacy_release_ready=False)).passed
    )


def test_g4_to_g6_interop_accessibility_fairness_and_operations_need_their_cases_and_a_signed_policy() -> (
    None
):
    c = green()
    broken = [replace(x, status=Status.FAIL) if x.case_id == "INT-02" else x for x in c.cases]
    assert "INT-02_FAIL" in r.g4(replace(c, cases=broken)).reasons
    no_a11y = [x for x in c.cases if x.case_id != "PRI-08"]
    assert "ACCESSIBILITY_OR_FAIRNESS_CASE_NOT_IN_SCOPE" in r.g5(replace(c, cases=no_a11y)).reasons
    assert (
        "THRESHOLDS_POLICY_NOT_SIGNED" in r.g6(replace(c, thresholds_policy_signed=False)).reasons
    )
    assert r.evaluate(replace(c, thresholds_policy_signed=False)).status == "NO_GO", (
        "no number is defaulted: an unsigned policy blocks"
    )


def test_g7_needs_every_role_and_a_flag_and_an_owner() -> None:
    c = green()
    for role in ("QA_LEAD", "CTO", "SECURITY_GRC", "PRIVACY", "COUNTRY_LEGAL"):
        sigs = [s for s in c.signatures if s.role != role]
        assert f"{role}_NOT_APPROVED" in r.g7(replace(c, signatures=sigs)).reasons
    assert r.g7(
        replace(
            c,
            signatures=[s for s in c.signatures if s.role != "COUNTRY_LEGAL"],
            country_legal_required=False,
        )
    ).passed
    assert (
        not r.g7(replace(c, flag_documented=False)).passed
        and not r.g7(replace(c, post_launch_owner=" ")).passed
    )
    rejected = [replace(s, decision="REJECT") if s.role == "CTO" else s for s in c.signatures]
    assert "CTO_REJECTED" in r.g7(replace(c, signatures=rejected)).reasons


def test_no_product_owner_ceo_or_operator_can_sign_around_a_failed_gate() -> None:
    c = green()
    failing = replace(
        c,
        official_source_status_in_date=False,
        signatures=[
            *c.signatures,
            Signature("CEO", "ceo", "APPROVE"),
            Signature("PRODUCT_OWNER", "po", "APPROVE"),
            Signature("OPERATOR", "op", "APPROVE"),
        ],
    )
    d = r.evaluate(failing)
    assert d.status == "NO_GO"
    why = r.g7(failing).reasons
    assert {
        "ROLE_CEO_CANNOT_SIGN_A_RELEASE",
        "ROLE_PRODUCT_OWNER_CANNOT_SIGN_A_RELEASE",
        "ROLE_OPERATOR_CANNOT_SIGN_A_RELEASE",
    } <= set(why)


def test_separation_of_duties_one_person_one_role_and_a_qa_lead_who_is_independent_of_the_build() -> (
    None
):
    c = green()
    same = [
        replace(s, principal="p") if s.role in ("CTO", "SECURITY_GRC") else s for s in c.signatures
    ]
    assert any("ONE_PERSON_HOLDS" in p for p in r.signature_problems(replace(c, signatures=same)))
    twice = [*c.signatures, Signature("CTO", "other", "APPROVE")]
    assert "ROLE_CTO_SIGNED_TWICE" in r.signature_problems(replace(c, signatures=twice))
    for qa in ("eng", "dev2"):
        sigs = [replace(s, principal=qa) if s.role == "QA_LEAD" else s for s in c.signatures]
        assert "QA_LEAD_IS_NOT_INDEPENDENT_OF_THE_BUILD" in r.signature_problems(
            replace(c, signatures=sigs)
        )


@pytest.mark.parametrize("key", list(r.NO_GO))
def test_each_mandatory_no_go_condition_blocks_the_release_for_its_scope(key: str) -> None:
    c = green()
    d = r.evaluate(replace(c, no_go_facts={**c.no_go_facts, key: True}))
    assert d.status == "NO_GO" and d.reason == "NO_GO_CONDITION"
    assert r.no_go_conditions({**c.no_go_facts, key: True}) == [key]


def test_a_condition_nobody_has_ruled_out_counts_as_present() -> None:
    assert set(r.no_go_conditions({})) == set(r.NO_GO), (
        "the platform cannot say a condition is absent"
    )
    assert r.no_go_conditions({k: False for k in r.NO_GO}) == []


def test_the_decision_reports_what_is_missing_for_the_release_preview() -> None:
    c = green()
    cases = [
        replace(x, status=Status.NOT_RUN) if x.case_id in ("GOV-01", "DOC-01") else x
        for x in c.cases
    ]
    d = r.evaluate(replace(c, cases=cases))
    assert (
        d.status == "NO_GO"
        and d.missing == ("DOC-01:NOT_RUN", "GOV-01:NOT_RUN")
        and d.p0_pass == d.p0_required - 2
    )


# ----------------------------------------------------------------------------------------------------------------------- policy thresholds


def test_a_policy_holds_only_numbers_a_person_set_and_supplies_none_itself() -> None:
    assert r.policy_problems({"p95_ms": 400, "availability_percent": 99.9}) == []
    assert r.policy_problems({}) == ["POLICY_HAS_NO_THRESHOLDS"]
    assert "THRESHOLD_UNKNOWN_MAKE_BELIEVE_MS" in r.policy_problems({"make_believe_ms": 1})
    assert "THRESHOLD_NOT_A_POSITIVE_NUMBER_P95_MS" in r.policy_problems({"p95_ms": -1})
    assert "THRESHOLD_NOT_A_POSITIVE_NUMBER_P95_MS" in r.policy_problems({"p95_ms": True})


def test_measurements_are_judged_in_the_direction_each_threshold_means() -> None:
    policy = {"availability_percent": 99.9, "p95_ms": 400, "rto_minutes": 60}
    assert (
        r.check_thresholds(
            policy, {"availability_percent": 99.95, "p95_ms": 399, "rto_minutes": 60}
        )
        == []
    )
    assert r.check_thresholds(
        policy, {"availability_percent": 99.0, "p95_ms": 401, "rto_minutes": 61}
    ) == [
        "AVAILABILITY_PERCENT_BREACHED",
        "P95_MS_BREACHED",
        "RTO_MINUTES_BREACHED",
    ]


def test_a_threshold_nobody_measured_is_a_breach_and_an_unjudged_measurement_is_ignored() -> None:
    assert r.check_thresholds({"p95_ms": 400}, {}) == ["P95_MS_NOT_MEASURED"]
    assert r.check_thresholds({"p95_ms": 400}, {"p95_ms": "fast"}) == ["P95_MS_NOT_MEASURED"]
    assert r.check_thresholds({"p95_ms": 400}, {"p95_ms": 100, "p99_ms": 9999}) == [], (
        "no threshold was set for p99, so it is not judged"
    )


def test_a_policy_is_signed_only_by_three_different_role_holders_and_a_rejection_voids_it() -> None:
    def sig(role, who, decision="APPROVE"):
        return {"role": role, "principal": who, "decision": decision}

    ok = [sig("QA_LEAD", "a"), sig("CTO", "b"), sig("SECURITY_GRC", "c")]
    assert r.policy_signed(ok) is True
    assert r.policy_signed(ok[:2]) is False
    assert (
        r.policy_signed([sig("QA_LEAD", "a"), sig("CTO", "a"), sig("SECURITY_GRC", "c")]) is False
    ), "one person cannot be two of the three"
    assert r.policy_signed([*ok, sig("PRIVACY", "d", "REJECT")]) is False
    assert (
        r.policy_signed([sig("QA_LEAD", "a"), sig("CTO", "b", "REJECT"), sig("SECURITY_GRC", "c")])
        is False
    )
