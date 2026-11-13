"""PAdES-LTA: document timestamps appended after signing, and the archive chain they form (CR-073 P2-1, the last open piece).

A document timestamp is not a signature: it time-stamps the whole file so far. A file may gain one, and several in a chain, each covering everything
before it. The rule being proved is the narrow one: an update that is ONLY a timestamp dictionary, one invisible signature field for it, the form
entry gaining that field and the page's annotation list gaining it is accepted; anything else beside it makes the whole update a change.
"""

from __future__ import annotations

from datetime import timedelta

import pdf_signing
import pytest
from pdf_signing import NOW, pki, sign_pdf, token_from_response
from tsa_authority import authority, response
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

HEX = pdf_signing.CONTENTS_BYTES * 2 + 2
T0 = NOW - timedelta(days=20)
T1 = NOW - timedelta(days=10)
T2 = NOW - timedelta(days=2)

PAGE_HEAD = (
    b"%PDF-1.7\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 4 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    b"3 0 obj\n<< /Type /Page /Parent 4 0 R /MediaBox [0 0 10 10] >>\nendobj\n"
    b"2 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite /SubFilter /adbe.pkcs7.detached "
    b"/ByteRange [0000000000 0000000000 0000000000 0000000000] /Contents "
)
TAIL = b" /M (D:20260101000000Z) >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"


@pytest.fixture(autouse=True)
def _real_page(monkeypatch) -> None:
    monkeypatch.setattr(pdf_signing, "pdf_template", lambda: (PAGE_HEAD, TAIL))


def pack_object_stream(
    members: list[tuple[int, bytes]],
    *,
    number: int = 40,
    xref: bool = True,
    flate: bool = True,
    n_override: int | None = None,
    first_override: int | None = None,
    truncate: int = 0,
) -> bytes:
    """The given objects as one object stream (PDF 1.5), and a cross-reference stream beside it, as an incremental update from a modern writer has."""
    import zlib

    values = b"".join(v + b"\n" for _, v in members)
    offsets, at = [], 0
    for _, v in members:
        offsets.append(at)
        at += len(v) + 1
    header = (
        " ".join(f"{n} {o}" for (n, _), o in zip(members, offsets, strict=True)).encode() + b"\n"
    )
    data = (header + values)[: len(header + values) - truncate]
    first = len(header) if first_override is None else first_override
    count = len(members) if n_override is None else n_override
    raw = zlib.compress(data) if flate else data
    flt = b" /Filter /FlateDecode" if flate else b""
    out = (
        f"{number} 0 obj\n<< /Type /ObjStm /N {count} /First {first}".encode()
        + flt
        + f" /Length {len(raw)} >>\nstream\n".encode()
        + raw
        + b"\nendstream\nendobj\n"
    )
    if xref:
        out += (
            f"{number + 1} 0 obj\n<< /Type /XRef /W [1 2 1] /Size {number + 2} /Root 1 0 R /Length 4 >>\nstream\n".encode()
            + b"\x01\x00\x10\x00"
            + b"\nendstream\nendobj\n"
        )
    return out


