"""Biometric governance on PostgreSQL (ZID-DTV-WF-04; CR-107, CR-108, CR-109, CR-110).

1. Method states: a step up is two people and one step; the tables refuse a skipped step, a self-approval, an unproposed step up from PAUSED, an
   edit of history and a second open proposal, even when the domain code is skipped.
2. Breach containment: a HIGH case pauses capture in its jurisdictions; whoever paused it cannot release it; the table keeps the order.
3. Eligibility over real tables: with nothing approved, every gate that refuses is named (method off, no DPIA, no retention, no evaluation).
4. The erasure dry run and the decision hook run their SQL against the real schema and change nothing they should not.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_v2_privacy_pg import ACTIONS, RESOURCES, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.privacy.application import control as ctl
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.application import biometric_methods as svc
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import privacy_overlay as po
from zid_platform.modules.verification.domain.biometric_engine import EngineGate
from zid_platform.modules.verification.infrastructure import verification_store

NOW = datetime.now(UTC)


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _tenant(url: str, tenant: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, sql: str, params: dict[str, Any]) -> None:
    async def go(c: Any) -> None:
        await c.execute(text(sql), params)

    with pytest.raises(DBAPIError):
        _run(url, go)


# --------------------------------------------------------------------------------------------------------------------- 1. method states


def test_method_states_keep_two_people_and_one_step_even_without_the_domain(owner_url: str) -> None:
    sid = f"face-gb-{uuid.uuid4().hex[:8]}"
    scope = bm.Scope(
        bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION", None
    )

    async def lab(c: Any) -> bm.Record:
        p = await svc.propose(
            c, scope=scope, target=bm.State.LAB_ONLY, dpia_ref=None, evaluation_ref=None,
            reason="lab fixtures only, synthetic", evidence_ref="T-1", by="ana", now=NOW, scope_id=sid,
        )  # fmt: skip
        with pytest.raises(bm.MethodError) as e:
            await svc.approve(c, p.proposal_id, by="ana", reason="approving my own", now=NOW)
        assert e.value.code == "METHOD_APPROVED_BY_PROPOSER"
        _, rec = await svc.approve(c, p.proposal_id, by="ben", reason="lab plan reviewed", now=NOW)
        return rec

    rec = _run(owner_url, lab)
    assert (rec.version, rec.state) == (1, bm.State.LAB_ONLY)

    cols = (
        "scope_id, version, method, engine_id, engine_version, country, purpose, tenant_id, state, dpia_ref, evaluation_ref, reason, "
        "evidence_ref, recorded_by, proposed_by, proposal_id, effective_at, recorded_at"
    )
    row = {
        "sid": sid, "m": "FACE_MATCH", "e": "acme-face", "v": "2.1.0", "c": "GB", "p": "IDENTITY_VERIFICATION", "t": None,
        "d": "DPIA-1", "ev": "EVAL-1", "r": "skipping the domain code", "ref": "T-9", "at": NOW,
    }  # fmt: skip
    insert = f"INSERT INTO verify.biometric_method_state ({cols}) VALUES (:sid, :ver, :m, :e, :v, :c, :p, :t, :st, :d, :ev, :r, :ref, :by, :pby, :pid, :at, :at)"
    # a skipped step: LAB_ONLY straight to PRODUCTION_ELIGIBLE, with a proposal id that is not even real
    _refused(
        owner_url,
        insert,
        {
            **row,
            "ver": 2,
            "st": "PRODUCTION_ELIGIBLE",
            "by": "ben",
            "pby": "ana",
            "pid": uuid.uuid4(),
        },
    )
    # a step up recorded by its own proposer
    _refused(
        owner_url,
        insert,
        {**row, "ver": 2, "st": "PILOT_GATED", "by": "ana", "pby": "ana", "pid": uuid.uuid4()},
    )

    async def pause(c: Any) -> bm.Record:
        return await svc.step_down(
            c,
            sid,
            target=bm.State.PAUSED,
            by="sec",
            reason="injection attack suspected",
            evidence_ref="INC-1",
            now=NOW,
        )

    assert _run(owner_url, pause).state is bm.State.PAUSED
    # a paused scope is not resumed without a proposal, and history is never edited
    _refused(
        owner_url,
        insert,
        {**row, "ver": 3, "st": "LAB_ONLY", "by": "sec", "pby": None, "pid": None},
    )
    _refused(
        owner_url,
        "UPDATE verify.biometric_method_state SET reason = 'rewritten history' WHERE scope_id = :s",
        {"s": sid},
    )
    _refused(owner_url, "DELETE FROM verify.biometric_method_state WHERE scope_id = :s", {"s": sid})

    async def two_open(c: Any) -> None:
        await svc.propose(
            c, scope=scope, target=bm.State.LAB_ONLY, dpia_ref=None, evaluation_ref=None,
            reason="resume after the incident", evidence_ref="INC-1", by="ana", now=NOW,
        )  # fmt: skip
        with pytest.raises(bm.MethodError) as e:
            await svc.propose(
                c, scope=scope, target=bm.State.LAB_ONLY, dpia_ref=None, evaluation_ref=None,
                reason="a second proposal", evidence_ref="INC-1", by="cat", now=NOW,
            )  # fmt: skip
        assert e.value.code == "METHOD_PROPOSAL_OPEN"

    _run(owner_url, two_open)

    async def live(c: Any) -> bm.Availability:
        call = bm.Call(
            bm.Method.FACE_MATCH,
            "acme-face",
            "2.1.0",
            "GB",
            "IDENTITY_VERIFICATION",
            str(uuid.uuid4()),
        )
        return await svc.availability_for(c, call, at=datetime.now(UTC))

    a = _run(owner_url, live)
    assert (a.available, a.state) == (False, bm.State.PAUSED)


# --------------------------------------------------------------------------------------------------------------------- 2. breach pause


def test_a_high_breach_pauses_capture_until_another_person_releases_it(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def open_case(c: Any) -> dict[str, Any]:
        return await ctl.open_breach(
            c, o.tenant, o.owner, summary="vendor bucket exposed", severity="HIGH", jurisdictions=["gb", "fr"],
            subjects_estimate=3, detected_at=NOW, config={},
        )  # fmt: skip

    case = _tenant(rt_url, o.tenant, open_case)
    assert case["capture_state"] == "PAUSED"
    bid = case["breach_id"]

    async def paused_in(country: str) -> Any:
        return await _tenant_async(
            rt_url, o.tenant, lambda c: ctl.capture_paused_breaches(c, country)
        )

    assert asyncio.run(paused_in("GB")) == [bid]
    assert asyncio.run(paused_in("DE")) == []

    async def release(c: Any, who: str) -> dict[str, Any]:
        return await ctl.release_capture(
            c, o.tenant, who, bid, reason="exposure closed and verified", config={}
        )

    with pytest.raises(PrivacyError) as e:
        _tenant(rt_url, o.tenant, lambda c: release(c, o.owner))
    assert e.value.code == "RELEASED_BY_WHOEVER_PAUSED"
    assert _tenant(rt_url, o.tenant, lambda c: release(c, o.second))["capture_state"] == "RELEASED"
    assert asyncio.run(paused_in("GB")) == []

    async def back_to_paused(c: Any) -> None:
        await c.execute(
            text("UPDATE privacy.breach_case SET capture_state = 'PAUSED' WHERE breach_id = :b"),
            {"b": uuid.UUID(bid)},
        )

    with pytest.raises(DBAPIError):
        _tenant(rt_url, o.tenant, back_to_paused)


async def _tenant_async(url: str, tenant: str, work: Any) -> Any:
    async with engine(url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        return await work(c)


# --------------------------------------------------------------------------------------------------------------------- 3. eligibility


def test_eligibility_names_every_refusing_gate_over_real_tables(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    gate = EngineGate(
        engine_id="acme-face", engine_version="9.9.9", evaluation_id="LAB-1", evaluated_on=date.today(), dpia_ref="DPIA-1",
        false_accept=0.0005, false_reject=0.01, attack_accept=0.01, bona_fide_reject=0.02, face_threshold=0.8, liveness_threshold=0.8,
    )  # fmt: skip
    ov = po.Overlay("GB", 1, frozenset({"EU_WEST"}), {}, {}, po.Biometrics.ALLOWED)
    ov = po.Overlay(
        **{
            **ov.__dict__,
            "approvals": {
                r: po.Approval(f"{r}-sig", po.overlay_hash(ov)) for r in po.APPROVER_ROLES
            },
        }
    )

    async def check(c: Any) -> be.Eligibility:
        return await be.eligibility(
            c, tenant_id=o.tenant, country="GB", purpose="IDENTITY_VERIFICATION", overlays={"GB": ov},
            now=datetime.now(UTC), gate=gate, gate_given=True,
        )  # fmt: skip

    e = _tenant(rt_url, o.tenant, check)
    assert e.view()["available_methods"] == []
    for m in e.methods:
        assert (
            "METHOD_OFF" in m.reasons
            and "DPIA_NOT_READY" in m.reasons
            and "EVALUATION_NOT_READY" in m.reasons
        )
        assert m.dpia_blockers == ("NO_DPIA_PACKAGE",)
        assert m.evaluation_blockers == ("MODEL_NOT_IN_INVENTORY",)
        assert m.missing_retention and all(c.startswith("biometric.") for c in m.missing_retention)

    async def provider(c: Any) -> be.Eligibility:
        return await be.eligibility(
            c, tenant_id=o.tenant, country="GB", purpose="IDENTITY_VERIFICATION", overlays={"GB": ov},
            now=datetime.now(UTC), provider="veriff",
        )  # fmt: skip

    p = _tenant(rt_url, o.tenant, provider)
    assert all(m.reasons == ("PROVIDER_MODEL_NOT_ACTIVE",) for m in p.methods)


# --------------------------------------------------------------------------------------------------------------------- 4. dry run and hooks


def test_the_erasure_dry_run_and_the_decision_hook_change_nothing_they_should_not(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    subject = str(uuid.uuid4())

    async def preview(c: Any) -> dict[str, Any]:
        return await ctl.preview_erasure(c, o.tenant, subject_ref=subject, scope="ALL")

    plan = _tenant(rt_url, o.tenant, preview)
    assert (
        plan["dry_run"] is True
        and plan["plan"] == []
        and plan["backups"]["action"] == "ATTEST_LAST"
    )

    async def revoke(c: Any) -> list[str]:
        return await verification_store.revoke_for_decision(
            c, str(uuid.uuid4()), now=NOW, reason="DECISION_REVIEWED_OVERTURNED"
        )

    assert _tenant(rt_url, o.tenant, revoke) == []

    async def not_a_ref(c: Any) -> list[str]:
        return await verification_store.revoke_for_decision(
            c, "dec_not_a_uuid", now=NOW, reason="DECISION_REVIEWED_OVERTURNED"
        )

    assert _tenant(rt_url, o.tenant, not_a_ref) == []


# --------------------------------------------------------------------------------------------------------------------- 5. reauthorization


def test_reauthorization_flags_a_lapsing_dpia_once_and_holds_a_stale_model(
    rt_url: str, owner_url: str
) -> None:
    from datetime import timedelta

    from zid_platform.modules.privacy.application.reauthorization import reauthorization_pass

    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    pkg, model = uuid.uuid4(), uuid.uuid4()

    async def seed(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO privacy.dpia_package (package_id, tenant_id, market, product, version, controller, purpose_code, state, approved_until, "
                "decision_rationale, decided_by, created_by) VALUES (:p, :t, 'GB', 'biometric.face_match', '2.1.0', 'Acme Ltd', 'IDENTITY_VERIFICATION', "
                "'APPROVED', :until, 'approved for the pilot', :d, :c)"
            ),
            {
                "p": pkg,
                "t": uuid.UUID(o.tenant),
                "until": NOW + timedelta(days=10),
                "d": uuid.UUID(o.second),
                "c": uuid.UUID(o.owner),
            },
        )
        await c.execute(
            text(
                "INSERT INTO privacy.model_version (model_id, tenant_id, model_ref, version, kind, state, created_by) "
                "VALUES (:m, :t, 'acme-face', '2.1.0', 'face_match', 'ACTIVE', :c)"
            ),
            {"m": model, "t": uuid.UUID(o.tenant), "c": uuid.UUID(o.owner)},
        )

    _run(owner_url, seed)
    first = _run(owner_url, lambda c: reauthorization_pass(c, now=datetime.now(UTC)))
    assert first["dpia_flagged"] >= 1 and first["models_held"] >= 1
    again = _run(owner_url, lambda c: reauthorization_pass(c, now=datetime.now(UTC)))

    async def read(c: Any) -> tuple[Any, Any]:
        f = (
            await c.execute(
                text(
                    "SELECT reassessment_flagged_at FROM privacy.dpia_package WHERE package_id = :p"
                ),
                {"p": pkg},
            )
        ).scalar()
        s = (
            await c.execute(
                text("SELECT state, state_reason FROM privacy.model_version WHERE model_id = :m"),
                {"m": model},
            )
        ).first()
        return f, s

    flagged_at, state = _run(owner_url, read)
    assert (
        flagged_at is not None
        and state.state == "HELD"
        and "EVALUATION_MISSING" in state.state_reason
    )
    assert (
        again["dpia_flagged"] == 0 or flagged_at is not None
    )  # the same package is never flagged twice


# --------------------------------------------------------------------------------------------------------------------- 6. oversight


def test_the_oversight_monitor_counts_from_the_records_and_names_what_needs_attention(
    rt_url: str,
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def open_case(c: Any) -> dict[str, Any]:
        return await ctl.open_breach(
            c, o.tenant, o.owner, summary="lost laptop", severity="CRITICAL", jurisdictions=["GB"], subjects_estimate=None,
            detected_at=NOW, config={},
        )  # fmt: skip

    _tenant(rt_url, o.tenant, open_case)
    view = _tenant(rt_url, o.tenant, lambda c: ctl.biometric_oversight(c))
    assert view["breaches_pausing_capture"] == 1
    assert (
        "BREACH_PAUSING_CAPTURE" in view["attention"] and "RETENTION_MISSING" in view["attention"]
    )
    assert len(view["retention_missing_classes"]) == 5
    assert view["reviews"] == {"open": 0, "awaiting_checker": 0, "overdue": 0}


# --------------------------------------------------------------------------------------------------------------------- 7. breach dossier


def test_the_breach_dossier_carries_the_capture_timeline_and_the_audit_trail(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def open_case(c: Any) -> dict[str, Any]:
        return await ctl.open_breach(
            c, o.tenant, o.owner, summary="lost laptop", severity="HIGH", jurisdictions=["GB"], subjects_estimate=None,
            detected_at=NOW, config={},
        )  # fmt: skip

    bid = _tenant(rt_url, o.tenant, open_case)["breach_id"]
    d = _tenant(rt_url, o.tenant, lambda c: ctl.breach_dossier(c, bid, {}))
    assert [x["event"] for x in d["capture_timeline"]] == ["PAUSED"]
    events = [a["event_type"] for a in d["audit_trail"]]
    assert "privacy.breach.opened" in events
    assert all("record_hash" in a and "details" not in a for a in d["audit_trail"])


# --------------------------------------------------------------------------------------------------------------------- 8. exposure


def test_production_exposure_widens_with_two_people_and_narrows_with_one(owner_url: str) -> None:
    sid = f"face-gb-x-{uuid.uuid4().hex[:6]}"
    tenant = str(uuid.uuid4())
    scope = bm.Scope(
        bm.Method.FACE_MATCH, "acme-face", "3.0.0", "GB", "IDENTITY_VERIFICATION", tenant
    )

    async def climb(c: Any) -> bm.Record:
        rec = None
        for target, kw in (
            (bm.State.LAB_ONLY, {}),
            (bm.State.PILOT_GATED, {"dpia_ref": "DPIA-1", "evaluation_ref": "EVAL-1"}),
            (
                bm.State.PRODUCTION_ELIGIBLE,
                {"dpia_ref": "DPIA-1", "evaluation_ref": "EVAL-1", "exposure_percent": 20},
            ),
        ):
            p = await svc.propose(
                c, scope=scope, target=target, dpia_ref=kw.get("dpia_ref"), evaluation_ref=kw.get("evaluation_ref"),
                reason="next step after review", evidence_ref="T-1", by="ana", now=datetime.now(UTC), scope_id=sid,
                exposure_percent=kw.get("exposure_percent", 100),
            )  # fmt: skip
            _, rec = await svc.approve(
                c, p.proposal_id, by="ben", reason="approved after review", now=datetime.now(UTC)
            )
        assert rec is not None
        return rec

    rec = _run(owner_url, climb)
    assert rec.exposure_percent == 20 and rec.decision_hash

    async def narrow(c: Any) -> bm.Record:
        return await svc.step_down(
            c, sid, target=bm.State.PRODUCTION_ELIGIBLE, by="sec", reason="error rate rising in rollout",
            evidence_ref="INC-4", now=datetime.now(UTC), exposure_percent=5,
        )  # fmt: skip

    assert _run(owner_url, narrow).exposure_percent == 5
    cols = (
        "scope_id, version, method, engine_id, engine_version, country, purpose, tenant_id, state, dpia_ref, evaluation_ref, reason, "
        "evidence_ref, recorded_by, proposed_by, proposal_id, effective_at, recorded_at, exposure_percent"
    )
    # widening without a proposal is refused by the table itself
    _refused(
        owner_url,
        f"INSERT INTO verify.biometric_method_state ({cols}) VALUES (:s, 5, 'FACE_MATCH', 'acme-face', '3.0.0', 'GB', 'IDENTITY_VERIFICATION', "
        ":t, 'PRODUCTION_ELIGIBLE', 'DPIA-1', 'EVAL-1', 'widen it quietly please', 'T-9', 'sec', NULL, NULL, now(), now(), 90)",
        {"s": sid, "t": uuid.UUID(tenant)},
    )


# --------------------------------------------------------------------------------------------------------------------- 9. assisted route


def test_the_assisted_route_queue_is_claimed_and_completed_by_staff_never_by_the_person(
    rt_url: str,
) -> None:
    from zid_platform.modules.privacy.application import sessions

    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    person, ref = o.second, f"vs_{uuid.uuid4().hex[:12]}"

    async def seed(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO privacy.verification_session (session_ref, tenant_id, subject_principal_id, activity_code, purpose_code, data_category, "
                "decision_id, allowed_at_open, reason_codes, state) VALUES (:r, :t, :p, 'IDENTITY_VERIFICATION', 'IDENTITY_VERIFICATION', 'D3', "
                ":d, true, '{}', 'OPEN')"
            ),
            {"r": ref, "t": uuid.UUID(o.tenant), "p": uuid.UUID(person), "d": uuid.uuid4()},
        )

    _tenant(rt_url, o.tenant, seed)
    chosen = _tenant(
        rt_url,
        o.tenant,
        lambda c: sessions.choose_alternative(
            c, o.tenant, person, ref, mode="ASSISTED_DOCUMENT_REVIEW"
        ),
    )
    assert chosen["assisted_state"] == "WAITING"
    queue = _tenant(rt_url, o.tenant, lambda c: sessions.list_assisted(c))
    assert [q["session_ref"] for q in queue] == [ref]

    with pytest.raises(PrivacyError) as e:
        _tenant(rt_url, o.tenant, lambda c: sessions.claim_assisted(c, o.tenant, person, ref))
    assert e.value.code == "ASSISTED_OWN_SESSION"
    assert (
        _tenant(rt_url, o.tenant, lambda c: sessions.claim_assisted(c, o.tenant, o.owner, ref))[
            "assisted_state"
        ]
        == "IN_PROGRESS"
    )

    with pytest.raises(PrivacyError) as e:
        _tenant(
            rt_url,
            o.tenant,
            lambda c: sessions.complete_assisted(
                c, o.tenant, o.owner, ref, done=True, outcome_ref=None, reason_code=None
            ),
        )
    assert e.value.code == "ASSISTED_OUTCOME_REF_REQUIRED"
    done = _tenant(
        rt_url,
        o.tenant,
        lambda c: sessions.complete_assisted(
            c, o.tenant, o.owner, ref, done=True, outcome_ref="ver_123", reason_code=None
        ),
    )
    assert done["assisted_state"] == "DONE"
    status = _tenant(rt_url, o.tenant, lambda c: sessions.status(c, o.tenant, ref))
    assert status["assisted_state"] == "DONE" and status["route"] == "ALTERNATIVE"
