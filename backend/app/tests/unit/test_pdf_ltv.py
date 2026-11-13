"""PAdES long-term validation data (CR-073 P2-1): validation data appended after signing is accepted, anything else appended is a change.

The danger being closed is the other direction as well as this one: a validator that ignores everything after the signature would accept a
document whose pages were rewritten. So an update counts only when EVERY object in it is validation data, and each case below breaks that
by exactly one object.
"""

from __future__ import annotations

import zlib
from datetime import timedelta

from cryptography.hazmat.primitives.serialization import Encoding
from pdf_signing import NOW, make_crl, make_ocsp, pki, sign_pdf, token_from_response
from tsa_authority import authority, response
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms, pdfobjects

SIGNED_AT = NOW - timedelta(days=10)


def stream(number: int, data: bytes, *, flate: bool = False) -> bytes:
    body = zlib.compress(data) if flate else data
    extra = b" /Filter /FlateDecode" if flate else b""
    return (
        f"{number} 0 obj\n<< /Length {len(body)}{extra.decode()} >>\nstream\n".encode()
        + body
        + b"\nendstream\nendobj\n"
    )


def update(
    *,
    certs=(),
    crls=(),
    ocsps=(),
    flate: bool = False,
    extra: bytes = b"",
    catalog: bytes = b"<< /Type /Catalog /Pages 3 0 R /DSS 20 0 R >>",
    first: int = 30,
) -> bytes:
    """An incremental update that adds a /DSS to the catalog (object 1 of the fixture)."""
    out = b"\n"
    refs = {"Certs": [], "CRLs": [], "OCSPs": []}
    n = first
    for key, items in (("Certs", certs), ("CRLs", crls), ("OCSPs", ocsps)):
        for item in items:
            out += stream(n, item, flate=flate)
            refs[key].append(f"{n} 0 R")
            n += 1
    parts = " ".join(f"/{k} [{' '.join(v)}]" for k, v in refs.items() if v)
    out += f"20 0 obj\n<< {parts} >>\nendobj\n".encode()
    out += b"1 0 obj\n" + catalog + b"\nendobj\n"
    out += extra
    out += b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
    return out


def der(cert) -> bytes:
    return cert.public_bytes(Encoding.DER)


def lt_pdf(p, *, tsa, **kw) -> bytes:
    """A PDF signed with a verified timestamp at SIGNED_AT, then given validation data."""
    signed = sign_pdf(
        p,
        token_for=lambda v: token_from_response(
            response(tsa, hash_bytes(v, alg="sha-256"), nonce=None, gen_time=SIGNED_AT)
        ),
    )
    return signed + update(**kw)


def expired_signer_pki():
    return pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=2))


def first(pdf: bytes, p, tsa) -> cms.Finding:
    return cms.inspect_pdf(pdf, anchors=(p.root.cert,), tsa_roots=(tsa.root,))[0]


# ------------------------------------------------------------------------------------------------ the object reader


def test_the_reader_reads_plain_and_flate_streams_and_the_last_definition_wins() -> None:
    data = stream(5, b"hello") + stream(6, b"world", flate=True) + b"5 0 obj\n<< /A 1 >>\nendobj\n"
    objs = pdfobjects.parse_objects(data)
    assert objs[6].stream == b"world"
    assert objs[5].stream is None and objs[5].value == {"A": 1}, (
        "the later definition replaced the first"
    )


def test_the_reader_skips_what_it_cannot_read_and_never_raises() -> None:
    junk = (
        b"1 0 obj\n<< /A [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ [ 1 ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] ] >>\nendobj\n"
        b"2 0 obj\n<< /Unterminated (abc\nendobj\n"
        b"3 0 obj\n<< /Ok 1 >>\nendobj\n"
        b"4 0 obj\n<< /Length 999999999 >>\nstream\nxx\nendstream\nendobj\n"
    )
    objs = pdfobjects.parse_objects(junk)
    assert objs[3].value == {"Ok": 1}
    assert pdfobjects.parse_objects(b"") == {} and pdfobjects.parse_objects(b"\x00\xff" * 50) == {}


def test_an_unknown_filter_or_a_bomb_is_not_decoded() -> None:
    lzw = b"7 0 obj\n<< /Length 3 /Filter /LZWDecode >>\nstream\nabc\nendstream\nendobj\n"
    assert pdfobjects.parse_objects(lzw)[7].stream is None
    bomb = zlib.compress(b"\x00" * (pdfobjects.MAX_STREAM + 10))
    big = (
        f"8 0 obj\n<< /Length {len(bomb)} /Filter /FlateDecode >>\nstream\n".encode()
        + bomb
        + b"\nendstream\nendobj\n"
    )
    assert pdfobjects.parse_objects(big)[8].stream is None


