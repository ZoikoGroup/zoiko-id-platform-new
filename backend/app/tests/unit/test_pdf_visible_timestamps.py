"""PAdES-LTA, visible document timestamps (the DTV-04 follow-up).

A document timestamp may be drawn on a page. That is the one shape where something added after signing could cover content, so it is accepted only
when it is small (at most a quarter of the page), when its appearance is a plain Form XObject stream (no scripts, no actions, no new resources), and
when the whole update is otherwise exactly the invisible timestamp update. The caller is told a visible stamp is present. A visible field with no
appearance, a huge one, one with a hostile appearance or any other extra object is refused, and the validator keeps reporting content added after
signing.
"""

from __future__ import annotations

import pdf_signing
import pytest
from pdf_signing import pki
from test_pdf_doc_timestamps import PAGE_HEAD, T1, TAIL, findings, signed, stamp, stamps
from tsa_authority import authority
from zid_platform.modules.evidence.application import cms

BIG = b"[0 0 612 792]"
DRAW = b"0 0 1 rg 0 0 80 30 re f"


@pytest.fixture(autouse=True)
def _big_page(monkeypatch) -> None:
    monkeypatch.setattr(
        pdf_signing, "pdf_template", lambda: (PAGE_HEAD.replace(b"[0 0 10 10]", BIG), TAIL)
    )


def visible(pdf: bytes, tsa, **over) -> bytes:
    args = {"visible": True, "appearance": DRAW, "mediabox": BIG}
    args.update(over)
    return stamp(pdf, tsa, T1, **args)


def test_a_small_visible_stamp_with_a_plain_appearance_is_accepted_and_reported() -> None:
    p, tsa = pki(), authority()
    pdf = visible(signed(p), tsa)
    (f,) = findings(pdf, p, tsa)
    assert f.covers_whole_file and "CONTENT_ADDED_AFTER_SIGNING" not in f.problems
    assert f.visible_timestamp_stamps == 1
    assert f.public()["visible_timestamp_stamps"] == 1
    (s,) = stamps(pdf, tsa)
    assert s["state"] == "VALID" and s["problems"] == []


def test_an_invisible_stamp_reports_no_visible_stamp() -> None:
    p, tsa = pki(), authority()
    (f,) = findings(stamp(signed(p), tsa, T1, mediabox=BIG), p, tsa)
    assert f.covers_whole_file and f.visible_timestamp_stamps == 0


def test_a_visible_stamp_with_no_appearance_is_refused() -> None:
    p, tsa = pki(), authority()
    (f,) = findings(visible(signed(p), tsa, appearance=None), p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems


def test_a_stamp_that_could_cover_much_of_the_page_is_refused() -> None:
    p, tsa = pki(), authority()
    big = visible(signed(p), tsa, rect=b"[0 0 612 500]")  # most of the page
    (f,) = findings(big, p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems
    just_over = visible(signed(p), tsa, rect=b"[0 0 306 400]")  # more than a quarter
    assert "CONTENT_ADDED_AFTER_SIGNING" in findings(just_over, p, tsa)[0].problems
    ok = visible(signed(p), tsa, rect=b"[0 0 300 400]")  # a little less than a quarter
    assert findings(ok, p, tsa)[0].visible_timestamp_stamps == 1


@pytest.mark.parametrize(
    "rect", [b"[10 10 10 40]", b"[10 10 90 10]", b"[10 10]", b"[a b c d]"], ids=lambda r: r.decode()
)
def test_a_degenerate_or_unreadable_rectangle_is_refused(rect) -> None:
    p, tsa = pki(), authority()
    (f,) = findings(visible(signed(p), tsa, rect=rect), p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems


@pytest.mark.parametrize(
    "form_dict",
    [
        b"/Type /XObject /Subtype /Form /BBox [0 0 80 30] /OC 99 0 R",
        b"/Type /XObject /Subtype /Form /BBox [0 0 80 30] /AA << /O << /S /JavaScript /JS (x) >> >>",
        b"/Type /XObject /Subtype /Image /BBox [0 0 80 30]",
        b"/Type /XObject /Subtype /Form",  # no bounding box
        b"/Type /XObject /Subtype /Form /BBox [0 0 80 30] /Resources << /Font << /F1 77 0 R >> >>",
    ],
    ids=["optional-content", "javascript", "not-a-form", "no-bbox", "font-not-in-signed-file"],
)
def test_an_appearance_that_is_more_than_plain_drawing_is_refused(form_dict) -> None:
    p, tsa = pki(), authority()
    (f,) = findings(visible(signed(p), tsa, form_dict=form_dict), p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems


def test_an_action_on_the_stamps_field_is_refused() -> None:
    p, tsa = pki(), authority()
    pdf = visible(signed(p), tsa, widget_extra=b" /A << /S /JavaScript /JS (app.alert(1)) >>")
    (f,) = findings(pdf, p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems


def test_another_object_beside_a_visible_stamp_still_makes_the_update_a_change() -> None:
    p, tsa = pki(), authority()
    pdf = visible(signed(p), tsa, extra=b"9 0 obj\n<< /Type /Annot /Subtype /FreeText >>\nendobj\n")
    (f,) = findings(pdf, p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems


def test_a_visible_stamp_over_other_bytes_is_still_an_invalid_timestamp() -> None:
    p, tsa = pki(), authority()
    pdf = visible(signed(p), tsa, wrong_data=True)
    (s,) = stamps(pdf, tsa)
    assert s["state"] == "INVALID"


def test_the_validator_reports_a_visible_stamp_in_the_signature_detail() -> None:
    from zid_platform.modules.evidence.application import signatures as sg
    from zid_platform.modules.evidence.application import trust as tr

    p, tsa = pki(), authority()
    f = findings(visible(signed(p), tsa), p, tsa)
    r = sg.signature_result(f, tr.TrustConfig(anchors=(p.root.cert,), tsa_roots=(tsa.root,)))
    assert r.detail["signatures"][0]["visible_timestamp_stamps"] == 1
    assert cms.Chain.TRUSTED.value == r.detail["signatures"][0]["chain"]
