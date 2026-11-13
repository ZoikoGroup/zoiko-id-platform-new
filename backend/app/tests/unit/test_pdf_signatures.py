"""ZID-INT-002 section 09 / DTV-02 / T26 / T27: PDF signatures verified for real, one broken property at a time.

Every case signs a genuine PDF byte range with a genuine PKCS#7 signature (see `pdf_signing`), then changes exactly one thing. The result is
read through the same functions the API uses, so a case that passes here is what a caller would be told.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import hashes
from pdf_signing import (
    NOW,
    Pki,
    append_after_signing,
    flip_signature_value,
    make_crl,
    pki,
    rsa_key,
    sign_pdf,
    token_from_response,
)
from tsa_authority import authority, response
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

S = docs.Status


def result(pdf: bytes, config: tr.TrustConfig, at=None) -> docs.CheckResult:
    return sg.signature_result(
        cms.inspect_pdf(
            pdf,
            anchors=config.anchors,
            at=at,
            tsa_roots=config.tsa_roots,
            crls=config.crls,
            ocsp_responses=config.ocsp_responses,
        ),
        config,
    )


def trusting(p: Pki, **kw) -> tr.TrustConfig:
    return tr.TrustConfig(anchors=(p.root.cert,), **kw)


# ---------------------------------------------------------------------------------------------------------- the happy path


def test_a_valid_signature_chained_to_a_configured_anchor_is_verified_limited_with_the_gaps_named() -> (
    None
):
    p = pki()
    r = result(sign_pdf(p), trusting(p))
    assert r.status is S.VERIFIED_LIMITED
    assert {
        "CRYPTOGRAPHICALLY_VALID",
        "CHAIN_TO_CONFIGURED_ANCHOR",
        "SIGNING_TIME_NOT_TRUSTED",
        "REVOCATION_NOT_CHECKED",
    } <= set(r.reasons)
    sig = r.detail["signatures"][0]
    assert (
        sig["digest_ok"]
        and sig["signature_ok"]
        and sig["covers_whole_file"]
        and sig["chain"] == "TRUSTED"
    )
    assert sig["signer"] == "CN=Dana Lee (signer)" and sig["anchor"] == "CN=Test Root CA"
    assert sig["claimed_signing_time"] and sig["claimed_signing_time_trusted"] is False


def test_a_chain_through_an_intermediate_and_an_rsa_signer_also_verify() -> None:
    p = pki(intermediate=True, signer_key=rsa_key())
    assert result(sign_pdf(p), trusting(p)).status is S.VERIFIED_LIMITED
    flat = pki(signer_key=rsa_key())
    assert result(sign_pdf(flat), trusting(flat)).status is S.VERIFIED_LIMITED


def test_the_intermediate_must_actually_be_in_the_signature_to_build_the_chain() -> None:
    p = pki(intermediate=True)
    r = result(
        sign_pdf(p, certs=[]), trusting(p)
    )  # the signature carries only the signer certificate
    assert r.status is S.INDETERMINATE and "ISSUER_NOT_TRUSTED" in r.reasons


# ---------------------------------------------------------------------------------------------------------- not trusted


def test_t26_a_valid_signature_from_an_issuer_nobody_configured_is_never_issuer_authentic() -> None:
    p, other = pki(), pki()
    r = result(sign_pdf(p), trusting(other))  # the anchor configured is somebody else's root
    assert (
        r.status is S.INDETERMINATE
        and "ISSUER_NOT_TRUSTED" in r.reasons
        and "SIGNATURE_VALID" in r.reasons
    )
    assert r.detail["signatures"][0]["chain"] == "UNTRUSTED"


def test_with_no_anchor_configured_the_signature_is_sound_but_cannot_be_relied_on() -> None:
    p = pki()
    r = result(sign_pdf(p), tr.TrustConfig())
    assert r.status is S.INDETERMINATE and "NO_TRUST_ANCHORS_CONFIGURED" in r.reasons
    assert r.detail["signatures"][0]["signature_ok"] is True
    assert "NO_TRUST_ANCHORS_CONFIGURED" in r.detail["trust_notes"]


# ---------------------------------------------------------------------------------------------------------- broken


def test_a_document_changed_after_signing_is_not_verified() -> None:
    p = pki()
    r = result(sign_pdf(p, tamper_document=True), trusting(p))
    assert r.status is S.NOT_VERIFIED and "DOCUMENT_ALTERED_AFTER_SIGNING" in r.reasons
    assert r.detail["signatures"][0]["digest_ok"] is False


def test_a_changed_signature_value_is_not_verified() -> None:
    p = pki()
    r = result(flip_signature_value(sign_pdf(p)), trusting(p))
    assert r.status is S.NOT_VERIFIED and "SIGNATURE_VALUE_INVALID" in r.reasons


def test_a_signature_made_with_another_key_than_the_certificate_names_is_not_verified() -> None:
    p, imposter = pki(), pki()
    pdf = sign_pdf(
        p, key=imposter.signer.key
    )  # signed by someone else's key, carrying p's certificate
    assert result(pdf, trusting(p)).status is S.NOT_VERIFIED


def test_content_added_after_signing_is_reported_and_never_ignored() -> None:
    p = pki()
    r = result(append_after_signing(sign_pdf(p)), trusting(p))
    assert r.status is S.INDETERMINATE and "CONTENT_ADDED_AFTER_SIGNING" in r.reasons
    assert (
        r.detail["signatures"][0]["covers_whole_file"] is False
        and r.detail["signatures"][0]["digest_ok"] is True
    )


@pytest.mark.parametrize(
    ("digest_oid", "sig_oid", "problem"),
    [
        ("1.3.14.3.2.26", "1.2.840.113549.1.1.11", "WEAK_DIGEST_SHA1"),
        ("1.2.840.113549.2.5", "1.2.840.113549.1.1.11", "WEAK_DIGEST_MD5"),
        (
            "2.16.840.1.101.3.4.2.1",
            "1.2.840.113549.1.1.10",
            "UNSUPPORTED_SIGNATURE_ALGORITHM",
        ),  # RSASSA-PSS
        ("2.16.840.1.101.3.4.2.1", "1.2.840.10040.4.3", "UNSUPPORTED_SIGNATURE_ALGORITHM"),  # DSA
        (
            "2.16.840.1.101.3.4.2.7",
            "1.2.840.113549.1.1.11",
            "UNSUPPORTED_DIGEST",
        ),  # SHA3: not on the list
    ],
)
def test_weak_and_unsupported_algorithms_are_refused_never_accepted(
    digest_oid: str, sig_oid: str, problem: str
) -> None:
    p = pki()
    cert, _ = p.signer.cert, None
    s = cms._Signer(None, None, None, digest_oid, sig_oid, b"\x31\x00", b"", None, b"x", None)
    ok, found = cms._verify_value(cert, s)
    assert ok is False and found == problem


def test_a_signature_without_signed_attributes_is_not_accepted_because_it_binds_no_digest() -> None:
    p = pki()
    s = cms._Signer(
        None,
        None,
        None,
        "2.16.840.1.101.3.4.2.1",
        "1.2.840.10045.4.3.2",
        None,
        None,
        None,
        b"x",
        None,
    )
    assert cms._verify_value(p.signer.cert, s) == (False, "NO_SIGNED_ATTRIBUTES")


def test_unreadable_signature_bytes_are_reported_not_crashed_on() -> None:
    pdf = sign_pdf(pki())
    broken = pdf.replace(b"/Contents <30", b"/Contents <ff", 1)
    r = result(broken, tr.TrustConfig())
    assert r.status is S.INDETERMINATE and "SIGNATURE_UNREADABLE" in r.reasons


def test_a_pdf_with_two_signatures_needs_both_to_hold() -> None:
    p = pki()
    first = sign_pdf(p)
    findings = cms.inspect_pdf(first, anchors=[p.root.cert])
    assert len(findings) == 1
    doubled = (
        first + b"\n" + first[first.index(b"2 0 obj") :]
    )  # a second /ByteRange that does not cover the file
    two = cms.inspect_pdf(doubled, anchors=[p.root.cert])
    assert len(two) == 2 and not all(f.covers_whole_file for f in two)


# ---------------------------------------------------------------------------------------------------------- time and revocation


def test_t27_an_expired_signer_certificate_without_a_trusted_timestamp_is_not_held_against_the_past_but_not_trusted_either() -> (
    None
):
    p = pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1))
    r = result(sign_pdf(p), trusting(p))
    assert r.status is S.INDETERMINATE and {
        "CERTIFICATE_NOT_VALID_NOW",
        "SIGNING_TIME_NOT_TRUSTED",
    } <= set(r.reasons)
    assert r.detail["signatures"][0]["chain"] == "EXPIRED"


def timestamped(p: Pki, signed_at, *, tsa=None):
    """Sign, adding a real RFC 3161 token (over THIS signature value) stamped at `signed_at` by `tsa`."""
    tsa = tsa or authority()

    def token_for(signature: bytes) -> bytes:
        return token_from_response(
            response(tsa, hash_bytes(signature, alg="sha-256"), nonce=None, gen_time=signed_at)
        )

    return sign_pdf(p, token_for=token_for), tsa


def test_t27_a_verified_timestamp_fixes_the_signing_time_so_a_later_expiry_does_not_matter() -> (
    None
):
    p = pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1))
    pdf, tsa = timestamped(
        p, NOW - timedelta(days=10)
    )  # stamped while the certificate was still valid
    cfg = trusting(p, tsa_roots=(tsa.root,))
    r = result(pdf, cfg)
    sig = r.detail["signatures"][0]
    assert sig["timestamp"] == "VALID" and sig["trusted_time"] and sig["chain"] == "TRUSTED"
    assert (
        r.status is S.VERIFIED_LIMITED
        and "SIGNING_TIME_TRUSTED_BY_TIMESTAMP" in r.reasons
        and "REVOCATION_NOT_CHECKED" in r.reasons
    )


def test_a_timestamp_from_an_authority_nobody_configured_does_not_fix_the_time() -> None:
    p = pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=1))
    pdf, _ = timestamped(p, NOW - timedelta(days=10))
    r = result(pdf, trusting(p, tsa_roots=(authority().root,)))
    assert r.detail["signatures"][0]["timestamp"] == "INVALID" and r.status is S.INDETERMINATE
    none = result(pdf, trusting(p))  # no TSA root configured at all
    assert (
        none.detail["signatures"][0]["timestamp"] == "NO_TSA_ROOTS"
        and none.status is S.INDETERMINATE
    )


def test_a_certificate_not_yet_valid_when_the_timestamp_says_it_signed_is_not_verified() -> None:
    p = pki(not_before=NOW - timedelta(days=5), not_after=NOW + timedelta(days=300))
    pdf, tsa = timestamped(p, NOW - timedelta(days=10))  # stamped BEFORE the certificate existed
    r = result(pdf, trusting(p, tsa_roots=(tsa.root,)))
    assert r.status is S.NOT_VERIFIED and "CERTIFICATE_NOT_VALID_AT_SIGNING_TIME" in r.reasons


def test_revocation_before_the_trusted_signing_time_fails_and_revocation_after_it_does_not() -> (
    None
):
    p = pki()
    pdf, tsa = timestamped(p, NOW - timedelta(days=10))
    before = make_crl(p.root, [(p.signer.cert, NOW - timedelta(days=20))])
    after = make_crl(p.root, [(p.signer.cert, NOW - timedelta(days=5))])
    revoked = result(pdf, trusting(p, tsa_roots=(tsa.root,), crls=(before,)))
    assert (
        revoked.status is S.NOT_VERIFIED and "CERTIFICATE_REVOKED_BEFORE_SIGNING" in revoked.reasons
    )
    later = result(pdf, trusting(p, tsa_roots=(tsa.root,), crls=(after,)))
    assert later.detail["signatures"][0]["revocation"] == "GOOD", (
        "revoked after the stamp: it was good when it signed"
    )
    assert (
        later.status is S.VERIFIED
        and "REVOCATION_CHECKED_GOOD" in later.reasons
        and "SIGNING_TIME_TRUSTED_BY_TIMESTAMP" in later.reasons
    )


def test_a_crl_that_does_not_cover_the_chain_or_is_not_signed_by_its_issuer_proves_nothing() -> (
    None
):
    p, other = pki(), pki()
    pdf = sign_pdf(p)
    unrelated = make_crl(other.root, [])  # someone else's CRL
    assert (
        result(pdf, trusting(p, crls=(unrelated,))).detail["signatures"][0]["revocation"]
        == "PARTIAL"
    )
    # the other root has the same NAME as p's root but a different key: a CRL it signs claims to be p's root's and cannot verify
    forged = make_crl(other.root, [(p.signer.cert, NOW - timedelta(days=2))])
    r = result(pdf, trusting(p, crls=(forged,)))
    assert (
        r.detail["signatures"][0]["revocation"] == "PARTIAL" and r.status is S.VERIFIED_LIMITED
    ), "a forged revocation neither fails nor passes it"


def test_a_good_crl_without_a_trusted_time_still_leaves_the_time_unproven() -> None:
    p = pki()
    r = result(sign_pdf(p), trusting(p, crls=(make_crl(p.root, []),)))
    assert (
        r.status is S.VERIFIED_LIMITED
        and "REVOCATION_CHECKED_GOOD" in r.reasons
        and "SIGNING_TIME_NOT_TRUSTED" in r.reasons
    )


# ---------------------------------------------------------------------------------------------------------- the validation as a whole


def test_a_pdf_signature_is_replaced_in_the_document_validation_and_nothing_authority_shaped_is_ever_plain_verified_without_proof() -> (
    None
):
    p = pki()
    pdf = sign_pdf(p)
    v = docs.validate(
        pdf, declared_type="application/pdf", expected_sha256=None, checks=docs.CHECKS
    )
    assert {r.check: r.status for r in v.results}[
        "signature"
    ] is S.INDETERMINATE  # detection only, before the validator
    upgraded = sg.apply(v, pdf, trusting(p))
    by = {r.check: r for r in upgraded.results}
    assert by["signature"].status is S.VERIFIED_LIMITED
    assert by["issuer"].status is S.UNAVAILABLE, (
        "no issuer source ran: the issuer is still not authenticated"
    )
    assert (
        by["seal"].status is S.UNAVAILABLE
        and by["seal"].detail["qualified_status"] == "NOT_ASSESSED"
    )
    assert "NO_TRUST_LIST" in by["seal"].reasons


def test_an_unsigned_pdf_and_a_non_pdf_keep_the_detection_only_answers() -> None:
    plain = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
    v = docs.validate(
        plain, declared_type="application/pdf", expected_sha256=None, checks=docs.CHECKS
    )
    assert sg.apply(v, plain, tr.TrustConfig()) is v
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    pv = docs.validate(png, declared_type="image/png", expected_sha256=None, checks=docs.CHECKS)
    assert sg.apply(pv, png, tr.TrustConfig()) is pv


def test_plain_verified_needs_every_proof_and_anything_less_is_limited_or_worse() -> None:
    p = pki()
    pdf, tsa = timestamped(p, NOW - timedelta(days=10))
    crl = make_crl(p.root, [])
    full = result(pdf, trusting(p, tsa_roots=(tsa.root,), crls=(crl,)))
    assert full.status is S.VERIFIED
    for missing in (
        trusting(p, tsa_roots=(tsa.root,)),  # no CRL
        trusting(p, crls=(crl,)),  # no TSA root
        trusting(p),  # neither
    ):
        assert result(pdf, missing).status is S.VERIFIED_LIMITED
    assert (
        result(pdf, tr.TrustConfig(tsa_roots=(tsa.root,), crls=(crl,))).status is S.INDETERMINATE
    )  # no anchor


# ---------------------------------------------------------------------------------------------------------- OCSP (DTV-02)


def _ocsp(blob: bytes):
    from cryptography.x509 import ocsp

    return ocsp.load_der_ocsp_response(blob)


def _rev(p: Pki, *responses, crls=(), tsa_roots=(), pdf=None):
    cfg = trusting(
        p, tsa_roots=tsa_roots, crls=crls, ocsp_responses=tuple(_ocsp(r) for r in responses)
    )
    return result(pdf or sign_pdf(p), cfg).detail["signatures"][0]["revocation"]


def test_a_good_ocsp_response_from_the_issuer_checks_revocation() -> None:
    from pdf_signing import make_ocsp

    p = pki()
    assert _rev(p, make_ocsp(p.signer.cert, p.root)) == "GOOD"


def test_an_ocsp_revocation_before_the_trusted_signing_time_fails_and_after_it_does_not() -> None:
    from pdf_signing import make_ocsp

    p = pki()
    pdf, tsa = timestamped(p, NOW - timedelta(days=10))
    before = make_ocsp(p.signer.cert, p.root, status="revoked", revoked_at=NOW - timedelta(days=20))
    after = make_ocsp(p.signer.cert, p.root, status="revoked", revoked_at=NOW - timedelta(days=5))
    assert _rev(p, before, tsa_roots=(tsa.root,), pdf=pdf) == "REVOKED"
    assert _rev(p, after, tsa_roots=(tsa.root,), pdf=pdf) == "GOOD"


def test_an_ocsp_response_signed_by_an_unrelated_key_proves_nothing() -> None:
    from pdf_signing import make_ocsp

    p, other = pki(), pki()
    forged = make_ocsp(
        p.signer.cert,
        p.root,
        status="revoked",
        revoked_at=NOW - timedelta(days=2),
        signer=other.root,
    )
    assert _rev(p, forged) == "PARTIAL", (
        "a forged revocation must not count, and neither may it count as good"
    )


def test_a_stale_or_future_ocsp_response_proves_nothing() -> None:
    from pdf_signing import make_ocsp

    p = pki()
    stale = make_ocsp(
        p.signer.cert,
        p.root,
        this_update=NOW - timedelta(days=9),
        next_update=NOW - timedelta(days=2),
    )
    future = make_ocsp(
        p.signer.cert,
        p.root,
        this_update=NOW + timedelta(days=1),
        next_update=NOW + timedelta(days=3),
    )
    assert _rev(p, stale) == "PARTIAL"
    assert _rev(p, future) == "PARTIAL"


def test_an_unknown_status_and_a_response_for_another_certificate_prove_nothing() -> None:
    from pdf_signing import make_ocsp

    p, q = pki(), pki()
    assert _rev(p, make_ocsp(p.signer.cert, p.root, status="unknown")) == "PARTIAL"
    assert _rev(p, make_ocsp(q.signer.cert, q.root)) == "PARTIAL"


def test_a_delegated_responder_signs_only_when_the_issuer_issued_it_for_ocsp() -> None:
    from pdf_signing import ec_key, make_cert, make_ocsp, ocsp_signing_cert

    p = pki()
    responder = ocsp_signing_cert(p.root)
    ok = make_ocsp(p.signer.cert, p.root, signer=responder, include_signer_cert=True)
    assert _rev(p, ok) == "GOOD"
    stranger = ocsp_signing_cert(pki().root)  # issued by someone else
    bad = make_ocsp(p.signer.cert, p.root, signer=stranger, include_signer_cert=True)
    assert _rev(p, bad) == "PARTIAL"
    plain = make_cert("Not an OCSP signer", ec_key(), p.root)  # no OCSP-signing key usage
    nok = make_ocsp(p.signer.cert, p.root, signer=plain, include_signer_cert=True)
    assert _rev(p, nok) == "PARTIAL"


def test_the_library_refuses_to_build_a_sha1_signed_ocsp_response() -> None:
    from pdf_signing import make_ocsp

    p = pki()
    # The library will not even build a SHA-1 signed response, so no such response can be made here; the guard in
    # `_ocsp_signed_properly` covers responses produced elsewhere.
    with pytest.raises(UnsupportedAlgorithm):
        make_ocsp(p.signer.cert, p.root, sign_hash=hashes.SHA1())


def test_the_ocsp_folder_is_read_and_a_bad_file_is_skipped(tmp_path) -> None:
    from pdf_signing import make_ocsp

    p = pki()
    (tmp_path / "ocsp").mkdir()
    (tmp_path / "ocsp" / "signer.ocsp").write_bytes(make_ocsp(p.signer.cert, p.root))
    (tmp_path / "ocsp" / "junk.der").write_bytes(b"not an ocsp response")
    cfg = tr.load_trust(tmp_path)
    assert len(cfg.ocsp_responses) == 1
    assert "NO_CRLS_SUPPLIED" not in cfg.notes()
