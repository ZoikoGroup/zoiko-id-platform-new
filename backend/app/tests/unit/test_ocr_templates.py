"""DTV-16: layout templates and the OCR sidecar client. A template bounds where to read; it proves nothing about the document."""

from __future__ import annotations

import base64
import json

import httpx
import pytest
from zid_platform.modules.evidence.domain import templates as tpl
from zid_platform.modules.evidence.infrastructure import ocr_client

GOOD = {
    "templates": [
        {
            "id": "t",
            "version": 1,
            "status": "SAMPLE",
            "lang": "eng",
            "description": "d",
            "min_confidence": 60,
            "regions": [
                {
                    "name": "surname",
                    "x": 0,
                    "y": 0,
                    "w": 1,
                    "h": 0.5,
                    "pattern": r"SURNAME: (?P<value>[A-Z]+)",
                },
                {
                    "name": "number",
                    "x": 0,
                    "y": 0.5,
                    "w": 1,
                    "h": 0.5,
                    "pattern": r"[A-Z0-9]{6,12}",
                },
            ],
        }
    ]
}


def read(**kw):
    t = tpl.parse(GOOD)["t"]
    ocr = [
        {"name": "surname", "text": "SURNAME: ERIKSSON", "confidence": 93.0},
        {"name": "number", "text": "L898902C3", "confidence": 90.0},
    ]
    for k, v in kw.items():
        for r in ocr:
            if r["name"] == k:
                r.update(v)
    return t, tpl.apply(t, ocr)


def test_the_shipped_templates_load_and_every_one_is_a_sample() -> None:
    shipped = ocr_client.templates()
    assert shipped and all(t.status == "SAMPLE" for t in shipped.values())
    assert all(t.approved_by is None for t in shipped.values())


def test_a_matching_region_keeps_the_value_group_and_is_usable() -> None:
    _t, fields = read()
    assert [(f.name, f.value, f.usable) for f in fields] == [
        ("surname", "ERIKSSON", True),
        ("number", "L898902C3", True),
    ]
    assert all(f.reasons == () for f in fields)


def test_a_field_that_does_not_match_is_reported_not_repaired_or_dropped() -> None:
    _t, fields = read(number={"text": "L898902C?"})
    f = {x.name: x for x in fields}["number"]
    assert f.matched is False and f.usable is False and f.value is None
    assert f.text == "L898902C?", "what was read is shown as read"
    assert f.reasons == ("PATTERN_NOT_MATCHED",)


def test_low_confidence_is_flagged_even_when_the_pattern_matches() -> None:
    _t, fields = read(surname={"confidence": 41.0})
    f = {x.name: x for x in fields}["surname"]
    assert f.matched is True and f.usable is False and f.reasons == ("LOW_CONFIDENCE",)


def test_a_region_the_reader_did_not_return_or_returned_empty_is_a_named_gap() -> None:
    t = tpl.parse(GOOD)["t"]
    fields = tpl.apply(t, [{"name": "surname", "text": "", "confidence": 0.0}])
    by = {f.name: f for f in fields}
    assert by["surname"].reasons[0] == "REGION_EMPTY" and by["number"].reasons == (
        "REGION_NOT_READ",
    )
    assert not any(f.usable for f in fields)


