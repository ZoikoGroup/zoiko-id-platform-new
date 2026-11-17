"""Biometric method lifecycle and kill switch (ZID-DTV-WF-04 section 06, BIO-03; QA-001, QA-015, QA-043; CR-107): nothing is on by default, a step
up is two people and one step at a time, a step down is one person at once, and a pause always wins."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.verification.domain import biometric_methods as bm

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
GLOBAL = bm.Scope(bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION")
PILOT = replace(GLOBAL, tenant_id="t1")


def call(**over: str) -> bm.Call:
    base = bm.Call(bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION", "t1")
    return replace(base, **over)


def proposal(
    history: list[bm.Record], target: bm.State, scope: bm.Scope = GLOBAL, **over: object
) -> bm.Proposal:
    last = bm.current(history)
    base = bm.Proposal(
        proposal_id=f"p-{len(history)}",
        scope_id="face-gb",
        scope=scope,
        from_version=last.version if last else 0,
        target=target,
        dpia_ref="DPIA-GB-7" if target in bm.NEEDS_EVIDENCE else None,
        evaluation_ref="LAB-2026-11" if target in bm.NEEDS_EVIDENCE else None,
        reason="independent evaluation accepted",
        evidence_ref="TICKET-1",
        proposed_by="ana",
        proposed_at=NOW - timedelta(hours=1),
    )
    return replace(base, **over)


def climb(scope: bm.Scope, to: bm.State) -> list[bm.Record]:
    history: list[bm.Record] = []
    state = bm.State.OFF
    while state is not to:
        target = bm.NEXT_UP[state]
        p = proposal(history, target, scope)
        bm.check_proposal(p, history=history, now=NOW)
        _, rec = bm.approve(p, history=history, by="ben", reason="approved after review", now=NOW)
        history.append(rec)
        state = target
    return history


# ------------------------------------------------------------------------------------------------------------------------ default off


def test_no_record_is_off() -> None:
    a = bm.availability([], call())
    assert (a.available, a.state, a.reasons) == (False, bm.State.OFF, ("METHOD_OFF",))
    assert bm.state_of([]) is bm.State.OFF


# ------------------------------------------------------------------------------------------------------------------------ step up


def test_step_up_is_one_step_at_a_time() -> None:
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(proposal([], bm.State.PRODUCTION_ELIGIBLE), history=[], now=NOW)
    assert e.value.code == "METHOD_STEP_NOT_ALLOWED"
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(proposal([], bm.State.PILOT_GATED, PILOT), history=[], now=NOW)
    assert e.value.code == "METHOD_STEP_NOT_ALLOWED"


def test_proposer_cannot_approve() -> None:
    p = proposal([], bm.State.LAB_ONLY)
    bm.check_proposal(p, history=[], now=NOW)
    with pytest.raises(bm.MethodError) as e:
        bm.approve(p, history=[], by="ana", reason="looks fine to me", now=NOW)
    assert e.value.code == "METHOD_APPROVED_BY_PROPOSER"


def test_approval_records_both_people() -> None:
    p = proposal([], bm.State.LAB_ONLY)
    decided, rec = bm.approve(p, history=[], by="ben", reason="lab fixtures only", now=NOW)
    assert decided.status is bm.ProposalStatus.APPROVED and decided.decided_by == "ben"
    assert (rec.version, rec.state, rec.recorded_by, rec.proposed_by) == (
        1,
        bm.State.LAB_ONLY,
        "ben",
        "ana",
    )


def test_pilot_names_a_tenant() -> None:
    history = climb(GLOBAL, bm.State.LAB_ONLY)
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(proposal(history, bm.State.PILOT_GATED, GLOBAL), history=history, now=NOW)
    assert e.value.code == "METHOD_PILOT_NEEDS_TENANT"


@pytest.mark.parametrize(
    ("field", "code"),
    [
        ("dpia_ref", "METHOD_DPIA_REFERENCE_REQUIRED"),
        ("evaluation_ref", "METHOD_EVALUATION_REFERENCE_REQUIRED"),
    ],
)
def test_live_states_need_dpia_and_evaluation(field: str, code: str) -> None:
    history = climb(PILOT, bm.State.LAB_ONLY)
    p = proposal(history, bm.State.PILOT_GATED, PILOT, **{field: None})
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(p, history=history, now=NOW)
    assert e.value.code == code


def test_stale_proposal_is_refused() -> None:
    history = climb(PILOT, bm.State.LAB_ONLY)
    p = proposal(history, bm.State.PILOT_GATED, PILOT)
    bm.check_proposal(p, history=history, now=NOW)
    paused = bm.step_down(
        history,
        target=bm.State.PAUSED,
        by="sec",
        reason="vendor incident open",
        evidence_ref="INC-9",
        now=NOW,
    )
    with pytest.raises(bm.MethodError) as e:
        bm.approve(p, history=[*history, paused], by="ben", reason="approved after review", now=NOW)
    assert e.value.code == "METHOD_PROPOSAL_STALE"


def test_expired_proposal_is_refused() -> None:
    p = proposal([], bm.State.LAB_ONLY, proposed_at=NOW - bm.PROPOSAL_TTL - timedelta(minutes=1))
    with pytest.raises(bm.MethodError) as e:
        bm.approve(p, history=[], by="ben", reason="approved after review", now=NOW)
    assert e.value.code == "METHOD_PROPOSAL_EXPIRED"


def test_a_decided_proposal_cannot_be_decided_again() -> None:
    p = bm.reject(
        proposal([], bm.State.LAB_ONLY), by="ben", reason="evaluation plan missing", now=NOW
    )
    with pytest.raises(bm.MethodError) as e:
        bm.approve(p, history=[], by="ben", reason="approved after review", now=NOW)
    assert e.value.code == "METHOD_PROPOSAL_NOT_OPEN"


def test_scope_id_always_names_the_same_scope() -> None:
    history = climb(GLOBAL, bm.State.LAB_ONLY)
    other = replace(GLOBAL, country="FR")
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(proposal(history, bm.State.PILOT_GATED, other), history=history, now=NOW)
    assert e.value.code == "METHOD_SCOPE_CHANGED"


# ------------------------------------------------------------------------------------------------------------------------ step down


def test_pause_is_one_person_at_once() -> None:
    history = climb(PILOT, bm.State.PRODUCTION_ELIGIBLE)
    rec = bm.step_down(
        history,
        target=bm.State.PAUSED,
        by="sec",
        reason="injection attack suspected",
        evidence_ref="INC-1",
        now=NOW,
    )
    assert (rec.state, rec.version, rec.proposed_by) == (bm.State.PAUSED, 4, None)


def test_resume_from_pause_is_a_fresh_approval_from_lab() -> None:
    history = climb(PILOT, bm.State.PRODUCTION_ELIGIBLE)
    history.append(
        bm.step_down(
            history,
            target=bm.State.PAUSED,
            by="sec",
            reason="injection attack suspected",
            evidence_ref="INC-1",
            now=NOW,
        )
    )
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(
            proposal(history, bm.State.PRODUCTION_ELIGIBLE, PILOT), history=history, now=NOW
        )
    assert e.value.code == "METHOD_STEP_NOT_ALLOWED"
    bm.check_proposal(proposal(history, bm.State.LAB_ONLY, PILOT), history=history, now=NOW)


def test_step_down_cannot_go_up() -> None:
    history = climb(PILOT, bm.State.PILOT_GATED)
    with pytest.raises(bm.MethodError) as e:
        bm.step_down(
            history,
            target=bm.State.PRODUCTION_ELIGIBLE,
            by="sec",
            reason="just switch it on",
            evidence_ref="T-1",
            now=NOW,
        )
    assert e.value.code == "METHOD_STEP_NOT_DOWN"


def test_all_tenant_scope_never_steps_down_to_pilot() -> None:
    history = climb(GLOBAL, bm.State.LAB_ONLY)
    # build an all-tenant production record directly: an all-tenant scope reaches production through its own pilot elsewhere
    prod = replace(
        history[-1],
        version=2,
        state=bm.State.PRODUCTION_ELIGIBLE,
        dpia_ref="D-1",
        evaluation_ref="E-1",
    )
    with pytest.raises(bm.MethodError) as e:
        bm.step_down(
            [*history, prod],
            target=bm.State.PILOT_GATED,
            by="sec",
            reason="narrow it to one tenant",
            evidence_ref="T-1",
            now=NOW,
        )
    assert e.value.code == "METHOD_PILOT_NEEDS_TENANT"


def test_step_down_needs_reason_and_evidence() -> None:
    history = climb(PILOT, bm.State.LAB_ONLY)
    with pytest.raises(bm.MethodError) as e:
        bm.step_down(
            history, target=bm.State.OFF, by="sec", reason="no", evidence_ref="T-1", now=NOW
        )
    assert e.value.code == "METHOD_REASON_REQUIRED"
    with pytest.raises(bm.MethodError) as e:
        bm.step_down(
            history,
            target=bm.State.OFF,
            by="sec",
            reason="retired after review",
            evidence_ref="",
            now=NOW,
        )
    assert e.value.code == "METHOD_EVIDENCE_REQUIRED"


def test_nothing_to_pause_when_off() -> None:
    with pytest.raises(bm.MethodError) as e:
        bm.step_down(
            [],
            target=bm.State.PAUSED,
            by="sec",
            reason="stop everything now",
            evidence_ref="INC-1",
            now=NOW,
        )
    assert e.value.code == "METHOD_NOT_FOUND"


# ------------------------------------------------------------------------------------------------------------------------ runtime


def test_lab_only_is_never_live() -> None:  # WF01 T02
    a = bm.availability(bm.in_force([climb(PILOT, bm.State.LAB_ONLY)], NOW), call())
    assert (a.available, a.reasons) == (False, ("METHOD_LAB_ONLY",))


def test_pilot_is_live_only_for_its_tenant() -> None:
    records = bm.in_force([climb(PILOT, bm.State.PILOT_GATED)], NOW)
    assert bm.availability(records, call()).available
    other = bm.availability(records, call(tenant_id="t2"))
    assert (other.available, other.reasons) == (False, ("METHOD_OFF",))


def test_scope_must_match_engine_version_exactly() -> (
    None
):  # QA-015: a model upgrade has no approval
    records = bm.in_force([climb(PILOT, bm.State.PILOT_GATED)], NOW)
    assert not bm.availability(records, call(engine_version="2.2.0")).available
    assert not bm.availability(records, call(country="FR")).available
    assert not bm.availability(records, call(method=bm.Method.LIVENESS)).available


def test_pause_always_wins() -> None:
    tenant = climb(PILOT, bm.State.PRODUCTION_ELIGIBLE)
    everyone = [
        replace(r, scope_id="face-gb-all", scope=GLOBAL) for r in climb(GLOBAL, bm.State.LAB_ONLY)
    ]
    everyone.append(
        bm.step_down(
            everyone,
            target=bm.State.PAUSED,
            by="sec",
            reason="provider breach reported",
            evidence_ref="INC-2",
            now=NOW,
        )
    )
    a = bm.availability(bm.in_force([tenant, everyone], NOW), call())
    assert (a.available, a.state, a.reasons) == (False, bm.State.PAUSED, ("METHOD_PAUSED",))


def test_tenant_scope_overrides_the_all_tenant_one() -> None:
    everyone = [
        replace(r, scope_id="face-gb-all", scope=GLOBAL) for r in climb(GLOBAL, bm.State.LAB_ONLY)
    ]
    tenant = climb(PILOT, bm.State.PILOT_GATED)
    a = bm.availability(bm.in_force([everyone, tenant], NOW), call())
    assert (a.available, a.state) == (True, bm.State.PILOT_GATED)


def test_history_answers_a_past_moment() -> None:
    history = climb(PILOT, bm.State.PRODUCTION_ELIGIBLE)
    later = NOW + timedelta(hours=1)
    history.append(
        bm.step_down(
            history,
            target=bm.State.PAUSED,
            by="sec",
            reason="injection attack suspected",
            evidence_ref="INC-1",
            now=later,
        )
    )
    assert bm.availability(bm.in_force([history], NOW), call()).available
    assert not bm.availability(bm.in_force([history], later), call()).available


# ------------------------------------------------------------------------------------------------------------------------ exposure (CR-110)


def _production(exposure: int) -> list[bm.Record]:
    history = climb(PILOT, bm.State.PILOT_GATED)
    p = proposal(history, bm.State.PRODUCTION_ELIGIBLE, PILOT, exposure_percent=exposure)
    bm.check_proposal(p, history=history, now=NOW)
    _, rec = bm.approve(
        p, history=history, by="ben", reason="rollout to a share of people", now=NOW
    )
    return [*history, rec]


def test_an_approved_step_up_carries_its_decision_hash() -> None:
    history = _production(25)
    rec = history[-1]
    assert (
        rec.exposure_percent == 25
        and rec.decision_hash == bm.decision_hash(rec)
        and len(rec.decision_hash) == 64
    )
    assert bm.decision_hash(replace(rec, exposure_percent=50)) != rec.decision_hash


def test_exposure_below_100_only_in_production() -> None:
    with pytest.raises(bm.MethodError) as e:
        bm.check_proposal(proposal([], bm.State.LAB_ONLY, exposure_percent=50), history=[], now=NOW)
    assert e.value.code == "METHOD_EXPOSURE_ONLY_FOR_PRODUCTION"


def test_widening_is_a_proposal_and_narrowing_a_step_down() -> None:
    history = _production(25)
    wider = proposal(history, bm.State.PRODUCTION_ELIGIBLE, PILOT, exposure_percent=60)
    bm.check_proposal(wider, history=history, now=NOW)
    with pytest.raises(bm.MethodError):
        bm.check_proposal(
            proposal(history, bm.State.PRODUCTION_ELIGIBLE, PILOT, exposure_percent=10),
            history=history,
            now=NOW,
        )
    narrow = bm.step_down(
        history,
        target=bm.State.PRODUCTION_ELIGIBLE,
        by="sec",
        reason="error rate rising in the rollout",
        evidence_ref="INC-3",
        now=NOW,
        exposure_percent=10,
    )
    assert (narrow.state, narrow.exposure_percent, narrow.proposed_by) == (
        bm.State.PRODUCTION_ELIGIBLE,
        10,
        None,
    )
    with pytest.raises(bm.MethodError):
        bm.step_down(
            history,
            target=bm.State.PRODUCTION_ELIGIBLE,
            by="sec",
            reason="widen without approval",
            evidence_ref="INC-3",
            now=NOW,
            exposure_percent=80,
        )


def test_a_person_always_gets_the_same_rollout_answer() -> None:
    records = bm.in_force([_production(30)], NOW)
    people = [f"person-{i}" for i in range(400)]
    first = [bm.availability(records, call(rollout_key=p)).available for p in people]
    again = [bm.availability(records, call(rollout_key=p)).available for p in people]
    assert first == again
    share = sum(first) / len(first)
    assert 0.2 < share < 0.4  # about 30% of people
    readiness = bm.availability(records, call())
    assert readiness.available and readiness.reasons == ("METHOD_ROLLOUT_30",)
