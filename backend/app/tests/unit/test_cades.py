"""DTV-02: standalone CAdES (a detached .p7s over any file), one broken property at a time."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import timedelta

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.serialization import Encoding, pkcs7
from cryptography.x509 import ocsp
from pdf_signing import NOW, Pki, add_timestamp, der, make_ocsp, pki, token_from_response
from tsa_authority import authority, response
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

CONTENT = b"The quick brown fox signs a file that is not a PDF.\n" * 20


def detached(p: Pki, content: bytes = CONTENT, *, digest=None, token_for=None) -> bytes:
    b = pkcs7.PKCS7SignatureBuilder().set_data(content)
    b = b.add_signer(p.signer.cert, p.signer.key, digest or hashes.SHA256())  # type: ignore[arg-type]
    for extra in p.pool:
        b = b.add_certificate(extra)
    out = b.sign(Encoding.DER, [pkcs7.PKCS7Options.DetachedSignature, pkcs7.PKCS7Options.Binary])
    if token_for is not None:
        _, signer = cms._parse_cms(out)
        out = add_timestamp(out, token_for(signer.signature))
    return out


def one(sig: bytes, content: bytes, p: Pki | None = None, **kw) -> cms.Finding:
    found = cms.inspect_cades(sig, content, anchors=(p.root.cert,) if p else (), **kw)
    assert len(found) == 1
    return found[0]


def test_a_valid_detached_signature_over_any_file() -> None:
    p = pki()
    f = one(detached(p), CONTENT, p)
    assert f.cryptographically_valid and f.chain is cms.Chain.TRUSTED and not f.problems
    r = sg.signature_result([f], tr.TrustConfig(anchors=(p.root.cert,)))
    assert r.status is docs.Status.VERIFIED_LIMITED  # no trusted time, no revocation evidence


def test_a_changed_file_is_detected() -> None:
    p = pki()
    f = one(detached(p), CONTENT + b"!", p)
    assert not f.digest_ok and "DOCUMENT_ALTERED_AFTER_SIGNING" in f.problems
    r = sg.signature_result([f], tr.TrustConfig(anchors=(p.root.cert,)))
    assert r.status is docs.Status.NOT_VERIFIED


def test_a_flipped_signature_value_is_detected() -> None:
    p = pki()
    sig = bytearray(detached(p))
    _, signer = cms._parse_cms(bytes(sig))
    at = bytes(sig).rindex(signer.signature)
    sig[at + len(signer.signature) // 2] ^= 0x01
    f = one(bytes(sig), CONTENT, p)
    assert not f.signature_ok


def test_garbage_is_unreadable_not_an_exception() -> None:
    f = one(b"not a signature", CONTENT)
    assert f.problems == ("SIGNATURE_UNREADABLE",) and not f.signer


def test_without_an_anchor_nothing_is_trusted() -> None:
    p = pki()
    f = one(detached(p), CONTENT)
    assert f.cryptographically_valid and f.chain is cms.Chain.NO_ANCHORS


def test_an_intermediate_in_the_signature_builds_the_chain() -> None:
    p = pki(intermediate=True)
    assert one(detached(p), CONTENT, p).chain is cms.Chain.TRUSTED


def test_a_timestamp_and_ocsp_apply_to_cades_like_to_pdf() -> None:
    p = pki()
    tsa = authority()
    sig = detached(
        p,
        token_for=lambda v: token_from_response(
            response(
                tsa,
                hash_bytes(v, alg="sha-256"),
                nonce=None,
                gen_time=NOW - timedelta(days=10),
            )
        ),
    )
    revoked_later = ocsp.load_der_ocsp_response(
        make_ocsp(p.signer.cert, p.root, status="revoked", revoked_at=NOW - timedelta(days=5))
    )
    f = one(sig, CONTENT, p, tsa_roots=(tsa.root,), ocsp_responses=(revoked_later,))
    assert (
        f.timestamp == "VALID" and f.revocation == "GOOD"
    )  # revoked after the stamp: good when signed
    revoked_before = ocsp.load_der_ocsp_response(
        make_ocsp(p.signer.cert, p.root, status="revoked", revoked_at=NOW - timedelta(days=20))
    )
    g = one(sig, CONTENT, p, tsa_roots=(tsa.root,), ocsp_responses=(revoked_before,))
    assert g.revocation == "REVOKED"


# ------------------------------------------------------------------ the signing-certificate attribute (ESSCertIDv2)


def _ess_cert_id_v2(cert_hash: bytes, *, explicit_sha256: bool) -> bytes:
    parts = b""
    if explicit_sha256:
        oid = bytes.fromhex("0609608648016503040201")  # id-sha256
        parts += der(0x30, oid)
    parts += der(0x04, cert_hash)
    # the contents of a SigningCertificateV2 (the attribute value the parser is handed): certs { ESSCertIDv2 }
    return der(0x30, der(0x30, parts))


def test_the_signing_certificate_attribute_is_parsed_with_and_without_the_default_hash() -> None:
    h = hashlib.sha256(b"x").digest()
    for explicit in (True, False):
        value = _ess_cert_id_v2(h, explicit_sha256=explicit)
        assert cms._essc_cert_hash(value) == (cms.OID_SHA256, h)
    assert cms._essc_cert_hash(b"\x30\x00") is None  # an empty structure names nothing


def test_a_signature_that_names_another_certificate_is_refused() -> None:
    p = pki()
    sig = detached(p)
    certs, signer = cms._parse_cms(sig)
    wrong = replace(signer, signing_cert_hash=(cms.OID_SHA256, b"\x00" * 32))
    f = cms._assess(
        0,
        certs,
        wrong,
        CONTENT,
        True,
        [],
        anchors=(p.root.cert,),
        when=NOW,
        tsa_roots=(),
        crls=(),
        ocsp_responses=(),
    )
    assert "SIGNING_CERTIFICATE_DIGEST_MISMATCH" in f.problems and not f.cryptographically_valid
    cert = cms._signer_certificate(certs, signer)
    right = replace(
        signer,
        signing_cert_hash=(
            cms.OID_SHA256,
            hashlib.sha256(cert.public_bytes(Encoding.DER)).digest(),
        ),
    )
    ok = cms._assess(
        0,
        certs,
        right,
        CONTENT,
        True,
        [],
        anchors=(p.root.cert,),
        when=NOW,
        tsa_roots=(),
        crls=(),
        ocsp_responses=(),
    )
    assert ok.cryptographically_valid and not ok.problems


# ------------------------------------------------------------------ through the document validator

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _validate(content: bytes, sig: bytes, config: tr.TrustConfig, declared: str = "image/png"):
    v = docs.validate(
        content,
        declared_type=declared,
        expected_sha256=None,
        checks=["integrity", "signature", "seal"],
    )
    return {r.check: r for r in sg.apply_detached(v, content, sig, config).results}


def test_a_detached_signature_over_an_image_validates_through_the_document_validator() -> None:
    p = pki()
    out = _validate(PNG, detached(p, PNG), tr.TrustConfig(anchors=(p.root.cert,)))
    assert out["signature"].status is docs.Status.VERIFIED_LIMITED
    assert out["seal"].status is docs.Status.UNAVAILABLE  # no authenticated trusted list
    assert out["seal"].detail["qualified_status"] == "NOT_ASSESSED"


def test_the_wrong_file_for_a_signature_is_not_verified() -> None:
    p = pki()
    other = PNG + b"x"
    out = _validate(other, detached(p, PNG), tr.TrustConfig(anchors=(p.root.cert,)))
    assert out["signature"].status is docs.Status.NOT_VERIFIED