def stamp(
    pdf: bytes,
    tsa,
    gen_time,
    *,
    number: int = 10,
    earlier: tuple[int, ...] = (),
    catalog: bytes | None = b"<< /Type /Catalog /Pages 4 0 R /AcroForm 5 0 R >>",
    visible: bool = False,
    extra: bytes = b"",
    link_widget: bool = True,
    wrong_data: bool = False,
    start_at: int = 0,
    appearance: bytes | None = None,
    rect: bytes | None = None,
    mediabox: bytes = b"[0 0 10 10]",
    form_dict: bytes = b"/Type /XObject /Subtype /Form /BBox [0 0 80 30]",
    widget_extra: bytes = b"",
    objstm: dict | None = None,
) -> bytes:
    """Append one document timestamp update: the timestamp, its field, the page's annotations, the form and (the first time) the catalog."""
    widget = number + 1
    widgets = [*earlier, widget]
    refs = " ".join(f"{w} 0 R" for w in widgets)
    rect = rect or (b"[10 10 90 40]" if visible else b"[0 0 0 0]")
    ap_no = widget + 1
    ap_ref = f" /AP << /N {ap_no} 0 R >>".encode() if appearance is not None else b""
    ap_obj = (
        f"{ap_no} 0 obj\n<< ".encode()
        + form_dict
        + f" /Length {len(appearance)} >>\nstream\n".encode()
        + appearance
        + b"\nendstream\nendobj\n"
        if appearance is not None
        else b""
    )
    head = (
        b"\n"
        + f"{number} 0 obj\n<< /Type /DocTimeStamp /Filter /Adobe.PPKLite /SubFilter /ETSI.RFC3161 ".encode()
        + b"/ByteRange [0000000000 0000000000 0000000000 0000000000] /Contents "
    )
    link = f" /V {number} 0 R".encode() if link_widget else b""
    page_val = (
        b"<< /Type /Page /Parent 4 0 R /MediaBox " + mediabox + f" /Annots [{refs}] >>".encode()
    )
    form_val = f"<< /Fields [{refs}] /SigFlags 3 >>".encode()
    members = [(3, page_val), (5, form_val), *([(1, catalog)] if catalog else [])]
    if objstm is None:
        structure = b"".join(b"%d 0 obj\n" % n + v + b"\nendobj\n" for n, v in members)
    else:
        structure = pack_object_stream(members, **objstm)
    body = (
        b" >>\nendobj\n"
        + f"{widget} 0 obj\n<< /Type /Annot /Subtype /Widget /FT /Sig /T (DocTS{number}) /Rect ".encode()
        + rect
        + b" /F 132 /P 3 0 R"
        + link
        + ap_ref
        + widget_extra
        + b" >>\nendobj\n"
        + ap_obj
        + structure
        + extra
        + b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    a = len(pdf) + len(head)
    rng = f"[{start_at:010d} {a:010d} {a + HEX:010d} {len(body):010d}]".encode()
    head = head.replace(b"[0000000000 0000000000 0000000000 0000000000]", rng)
    covered = pdf + head + body  # what the stamp covers: everything except its own placeholder
    imprint_of = covered if not wrong_data else covered + b"x"
    token = token_from_response(
        response(tsa, hash_bytes(imprint_of, alg="sha-256"), nonce=None, gen_time=gen_time)
    )
    contents = b"<" + token.hex().encode().ljust(HEX - 2, b"0") + b">"
    return pdf + head + contents + body


def signed(p) -> bytes:
    return sign_pdf(p)


def findings(pdf: bytes, p, tsa=None):
    return cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,) if tsa else ())


def stamps(pdf: bytes, tsa=None):
    return cms.inspect_document_timestamps(pdf, tsa_roots=(tsa.root,) if tsa else ())


# ------------------------------------------------------------------------------------------------------------- accepted


def test_a_document_timestamp_does_not_make_the_signature_look_changed_or_broken() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    f = findings(pdf, p, tsa)
    assert len(f) == 1, "the timestamp is not a signature"
    assert f[0].covers_whole_file and "CONTENT_ADDED_AFTER_SIGNING" not in f[0].problems
    assert f[0].cryptographically_valid and f[0].chain is cms.Chain.TRUSTED


def test_a_document_timestamp_is_verified_and_reported() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    (s,) = stamps(pdf, tsa)
    assert (
        s["state"] == "VALID"
        and s["problems"] == []
        and s["time"].startswith(T1.strftime("%Y-%m-%dT%H:%M"))
    )
    assert s["covers_bytes"] > len(signed(p)) and cms.archive_state([s]) == "ARCHIVE_VALID"


def test_a_chain_of_two_stamps_is_valid_in_order() -> None:
    p, tsa = pki(), authority()
    one = stamp(signed(p), tsa, T1)
    two = stamp(one, tsa, T2, number=12, earlier=(11,), catalog=None)
    a, b = stamps(two, tsa)
    assert a["state"] == b["state"] == "VALID" and b["covers_bytes"] > a["covers_bytes"]
    assert cms.archive_state([a, b]) == "ARCHIVE_VALID"
    f = findings(two, p, tsa)
    assert (
        len(f) == 1
        and f[0].covers_whole_file
        and "CONTENT_ADDED_AFTER_SIGNING" not in f[0].problems
    )


def test_validation_data_and_a_timestamp_may_arrive_together() -> None:
    from cryptography.hazmat.primitives.serialization import Encoding
    from test_pdf_ltv import update as dss_update

    p, tsa = pki(), authority()
    base = signed(p) + dss_update(
        certs=[p.signer.cert.public_bytes(Encoding.DER)],
        catalog=b"<< /Type /Catalog /Pages 4 0 R /DSS 20 0 R >>",
    )
    both = stamp(
        base, tsa, T1, catalog=b"<< /Type /Catalog /Pages 4 0 R /DSS 20 0 R /AcroForm 5 0 R >>"
    )
    f = findings(both, p, tsa)[0]
    assert f.covers_whole_file and "CONTENT_ADDED_AFTER_SIGNING" not in f.problems


