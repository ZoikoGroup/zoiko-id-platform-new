"""ZID-PRI-004 control-plane rules: risk bands, the strict no-release criteria, default-deny transfers, erasure that never claims completion
early, breach clocks that never guess a deadline, and reviews by someone other than the requester."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.privacy.domain import control as c

NOW = datetime(2026, 11, 11, tzinfo=UTC)


def risk(**over):
    base = {
        "risk_id": "R01", "title": "Biometric leak", "mitigation": "Envelope encryption", "owner": "ciso",
        "inherent_likelihood": 5, "inherent_impact": 5, "residual_likelihood": 2, "residual_impact": 5,
    }  # fmt: skip
    return {**base, **over}


def full_gates():
    return {
        g: {role: {"signer": f"{g}-{role}", "evidence_uri": "https://e/1"} for role in roles}
        for g, (_, roles) in c.GATES.items()
    }


# ------------------------------------------------------------------------------------------------------------------------------ risks


@pytest.mark.parametrize(
    ("value", "name"),
    [
        (1, "LOW"),
        (5, "LOW"),
        (6, "MODERATE"),
        (11, "MODERATE"),
        (12, "HIGH"),
        (17, "HIGH"),
        (18, "CRITICAL"),
        (25, "CRITICAL"),
    ],
)
def test_the_documents_bands(value: int, name: str) -> None:
    assert c.band(value) == name


def test_a_score_is_likelihood_times_impact_and_out_of_range_is_refused() -> None:
    assert c.score(5, 5) == 25 and c.score(2, 5) == 10
    for bad in ((0, 3), (3, 6)):
        with pytest.raises(ValueError):
            c.score(*bad)


def test_an_incomplete_or_inconsistent_risk_is_named_not_guessed() -> None:
    assert c.risk_problems(risk()) == []
    assert "RISK_OWNER_MISSING" in c.risk_problems(risk(owner=" "))
    assert "RISK_RESIDUAL_LIKELIHOOD_INVALID" in c.risk_problems(risk(residual_likelihood=9))
    assert "RISK_ID_INVALID" in c.risk_problems(risk(risk_id="X1"))
    assert c.risk_problems(
        risk(residual_likelihood=5, inherent_likelihood=2, inherent_impact=2, residual_impact=5)
    ) == ["RISK_RESIDUAL_ABOVE_INHERENT"]
    assert c.risk_view(risk())["residual_band"] == "MODERATE"


# ------------------------------------------------------------------------------------------------------------------------------ gates


def test_a_gate_signoff_needs_the_accountable_role_a_different_person_and_evidence() -> None:
    ok = ("G3", "SECURITY_CISO", "ciso", "author", "https://e/1", {})
    assert c.gate_problem(*ok) is None
    assert c.gate_problem("G9", "X", "a", "b", "e", {}) == "GATE_UNKNOWN"
    assert (
        c.gate_problem("G3", "LEGAL", "ciso", "author", "e", {}) == "ROLE_NOT_ACCOUNTABLE_FOR_GATE"
    )
    assert c.gate_problem("G3", "SECURITY_CISO", "author", "author", "e", {}) == "SIGNER_IS_AUTHOR"
    assert c.gate_problem("G3", "SECURITY_CISO", "ciso", "author", " ", {}) == "EVIDENCE_REQUIRED"
    assert (
        c.gate_problem(
            "G3",
            "SECURITY_CISO",
            "ciso2",
            "author",
            "e",
            {"G3": {"SECURITY_CISO": {"signer": "ciso"}}},
        )
        == "ROLE_ALREADY_SIGNED"
    )
    # one person cannot be two of the accountable parties of a package
    assert (
        c.gate_problem(
            "G1", "LEGAL", "dpo", "author", "e", {"G2": {"PRIVACY_DPO": {"signer": "dpo"}}}
        )
        == "SIGNER_ALREADY_SIGNED_ANOTHER_ROLE"
    )


def test_release_is_ready_only_when_nothing_blocks_it() -> None:
    r = c.release_state(
        state="APPROVED",
        approved_until=NOW + timedelta(days=30),
        risks=[risk()],
        gates=full_gates(),
        consultation_ref=None,
        now=NOW,
    )
    assert r.ready and r.blockers == ()


def test_each_no_release_criterion_is_a_named_blocker() -> None:
    base = {
        "state": "APPROVED",
        "approved_until": NOW + timedelta(days=1),
        "risks": [risk()],
        "gates": full_gates(),
        "consultation_ref": None,
        "now": NOW,
    }
    assert "DPIA_NOT_APPROVED" in c.release_state(**{**base, "state": "IN_REVIEW"}).blockers
    assert "DPIA_EXPIRED" in c.release_state(**{**base, "approved_until": NOW}).blockers
    assert c.release_state(**{**base, "risks": []}).blockers == ("NO_RISK_ASSESSMENT",)
    high = risk(residual_likelihood=3, residual_impact=5)
    assert "R01_RESIDUAL_HIGH_UNCONSULTED" in c.release_state(**{**base, "risks": [high]}).blockers
    assert c.release_state(**{**base, "risks": [high], "consultation_ref": "ICO-2026-1"}).ready, (
        "a recorded consultation lifts the block"
    )
    crit = risk(inherent_likelihood=5, residual_likelihood=5)
    assert (
        "R01_RESIDUAL_CRITICAL_UNCONSULTED" in c.release_state(**{**base, "risks": [crit]}).blockers
    )
    gates = full_gates()
    del gates["G3"]["SECURITY_CISO"]
    assert "G3_SECURITY_CISO_NOT_SIGNED" in c.release_state(**{**base, "gates": gates}).blockers
    assert "R01_INCOMPLETE" in c.release_state(**{**base, "risks": [risk(owner="")]}).blockers


# ------------------------------------------------------------------------------------------------------------------------------ transfers


def facts(**over):
    base = {"processor_found": True, "processor_status": "APPROVED", "data_categories": frozenset({"D2", "D3"}), "locations": frozenset({"GB", "IE"}),
            "processor_mechanism": "uk_idta", "pending_change": False, "pack_mechanisms": ("uk_idta", "adequacy")}  # fmt: skip
    return c.TransferFacts(**{**base, **over})


def test_a_transfer_is_permitted_only_on_recorded_facts() -> None:
    ok, reasons, mech = c.decide_transfer(
        facts(), data_category="D3", origin_country="GB", destination_country="IE"
    )
    assert ok and reasons == ["TRANSFER_PERMITTED_BY_RECORDED_FACTS"] and mech == "uk_idta"
    ok, _, mech = c.decide_transfer(
        facts(processor_mechanism=None),
        data_category="D3",
        origin_country="GB",
        destination_country="GB",
    )
    assert ok and mech is None, "no mechanism is needed inside one country"


@pytest.mark.parametrize(
    ("changes", "call", "reason"),
    [
        ({"processor_found": False}, {}, "PROCESSOR_NOT_FOUND"),
        ({"processor_status": "SUSPENDED"}, {}, "PROCESSOR_NOT_APPROVED"),
        ({}, {"data_category": "D4"}, "DATA_CATEGORY_NOT_COVERED"),
        ({"pending_change": True}, {}, "SUBPROCESSOR_CHANGE_PENDING"),
        ({}, {"destination_country": "US"}, "DESTINATION_NOT_AN_APPROVED_LOCATION"),
        ({}, {"remote_access_countries": ["IN"]}, "REMOTE_ACCESS_FROM_IN_NOT_APPROVED"),
        ({"processor_mechanism": None}, {}, "NO_TRANSFER_MECHANISM_RECORDED"),
        ({"processor_mechanism": "bcr"}, {}, "MECHANISM_NOT_SUPPORTED_BY_THE_PACK"),
    ],
)
def test_every_denial_names_its_reason(changes, call, reason) -> None:
    args = {"data_category": "D3", "origin_country": "GB", "destination_country": "IE", **call}
    ok, reasons, _ = c.decide_transfer(facts(**changes), **args)
    assert not ok and reason in reasons


# ------------------------------------------------------------------------------------------------------------------------------ erasure


def test_a_job_is_complete_only_when_every_task_is_acknowledged() -> None:
    A, P = "ACKNOWLEDGED", "PENDING"
    assert c.job_state([], held=False) == "PENDING"
    assert c.job_state([A, A], held=False) == "COMPLETE"
    assert c.job_state([A, P], held=False) == "PARTIAL"
    assert c.job_state([P, P], held=False) == "PENDING"
    assert c.job_state([A, "FAILED"], held=False) == "FAILED"
    assert c.job_state([A, "HELD"], held=False) == "HELD"
    assert c.job_state([A, A], held=True) == "HELD", "a legal hold keeps it held"


def test_the_proof_never_claims_completion_early_and_carries_no_data() -> None:
    job = {"job_id": "j1", "state": "PARTIAL"}
    tasks = [
        {
            "task_id": "t1",
            "target_kind": "PROVIDER",
            "target_ref": "veriff",
            "state": "ACKNOWLEDGED",
            "receipt_hash": "ab" * 32,
        },
        {
            "task_id": "t2",
            "target_kind": "BACKUP",
            "target_ref": "cell",
            "state": "PENDING",
            "receipt_hash": None,
        },
    ]
    p = c.proof(job, tasks)
    assert p["complete"] is False and p["outstanding"] == [
        {"target_kind": "BACKUP", "target_ref": "cell", "state": "PENDING"}
    ]
    tasks[1] |= {"state": "ACKNOWLEDGED", "receipt_hash": "cd" * 32}
    done = c.proof({"job_id": "j1", "state": "COMPLETE"}, tasks)
    assert (
        done["complete"] is True
        and done["outstanding"] == []
        and len(done["receipts_commitment"]) == 64
    )


def test_the_receipt_bytes_are_canonical() -> None:
    assert (
        c.receipt_payload("t", "j", "2026-11-11T00:00:00Z", 3)
        == b'{"deleted_at":"2026-11-11T00:00:00Z","job_id":"j","object_count":3,"task_id":"t"}'
    )


# ------------------------------------------------------------------------------------------------------------------------------ breaches


def test_a_clock_runs_only_where_counsel_set_it_and_is_never_guessed() -> None:
    cfg = {"EU": 72, "UK": "COUNSEL"}
    detected = NOW
    clocks = {
        x["jurisdiction"]: x
        for x in c.breach_clocks(cfg, ["eu", "UK", "BR"], detected, {}, NOW + timedelta(hours=10))
    }
    assert (
        clocks["EU"]["state"] == "RUNNING"
        and clocks["EU"]["due_at"] == (NOW + timedelta(hours=72)).isoformat()
    )
    assert clocks["UK"]["state"] == "NOT_SET_BY_COUNSEL" and clocks["UK"]["due_at"] is None
    assert clocks["BR"]["state"] == "NOT_SET_BY_COUNSEL", (
        "an unlisted jurisdiction is not assumed to have a deadline"
    )
    late = {
        x["jurisdiction"]: x
        for x in c.breach_clocks(cfg, ["EU"], detected, {}, NOW + timedelta(hours=73))
    }
    assert late["EU"]["state"] == "OVERDUE"
    done = c.breach_clocks(
        cfg, ["EU"], detected, {"EU": NOW + timedelta(hours=5)}, NOW + timedelta(hours=100)
    )
    assert done[0]["state"] == "NOTIFIED"


# ------------------------------------------------------------------------------------------------------------------------------ reviews


def test_a_review_decision_needs_another_reviewer_a_rationale_and_a_known_outcome() -> None:
    assert c.review_problem("a", "b", "checked the record", "OVERTURNED") is None
    assert c.review_problem("a", "a", "r", "UPHELD") == "REVIEWER_IS_REQUESTER"
    assert c.review_problem("a", "b", " ", "UPHELD") == "RATIONALE_REQUIRED"
    assert c.review_problem("a", "b", "r", "MAYBE") == "OUTCOME_INVALID"