# ------------------------------------------------------------------------------ what counts as only validation data


def signed_part(p) -> bytes:
    return sign_pdf(p)


def test_a_dss_update_with_certificates_crls_and_ocsp_is_read() -> None:
    p = pki()
    crl = make_crl(p.root, [])
    ocsp_der = make_ocsp(p.signer.cert, p.root)
    upd = update(
        certs=[der(p.signer.cert)], crls=[crl.public_bytes(Encoding.DER)], ocsps=[ocsp_der]
    )
    found = pdfobjects.dss_update(signed_part(p), upd)
    assert found is not None and len(found.certificates) == 1
    assert len(found.crls) == 1 and len(found.ocsp_responses) == 1


def test_flate_compressed_validation_data_is_read() -> None:
    p = pki()
    found = pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], flate=True))
    assert found is not None and len(found.certificates) == 1


def test_one_extra_object_makes_the_whole_update_not_validation_data() -> None:
    p = pki()
    extra = b"40 0 obj\n<< /Type /Page /Parent 3 0 R /Contents 41 0 R >>\nendobj\n"
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], extra=extra))
        is None
    )


def test_redefining_an_object_of_the_signed_revision_is_refused() -> None:
    p = pki()
    # object 2 is the signature dictionary itself
    extra = b"2 0 obj\n<< /Type /Sig /Filter /Adobe.PPKLite >>\nendobj\n"
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], extra=extra))
        is None
    )


def test_a_catalog_that_changed_in_any_other_way_is_refused() -> None:
    p = pki()
    moved = (
        b"<< /Type /Catalog /Pages 99 0 R /DSS 20 0 R >>"  # the page tree now points somewhere else
    )
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], catalog=moved))
        is None
    )
    added = b"<< /Type /Catalog /Pages 3 0 R /DSS 20 0 R /OpenAction 5 0 R >>"
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], catalog=added))
        is None
    )


def test_object_streams_and_document_timestamps_are_not_recognised_so_they_are_refused() -> None:
    p = pki()
    objstm = stream(41, b"1 0 2 5 << >>").replace(
        b"<< /Length", b"<< /Type /ObjStm /N 1 /First 4 /Length"
    )
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], extra=objstm))
        is None
    )
    doc_ts = b"42 0 obj\n<< /Type /DocTimeStamp /Filter /Adobe.PPKLite /SubFilter /ETSI.RFC3161 >>\nendobj\n"
    assert (
        pdfobjects.dss_update(signed_part(p), update(certs=[der(p.signer.cert)], extra=doc_ts))
        is None
    )


def test_an_update_with_no_dss_is_not_validation_data() -> None:
    p = pki()
    plain = (
        b"\n1 0 obj\n<< /Type /Catalog /Pages 3 0 R >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
    )
    assert pdfobjects.dss_update(signed_part(p), plain) is None
    assert pdfobjects.dss_update(signed_part(p), b"") is None


def test_an_item_that_does_not_parse_is_skipped_not_fatal() -> None:
    p = pki()
    found = pdfobjects.dss_update(
        signed_part(p), update(certs=[b"not a certificate", der(p.signer.cert)])
    )
    assert found is not None and len(found.certificates) == 1


# ------------------------------------------------------------------------------------------ through the signature check


