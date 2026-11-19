"""The face-match lab (CR-128): the sidecar client, the engine adapter and the capability. No sidecar runs here; a transport stands in for it."""

from __future__ import annotations

import asyncio
import base64

import httpx
import pytest
from zid_platform.modules.operator_access.api import face_lab as api
from zid_platform.modules.operator_access.domain.model import CAPABILITIES
from zid_platform.modules.verification.infrastructure import face_lab as lab
from zid_platform.modules.verification.infrastructure.engine_adapters import EngineUnavailable

PHOTO = b"\xff\xd8\xff" + b"x" * 200  # not a real photograph: the sidecar is a stand-in


@pytest.fixture(autouse=True)
def _url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZID_FACE_URL", "http://face:8091")


def _ok() -> dict:
    return {
        "cosine": 0.71,
        "score": 0.71,
        "probe": {"faces": 1, "detection": 0.93, "face_ratio": 0.2},
        "reference": {"faces": 1, "detection": 0.9, "face_ratio": 0.05},
        "engine": "opencv-yunet-2023mar+sface-2021dec",
        "recommended_cosine_threshold": 0.363,
    }


def _transport(status: int, body: object) -> httpx.MockTransport:
    return httpx.MockTransport(lambda request: httpx.Response(status, json=body))


def test_a_measurement_comes_back_from_the_sidecar() -> None:
    got = asyncio.run(lab.compare(PHOTO, PHOTO, transport=_transport(200, _ok())))
    assert got["cosine"] == 0.71 and got["recommended_cosine_threshold"] == 0.363


def test_the_photographs_are_sent_as_base64_and_nothing_else() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"], seen["body"] = str(request.url), request.content
        return httpx.Response(200, json=_ok())

    asyncio.run(lab.compare(PHOTO, PHOTO, transport=httpx.MockTransport(handler)))
    assert seen["url"] == "http://face:8091/match"
    assert base64.b64encode(PHOTO) in seen["body"]


@pytest.mark.parametrize("reason", ["NO_FACE_PROBE", "NO_FACE_REFERENCE", "MULTIPLE_FACES_PROBE", "IMAGE_UNREADABLE"])
def test_the_sidecars_own_reason_is_passed_on_as_a_refusal(reason: str) -> None:
    with pytest.raises(lab.FaceLabError) as e:
        asyncio.run(lab.compare(PHOTO, PHOTO, transport=_transport(422, {"error": reason})))
    assert e.value.code == reason and e.value.http_status == 422


def test_an_unreachable_or_broken_sidecar_is_unavailable_never_a_score() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down")

    for transport in (httpx.MockTransport(boom), _transport(500, {"error": "ENGINE_FAILED"}), _transport(200, ["not", "an", "object"])):
        with pytest.raises(lab.FaceLabError) as e:
            asyncio.run(lab.compare(PHOTO, PHOTO, transport=transport))
        assert e.value.code == "FACE_ENGINE_UNAVAILABLE" and e.value.http_status == 503


@pytest.mark.parametrize("value", ["", "ftp://face", "face:8091", "javascript:alert(1)"])
def test_only_an_http_address_is_ever_called(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ZID_FACE_URL", value)
    assert lab.configured_url() is None
    with pytest.raises(lab.FaceLabError) as e:
        asyncio.run(lab.compare(PHOTO, PHOTO, transport=_transport(200, _ok())))
    assert e.value.code == "FACE_ENGINE_NOT_CONFIGURED"


def test_the_engine_adapter_maps_the_score_and_names_itself() -> None:
    got = lab.SidecarFaceEngine(_transport(200, _ok())).match(PHOTO, PHOTO)
    assert got.score == 0.71 and got.engine_id == lab.ENGINE_ID and got.engine_version == lab.ENGINE_VERSION


def test_the_engine_adapter_never_invents_a_score() -> None:
    for transport in (_transport(422, {"error": "NO_FACE_PROBE"}), _transport(500, {}), _transport(200, {"score": 1.5}), _transport(200, {"score": True}), _transport(200, {})):
        with pytest.raises(EngineUnavailable):
            lab.SidecarFaceEngine(transport).match(PHOTO, PHOTO)


def test_there_is_no_liveness_so_a_match_alone_can_never_pass() -> None:
    with pytest.raises(EngineUnavailable, match="ENGINE_HAS_NO_LIVENESS"):
        lab.SidecarFaceEngine(_transport(200, _ok())).liveness(PHOTO)


def test_the_lab_capability_exists_and_reads_no_customer_data() -> None:
    cap = CAPABILITIES["regulatory.face_lab.run"]
    assert cap.customer_data is False and cap.two_person is False


def test_the_gate_status_lists_what_is_missing_and_approves_nothing() -> None:
    status = lab.gate_status()
    assert status["mode"] == "DISABLED" and status["approvable"] is False
    assert "DPIA_REFERENCE_MISSING" in status["problems"] and "EVALUATION_ID_MISSING" in status["problems"]


def test_the_operator_api_refuses_a_photograph_it_cannot_decode_or_that_is_too_large() -> None:
    from zid_errors import ZidError

    with pytest.raises(ZidError) as e:
        api._decode("not base64!")
    assert e.value.code == "IMAGE_UNREADABLE"
    with pytest.raises(ZidError) as e:
        api._decode(base64.b64encode(b"x" * (api.MAX_IMAGE + 1)).decode())
    assert e.value.code == "IMAGE_TOO_LARGE"
    assert api._decode(base64.b64encode(PHOTO).decode()) == PHOTO


def test_the_calibrate_request_takes_scores_only_and_refuses_anything_else() -> None:
    from pydantic import ValidationError

    ok = api.CalibrateIn(pairs=[{"score": 0.6, "same": True}, {"score": 0.7, "same": True}, {"score": 0.1, "same": False}, {"score": 0.0, "same": False}])
    assert ok.targets == [0.1, 0.01, 0.001]
    for bad in (
        {"pairs": [{"score": 0.6, "same": True, "name": "A. Person"}] * 4},  # nothing that names a person
        {"pairs": [{"score": 2.0, "same": True}] * 4},  # not a cosine
        {"pairs": [{"score": 0.5, "same": True}] * 3},  # too few to mean anything
    ):
        with pytest.raises(ValidationError):
            api.CalibrateIn(**bad)