def test_the_token_padding_in_contents_is_handled() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    slot = next(
        s
        for s, k in zip(cms.signature_slots(pdf), cms.slot_subfilters(pdf), strict=True)
        if k == cms.DOCUMENT_TIMESTAMP
    )
    assert len(slot[1]) == pdf_signing.CONTENTS_BYTES and len(cms._der_prefix(slot[1])) < len(
        slot[1]
    )


# ----------------------------------------------------------------------------------------------------- the timestamp itself


def test_without_a_timestamp_authority_root_nothing_is_verified_but_nothing_is_called_broken() -> (
    None
):
    p, tsa = pki(), authority()
    (s,) = stamps(stamp(signed(p), tsa, T1), None)
    assert s["state"] == "NO_TSA_ROOTS" and cms.archive_state([s]) == "ARCHIVE_UNVERIFIED"


def test_a_stamp_from_an_authority_nobody_configured_is_invalid() -> None:
    p, tsa, other = pki(), authority(), authority()
    (s,) = stamps(stamp(signed(p), tsa, T1), other)
    assert s["state"] == "INVALID" and cms.archive_state([s]) == "ARCHIVE_BROKEN"


def test_a_stamp_over_other_bytes_is_invalid() -> None:
    p, tsa = pki(), authority()
    (s,) = stamps(stamp(signed(p), tsa, T1, wrong_data=True), tsa)
    assert s["state"] == "INVALID" and cms.archive_state([s]) == "ARCHIVE_BROKEN"


def test_a_stamp_dated_before_the_one_it_follows_breaks_the_chain() -> None:
    p, tsa = pki(), authority()
    one = stamp(signed(p), tsa, T2)
    two = stamp(
        one, tsa, T0, number=12, earlier=(11,), catalog=None
    )  # claims to be older than the stamp it covers
    a, b = stamps(two, tsa)
    assert (
        "DATED_BEFORE_THE_EARLIER_STAMP" in b["problems"]
        and cms.archive_state([a, b]) == "ARCHIVE_BROKEN"
    )


def test_a_byte_range_that_does_not_start_at_zero_is_refused() -> None:
    p, tsa = pki(), authority()
    (s,) = stamps(stamp(signed(p), tsa, T1, start_at=5), tsa)
    assert s["problems"] == ["BYTE_RANGE_INVALID"] and cms.archive_state([s]) == "ARCHIVE_BROKEN"


def test_a_file_with_no_document_timestamp_has_no_archive() -> None:
    p = pki()
    assert stamps(signed(p)) == [] and cms.archive_state([]) == "NONE"


# --------------------------------------------------------------------------------------- the narrow shape, one break at a time


def refused(pdf: bytes, p, tsa) -> bool:
    f = findings(pdf, p, tsa)[0]
    return "CONTENT_ADDED_AFTER_SIGNING" in f.problems and not f.covers_whole_file


def test_a_visible_stamp_is_refused_because_it_needs_an_appearance_we_do_not_read() -> None:
    p, tsa = pki(), authority()
    assert refused(stamp(signed(p), tsa, T1, visible=True), p, tsa)


def test_any_other_object_beside_the_timestamp_makes_the_update_a_change() -> None:
    p, tsa = pki(), authority()
    page_content = b"40 0 obj\n<< /Length 5 >>\nstream\nhello\nendstream\nendobj\n"
    assert refused(stamp(signed(p), tsa, T1, extra=page_content), p, tsa)


def test_a_timestamp_with_no_field_or_a_field_that_is_not_its_own_is_refused() -> None:
    p, tsa = pki(), authority()
    assert refused(stamp(signed(p), tsa, T1, link_widget=False), p, tsa)


def test_a_catalog_that_changed_beside_the_form_entry_is_refused() -> None:
    p, tsa = pki(), authority()
    sneaky = b"<< /Type /Catalog /Pages 4 0 R /AcroForm 5 0 R /OpenAction 3 0 R >>"
    assert refused(stamp(signed(p), tsa, T1, catalog=sneaky), p, tsa)


def test_a_page_that_changed_beside_its_annotation_list_is_refused() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    tampered = pdf.replace(b"/MediaBox [0 0 10 10] /Annots", b"/MediaBox [0 0 99 99] /Annots")
    assert tampered != pdf
    # the stamp no longer covers these bytes either, which is what a validator must also notice
    assert refused(tampered, p, tsa) and stamps(tampered, tsa)[0]["state"] == "INVALID"


def test_a_form_that_gained_anything_besides_the_timestamp_field_is_refused() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    smuggled = pdf.replace(b"/Fields [11 0 R] /SigFlags 3", b"/Fields [11 0 R 99 0 R] /SigFlags 3")
    assert smuggled != pdf and refused(smuggled, p, tsa)


