"""Biometric method eligibility (ZID-DTV-WF-04 section 19; QA-001, QA-015, QA-043; CR-107): a method is available only when the engine gate, the
method state, the tenant's live DPIA and the country overlay all allow it, and every gate that refuses is named."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

import pytest
from zid_platform.modules.verification.application import biometric_eligibility as be
from zid_platform.modules.verification.domain import biometric_methods as bm
from zid_platform.modules.verification.domain import privacy_overlay as po

NOW = datetime(2026, 11, 17, 12, tzinfo=UTC)
SCOPE = bm.Scope(bm.Method.FACE_MATCH, "acme-face", "2.1.0", "GB", "IDENTITY_VERIFICATION", "t1")
RECORD = bm.Record(
    scope_id="face-gb",
    version=3,
    scope=SCOPE,
    state=bm.State.PILOT_GATED,
    dpia_ref="DPIA-GB-7",
    evaluation_ref="LAB-1",
    reason="pilot approved after review",
    evidence_ref="T-1",
    recorded_by="ben",
    proposed_by="ana",
    proposal_id="p1",
    effective_at=NOW,
    recorded_at=NOW,
)
LIVE = bm.Availability(True, bm.State.PILOT_GATED, ("METHOD_PILOT",), RECORD)
READY: dict[str, Any] = {"ready": True, "blockers": [], "package_id": "pkg-1"}


def overlay(
    biometrics: po.Biometrics = po.Biometrics.ALLOWED, *, signed: bool = True
) -> po.Overlay:
    o = po.Overlay(
        country="GB",
        version=1,
        home_regions=frozenset({"EU_WEST"}),
        transfer_regions={},
        provider_regions={},
        biometrics=biometrics,
    )
    if not signed:
        return o
    digest = po.overlay_hash(o)
    return replace(o, approvals={r: po.Approval(f"{r}-sig", digest) for r in po.APPROVER_ROLES})


def answer(**over: Any) -> be.MethodAnswer:
    args: dict[str, Any] = {
        "engine_reasons": [],
        "state": LIVE,
        "dpia": READY,
        "overlay": overlay(),
    }
    args.update(over)
    return be.combine(bm.Method.FACE_MATCH, **args)


def test_all_gates_open_is_available() -> None:
    a = answer()
    assert (
        a.available
        and a.reasons == ()
        and a.scope_ref == "face-gb:v3"
        and a.dpia_package_id == "pkg-1"
    )


def test_engine_not_accepted_refuses() -> None:
    a = answer(engine_reasons=["ENGINE_NOT_ACCEPTED"], state=None, dpia=None)
    assert not a.available and a.state is bm.State.OFF and "ENGINE_NOT_ACCEPTED" in a.reasons


def test_paused_method_refuses() -> None:
    paused = bm.Availability(
        False, bm.State.PAUSED, ("METHOD_PAUSED",), replace(RECORD, state=bm.State.PAUSED)
    )
    a = answer(state=paused)
    assert not a.available and a.reasons == ("METHOD_PAUSED",)


def test_dpia_not_ready_refuses_and_names_blockers() -> None:  # QA-001
    a = answer(
        dpia={
            "ready": False,
            "blockers": ["DPIA_EXPIRED", "G3_SECURITY_CISO_NOT_SIGNED"],
            "package_id": "pkg-1",
        }
    )
    assert not a.available
    assert "DPIA_NOT_READY" in a.reasons
    assert a.dpia_blockers == ("DPIA_EXPIRED", "G3_SECURITY_CISO_NOT_SIGNED")


def test_no_dpia_package_refuses() -> None:
    a = answer(dpia={"ready": False, "blockers": ["NO_DPIA_PACKAGE"], "package_id": None})
    assert not a.available and a.dpia_blockers == ("NO_DPIA_PACKAGE",) and a.dpia_package_id is None


def test_country_prohibits_biometrics() -> None:
    a = answer(overlay=overlay(po.Biometrics.PROHIBITED))
    assert not a.available and "BIOMETRIC_NOT_PERMITTED_IN_COUNTRY" in a.reasons


def test_missing_or_unsigned_overlay_refuses() -> None:
    assert answer(overlay=None).reasons == ("PRIVACY_OVERLAY_MISSING",)
    unsigned = answer(overlay=overlay(signed=False))
    assert not unsigned.available
    assert (
        "OVERLAY_APPROVAL_MISSING_DPO" in unsigned.reasons
        and "OVERLAY_APPROVAL_MISSING_LEGAL" in unsigned.reasons
    )


def test_consent_required_is_reported_not_refused() -> None:
    a = answer(overlay=overlay(po.Biometrics.CONSENT_REQUIRED))
    assert a.available and a.consent_required


def test_every_failing_gate_is_named() -> None:
    paused = bm.Availability(False, bm.State.PAUSED, ("METHOD_PAUSED",), RECORD)
    a = answer(
        state=paused,
        dpia={"ready": False, "blockers": ["DPIA_EXPIRED"]},
        overlay=overlay(po.Biometrics.PROHIBITED),
    )
    assert set(a.reasons) == {
        "METHOD_PAUSED",
        "DPIA_NOT_READY",
        "BIOMETRIC_NOT_PERMITTED_IN_COUNTRY",
    }


def test_policy_hash_changes_with_what_it_rests_on() -> None:
    a = (answer(),)
    h1 = be.policy_hash(
        country="GB",
        purpose="IDENTITY_VERIFICATION",
        engine={"engine_id": "acme-face"},
        answers=a,
        overlay=overlay(),
    )
    h2 = be.policy_hash(
        country="GB",
        purpose="IDENTITY_VERIFICATION",
        engine={"engine_id": "acme-face"},
        answers=(replace(a[0], scope_ref="face-gb:v4"),),
        overlay=overlay(),
    )
    assert h1 != h2 and len(h1) == 64


def test_dpia_product_names() -> None:
    assert be.dpia_product(bm.Method.FACE_MATCH) == "biometric.face_match"
    assert be.dpia_product(bm.Method.LIVENESS) == "biometric.liveness"


# ------------------------------------------------------------------------------------------------------------- independent evaluation (CR-109)


def test_evaluation_not_ready_refuses_and_names_blockers() -> None:  # QA-015
    a = answer(
        evaluation={
            "ready": False,
            "blockers": ["EVALUATION_NOT_INDEPENDENT", "EVALUATION_STALE"],
            "evaluation_id": "e1",
        }
    )
    assert not a.available and "EVALUATION_NOT_READY" in a.reasons
    assert a.evaluation_blockers == ("EVALUATION_NOT_INDEPENDENT", "EVALUATION_STALE")
    assert a.view()["evaluation_id"] == "e1"


def test_model_missing_from_inventory_refuses() -> None:
    a = answer(
        evaluation={"ready": False, "blockers": ["MODEL_NOT_IN_INVENTORY"], "evaluation_id": None}
    )
    assert not a.available and a.evaluation_blockers == ("MODEL_NOT_IN_INVENTORY",)


def test_ready_evaluation_keeps_the_method_available() -> None:
    a = answer(evaluation={"ready": True, "blockers": [], "evaluation_id": "e2"})
    assert a.available and a.evaluation_id == "e2" and a.evaluation_blockers == ()


# ------------------------------------------------------------------------------------------------------------- provider-hosted (CR-110)


def _provider_world(
    monkeypatch: pytest.MonkeyPatch, versions: dict[str, str | None]
) -> dict[str, list[Any]]:
    import zid_platform.modules.privacy.ports as pp
    from zid_platform.modules.verification.application import biometric_methods as svc

    seen: dict[str, list[Any]] = {"calls": [], "dpia": [], "evidence": []}

    async def active(conn: Any, ref: str, method: str) -> str | None:
        return versions.get(method)

    async def avail(conn: Any, call: bm.Call, *, at: datetime) -> bm.Availability:
        seen["calls"].append(call)
        return bm.Availability(True, bm.State.PRODUCTION_ELIGIBLE, (), None)

    async def gate(conn: Any, market: str, product: str, version: str) -> dict[str, Any]:
        seen["dpia"].append((market, product, version))
        return READY

    async def evidence(conn: Any, method: str, ref: str, version: str) -> dict[str, Any]:
        seen["evidence"].append((method, ref, version))
        return {"ready": True, "blockers": [], "evaluation_id": "e"}

    async def none(*a: Any, **k: Any) -> tuple[str, ...]:
        return ()

    monkeypatch.setattr(pp, "active_model_version", active)
    monkeypatch.setattr(pp, "release_gate", gate)
    monkeypatch.setattr(pp, "method_evidence", evidence)
    monkeypatch.setattr(pp, "capture_paused_breaches", none)
    monkeypatch.setattr(pp, "missing_biometric_retention", none)
    monkeypatch.setattr(svc, "availability_for", avail)
    return seen


def _run_provider(**kw: Any) -> be.Eligibility:
    import asyncio

    return asyncio.run(
        be.eligibility(
            None,  # type: ignore[arg-type]
            tenant_id="t1",
            country="gb",
            purpose="IDENTITY_VERIFICATION",
            overlays={"GB": overlay()},
            now=NOW,
            provider="veriff",
            **kw,
        )
    )


def test_a_provider_method_uses_the_pinned_model_version(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _provider_world(monkeypatch, {"FACE_MATCH": "2026.11", "LIVENESS": "pad-7"})
    e = _run_provider()
    assert e.available(bm.Method.FACE_MATCH) and e.available(bm.Method.LIVENESS)
    assert {(c.engine_id, c.engine_version) for c in seen["calls"]} == {
        ("veriff", "2026.11"),
        ("veriff", "pad-7"),
    }
    assert ("GB", "biometric.face_match", "2026.11") in seen["dpia"]
    assert e.engine == {
        "provider": "veriff",
        "versions": {"FACE_MATCH": "2026.11", "LIVENESS": "pad-7"},
    }


def test_a_provider_without_an_active_model_is_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # drift holds the model: no version
    seen = _provider_world(monkeypatch, {"FACE_MATCH": "2026.11", "LIVENESS": None})
    e = _run_provider()
    live = next(m for m in e.methods if m.method is bm.Method.LIVENESS)
    assert not live.available and live.reasons == ("PROVIDER_MODEL_NOT_ACTIVE",)
    assert all(c.method is bm.Method.FACE_MATCH for c in seen["calls"])
