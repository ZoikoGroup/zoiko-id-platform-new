"""The hosted applicant's rules (ZID-CP-WF01 sections 05, 10, S02, S10): eligible methods, the one journey state, the safe message, the return address."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.privacy.application.applicant_session import return_url_allowed, tenant_of
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.verification.domain import applicant as ap
from zid_platform.modules.verification.domain import idv_policy
from zid_platform.modules.verification.domain.idv_decision import (
    NfcAccess,
    Optical,
    PassiveAuth,
    Signals,
)

NOW = datetime(2026, 11, 24, tzinfo=UTC)


def rules(biometric: dict[str, str] | None = None) -> idv_policy.MarketRules:
    doc = {
        "policy_version": "gb-1", "purpose": "KYC", "chip": "NOT_USED", "face": "NOT_USED", "clone_check": "NOT_USED", "capture_integrity": "NOT_USED",
        "retries": {"nfc": 1, "capture": 3}, "biometric": biometric or {},
        "alternates": [{"route_id": "WEB_AUTO", "may_approve": True, "automatic": True}, {"route_id": "WEB_FACE", "may_approve": True, "automatic": True, "face_match": True}],
    }  # fmt: skip
    return idv_policy.parse({"markets": {"GB": doc}})["GB"]


def test_a_face_route_is_withheld_without_the_markets_basis_and_the_deployments_acknowledgement() -> (
    None
):
    offered, withheld = ap.eligible_methods(rules(), nfc=True, camera=True, face_route_on=True)
    assert {m.method for m in offered} == {"CHIP", "WEB_AUTO"} and withheld == [
        {"method": "WEB_FACE", "reason": "BIOMETRICS_NOT_AUTHORIZED_IN_MARKET"}
    ]
    basis = {"lawful_basis": "CONSENT", "impact_assessment_ref": "DPIA-1"}
    assert "WEB_FACE" not in {
        m.method
        for m in ap.eligible_methods(rules(basis), nfc=True, camera=True, face_route_on=False)[0]
    }
    face = next(
        m
        for m in ap.eligible_methods(rules(basis), nfc=True, camera=True, face_route_on=True)[0]
        if m.method == "WEB_FACE"
    )
    assert face.biometric and face.kind == "DOCUMENT_AND_FACE"


def test_without_nfc_or_a_camera_a_method_is_reached_through_another_device_never_hidden_or_faked() -> (
    None
):
    offered, _ = ap.eligible_methods(rules(), nfc=False, camera=False, face_route_on=False)
    assert all(m.handoff for m in offered)


def case(
    decision: str, reasons: tuple[str, ...] = (), attempts: dict[str, int] | None = None
) -> dict[str, object]:
    return {"decision": decision, "reasons": reasons, "attempts": attempts or {}}


def phase(**kw: object) -> str:
    base: dict[str, object] = {
        "canceled": False,
        "expires_at": NOW + timedelta(hours=1),
        "now": NOW,
        "presented": False,
        "acknowledged": False,
        "case": None,
        "submitted": False,
    }
    return ap.phase(**{**base, **kw})  # type: ignore[arg-type]


def test_the_journey_has_one_state_and_a_result_only_after_submission() -> None:
    assert phase() == "CREATED"
    assert phase(presented=True) == "NOTICE_PRESENTED"
    assert phase(case=case("INDETERMINATE")) == "METHOD_SELECTED"
    assert (
        phase(case=case("APPROVED", attempts={"CAPTURE": 1})) == "CAPTURING"
    )  # an approval is never shown before the person submits
    assert phase(case=case("APPROVED"), submitted=True) == "FINAL_APPROVED"
    assert phase(case=case("REJECTED"), submitted=True) == "FINAL_DECLINED"
    assert (
        phase(case=case("REVIEW_REQUIRED"), submitted=True) == "MANUAL_REVIEW"
    )  # review is never approval (Q34)
    assert (
        phase(
            case=case("REVIEW_REQUIRED", ("RECAPTURE_REQUESTED:GLARE_OR_SHADOW",)), submitted=True
        )
        == "RESUBMISSION_REQUESTED"
    )
    assert phase(case=case("INDETERMINATE"), submitted=True) == "INCONCLUSIVE"
    assert phase(expires_at=NOW - timedelta(seconds=1)) == "EXPIRED"
    assert phase(canceled=True) == "CANCELED" and phase(case=case("ABANDONED")) == "CANCELED"


def test_the_person_is_told_one_safe_thing_and_never_a_detector_or_an_accusation() -> None:
    s = Signals(route="WEB_AUTO")
    assert (
        ap.action("CAPTURING", replace(s, injection_risk=True), submitted=False)
        == "SUSPICIOUS_SIGNAL"
    )
    assert (
        ap.action("CAPTURING", replace(s, optical=Optical.FAIL), submitted=False)
        == "CAPTURE_QUALITY"
    )
    assert (
        ap.action("CAPTURING", Signals(nfc=NfcAccess.UNSUPPORTED), submitted=False)
        == "NFC_UNAVAILABLE"
    )
    assert (
        ap.action("INCONCLUSIVE", Signals(passive_auth=PassiveAuth.CERT_MISSING), submitted=True)
        == "NFC_TRUST_INDETERMINATE"
    )  # Q21: never pass, never fraud
    assert ap.action("MANUAL_REVIEW", s, submitted=True) == "REVIEW_REQUIRED"
    assert ap.action("EXPIRED", None, submitted=False) == "SESSION_EXPIRED"
    for code, (category, text) in ap.MESSAGES.items():
        assert category in {
            "USER_CORRECTABLE",
            "PROVIDER_DOWN",
            "UNSUPPORTED",
            "INDETERMINATE",
            "SUSPICIOUS",
        }, code
        assert not any(w in text.lower() for w in ("fraud", "sanction", "pep", "score")), code
    assert all(v in ap.MESSAGES for v in ap.FROM_ERROR.values())


def test_the_reference_is_short_and_says_nothing() -> None:
    assert ap.reference("vs_0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0") == "ZID-0F1E2D3C4B"


def test_a_return_address_is_https_on_a_verified_domain_only() -> None:
    domains = ["shop.example"]
    assert return_url_allowed("https://shop.example/done", domains, local=False)
    assert return_url_allowed("https://pay.shop.example/done", domains, local=False)
    for bad in (
        "http://shop.example/done",
        "https://evilshop.example/",
        "https://shop.example.evil.io/",
        "https://u:p@shop.example/",
        "javascript:alert(1)",
    ):
        assert not return_url_allowed(bad, domains, local=False), bad
    assert return_url_allowed("http://localhost:3000/x", [], local=True) and not return_url_allowed(
        "http://localhost:3000/x", [], local=False
    )


def test_a_token_names_its_tenant_or_is_refused() -> None:
    assert (
        tenant_of("0f1e2d3c4b5a69788796a5b4c3d2e1f0." + "a" * 43)
        == "0f1e2d3c-4b5a-6978-8796-a5b4c3d2e1f0"
    )
    for bad in ("nope", "0f1e2d3c4b5a69788796a5b4c3d2e1f0.short", "zz." + "a" * 43):
        with pytest.raises(PrivacyError):
            tenant_of(bad)
