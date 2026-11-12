"""ZID-INT-002 section 09, document analysis (T19, the own-process half): signals for a person, never a verdict.

The rules: the check is INDETERMINATE whether signals are found or not (no signals does not mean genuine), nothing is repaired, and a type that is
not analysed says so instead of returning an empty list that looks clean.
"""

from __future__ import annotations

import struct

from zid_platform.modules.evidence.domain import analysis as an
from zid_platform.modules.evidence.domain import documents as docs

CLEAN_PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


def codes(signals) -> set[str]:
    return {s.code for s in signals}


def png(*extra_chunks: tuple[bytes, bytes], trailing: bytes = b"") -> bytes:
    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", 0)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", b"\x00" * 13)
        + b"".join(chunk(k, b) for k, b in extra_chunks)
        + chunk(b"IDAT", b"\x01\x02")
        + chunk(b"IEND", b"")
        + trailing
    )


def test_a_plain_pdf_has_no_signals_and_that_is_not_called_genuine() -> None:
    signals, read_as = an.analyse(CLEAN_PDF, "application/pdf")
    assert signals == [] and read_as == "pdf"
    r = docs.validate(
        CLEAN_PDF, declared_type="application/pdf", expected_sha256=None, checks=["analysis"]
    ).results[0]
    assert r.status is docs.Status.INDETERMINATE and r.reasons == ("NO_SIGNALS_FOUND",)
    assert "not thereby genuine" in r.detail["limitation"]


def test_every_pdf_signal_is_found_and_described() -> None:
    pdf = (
        CLEAN_PDF
        + b"3 0 obj\n<< /S /JavaScript /JS (app.alert(1)) >>\nendobj\n"
        + b"4 0 obj\n<< /Type /Catalog /OpenAction 3 0 R >>\nendobj\n5 0 obj\n<< /Type /EmbeddedFile >>\nendobj\n"
        + b"6 0 obj\n<< /S /Launch >>\nendobj\n7 0 obj\n<< /XFA 8 0 R >>\nendobj\n"
        + b"/Producer (Adobe Photoshop 25) /CreationDate (D:20240101000000) /ModDate (D:20260301000000)\n%%EOF\n"
    )
    found = {s.code: s for s in an.pdf_signals(pdf)}
    assert {
        "INCREMENTAL_UPDATES",
        "ACTIVE_CONTENT_JAVASCRIPT",
        "ACTIVE_CONTENT_LAUNCH_ACTION",
        "AUTOMATIC_ACTION_ON_OPEN",
        "EMBEDDED_FILES",
        "XFA_FORM",
        "EDITING_TOOL_NAMED",
        "MODIFIED_AFTER_CREATION",
    } <= set(found)
    assert (
        found["EDITING_TOOL_NAMED"].severity == "NOTICE"
        and "photoshop" in found["EDITING_TOOL_NAMED"].detail.lower()
    )
    assert all(s.meaning and s.detail for s in found.values())


def test_a_producer_that_is_not_an_editor_is_information_and_a_small_date_gap_is_not_a_signal() -> (
    None
):
    pdf = (
        CLEAN_PDF
        + b"/Producer (Acme Report Engine) /CreationDate (D:20260101120000) /ModDate (D:20260101130000)\n"
    )
    found = {s.code: s for s in an.pdf_signals(pdf)}
    assert found["PRODUCER_NAMED"].severity == "INFO" and "MODIFIED_AFTER_CREATION" not in found


def test_an_unreadable_date_is_not_compared_or_guessed() -> None:
    pdf = CLEAN_PDF + b"/CreationDate (not a date) /ModDate (D:20260301000000)\n"
    assert "MODIFIED_AFTER_CREATION" not in codes(an.pdf_signals(pdf))
    assert an._pdf_date("D:2026133") is None or an._pdf_date("D:20261399") is None


def test_an_encrypted_pdf_says_it_could_not_be_fully_examined() -> None:
    s = {x.code: x for x in an.pdf_signals(CLEAN_PDF + b"/Encrypt 9 0 R\n")}
    assert (
        s["ENCRYPTED"].severity == "INFO"
        and "could not be fully examined" in s["ENCRYPTED"].meaning
    )


def test_png_signals_data_after_the_end_and_an_editor_in_the_metadata() -> None:
    clean = an.image_signals(png())
    assert clean == []
    tampered = an.image_signals(
        png((b"tEXt", b"Software\x00Adobe Photoshop 25"), trailing=b"HIDDEN" * 5)
    )
    assert {"EDITING_TOOL_IN_METADATA", "DATA_AFTER_IMAGE_END"} <= codes(tampered)
    assert "EXIF_PRESENT" in codes(an.image_signals(png((b"eXIf", b"II*\x00"))))


def test_jpeg_trailing_data_and_exif_are_reported_but_a_normal_tail_is_not() -> None:
    body = b"\xff\xd8\xff\xe1\x00\x08Exif\x00\x00" + b"\x00" * 40 + b"\xff\xd9"
    assert codes(an.image_signals(body)) == {"EXIF_PRESENT"}
    assert "DATA_AFTER_IMAGE_END" in codes(an.image_signals(body + b"X" * 64))
    assert "DATA_AFTER_IMAGE_END" not in codes(an.image_signals(body + b"\n"))


def test_a_type_that_is_not_analysed_says_so_and_is_never_empty_and_clean_looking() -> None:
    signals, read_as = an.analyse(b"<xml/>", "application/xml")
    assert signals == [] and read_as == "not_analysed"
    r = docs._analysis(b"<xml/>", "application/xml")
    assert r.reasons == ("TYPE_NOT_ANALYSED",) and r.status is docs.Status.INDETERMINATE


def test_signals_never_change_the_status_whatever_is_found() -> None:
    nasty = CLEAN_PDF + b"/JavaScript /Launch /OpenAction /EmbeddedFile /Producer (GIMP)\n%%EOF\n"
    r = docs.validate(
        nasty, declared_type="application/pdf", expected_sha256=None, checks=["analysis"]
    ).results[0]
    assert r.status is docs.Status.INDETERMINATE, (
        "signals are for a person; the check never fails or passes a document"
    )
    assert len(r.reasons) >= 4


def test_analysis_is_one_of_the_checks_a_caller_may_ask_for() -> None:
    assert "analysis" in docs.CHECKS
