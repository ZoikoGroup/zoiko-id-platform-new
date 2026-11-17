"""Biometric method eligibility (ZID-DTV-WF-04 section 19; QA-001, QA-015, QA-043; CR-107): a method is available only when the engine gate, the
method state, the tenant's live DPIA and the country overlay all allow it, and every gate that refuses is named."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

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
