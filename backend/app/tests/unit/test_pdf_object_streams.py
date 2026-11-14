"""PAdES-LTA, object streams and cross-reference streams (the DTV-04 follow-up).

A modern writer puts the catalog, pages and form in an object stream and indexes them with a cross-reference stream. The reader now opens an object
stream and sees the objects inside it, and ignores a cross-reference stream, so such an update is judged by exactly the same rules as one with plain
objects. The rule that matters is the failure case: an object stream that cannot be read in full (bad header, short data, wrong counts, a member that
does not parse) is NOT expanded and stays in the result as an object nothing may add, so hidden content can never ride in unread. The same goes for
any object that is defined but unreadable.
"""

from __future__ import annotations

import pdf_signing
import pytest
from pdf_signing import pki
from test_pdf_doc_timestamps import (
    PAGE_HEAD,
    T1,
    TAIL,
    findings,
    pack_object_stream,
    signed,
    stamp,
    stamps,
)
from tsa_authority import authority
from zid_platform.modules.evidence.application import cms, pdfobjects


@pytest.fixture(autouse=True)
def _real_page(monkeypatch) -> None:
    monkeypatch.setattr(pdf_signing, "pdf_template", lambda: (PAGE_HEAD, TAIL))


def packed(pdf: bytes, tsa, **objstm) -> bytes:
    return stamp(pdf, tsa, T1, objstm=objstm)


def refused(pdf: bytes, p, tsa) -> bool:
    (f,) = findings(pdf, p, tsa)
    return "CONTENT_ADDED_AFTER_SIGNING" in f.problems and not f.covers_whole_file


# ------------------------------------------------------------------------------------------------------------- the reader


def test_the_reader_sees_objects_inside_an_object_stream_and_ignores_the_xref_stream() -> None:
    members = [(3, b"<< /Type /Page /Parent 4 0 R >>"), (7, b"<< /Fields [] >>")]
    objs = pdfobjects.parse_objects(pack_object_stream(members, number=40))
    assert set(objs) == {3, 7}, "the container and the xref stream are not objects of their own"
    assert objs[3].value["Type"] == pdfobjects.Name("Page")


def test_an_unfiltered_object_stream_is_read_too() -> None:
    members = [(3, b"<< /Type /Page >>")]
    assert 3 in pdfobjects.parse_objects(pack_object_stream(members, flate=False))


@pytest.mark.parametrize(
    "bad",
    [
        {"truncate": 6},  # the last object is cut short
        {"n_override": 5},  # says more objects than it holds
        {"n_override": 1},  # says fewer than it holds
        {"first_override": 10_000},  # the first object starts beyond the data
        {"first_override": 3},  # the header is cut
    ],
    ids=["truncated", "n-too-big", "n-too-small", "first-beyond-data", "first-inside-header"],
)
def test_an_object_stream_that_cannot_be_read_in_full_stays_as_an_unknown_object(bad) -> None:
    members = [(3, b"<< /Type /Page >>"), (5, b"<< /Fields [] >>")]
    objs = pdfobjects.parse_objects(pack_object_stream(members, number=40, **bad))
    assert 40 in objs, (
        "the container stays, so an update that holds it is not 'only validation data'"
    )
    assert 3 not in objs and 5 not in objs


def test_an_object_defined_but_unreadable_still_counts_as_an_object() -> None:
    objs = pdfobjects.parse_objects(b"9 0 obj\n<< /Broken ( >>\nendobj\n")
    assert 9 in objs and objs[9].value is None


# ------------------------------------------------------------------------------------------------------------- whole updates


def test_a_timestamp_update_with_its_structure_in_an_object_stream_is_accepted() -> None:
    p, tsa = pki(), authority()
    pdf = packed(signed(p), tsa)
    (f,) = findings(pdf, p, tsa)
    assert f.covers_whole_file and "CONTENT_ADDED_AFTER_SIGNING" not in f.problems
    (s,) = stamps(pdf, tsa)
    assert s["state"] == "VALID" and s["problems"] == []


def test_the_same_with_an_unfiltered_object_stream_is_accepted() -> None:
    p, tsa = pki(), authority()
    (f,) = findings(packed(signed(p), tsa, flate=False), p, tsa)
    assert f.covers_whole_file


def test_a_cross_reference_stream_alone_adds_nothing_the_update_is_judged_by_its_objects() -> None:
    p, tsa = pki(), authority()
    with_xref = findings(packed(signed(p), tsa, xref=True), p, tsa)[0]
    without = findings(packed(signed(p), tsa, xref=False), p, tsa)[0]
    assert with_xref.covers_whole_file and without.covers_whole_file


def test_a_page_changed_inside_an_object_stream_is_still_a_change() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1, objstm={}, mediabox=b"[0 0 10 700]")
    assert refused(pdf, p, tsa)


def test_an_extra_object_hidden_in_a_second_object_stream_is_a_change() -> None:
    p, tsa = pki(), authority()
    hidden = pack_object_stream(
        [(9, b"<< /Type /Annot /Subtype /FreeText /Rect [0 0 10 10] >>")], number=50
    )
    pdf = stamp(signed(p), tsa, T1, objstm={}, extra=hidden)
    assert refused(pdf, p, tsa)


@pytest.mark.parametrize("bad", [{"truncate": 4}, {"n_override": 9}, {"first_override": 5}])
def test_a_timestamp_update_whose_object_stream_is_damaged_is_refused_not_guessed(bad) -> None:
    p, tsa = pki(), authority()
    assert refused(packed(signed(p), tsa, **bad), p, tsa)


def test_an_unreadable_object_beside_a_timestamp_makes_the_update_a_change() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1, extra=b"9 0 obj\n<< /Contents ( >>\nendobj\n")
    assert refused(pdf, p, tsa)


def test_the_plain_update_is_unchanged_by_all_of_this() -> None:
    p, tsa = pki(), authority()
    (f,) = findings(stamp(signed(p), tsa, T1), p, tsa)
    assert f.covers_whole_file and f.time_source == "DOCUMENT_TIMESTAMP"
    assert cms.archive_state(stamps(stamp(signed(p), tsa, T1), tsa)) == "ARCHIVE_VALID"
