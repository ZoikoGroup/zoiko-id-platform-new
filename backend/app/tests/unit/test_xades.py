"""DTV-02 / GID-051: XAdES signatures verified for real, one broken property at a time (see `xades_signing`)."""

from __future__ import annotations

from datetime import timedelta

from pdf_signing import NOW, make_ocsp, pki, token_from_response
from tsa_authority import authority, response
from xades_signing import add_timestamp, sign_xml
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms, xades
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

S = docs.Status


def one(xml: bytes, p=None, **kw) -> cms.Finding:
    anchors = (p.root.cert,) if p else ()
    found = xades.inspect_xml(xml, anchors=anchors, **kw)
    assert len(found) == 1
    return found[0]


def test_a_valid_xades_signature_chained_to_a_configured_anchor() -> None:
    p = pki()
    f = one(sign_xml(p.signer), p)
    assert f.cryptographically_valid and f.chain is cms.Chain.TRUSTED and not f.problems
    r = sg.signature_result([f], tr.TrustConfig(anchors=(p.root.cert,)))
    assert (
        r.status is S.VERIFIED_LIMITED
    )  # no trusted time and no revocation evidence: said, not hidden


def test_without_an_anchor_the_cryptography_is_sound_but_nothing_is_trusted() -> None:
    p = pki()
    f = one(sign_xml(p.signer))
    assert f.cryptographically_valid and f.chain is cms.Chain.NO_ANCHORS
    assert sg.signature_result([f], tr.TrustConfig()).status is S.INDETERMINATE


def test_an_untrusted_issuer_is_reported_as_such() -> None:
    p, other = pki(), pki()
    f = one(sign_xml(p.signer), other)
    assert f.chain is cms.Chain.UNTRUSTED


def test_changing_the_document_after_signing_is_detected() -> None:
    p = pki()
    xml = sign_xml(p.signer).replace(b"hello", b"HELLO")
    f = one(xml, p)
    assert not f.digest_ok and "DOCUMENT_ALTERED_AFTER_SIGNING" in f.problems
    assert sg.signature_result([f], tr.TrustConfig(anchors=(p.root.cert,))).status is S.NOT_VERIFIED


def test_a_flipped_signature_value_is_detected() -> None:
    p = pki()
    f = one(sign_xml(p.signer, tamper_value=True), p)
    assert not f.signature_ok and "SIGNATURE_VALUE_INVALID" in f.problems


def test_signed_properties_that_are_not_signed_make_the_signature_unreliable() -> None:
    p = pki()
    f = one(sign_xml(p.signer, sign_properties=False), p)
    assert "SIGNED_PROPERTIES_NOT_SIGNED" in f.problems and not f.cryptographically_valid


def test_a_signing_certificate_digest_that_names_another_certificate_is_refused() -> None:
    p, q = pki(), pki()
    f = one(sign_xml(p.signer, cert_digest_of=q.signer.cert), p)
    assert "SIGNING_CERTIFICATE_DIGEST_MISMATCH" in f.problems and not f.cryptographically_valid


def test_unsafe_xml_is_refused_before_it_is_parsed() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE d [<!ENTITY x "y">]><d>&x;</d>'
    f = one(bomb)
    assert f.problems == ("NOT_XML_OR_UNSAFE",)


def test_an_unsigned_document_has_no_findings_and_two_signatures_are_not_guessed_at() -> None:
    p = pki()
    assert xades.inspect_xml(b"<a/>") == []
    broken = xades.inspect_xml(sign_xml(p.signer, second_signature=True))
    assert len(broken) == 2 and "SIGNER_CERTIFICATE_MISSING" in broken[1].problems, (
        "every signature is judged, an empty one is reported and never skipped"
    )


def test_a_verified_timestamp_fixes_the_signing_time() -> None:
    p = pki()
    tsa = authority()
    stamped_at = NOW - timedelta(minutes=2)

    def token_for(value: bytes) -> bytes:
        return token_from_response(
            response(tsa, hash_bytes(value, alg="sha-256"), nonce=None, gen_time=stamped_at)
        )

    xml = add_timestamp(sign_xml(p.signer), token_for)
    f = one(xml, p, tsa_roots=(tsa.root,))
    assert f.timestamp == "VALID" and f.trusted_time is not None
    other = authority()
    assert one(xml, p, tsa_roots=(other.root,)).timestamp == "INVALID"
    assert one(xml, p).timestamp == "NO_TSA_ROOTS"


def test_a_stamp_over_something_else_is_not_trusted() -> None:
    p = pki()
    tsa = authority()
    xml = add_timestamp(
        sign_xml(p.signer),
        lambda _v: token_from_response(
            response(tsa, hash_bytes(b"unrelated", alg="sha-256"), nonce=None, gen_time=NOW)
        ),
    )
    assert one(xml, p, tsa_roots=(tsa.root,)).timestamp == "INVALID"


def test_ocsp_revocation_applies_to_xml_signatures_too() -> None:
    p = pki()
    xml = sign_xml(p.signer)
    from cryptography.x509 import ocsp

    revoked = ocsp.load_der_ocsp_response(
        make_ocsp(p.signer.cert, p.root, status="revoked", revoked_at=NOW - timedelta(days=3))
    )
    f = one(xml, p, ocsp_responses=(revoked,))
    assert f.revocation == "REVOKED" and "CERTIFICATE_REVOKED" in f.problems
    good = ocsp.load_der_ocsp_response(make_ocsp(p.signer.cert, p.root))
    assert one(xml, p, ocsp_responses=(good,)).revocation == "GOOD"


# ------------------------------------------------------------------------------------------ through the document validator


def _validate(xml: bytes, config: tr.TrustConfig):
    v = docs.validate(
        xml,
        declared_type="application/xml",
        expected_sha256=None,
        checks=["integrity", "signature", "seal"],
    )
    return {r.check: r for r in sg.apply(v, xml, config).results}


def test_signed_xml_goes_through_the_document_validator() -> None:
    p = pki()
    out = _validate(sign_xml(p.signer), tr.TrustConfig(anchors=(p.root.cert,)))
    assert out["signature"].status is S.VERIFIED_LIMITED
    assert (
        out["seal"].status is S.UNAVAILABLE
    )  # no authenticated trusted list: qualified status NOT_ASSESSED
    assert out["seal"].detail["qualified_status"] == "NOT_ASSESSED"


def test_altered_signed_xml_is_not_verified_through_the_validator() -> None:
    p = pki()
    xml = sign_xml(p.signer).replace(b"hello", b"HELLO")
    out = _validate(xml, tr.TrustConfig(anchors=(p.root.cert,)))
    assert out["signature"].status is S.NOT_VERIFIED


def test_xml_with_no_signature_is_indeterminate_and_a_lying_type_is_refused() -> None:
    out = _validate(b"<a/>", tr.TrustConfig())
    assert out["signature"].reasons == ("NO_SIGNATURE_FOUND",)
    lie = docs.validate(
        b"<a/>", declared_type="application/pdf", expected_sha256=None, checks=["integrity"]
    )
    assert lie.results[0].status is S.NOT_VERIFIED and "TYPE_MISMATCH" in lie.results[0].reasons


def test_xml_is_a_validation_type_but_never_an_upload_type() -> None:
    assert "application/xml" in docs.VALIDATION_TYPES
    assert "application/xml" not in docs.ALLOWED_TYPES