def test_an_old_pdf_signature_on_an_expired_certificate_validates_through_its_appended_evidence() -> (
    None
):
    p, tsa = expired_signer_pki(), authority()
    crl = make_crl(p.root, [], last_update=SIGNED_AT + timedelta(days=1))
    pdf = lt_pdf(p, tsa=tsa, crls=[crl.public_bytes(Encoding.DER)])
    f = first(pdf, p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" not in f.problems and f.covers_whole_file
    assert f.timestamp == "VALID" and f.chain is cms.Chain.TRUSTED
    assert f.revocation == "GOOD" and f.revocation_source == "EMBEDDED"
    assert f.cryptographically_valid


def test_content_added_after_signing_is_still_a_change_even_beside_validation_data() -> None:
    p, tsa = expired_signer_pki(), authority()
    crl = make_crl(p.root, [], last_update=SIGNED_AT + timedelta(days=1))
    page = b"40 0 obj\n<< /Type /Page /Parent 3 0 R >>\nendobj\n"
    pdf = lt_pdf(p, tsa=tsa, crls=[crl.public_bytes(Encoding.DER)], extra=page)
    f = first(pdf, p, tsa)
    assert "CONTENT_ADDED_AFTER_SIGNING" in f.problems and not f.covers_whole_file
    assert f.revocation_source != "EMBEDDED", "evidence from a tampered file is not used"


def test_a_revocation_before_the_signing_time_in_the_appended_evidence_fails_the_signature() -> (
    None
):
    p, tsa = pki(), authority()
    crl = make_crl(
        p.root,
        [(p.signer.cert, SIGNED_AT - timedelta(days=5))],
        last_update=SIGNED_AT + timedelta(days=1),
    )
    f = first(lt_pdf(p, tsa=tsa, crls=[crl.public_bytes(Encoding.DER)]), p, tsa)
    assert f.revocation == "REVOKED" and "CERTIFICATE_REVOKED" in f.problems


def test_appended_evidence_is_ignored_without_a_trusted_signing_time() -> None:
    p = pki()
    crl = make_crl(p.root, [], last_update=NOW - timedelta(days=1))
    pdf = sign_pdf(p) + update(
        crls=[crl.public_bytes(Encoding.DER)]
    )  # no timestamp in this signature
    f = cms.inspect_pdf(pdf, anchors=(p.root.cert,))[0]
    assert f.revocation_source == "NONE" and "CONTENT_ADDED_AFTER_SIGNING" not in f.problems


def test_appended_certificates_complete_a_chain_but_never_add_an_anchor() -> None:
    p, tsa = pki(intermediate=True), authority()
    pdf = lt_pdf(p, tsa=tsa, certs=[der(p.intermediate.cert)])
    assert first(pdf, p, tsa).chain is cms.Chain.TRUSTED
    stranger = pki()
    g = cms.inspect_pdf(pdf, anchors=(stranger.root.cert,), tsa_roots=(tsa.root,))[0]
    assert g.chain is cms.Chain.UNTRUSTED


# ------------------------------------------------------------------------------------------------ AcroForm fields

FORM_PDF = (
    b"%PDF-1.7\n"
    b"1 0 obj\n<< /Type /Catalog /Pages 3 0 R /AcroForm 4 0 R >>\nendobj\n"
    b"4 0 obj\n<< /Fields [5 0 R 6 0 R 8 0 R 9 0 R] >>\nendobj\n"
    b"5 0 obj\n<< /FT /Tx /T (holder) /V (Dana Lee) >>\nendobj\n"
    b"6 0 obj\n<< /T (address) /FT /Tx /Kids [7 0 R] >>\nendobj\n"
    b"7 0 obj\n<< /T (line1) /V (1 High St) >>\nendobj\n"
    b"8 0 obj\n<< /FT /Sig /T (sig1) >>\nendobj\n"
    b"9 0 obj\n<< /FT /Tx /T (name) /V <FEFF0645006F0068> >>\nendobj\n"
    b"trailer\n<< /Root 1 0 R >>\n%%EOF\n"
)


def test_form_fields_are_read_fully_qualified_and_signature_fields_are_skipped() -> None:
    found = {f.name: f for f in pdfobjects.form_fields(FORM_PDF)}
    assert set(found) == {"holder", "address.line1", "name"}
    assert found["holder"].value == "Dana Lee" and found["holder"].field_type == "Tx"
    assert found["address.line1"].field_type == "Tx", "a child inherits its parent's field type"
    assert found["name"].value == "مoh", "UTF-16 values are decoded"


def test_a_pdf_with_no_form_or_a_broken_one_yields_no_fields_and_no_error() -> None:
    assert pdfobjects.form_fields(b"%PDF-1.7\n") == []
    assert pdfobjects.form_fields(b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\n") == []
    loop = (
        b"1 0 obj\n<< /Type /Catalog /AcroForm 4 0 R >>\nendobj\n"
        b"4 0 obj\n<< /Fields [5 0 R] >>\nendobj\n5 0 obj\n<< /T (a) /Kids [5 0 R] >>\nendobj\n"
    )
    assert (
        pdfobjects.form_fields(loop) == []
    )  # a field that is its own child stops at the depth limit


def test_the_last_revision_of_a_field_is_the_one_read() -> None:
    changed = FORM_PDF + b"5 0 obj\n<< /FT /Tx /T (holder) /V (Someone Else) >>\nendobj\n"
    assert {f.name: f.value for f in pdfobjects.form_fields(changed)}["holder"] == "Someone Else"
