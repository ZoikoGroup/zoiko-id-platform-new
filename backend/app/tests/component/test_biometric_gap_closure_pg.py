"""WF-04 gap closure on PostgreSQL (CR-113).

1. QA-041: an approved step up records the build and capture SDK it was signed for; the row is append-only; enforce mode refuses a live scope
   approved for another build.
2. QA-039: retiring a market turns every scope in the country OFF and withdraws its open proposals in one action; history is kept.
3. QA-004: a DPIA package approved against a policy is refused (DPIA_POLICY_CHANGED) once the retention policy for a biometric class changes.
4. QA-023: an overdue provider deletion is asked for again with a growing wait, then FAILED and alerted once; it is never completed.
5. QA-034: a breach case records its affected data classes, adds more later and never removes one; the dossier carries them.
6. QA-029: a biometric route refused for its region writes its own event.
7. QA-045: rolling a provider's model back makes every gate ask about the rolled-back version, consistently.
8. QA-035: every event these flows wrote is scanned: no key names a person, a face, an image or a template.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from onboarding_fixture import onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_model_governance_pg import metrics
from test_v2_privacy_pg import ACTIONS, RESOURCES, runtime  # noqa: F401  (runtime is an autouse fixture)
from zid_db import tenant_transaction
from zid_platform.modules.privacy.application import control as ctl
from zid_platform.modules.privacy.application import dpia_policy
from zid_platform.modules.privacy.application import model_governance as models
from zid_platform.modules.privacy.application.erasure_retry import RETRY_WAITS, erasure_retry_pass
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.application import biometric_markets, privacy_gate
from zid_platform.modules.verification.application import biometric_methods as svc
from zid_platform.modules.verification.application import biometric_release as rel
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import privacy_overlay as po

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


def _overlay(country: str = "GB", **kw: Any) -> po.Overlay:
    o = po.Overlay(
        country,
        1,
        frozenset({"EU_WEST"}),
        {},
        kw.pop("provider_regions", {}),
        po.Biometrics.ALLOWED,
    )
    d = po.overlay_hash(o)
    return po.Overlay(
        **{**o.__dict__, "approvals": {r: po.Approval(f"{r}-sig", d) for r in po.APPROVER_ROLES}}
    )


def _events(owner_url: str, tenant: str) -> list[tuple[str, dict[str, Any]]]:
    async def read(c: Any) -> list[tuple[str, dict[str, Any]]]:
        rows = (
            await c.execute(
                text(
                    "SELECT event_type, payload::text AS p FROM events.outbox_event WHERE tenant_id = :t ORDER BY created_at"
                ),
                {"t": uuid.UUID(tenant)},
            )
        ).all()
        return [(r.event_type, json.loads(r.p)) for r in rows]

    return _run(owner_url, read)


def _climb(owner_url: str, sid: str, scope: bm.Scope, to: bm.State) -> bm.Record:
    """LAB_ONLY, then one step at a time to `to`, each proposed by ana and approved by ben."""
    order = [bm.State.LAB_ONLY, bm.State.PILOT_GATED, bm.State.PRODUCTION_ELIGIBLE]

    async def go(c: Any) -> bm.Record:
        rec: bm.Record | None = None
        for target in order[: order.index(to) + 1]:
            p = await svc.propose(
                c, scope=scope, target=target, dpia_ref=None if target is bm.State.LAB_ONLY else "DPIA-1",
                evaluation_ref=None if target is bm.State.LAB_ONLY else "LAB-1", reason=f"step up to {target.value}",
                evidence_ref="T-1", by="ana", now=datetime.now(UTC), scope_id=sid,
            )  # fmt: skip
            _, rec = await svc.approve(
                c, p.proposal_id, by="ben", reason="reviewed and approved", now=datetime.now(UTC)
            )
        assert rec is not None
        return rec

    return _run(owner_url, go)


# --------------------------------------------------------------------------------------------------------------------- 1. release attestation


def test_an_approval_records_the_build_it_was_signed_for(
    owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ZID_RELEASE_BUILD", "sha256:build-one")
    monkeypatch.setenv("ZID_BIOMETRIC_CAPTURE_SDK", "capture-4.2")
    sid = f"face-gb-rel-{uuid.uuid4().hex[:6]}"
    tenant = str(uuid.uuid4())
    scope = bm.Scope(
        bm.Method.FACE_MATCH, "acme-face", "4.0.0", "GB", "IDENTITY_VERIFICATION", tenant
    )
    rec = _climb(owner_url, sid, scope, bm.State.PILOT_GATED)

    async def read(c: Any) -> Any:
        return await rel.attestation_for(c, sid, rec.version)

    att = _run(owner_url, read)
    assert att is not None and (att.build_ref, att.sdk_version, att.version) == (
        "sha256:build-one",
        "capture-4.2",
        2,
    )
    assert att.decision_hash == rec.decision_hash
    assert att.attestation_hash == rel.attestation_hash(
        scope_id=sid,
        version=2,
        decision_hash=rec.decision_hash or "",
        release=rel.Release("sha256:build-one", "capture-4.2"),
        by="ben",
    )

    async def edit(c: Any) -> None:
        await c.execute(
            text(
                "UPDATE privacy.biometric_release_attestation SET build_ref = 'forged' WHERE scope_id = :s"
            ),
            {"s": sid},
        )

    with pytest.raises(DBAPIError):
        _run(owner_url, edit)

    # a pause after the approval still rests on the approval's attestation
    async def pause(c: Any) -> Any:
        r = await svc.step_down(
            c,
            sid,
            target=bm.State.PAUSED,
            by="sec",
            reason="incident at supplier",
            evidence_ref="INC-1",
            now=datetime.now(UTC),
        )
        return await rel.attestation_for(c, sid, r.version)

    assert _run(owner_url, pause).version == 2

    # the build changes: enforce refuses the live scope until it is approved again
    monkeypatch.setenv("ZID_RELEASE_BUILD", "sha256:build-two")
    assert rel.drift(bm.State.PILOT_GATED, att, rel.running()) == ("RELEASE_BUILD_CHANGED",)


# --------------------------------------------------------------------------------------------------------------------- 2. retire a market


def test_retiring_a_market_turns_every_scope_off_and_keeps_history(owner_url: str) -> None:
    country = "IS"  # a market no other test uses
    tenant = str(uuid.uuid4())
    face = bm.Scope(
        bm.Method.FACE_MATCH, "acme-face", "5.0.0", country, "IDENTITY_VERIFICATION", tenant
    )
    live = bm.Scope(
        bm.Method.LIVENESS, "acme-pad", "5.0.0", country, "IDENTITY_VERIFICATION", tenant
    )
    a, b = f"face-is-{uuid.uuid4().hex[:6]}", f"pad-is-{uuid.uuid4().hex[:6]}"
    _climb(owner_url, a, face, bm.State.PILOT_GATED)
    _climb(owner_url, b, live, bm.State.LAB_ONLY)

    async def open_proposal(c: Any) -> str:
        p = await svc.propose(
            c, scope=live, target=bm.State.PILOT_GATED, dpia_ref="DPIA-1", evaluation_ref="LAB-1", reason="pilot for the market",
            evidence_ref="T-2", by="ana", now=datetime.now(UTC),
        )  # fmt: skip
        return p.proposal_id

    pid = _run(owner_url, open_proposal)

    async def retire(c: Any) -> biometric_markets.Retirement:
        return await biometric_markets.retire_market(
            c,
            country.lower(),
            by="dpo",
            reason="market exit approved by the board",
            evidence_ref="BOARD-7",
            now=datetime.now(UTC),
        )

    r = _run(owner_url, retire)
    assert {x.scope_id for x in r.retired} == {a, b} and all(
        x.state is bm.State.OFF for x in r.retired
    )
    assert r.withdrawn == (pid,)
    again = _run(owner_url, retire)  # retiring twice changes nothing more
    assert again.retired == () and set(again.already_off) == {a, b}

    async def after(c: Any) -> tuple[Any, list[Any]]:
        call = bm.Call(
            bm.Method.FACE_MATCH, "acme-face", "5.0.0", country, "IDENTITY_VERIFICATION", tenant
        )
        listing = await svc.list_scopes(c)
        hist = next(vs for vs in listing["scopes"] if vs[-1].scope_id == a)
        return await svc.availability_for(c, call, at=datetime.now(UTC)), hist

    avail, hist = _run(owner_url, after)
    assert not avail.available and avail.state is bm.State.OFF
    assert [h.state for h in hist] == [
        bm.State.LAB_ONLY,
        bm.State.PILOT_GATED,
        bm.State.OFF,
    ]  # nothing rewritten

    with pytest.raises(bm.MethodError):
        _run(
            owner_url,
            lambda c: biometric_markets.retire_market(
                c,
                "AQ",
                by="dpo",
                reason="no such market here",
                evidence_ref="X-1",
                now=datetime.now(UTC),
            ),
        )


# --------------------------------------------------------------------------------------------------------------------- 3. DPIA policy change


def test_a_dpia_is_refused_once_the_policy_it_assessed_changes(rt_url: str, owner_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    pkg = uuid.uuid4()
    version = f"7.{uuid.uuid4().int % 1000}"

    async def seed(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO privacy.dpia_package (package_id, tenant_id, market, product, version, controller, purpose_code, state, approved_until, "
                "decision_rationale, decided_by, created_by) VALUES (:p, :t, 'GB', 'biometric.face_match', :v, 'Acme Ltd', 'IDENTITY_VERIFICATION', "
                "'APPROVED', :until, 'approved for the pilot', :d, :c)"
            ),
            {
                "p": pkg,
                "t": uuid.UUID(o.tenant),
                "v": version,
                "until": NOW + timedelta(days=200),
                "d": uuid.UUID(o.second),
                "c": uuid.UUID(o.owner),
            },
        )

    _run(owner_url, seed)

    async def stamp(c: Any) -> None:  # what decide_package does on approval
        row = (
            await c.execute(
                text("SELECT * FROM privacy.dpia_package WHERE package_id = :p"), {"p": pkg}
            )
        ).one()
        h = await ctl._package_policy(c, row)
        await c.execute(
            text("UPDATE privacy.dpia_package SET policy_hash = :h WHERE package_id = :p"),
            {"h": h, "p": pkg},
        )

    _tenant(rt_url, o.tenant, stamp)

    def blockers() -> list[str]:
        return _tenant(
            rt_url, o.tenant, lambda c: ctl.release_gate(c, "GB", "biometric.face_match", version)
        )["blockers"]

    assert dpia_policy.POLICY_CHANGED not in blockers()

    async def new_retention(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO privacy.retention_policy (retention_policy_id, tenant_id, policy_code, version, object_type, purpose_code, "
                "jurisdiction_pack_id, retention_trigger, retention_days, action_on_expiry, created_by, approved_by) VALUES (:id, :t, :code, 1, "
                "'biometric.face_template', 'IDENTITY_VERIFICATION', 'gb', 'VERIFIED_AT', 30, 'DELETE', :c, :a)"
            ),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(o.tenant),
                "code": f"BIO_TPL_{uuid.uuid4().hex[:6].upper()}",
                "c": uuid.UUID(o.owner),
                "a": uuid.UUID(o.second),
            },
        )

    _run(owner_url, new_retention)
    assert dpia_policy.POLICY_CHANGED in blockers()
    listed = _tenant(rt_url, o.tenant, lambda c: ctl.list_packages(c))
    mine = next(p for p in listed if p["package_id"] == str(pkg))
    assert (
        mine["release"]["ready"] is False
        and dpia_policy.POLICY_CHANGED in mine["release"]["blockers"]
    )


# --------------------------------------------------------------------------------------------------------------------- 4. erasure retry


def test_an_overdue_provider_deletion_is_retried_then_alerted_and_never_completed(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    job, task = uuid.uuid4(), uuid.uuid4()

    async def seed(c: Any) -> None:
        await c.execute(
            text(
                "INSERT INTO privacy.erasure_job (job_id, tenant_id, subject_ref, scope, reason, legal_basis, requested_by) "
                "VALUES (:j, :t, :s, 'ALL', 'subject asked', 'ART17', :by)"
            ),
            {"j": job, "t": uuid.UUID(o.tenant), "s": str(uuid.uuid4()), "by": uuid.UUID(o.owner)},
        )
        await c.execute(
            text(
                "INSERT INTO privacy.erasure_task (task_id, tenant_id, job_id, target_kind, target_ref, state, due_at, asset_classes) "
                "VALUES (:k, :t, :j, 'PROVIDER', 'veriff', 'PENDING', :due, ARRAY['biometric.face_template'])"
            ),
            {"k": task, "t": uuid.UUID(o.tenant), "j": job, "due": NOW - timedelta(days=1)},
        )

    _run(owner_url, seed)

    async def task_row(c: Any) -> Any:
        return (
            await c.execute(
                text("SELECT * FROM privacy.erasure_task WHERE task_id = :k"), {"k": task}
            )
        ).one()

    t0 = datetime.now(UTC)
    _run(owner_url, lambda c: erasure_retry_pass(c, now=t0))
    r = _run(owner_url, task_row)
    assert (r.attempts, r.state, r.alerted_at) == (1, "PENDING", None)
    assert r.next_attempt_at == t0 + RETRY_WAITS[0]
    _run(owner_url, lambda c: erasure_retry_pass(c, now=t0))  # not due again yet
    assert _run(owner_url, task_row).attempts == 1

    async def exhaust(c: Any) -> None:
        await c.execute(
            text(
                "UPDATE privacy.erasure_task SET attempts = :a, next_attempt_at = :n WHERE task_id = :k"
            ),
            {"a": len(RETRY_WAITS), "n": t0 - timedelta(minutes=1), "k": task},
        )

    _run(owner_url, exhaust)
    _run(owner_url, lambda c: erasure_retry_pass(c, now=t0))
    r = _run(owner_url, task_row)
    assert (r.state, r.failure, r.receipt_hash) == (
        "FAILED",
        "RETRIES_EXHAUSTED",
        None,
    ) and r.alerted_at is not None
    _run(
        owner_url, lambda c: erasure_retry_pass(c, now=t0 + timedelta(days=30))
    )  # alerted once, never again
    events = [(n, p) for n, p in _events(owner_url, o.tenant) if "erasure.deletion" in n]
    names = [n.split(".")[-2] for n, _ in events]
    assert names == ["deletion_requested", "deletion_failed"], names
    job_state = _run(
        owner_url,
        lambda c: c.execute(
            text("SELECT state FROM privacy.erasure_job WHERE job_id = :j"), {"j": job}
        ),
    )
    assert job_state.scalar() == "FAILED"

    async def complete_without_receipt(c: Any) -> None:
        await c.execute(
            text("UPDATE privacy.erasure_task SET state = 'ACKNOWLEDGED' WHERE task_id = :k"),
            {"k": task},
        )

    with pytest.raises(DBAPIError):
        _run(owner_url, complete_without_receipt)


# --------------------------------------------------------------------------------------------------------------------- 5. breach data classes


def test_a_breach_records_its_affected_data_classes_and_never_drops_one(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def open_case(c: Any) -> dict[str, Any]:
        return await ctl.open_breach(
            c, o.tenant, o.owner, summary="vendor bucket exposed", severity="MODERATE", jurisdictions=["GB"], subjects_estimate=None,
            detected_at=NOW, config={}, data_classes=["biometric.match_result"],
        )  # fmt: skip

    case = _tenant(rt_url, o.tenant, open_case)
    assert case["data_classes"] == ["biometric.match_result"]
    bid = case["breach_id"]

    async def add(c: Any) -> dict[str, Any]:
        return await ctl.add_breach_data_classes(
            c,
            o.tenant,
            o.owner,
            bid,
            data_classes=["biometric.face_template", "biometric.match_result"],
        )

    assert _tenant(rt_url, o.tenant, add)["data_classes"] == [
        "biometric.face_template",
        "biometric.match_result",
    ]
    dossier = _tenant(rt_url, o.tenant, lambda c: ctl.breach_dossier(c, bid, {}))
    assert dossier["breach"]["data_classes"] == [
        "biometric.face_template",
        "biometric.match_result",
    ]
    assert "privacy.breach.data_classes_added" in [a["event_type"] for a in dossier["audit_trail"]]
    with pytest.raises(PrivacyError):
        _tenant(
            rt_url,
            o.tenant,
            lambda c: ctl.add_breach_data_classes(
                c, o.tenant, o.owner, bid, data_classes=["Jane Smith"]
            ),
        )


# --------------------------------------------------------------------------------------------------------------------- 6. region block event


def test_a_biometric_route_refused_for_its_region_writes_its_own_event(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from zid_platform.modules.verification.infrastructure import verification_store as store

    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def profile(conn: Any, profile_id: str) -> Any:
        return SimpleNamespace(biometric_jurisdictions={"GB"})

    async def routes(conn: Any) -> list[Any]:
        return []

    monkeypatch.setattr(store, "load_profile", profile)
    monkeypatch.setattr(store, "list_profile_routes", routes)
    overlays = {"GB": _overlay(provider_regions={"veriff": "US_EAST"})}

    async def start(c: Any) -> Any:
        return await privacy_gate.check_start(
            c, tenant_id=o.tenant, profile_id=str(uuid.uuid4()), subject_principal_id=str(uuid.uuid4()), country="GB", provider="veriff",
            overlays=overlays, self_run=True,
        )  # fmt: skip

    decision, _ = _tenant(rt_url, o.tenant, start)
    assert not decision.allowed and "CROSS_BORDER_ROUTE_DENIED" in decision.reasons
    got = [p for n, p in _events(owner_url, o.tenant) if n.endswith("biometric.region_blocked.v1")]
    assert len(got) == 1
    data = got[0].get("data", got[0])
    assert (
        data["country"] == "GB"
        and data["provider_region"] == "US_EAST"
        and data["reasons"] == ["CROSS_BORDER_ROUTE_DENIED"]
    )


# --------------------------------------------------------------------------------------------------------------------- 7. model rollback


def test_rolling_a_provider_model_back_moves_every_gate_to_that_version(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)
    ref = f"prov-{uuid.uuid4().hex[:6]}"

    async def ready(c: Any, version: str) -> None:
        await models.register(c, o.tenant, o.owner, model_ref=ref, version=version, kind="face_match", provider_service_ref=None,
                              artifact_sha256=None, live_features=["capture_quality"])  # fmt: skip
        await models.record_evaluation(c, o.tenant, o.owner, model_ref=ref, version=version, evaluator_ref="lab-1", independent=True,
                                       cohorts=5, metrics=metrics(), accessible_alternative_confirmed=True, audit_attributes=[], report_ref=None)  # fmt: skip
        await models.promote(c, o.tenant, o.second, ref, version)

    async def check(c: Any) -> be.Eligibility:
        return await be.eligibility(
            c, tenant_id=o.tenant, country="GB", purpose="IDENTITY_VERIFICATION", overlays={"GB": _overlay()}, now=datetime.now(UTC), provider=ref,
        )  # fmt: skip

    _tenant(rt_url, o.tenant, lambda c: ready(c, "1.0"))
    _tenant(rt_url, o.tenant, lambda c: ready(c, "2.0"))
    on_two = _tenant(rt_url, o.tenant, check)
    _tenant(rt_url, o.tenant, lambda c: models.rollback(c, o.tenant, o.second, ref, "1.0"))
    on_one = _tenant(rt_url, o.tenant, check)

    def face(e: be.Eligibility) -> be.MethodAnswer:
        return next(m for m in e.methods if m.method is bm.Method.FACE_MATCH)

    assert on_two.engine and on_two.engine["versions"]["FACE_MATCH"] == "2.0"
    assert on_one.engine and on_one.engine["versions"]["FACE_MATCH"] == "1.0"
    # the evaluation, the DPIA lookup and the policy hash all follow the rolled-back version; nothing still points at 2.0
    assert (
        face(on_one).evaluation_blockers == ()
        and face(on_one).evaluation_id != face(on_two).evaluation_id
    )
    assert on_one.policy_hash != on_two.policy_hash
    versions = _tenant(rt_url, o.tenant, lambda c: models.list_models(c))
    states = {m["version"]: m["state"] for m in versions if m["model_ref"] == ref}
    assert states == {"1.0": "ACTIVE", "2.0": "RETIRED"}


# --------------------------------------------------------------------------------------------------------------------- 8. payload scan

_PROHIBITED = re.compile(
    r"(?i)(^|_)(e?mail|name|full_name|phone|address|passport|biometric|face|selfie|fingerprint|password|secret|token|raw|"
    r"ssn|national_id|dob|birth|image|photo|media|template|embedding|vector|frame|video|portrait)(_|$)"
)
_ALLOWED = frozenset({"action", "purpose_id", "outcome", "event_type", "type", "subject"})


def _keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [k for k in value] + [x for v in value.values() for x in _keys(v)]
    if isinstance(value, list):
        return [x for v in value for x in _keys(v)]
    return []


def test_no_event_these_flows_write_names_a_person_or_media(
    rt_url: str, owner_url: str
) -> None:  # QA-035
    o = onboard(rt_url, extra_actions=ACTIONS, extra_resources=RESOURCES)

    async def flows(c: Any) -> None:
        case = await ctl.open_breach(
            c, o.tenant, o.owner, summary="lost laptop", severity="HIGH", jurisdictions=["GB"], subjects_estimate=None, detected_at=NOW,
            config={}, data_classes=["biometric.face_template"],
        )  # fmt: skip
        await ctl.add_breach_data_classes(
            c, o.tenant, o.owner, case["breach_id"], data_classes=["biometric.live_selfie"]
        )
        await privacy_gate.record_region_blocked(
            c, o.tenant, country="GB", provider="veriff", overlay=_overlay(provider_regions={"veriff": "US_EAST"}),
            decision=po.RouteDecision(False, ("CROSS_BORDER_ROUTE_DENIED",)),
        )  # fmt: skip

    _tenant(rt_url, o.tenant, flows)
    events = _events(owner_url, o.tenant)
    assert events
    for name, payload in events:
        data = payload.get("data", payload)
        bad = [k for k in _keys(data) if k not in _ALLOWED and _PROHIBITED.search(k)]
        assert not bad, f"{name}: {bad}"
        blob = json.dumps(data)
        assert not re.search(r"[A-Za-z0-9+/]{200,}={0,2}", blob), f"{name} carries an encoded blob"
