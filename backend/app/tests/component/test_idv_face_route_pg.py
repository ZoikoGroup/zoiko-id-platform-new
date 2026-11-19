"""The face-match route over HTTP on PostgreSQL (WF03; CR-129): a live face against the photograph on an uploaded document, decided by the platform with no reviewer.

It is an owner-acknowledged route that runs no liveness test and does not check the document is genuine, so these tests keep it narrow: it needs the owner's acknowledgement and the
person's agreement, a match is the only thing that approves, a face that cannot be found is a retake, nothing is kept, and the claim it leaves is the lowest grade.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import ctx  # noqa: F401
from onboarding_fixture import onboard
from test_idv_media_pg import READING, app_for, entry, photo, rows  # noqa: F401
from test_idv_media_pg import fake_imaging  # noqa: F401  (autouse fixture)
from zid_platform.modules.verification.api.idv_cases import FACE_ROUTE_ACK
from zid_platform.modules.verification.infrastructure import face_lab

SCOPE = "verification:execute verification:read"
CLAIM = "SELECT grade FROM verify.idv_case_claim WHERE case_id = CAST(:c AS uuid)"


def face_entry(threshold: float = 0.4) -> dict[str, Any]:
    return entry(
        result_valid_days=30,
        alternates=[{"route_id": "WEB_FACE", "may_approve": True, "automatic": True, "face_match": True, "face_threshold": threshold}],
    )


def similarity(monkeypatch: pytest.MonkeyPatch, cosine: float | None, *, down: bool = False) -> None:
    """What the face service answers: a similarity, no face found, or nothing at all."""

    async def compare(probe: bytes, reference: bytes, **_: Any) -> dict[str, Any]:
        if down:
            raise face_lab.FaceLabError("FACE_ENGINE_UNAVAILABLE", 503)
        if cosine is None:
            raise face_lab.FaceLabError("NO_FACE_PROBE", 422)
        return {"cosine": cosine, "score": max(0.0, cosine), "recommended_cosine_threshold": 0.363}

    monkeypatch.setattr(face_lab, "compare", compare)


def run(api: Any, who: dict[str, str], *, selfie: bool = True, consent: bool = True) -> tuple[str, Any]:
    case = api.post("/v1/idv/cases", headers=who, json={"market": "GB", "route": "WEB_FACE"}).json()["case_id"]
    nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=who).json()["nonce"]
    body: dict[str, Any] = {"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB", "consent": consent}
    if selfie:
        body["selfie_base64"] = base64.b64encode(photo()).decode()
    return case, api.post(f"/v1/idv/cases/{case}/document-image", headers=who, json=body)


def test_a_match_is_verified_with_no_reviewer_nothing_kept_and_the_lowest_grade(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.405)  # the score a real Aadhaar card and a live photograph gave in the lab
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry(0.363)) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        case, done = run(api, me)
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["decision"] == "APPROVED", body
        assert "FACE_CONSENT_GIVEN" in body["reasons"]  # the agreement is on the record
        assert body["face_similarity"] == 0.405 and body["face_threshold"] == 0.363  # the two numbers are shown, so the comparison can be seen working
    assert rows(owner_url, CLAIM, c=case)[0][0] == "FACE_MATCH_NO_LIVENESS"  # and the claim says what was and was not done
    assert rows(owner_url, "SELECT 1 FROM verify.idv_case_media WHERE case_id = CAST(:c AS uuid)", c=case) == []  # neither photograph is kept


def test_a_document_with_no_machine_readable_lines_can_still_be_verified(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.6)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    READING["printed"] = None  # an Aadhaar card has none
    READING["checks"] = {k: v for k, v in READING["checks"].items() if not k.startswith("MRZ_")} | {"MRZ_READABLE": "INCONCLUSIVE"}
    with app_for(rt_url, o, face_entry()) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        case, done = run(api, me)
        assert done.json()["decision"] == "APPROVED", done.json()
    assert rows(owner_url, CLAIM, c=case)[0][0] == "FACE_MATCH_NO_LIVENESS"


def test_a_similarity_below_the_threshold_is_a_retake_never_an_accusation_and_leaves_no_claim(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.2)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry(0.363)) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        case, done = run(api, me)
        assert done.json()["decision"] == "INDETERMINATE" and "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO" in done.json()["reasons"]
    assert rows(owner_url, CLAIM, c=case) == []


def test_a_face_that_cannot_be_found_is_a_retake(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, None)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry()) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        _, done = run(api, me)
        assert done.json()["decision"] == "INDETERMINATE" and "FACE_NOT_FOUND_OR_UNCLEAR" in done.json()["reasons"]


def test_without_the_owners_acknowledgement_the_route_refuses(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.9)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry()) as api:  # the acknowledgement is not set
        case, refused = run(api, me)
        assert refused.status_code == 403 and refused.json()["error"]["code"] == "FACE_ROUTE_NOT_ENABLED"
    assert rows(owner_url, CLAIM, c=case) == []


def test_it_needs_the_persons_agreement_and_a_live_photograph(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.9)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry()) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        _, no_consent = run(api, me, consent=False)
        assert no_consent.status_code == 422 and no_consent.json()["error"]["code"] == "FACE_CONSENT_REQUIRED"
        _, no_photo = run(api, me, selfie=False)
        assert no_photo.status_code == 422 and no_photo.json()["error"]["code"] == "LIVE_PHOTO_REQUIRED"


def test_an_unavailable_face_service_is_a_retryable_refusal_never_a_decision(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    similarity(monkeypatch, 0.9, down=True)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry()) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        _, down = run(api, me)
        assert down.status_code == 503 and down.json()["error"]["code"] == "FACE_ENGINE_UNAVAILABLE"


def test_sending_the_same_photograph_again_on_the_same_case_is_a_retry_not_a_replay(rt_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry(0.363)) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        case = api.post("/v1/idv/cases", headers=me, json={"market": "GB", "route": "WEB_FACE"}).json()["case_id"]

        def send() -> Any:
            nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=me).json()["nonce"]
            body = {"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "selfie_base64": base64.b64encode(photo()).decode(), "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB", "consent": True}
            return api.post(f"/v1/idv/cases/{case}/document-image", headers=me, json=body)

        similarity(monkeypatch, 0.2)
        assert send().json()["decision"] == "INDETERMINATE"
        similarity(monkeypatch, 0.6)
        again = send().json()  # the very same photographs
        assert again["decision"] == "APPROVED" and "CAPTURE_INJECTION_RISK" not in again["reasons"], again


CARD = "Government of India\nANILKUMAR NAKULURI\nDOB: 14/03/1994\nMale\n7421 5530 9187\nMy Aadhaar, My Identity\nVID 9123 4567 8901 2345"


def with_details(api: Any, who: dict[str, str], details: dict[str, Any]) -> Any:
    case = api.post("/v1/idv/cases", headers=who, json={"market": "GB", "route": "WEB_FACE"}).json()["case_id"]
    nonce = api.post(f"/v1/idv/cases/{case}/nonce", headers=who).json()["nonce"]
    body = {"nonce": nonce, "image_base64": base64.b64encode(photo()).decode(), "selfie_base64": base64.b64encode(photo()).decode(), "captured_at": datetime.now(UTC).isoformat(), "channel": "WEB", "consent": True, "details": details}
    return api.post(f"/v1/idv/cases/{case}/document-image", headers=who, json=body)


def test_typed_details_that_are_on_the_document_are_reported_and_a_contradiction_is_refused_before_anything_is_decided(rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    from zid_platform.modules.verification.api import idv_cases

    async def read_page_text(image: bytes, **_: Any) -> str:
        return CARD

    monkeypatch.setattr(idv_cases, "read_page_text", read_page_text)
    similarity(monkeypatch, 0.5)
    o = onboard(rt_url)
    me = o.bearer(o.owner, scope=SCOPE)
    with app_for(rt_url, o, face_entry(0.363)) as api:
        api.app.state.idv_face_route_ack = FACE_ROUTE_ACK
        good = with_details(api, me, {"name": "Anilkumar Nakuluri", "number": "7421 5530 9187", "birth_date": "1994-03-14"})
        assert good.status_code == 200 and good.json()["decision"] == "APPROVED", good.text
        assert good.json()["details"] == {"name": "MATCH", "number": "MATCH", "birth_date": "MATCH"}
        bad = with_details(api, me, {"name": "Someone Else"})
        assert bad.status_code == 422 and bad.json()["error"]["code"] == "DETAILS_DO_NOT_MATCH_DOCUMENT" and "name" in bad.json()["error"]["message"]
