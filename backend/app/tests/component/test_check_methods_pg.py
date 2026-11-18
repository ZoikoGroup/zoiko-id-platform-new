"""WF-05 check method lifecycle and eligibility on PostgreSQL (ZID-DTV-WF-05 §04, §05, §19, §27; CR-112).

1. The check method tables keep the biometric rules even when the domain code is skipped: two people, one step at a time, no self-approval, no edit of
   history, no resume from PAUSED without a proposal. The two families never mix: a biometric scope cannot be written to the check tables.
2. Through the operator API: no capability is a 403; a proposer cannot approve; another person can; a step down is one person, at once; every action is
   audited under regulatory.check_method.*. The biometric routes, now built by the same code, still answer and audit under their own names.
3. Eligibility over real tables: with the shipped configuration every method is unavailable and says why; with an adapter, approved retention, a live
   scope and a signed overlay the method is available, and a pause takes it away at once.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import onboard
from operator_fixture import ISS
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_regulatory_releases_api_pg import _app, _audit, _grant, _people
from zid_db import tenant_transaction
from zid_platform.modules.verification.application import check_methods as svc
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import privacy_overlay as po

NOW = datetime.now(UTC)
EMAIL = cm.CheckMethod.EMAIL_CHALLENGE


def _run(url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(url) as eng, eng.begin() as c:
            return await work(c)

    return asyncio.run(go())


def _refused(url: str, sql: str, params: dict[str, Any]) -> None:
    async def go(c: Any) -> None:
        await c.execute(text(sql), params)

    with pytest.raises(DBAPIError):
        _run(url, go)


def _signed_overlay(country: str = "GB") -> po.Overlay:
    ov = po.Overlay(country, 1, frozenset({"EU_WEST"}), {}, {}, po.Biometrics.ALLOWED)
    return po.Overlay(
        **{
            **ov.__dict__,
            "approvals": {
                r: po.Approval(f"{r}-sig", po.overlay_hash(ov)) for r in po.APPROVER_ROLES
            },
        }
    )


async def _to_production(c: Any, scope: bm.Scope, sid: str) -> bm.Record:
    rec: bm.Record | None = None
    for target in (bm.State.LAB_ONLY, bm.State.PILOT_GATED, bm.State.PRODUCTION_ELIGIBLE):
        live = target is not bm.State.LAB_ONLY
        p = await svc.propose(
            c, scope=scope, target=target, dpia_ref="DPIA-12" if live else None, evaluation_ref="QA-G2-1" if live else None,
            reason=f"step to {target.value} after review", evidence_ref="T-1", by="ana", now=NOW, scope_id=sid,
        )  # fmt: skip
        _, rec = await svc.approve(
            c, p.proposal_id, by="ben", reason="checked and approved", now=NOW
        )
    assert rec is not None
    return rec


# --------------------------------------------------------------------------------------------------------------------- 1. the tables


def test_check_method_states_keep_two_people_and_one_step_even_without_the_domain(
    owner_url: str,
) -> None:
    sid = f"email-gb-{uuid.uuid4().hex[:8]}"
    scope = bm.Scope(EMAIL, "zid-email-challenge", "1", "GB", "ONBOARDING", None)

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
    assert (rec.version, rec.state, rec.scope.method) == (1, bm.State.LAB_ONLY, EMAIL)
    assert rec.decision_hash is not None

    cols = (
        "scope_id, version, method, engine_id, engine_version, country, purpose, tenant_id, state, dpia_ref, evaluation_ref, reason, "
        "evidence_ref, recorded_by, proposed_by, proposal_id, effective_at, recorded_at, decision_hash"
    )
    row = {
        "sid": sid, "m": "EMAIL_CHALLENGE", "e": "zid-email-challenge", "v": "1", "c": "GB", "p": "ONBOARDING", "t": None,
        "d": "DPIA-1", "ev": "EVAL-1", "r": "skipping the domain code", "ref": "T-9", "at": NOW, "dh": "0" * 64,
    }  # fmt: skip
    insert = (
        f"INSERT INTO verify.check_method_state ({cols}) "
        "VALUES (:sid, :ver, :m, :e, :v, :c, :p, :t, :st, :d, :ev, :r, :ref, :by, :pby, :pid, :at, :at, :dh)"
    )
    # a skipped step; a step up recorded by its own proposer; a method of another family
    _refused(
        owner_url,
        insert,
        {**row, "ver": 2, "st": "PRODUCTION_ELIGIBLE", "by": "ben", "pby": "ana", "pid": None},
    )
    _refused(
        owner_url,
        insert,
        {**row, "ver": 2, "st": "PILOT_GATED", "by": "ana", "pby": "ana", "pid": uuid.uuid4()},
    )
    _refused(
        owner_url,
        insert,
        {
            **row,
            "sid": f"face-{sid}",
            "m": "FACE_MATCH",
            "ver": 1,
            "st": "LAB_ONLY",
            "by": "ben",
            "pby": "ana",
            "pid": uuid.uuid4(),
        },
    )

    async def pause(c: Any) -> bm.Record:
        return await svc.step_down(
            c,
            sid,
            target=bm.State.PAUSED,
            by="sec",
            reason="OTP pumping suspected on this route",
            evidence_ref="INC-7",
            now=NOW,
        )

    assert _run(owner_url, pause).state is bm.State.PAUSED
    _refused(
        owner_url,
        insert,
        {**row, "ver": 3, "st": "LAB_ONLY", "by": "sec", "pby": None, "pid": None, "dh": None},
    )
    _refused(
        owner_url,
        "UPDATE verify.check_method_state SET reason = 'rewritten history' WHERE scope_id = :s",
        {"s": sid},
    )
    _refused(owner_url, "DELETE FROM verify.check_method_state WHERE scope_id = :s", {"s": sid})


def test_the_two_method_families_never_share_a_table(owner_url: str) -> None:
    face = bm.Scope(bm.Method.FACE_MATCH, "acme-face", "1", "GB", "ONBOARDING", None)

    async def wrong(c: Any) -> None:
        await svc.propose(
            c, scope=face, target=bm.State.LAB_ONLY, dpia_ref=None, evaluation_ref=None,
            reason="a biometric scope in the wrong place", evidence_ref="T-1", by="ana", now=NOW,
            scope_id=f"face-{uuid.uuid4().hex[:8]}",
        )  # fmt: skip

    with pytest.raises(bm.MethodError) as e:
        _run(owner_url, wrong)
    assert e.value.code == "METHOD_FAMILY_MISMATCH"


# --------------------------------------------------------------------------------------------------------------------- 2. the operator API


def test_the_operator_api_is_two_people_up_one_person_down_and_audited(
    rt_url: str, owner_url: str
) -> None:
    o, who = _people(rt_url)
    sid = f"sms-gb-{uuid.uuid4().hex[:8]}"
    body = {
        "scope_id": sid,
        "scope": {
            "method": "PHONE_SMS_CHALLENGE",
            "engine_id": "zid-sms",
            "engine_version": "1",
            "country": "GB",
            "purpose": "ONBOARDING",
        },
        "target": "LAB_ONLY",
        "reason": "lab fixtures for the SMS challenge engine",
        "evidence_ref": "T-100",
    }
    with TestClient(_app(rt_url, o), base_url=ISS) as api:

        def call(method: str, path: str, person: str, **kw: Any) -> Any:
            return api.request(method, path, headers=o.bearer(who[person]), **kw)

        assert (
            call("POST", "/v1/regulatory/check-methods/proposals", "author", json=body).status_code
            == 403
        )
        bad = body | {"scope": body["scope"] | {"method": "FACE_MATCH"}}
        _grant(api, o, who, "author", "regulatory.check_method.propose")
        _grant(api, o, who, "author", "regulatory.check_method.approve")
        _grant(api, o, who, "launcher", "regulatory.check_method.approve")
        _grant(api, o, who, "recorder", "regulatory.check_method.pause")
        assert (
            call("POST", "/v1/regulatory/check-methods/proposals", "author", json=bad).status_code
            == 422
        )

        proposed = call("POST", "/v1/regulatory/check-methods/proposals", "author", json=body)
        assert proposed.status_code == 201, proposed.text
        pid = proposed.json()["proposal_id"]
        decide = {"reason": "lab plan and fixtures reviewed"}
        own = call(
            "POST", f"/v1/regulatory/check-methods/proposals/{pid}:approve", "author", json=decide
        )
        assert own.status_code == 403
        ok = call(
            "POST", f"/v1/regulatory/check-methods/proposals/{pid}:approve", "launcher", json=decide
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["state"]["state"] == "LAB_ONLY" and ok.json()["state"]["decision_hash"]

        down = call(
            "POST", f"/v1/regulatory/check-methods/{sid}:step-down", "recorder",
            json={"target": "PAUSED", "reason": "send failures spiking in GB", "evidence_ref": "INC-8"},
        )  # fmt: skip
        assert down.status_code == 200 and down.json()["state"] == "PAUSED"

        listing = call("GET", "/v1/regulatory/check-methods", "recorder").json()
        mine = [i for i in listing["items"] if i["current"]["scope_id"] == sid]
        assert len(mine) == 1 and [v["state"] for v in mine[0]["versions"]] == [
            "LAB_ONLY",
            "PAUSED",
        ]

        # the biometric routes, built by the same code, still answer under their own names
        _grant(api, o, who, "vendor", "regulatory.biometric_method.propose")
        face = {
            **body,
            "scope_id": f"face-{sid}",
            "scope": body["scope"] | {"method": "FACE_MATCH", "engine_id": "acme-face"},
        }
        assert call(
            "POST", "/v1/regulatory/check-methods/proposals", "vendor", json=face
        ).status_code in (403, 422)
        bio = call("POST", "/v1/regulatory/biometric-methods/proposals", "vendor", json=face)
        assert bio.status_code == 201, bio.text
        bio_pid = bio.json()["proposal_id"]

    events = [a["event_type"] for a in _audit(owner_url, f"%{sid}%", f"%{pid}%")]
    assert "regulatory.check_method.proposed" in events
    assert "regulatory.check_method.approved" in events
    assert "regulatory.check_method.stepped_down" in events
    assert "regulatory.check_method.refused" in events  # the self-approval
    assert "regulatory.biometric_method.proposed" in [
        a["event_type"] for a in _audit(owner_url, f"%{bio_pid}%")
    ]


# --------------------------------------------------------------------------------------------------------------------- 3. eligibility


def test_eligibility_names_every_refusing_gate_and_a_pause_wins(
    rt_url: str, owner_url: str
) -> None:
    o = onboard(rt_url)
    ov = _signed_overlay("GB")

    def ask(**kw: Any) -> svc.Eligibility:
        async def go() -> svc.Eligibility:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(o.tenant)) as c:
                return await svc.eligibility(
                    c, tenant_id=o.tenant, country="GB", purpose="ONBOARDING", now=datetime.now(UTC), **kw
                )  # fmt: skip

        return asyncio.run(go())

    shipped = ask(overlays={"GB": ov})
    assert shipped.view()["available_methods"] == []
    assert all(m.reasons[0] == "NO_ADAPTER" for m in shipped.methods)

    version = uuid.uuid4().hex[:6]
    adapter = cm.Adapter(EMAIL, "zid-email-challenge", version, cm.Route.OPEN)
    approved = {
        c: cm.RetentionRule(c, "APPROVED", 30, "controller", "L-1") for c in cm.ALL_DATA_CLASSES
    }
    off = ask(overlays={"GB": ov}, adapters={EMAIL: adapter}, retention=approved)
    email = next(m for m in off.methods if m.method is EMAIL)
    assert not email.available and email.reasons == ("METHOD_OFF",)

    sid = f"email-gb-{version}"
    scope = bm.Scope(EMAIL, "zid-email-challenge", version, "GB", "ONBOARDING", o.tenant)
    _run(owner_url, lambda c: _to_production(c, scope, sid))
    on = ask(overlays={"GB": ov}, adapters={EMAIL: adapter}, retention=approved)
    assert on.view()["available_methods"] == ["EMAIL_CHALLENGE"]
    assert (
        next(m for m in on.view()["methods"] if m["method"] == "EMAIL_CHALLENGE")["scope_ref"]
        == f"{sid}:v3"
    )

    no_overlay = ask(overlays={}, adapters={EMAIL: adapter}, retention=approved)
    assert (
        "PRIVACY_OVERLAY_MISSING"
        in next(m for m in no_overlay.methods if m.method is EMAIL).reasons
    )
    assert no_overlay.policy_hash != on.policy_hash

    async def pause(c: Any) -> bm.Record:
        return await svc.step_down(
            c, sid, target=bm.State.PAUSED, by="sec", reason="abuse on this route", evidence_ref="INC-9", now=datetime.now(UTC)
        )  # fmt: skip

    _run(owner_url, pause)
    paused = ask(overlays={"GB": ov}, adapters={EMAIL: adapter}, retention=approved)
    assert next(m for m in paused.methods if m.method is EMAIL).reasons == ("METHOD_PAUSED",)