# --------------------------------------------------------------------------------------------------------- through validate


def test_the_document_validator_reports_the_timestamps_and_the_archive_state() -> None:
    p, tsa = pki(), authority()
    pdf = stamp(signed(p), tsa, T1)
    config = tr.TrustConfig(anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    v = docs.validate(
        pdf, declared_type="application/pdf", expected_sha256=None, checks=["signature"]
    )
    out = sg.apply(v, pdf, config).results[0]
    assert (
        out.detail["ltv_state"] == "ARCHIVE_VALID" and len(out.detail["document_timestamps"]) == 1
    )
    assert (
        out.status is docs.Status.VERIFIED_LIMITED
        and "DOCUMENT_TIMESTAMP_PROBLEM" not in out.reasons
    )
    broken = tr.TrustConfig(anchors=(p.root.cert,), tsa_roots=(authority().root,))
    bad = sg.apply(v, pdf, broken).results[0]
    assert (
        bad.detail["ltv_state"] == "ARCHIVE_BROKEN" and "DOCUMENT_TIMESTAMP_PROBLEM" in bad.reasons
    )


# ------------------------------------------------------------------------- a document timestamp bounds the signing time


def _cfg(p, tsa) -> tr.TrustConfig:
    return tr.TrustConfig(anchors=(p.root.cert,), tsa_roots=(tsa.root,))


def test_a_document_timestamp_over_the_signature_fixes_the_time_when_the_signature_has_no_timestamp() -> (
    None
):
    # the signer's certificate expired yesterday; the file was stamped while it was still valid
    p, tsa = (
        pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1)),
        authority(),
    )
    pdf = stamp(signed(p), tsa, T1)
    (f,) = cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "DOCUMENT_TIMESTAMP" and f.trusted_time is not None
    assert f.timestamp_token_present is False and f.chain is cms.Chain.TRUSTED
    r = sg.signature_result([f], _cfg(p, tsa))
    assert r.status is docs.Status.VERIFIED_LIMITED
    assert "SIGNING_TIME_BOUNDED_BY_DOCUMENT_TIMESTAMP" in r.reasons
    assert "SIGNING_TIME_TRUSTED_BY_TIMESTAMP" not in r.reasons


def test_the_same_file_without_the_document_timestamp_is_not_trusted() -> None:
    p, tsa = (
        pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1)),
        authority(),
    )
    (f,) = cms.inspect_pdf(signed(p), anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "NONE" and f.chain is cms.Chain.EXPIRED


def test_a_stamp_made_after_the_certificate_expired_does_not_rescue_it() -> None:
    p, tsa = (
        pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=5)),
        authority(),
    )
    pdf = stamp(signed(p), tsa, T2)  # two days ago: after the certificate's end
    (f,) = cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "DOCUMENT_TIMESTAMP" and f.chain is cms.Chain.EXPIRED
    r = sg.signature_result([f], _cfg(p, tsa))
    assert r.status is docs.Status.NOT_VERIFIED
    assert r.reasons == ("CERTIFICATE_NOT_VALID_AT_SIGNING_TIME",)


def test_a_stamp_from_an_unconfigured_authority_proves_nothing_about_time() -> None:
    p, tsa, other = (
        pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1)),
        authority(),
        authority(),
    )
    pdf = stamp(signed(p), other, T1)
    (f,) = cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "NONE" and f.trusted_time is None


def test_a_stamp_over_other_bytes_does_not_fix_the_time() -> None:
    p, tsa = (
        pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1)),
        authority(),
    )
    pdf = stamp(signed(p), tsa, T1, wrong_data=True)
    (f,) = cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "NONE"


def test_the_earliest_verified_stamp_is_the_one_used() -> None:
    p, tsa = pki(), authority()
    first = stamp(signed(p), tsa, T0)
    second = stamp(first, tsa, T1, number=20, earlier=(11,), catalog=None, start_at=0)
    (f,) = cms.inspect_pdf(second, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "DOCUMENT_TIMESTAMP"
    assert f.trusted_time is not None and f.trusted_time.startswith(T0.date().isoformat())


def test_a_signature_with_its_own_timestamp_keeps_that_as_the_source() -> None:
    from test_pdf_signatures import timestamped

    p = pki()
    own, tsa = timestamped(p, NOW - timedelta(days=15))
    (f,) = cms.inspect_pdf(own, anchors=(p.root.cert,), tsa_roots=(tsa.root,))
    assert f.time_source == "SIGNATURE_TIMESTAMP"
