"""T41: a PDF that can run something is found before the scanner or any validator reads it, including the disguises a polyglot uses."""

from __future__ import annotations

import hashlib

import pytest
from zid_platform.modules.evidence.domain import intake
from zid_platform.modules.evidence.domain.active_content import active_content, looks_like_pdf

CLEAN = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def with_body(extra: bytes) -> bytes:
    return CLEAN.replace(b"<< /Type /Catalog >>", b"<< /Type /Catalog " + extra + b" >>")


@pytest.mark.parametrize(
    ("extra", "kind"),
    [
        (b"/OpenAction << /S /JavaScript /JS (app.alert(1)) >>", "JAVASCRIPT"),
        (b"/AA << /O << /S /Launch >> >>", "LAUNCH_ACTION"),
        (b"/OpenAction 3 0 R", "ACTION_ON_OPEN"),
        (b"/Names << /EmbeddedFiles << >> >> /EmbeddedFile", "EMBEDDED_FILE"),
        (b"/AcroForm << /XFA 5 0 R >>", "XFA_FORM"),
        (b"/Subtype /RichMedia", "RICH_MEDIA"),
        (b"/S /SubmitForm", "FORM_SUBMISSION"),
    ],
)
def test_each_kind_of_active_content_is_named(extra: bytes, kind: str) -> None:
    assert kind in active_content(with_body(extra))


def test_a_clean_pdf_and_a_file_that_is_not_a_pdf_carry_nothing() -> None:
    assert active_content(CLEAN) == ()
    assert (
        active_content(b"\x89PNG\r\n\x1a\n/JavaScript") == ()
    )  # not a PDF: the PDF rules do not apply
    assert not looks_like_pdf(b"hello")
    assert active_content(with_body(b"/Author (A/JSON note)")) == ()  # a name must stand alone


def test_an_escaped_name_and_a_late_header_do_not_hide_it() -> None:
    assert "JAVASCRIPT" in active_content(with_body(b"/J#61vaScript (x)"))
    polyglot = b"GIF89a....\n" + with_body(b"/OpenAction << /S /JavaScript >>")
    assert looks_like_pdf(polyglot) and "JAVASCRIPT" in active_content(polyglot)


def test_the_upload_intake_refuses_it_with_its_kinds_before_the_type_is_weighed() -> None:
    body = with_body(b"/OpenAction << /S /JavaScript >>")
    with pytest.raises(intake.UploadError) as caught:
        intake.check_content(
            body,
            declared_type="application/pdf",
            size_bytes=len(body),
            sha256=hashlib.sha256(body).hexdigest(),
        )
    assert caught.value.code == "ACTIVE_CONTENT" and "JAVASCRIPT" in str(caught.value)
    assert (
        intake.check_content(
            CLEAN,
            declared_type="application/pdf",
            size_bytes=len(CLEAN),
            sha256=hashlib.sha256(CLEAN).hexdigest(),
        )
        == "application/pdf"
    )