def test_whitespace_in_what_was_read_does_not_defeat_a_pattern() -> None:
    t = tpl.parse(GOOD)["t"]
    f = tpl.apply(
        t,
        [
            {"name": "surname", "text": "SURNAME:   ERIKSSON \n", "confidence": 99},
            {"name": "number", "text": "X", "confidence": 99},
        ],
    )[0]
    assert f.value == "ERIKSSON"


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda t: t["regions"][0].update(x=0.9, w=0.5), "REGION_OUT_OF_BOUNDS"),
        (lambda t: t.update(regions=[]), "REGIONS_INVALID"),
        (lambda t: t["regions"][1].update(name="surname"), "REGION_NAME_DUPLICATE"),
        (lambda t: t.update(status="TRUSTED"), "STATUS_UNKNOWN"),
        (lambda t: t.update(lang="xx"), "LANGUAGE_NOT_SUPPORTED"),
        (lambda t: t.update(status="APPROVED"), "APPROVAL_NOT_RECORDED"),
        (lambda t: t["regions"][0].update(pattern="("), "TEMPLATE_INVALID"),
        (lambda t: t.pop("description"), "TEMPLATE_INVALID"),
    ],
)
def test_a_template_that_is_wrong_does_not_load(mutate, code: str) -> None:
    import copy

    doc = copy.deepcopy(GOOD)
    mutate(doc["templates"][0])
    with pytest.raises(tpl.TemplateError) as e:
        tpl.parse(doc)
    assert e.value.code == code


def test_a_template_is_approved_only_by_someone_on_a_date_and_ids_are_unique() -> None:
    import copy

    doc = copy.deepcopy(GOOD)
    doc["templates"][0].update(status="APPROVED", approved_by="Reviewer", approved_on="2026-12-01")
    assert tpl.parse(doc)["t"].status == "APPROVED"
    doc["templates"].append(copy.deepcopy(doc["templates"][0]))
    with pytest.raises(tpl.TemplateError) as e:
        tpl.parse(doc)
    assert e.value.code == "TEMPLATE_DUPLICATE"


# --------------------------------------------------------------------------------------------------------------- the client


def client_for(handler):
    return httpx.MockTransport(handler)


@pytest.mark.anyio
async def test_the_client_sends_the_image_and_regions_and_returns_what_was_read() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "regions": [{"name": "a", "text": "HI", "confidence": 90.0}],
                "engine": "tesseract 5",
            },
        )

    out, engine = await ocr_client.read(
        "http://ocr",
        b"\x89PNG",
        [{"name": "a", "x": 0, "y": 0, "w": 1, "h": 1}],
        "eng",
        transport=client_for(handler),
    )
    assert out == [{"name": "a", "text": "HI", "confidence": 90.0}] and engine == "tesseract 5"
    assert base64.b64decode(seen["image_base64"]) == b"\x89PNG" and seen["lang"] == "eng"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "code"),
    [
        (422, "OCR_INPUT_REFUSED"),
        (413, "OCR_INPUT_REFUSED"),
        (504, "OCR_TIMEOUT"),
        (500, "OCR_UNAVAILABLE"),
        (404, "OCR_UNAVAILABLE"),
    ],
)
async def test_the_clients_failures_are_named(status: int, code: str) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "X"})

    with pytest.raises(ocr_client.OcrError) as e:
        await ocr_client.read("http://ocr", b"x", [], "eng", transport=client_for(handler))
    assert e.value.code == code


@pytest.mark.anyio
async def test_an_unreachable_service_and_a_garbled_answer_are_unavailable_not_a_crash() -> None:
    def down(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(ocr_client.OcrError) as e:
        await ocr_client.read("http://ocr", b"x", [], "eng", transport=client_for(down))
    assert e.value.code == "OCR_UNAVAILABLE"
    with pytest.raises(ocr_client.OcrError) as e2:
        await ocr_client.read(
            "http://ocr",
            b"x",
            [],
            "eng",
            transport=client_for(lambda _r: httpx.Response(200, json={"nope": 1})),
        )
    assert e2.value.code == "OCR_UNAVAILABLE"


def test_only_an_http_address_with_a_host_is_accepted_as_the_service(monkeypatch) -> None:
    for bad in ("", "ocr:8090", "file:///etc/passwd", "ftp://x", "http://"):
        monkeypatch.setenv("ZID_OCR_URL", bad)
        assert ocr_client.configured_url() is None
    monkeypatch.setenv("ZID_OCR_URL", "http://ocr:8090/")
    assert ocr_client.configured_url() == "http://ocr:8090"
    monkeypatch.delenv("ZID_OCR_URL")
    assert ocr_client.configured_url() is None
