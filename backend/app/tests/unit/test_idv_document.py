"""Document photograph handling: the measurement rules, the printed lines, and the sidecar client (CR-110). No imaging library and no network."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from zid_platform.modules.verification.application import idv_document as doc
from zid_platform.modules.verification.domain import idv_image as img
from zid_platform.modules.verification.domain.idv_decision import Optical
from zid_platform.modules.verification.domain.idv_evidence import (
    compare_digests,
    engine_signals,
    optical_state,
    printed_digests,
)
from zid_platform.modules.verification.infrastructure import image_inspect as client

GOOD = {
    "width": 1600,
    "height": 1100,
    "format": "JPEG",
    "sharpness": 140.0,
    "glare_fraction": 0.001,
    "dark_fraction": 0.1,
    "ela_hot_fraction": 0.004,
}
# ICAO 9303 part 4 specimen lines
MRZ = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159ZE184226B<<<<<10"


def test_measurements_become_quality_checks_and_a_clean_screen() -> None:
    m = img.parse_metrics(GOOD)
    assert m is not None
    assert img.checks(m) == {
        "RESOLUTION": "PASS",
        "SHARPNESS": "PASS",
        "GLARE": "PASS",
        "EXPOSURE": "PASS",
        "ELA_ANOMALY": "PASS",
    }


def test_a_poor_capture_fails_quality_and_a_hot_screen_asks_for_a_person_but_is_never_fatal() -> (
    None
):
    m = img.parse_metrics(
        {
            **GOOD,
            "width": 400,
            "height": 300,
            "sharpness": 5.0,
            "glare_fraction": 0.2,
            "ela_hot_fraction": 0.2,
        }
    )
    assert m is not None
    out = img.checks(m)
    assert [k for k, v in out.items() if v == "FAIL"] == [
        "RESOLUTION",
        "SHARPNESS",
        "GLARE",
        "ELA_ANOMALY",
    ]
    state, why = optical_state(out, frozenset({"RESOLUTION"}))
    assert (
        state is Optical.REVIEW and "OPTICAL_ELA_ANOMALY_FAILED" in why
    )  # a person looks; the document is not rejected on a heuristic


def test_a_png_cannot_run_the_screen_and_says_so() -> None:
    m = img.parse_metrics({**GOOD, "format": "PNG", "ela_hot_fraction": None})
    assert m is not None and img.checks(m)["ELA_ANOMALY"] == "INCONCLUSIVE"


def test_no_template_or_security_feature_result_is_ever_produced() -> None:
    m = img.parse_metrics(GOOD)
    assert m is not None
    assert set(img.checks(m)) == set(img.PRODUCED)
    assert not {"TEMPLATE", "TAMPER", "PHOTO_SUBSTITUTION"} & set(img.PRODUCED)
    # so a market that requires one gets "not run", never a pass
    assert optical_state(img.checks(m), frozenset({"TEMPLATE"}))[0] is Optical.UNKNOWN


def test_an_unreadable_measurement_is_refused() -> None:
    for bad in (
        {},
        {**GOOD, "width": 0},
        {**GOOD, "sharpness": float("nan")},
        {**GOOD, "ela_hot_fraction": 2},
        {**GOOD, "glare_fraction": -1},
    ):
        assert img.parse_metrics(bad) is None


def test_the_printed_lines_are_found_parsed_and_their_check_digits_judged() -> None:
    lines = img.mrz_lines("Some page text\n" + MRZ.replace("\n", "\n ") + "\nfooter")
    assert len(lines) == 2
    fields, ok = doc.printed_fields(lines)
    assert fields is not None and ok is True
    assert fields["surname"] == "ERIKSSON" and fields["document_number"] == "L898902C3"
    broken = [lines[0], lines[1].replace("L898902C36", "L898902C37")]
    assert doc.printed_fields(broken)[1] is False
    assert doc.printed_fields([]) == (None, None) and doc.printed_fields(["NOT<AN<MRZ"]) == (
        None,
        None,
    )


def test_digests_compare_without_keeping_the_data() -> None:
    printed = {
        "surname": "Eriksson",
        "given_names": "Anna Maria",
        "document_number": "L898902C3",
        "birth_date": "1974-08-12",
    }
    d = printed_digests(printed, "salt1")
    assert "Eriksson" not in json.dumps(d) and "ERIKSSON" not in json.dumps(d)
    chip = {
        "surname": "ERIKSSON",
        "given_names": "ANNA<MARIA",
        "document_number": "L898902C3",
        "birth_date": "1974-08-12",
    }
    assert compare_digests(d, chip, "salt1") == ("MATCH", ())
    assert compare_digests(d, {**chip, "birth_date": "1974-08-13"}, "salt1") == (
        "MISMATCH",
        ("birth_date",),
    )
    assert compare_digests(d, chip, "another salt")[0] == "MISMATCH"  # the salt is per case
    assert compare_digests({}, chip, "salt1") == ("UNKNOWN", ())


def test_engine_scores_become_states_and_a_failed_presentation_is_never_matched() -> None:
    assert engine_signals(0.9, 0.9, liveness_min=0.7, face_min=0.6) == ("PASS", "MATCH")
    assert engine_signals(0.9, 0.3, liveness_min=0.7, face_min=0.6) == ("PASS", "NON_MATCH")
    assert engine_signals(0.2, 0.99, liveness_min=0.7, face_min=0.6) == ("FAIL", "NOT_RUN")
    assert engine_signals(None, 0.99, liveness_min=0.7, face_min=0.6) == ("INCONCLUSIVE", "NOT_RUN")
    assert engine_signals(0.9, None, liveness_min=0.7, face_min=0.6) == ("PASS", "INCONCLUSIVE")


def _transport(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_the_sidecar_client_reads_a_document_and_never_invents_a_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def answer(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/inspect":
            return httpx.Response(200, json=GOOD)
        return httpx.Response(
            200,
            json={
                "regions": [{"name": "mrz", "text": "page\n" + MRZ, "confidence": 80}],
                "engine": "tesseract 5",
            },
        )

    monkeypatch.setenv("ZID_OCR_URL", "http://ocr.test")
    got = asyncio.run(doc.read_document(b"x", transport=_transport(answer)))
    assert (
        got.checks["MRZ_READABLE"] == "PASS"
        and got.checks["MRZ_CHECK_DIGITS"] == "PASS"
        and got.printed is not None
    )

    def unreadable_mrz(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/inspect":
            return httpx.Response(200, json=GOOD)
        return httpx.Response(
            200, json={"regions": [{"name": "mrz", "text": "nothing here", "confidence": 10}]}
        )

    got = asyncio.run(doc.read_document(b"x", transport=_transport(unreadable_mrz)))
    assert got.printed is None and got.checks["MRZ_READABLE"] == "INCONCLUSIVE"

    def ocr_down(request: httpx.Request) -> httpx.Response:
        return (
            httpx.Response(200, json=GOOD)
            if request.url.path == "/inspect"
            else httpx.Response(500)
        )

    got = asyncio.run(doc.read_document(b"x", transport=_transport(ocr_down)))
    assert (
        got.printed is None and "MRZ_READABLE" not in got.checks and "MRZ_NOT_READ" in got.reasons
    )

    for bad in (
        httpx.Response(500),
        httpx.Response(200, json={"width": "?"}),
        httpx.Response(422, json={"error": "IMAGE_NOT_READABLE"}),
    ):
        with pytest.raises(client.ImagingUnavailable):
            asyncio.run(doc.read_document(b"x", transport=_transport(lambda r, b=bad: b)))


def test_with_no_sidecar_configured_nothing_is_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ZID_OCR_URL", raising=False)
    with pytest.raises(client.ImagingUnavailable, match="IMAGING_NOT_CONFIGURED"):
        asyncio.run(doc.read_document(b"x"))
    monkeypatch.setenv("ZID_OCR_URL", "file:///etc/passwd")
    assert client.configured_url() is None  # only an http(s) address with a host is accepted
