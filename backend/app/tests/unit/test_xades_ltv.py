"""Embedded-evidence long-term validation of XAdES signatures (CR-073 P2-1, steps 1 and 2).

The rule being proved: evidence carried inside the signed file helps only when a verified timestamp fixes the signing time and the evidence
was produced after it; it is read as it stood when it was produced, it is never trusted without passing the usual checks, and it can never
add a trust anchor.
"""

from __future__ import annotations

from datetime import timedelta

from lxml import etree
from pdf_signing import NOW, make_crl, make_ocsp, pki, token_from_response
from tsa_authority import authority, response
from xades_signing import add_ltv, add_timestamp, sign_xml
from zid_evidence import hash_bytes
from zid_platform.modules.evidence.application import cms, xades

SIGNED_AT = NOW - timedelta(days=10)
XA = "{http://uri.etsi.org/01903/v1.3.2#}"


def stamped(p, tsa=None):
    tsa = tsa or authority()
    xml = add_timestamp(
        sign_xml(p.signer),
        lambda v: token_from_response(
            response(tsa, hash_bytes(v, alg="sha-256"), nonce=None, gen_time=SIGNED_AT)
        ),
    )
    return xml, tsa


def run(xml, p, tsa=None, **kw):
    return xades.inspect_xml(
        xml, anchors=(p.root.cert,), tsa_roots=(tsa.root,) if tsa else (), **kw
    )[0]


def expired_signer_pki():
    # the signer certificate was valid when the document was signed and has expired since
    return pki(not_before=NOW - timedelta(days=60), not_after=NOW - timedelta(days=2))


def test_an_old_signature_on_an_expired_certificate_validates_through_its_embedded_evidence() -> (
    None
):
    p = expired_signer_pki()
    xml, tsa = stamped(p)
    crl = make_crl(p.root, [], last_update=SIGNED_AT + timedelta(days=1))
    f = run(add_ltv(xml, crls=[crl]), p, tsa)
    assert f.timestamp == "VALID" and f.chain is cms.Chain.TRUSTED
    assert f.revocation == "GOOD" and f.revocation_source == "EMBEDDED"
    assert f.cryptographically_valid and "CERTIFICATE_REVOKED" not in f.problems


def test_without_the_embedded_evidence_the_same_signature_shows_nothing_for_revocation() -> None:
    p = expired_signer_pki()
    xml, tsa = stamped(p)
    f = run(xml, p, tsa)
    assert f.revocation == "NOT_CHECKED" and f.revocation_source == "NONE"


def test_an_embedded_ocsp_response_works_the_same_way() -> None:
    p = expired_signer_pki()
    xml, tsa = stamped(p)
    resp = make_ocsp(p.signer.cert, p.root, this_update=SIGNED_AT + timedelta(hours=2))
    f = run(add_ltv(xml, ocsps=[resp]), p, tsa)
    assert f.revocation == "GOOD" and f.revocation_source == "EMBEDDED"


def test_evidence_produced_before_the_signing_time_says_nothing_about_the_signature() -> None:
    p = expired_signer_pki()
    xml, tsa = stamped(p)
    early = make_crl(p.root, [], last_update=SIGNED_AT - timedelta(days=3))
    f = run(add_ltv(xml, crls=[early]), p, tsa)
    assert f.revocation == "NOT_CHECKED" and f.revocation_source == "NONE"


def test_embedded_evidence_is_ignored_when_the_signing_time_is_not_trusted() -> None:
    p = pki()
    unstamped = sign_xml(p.signer)  # no timestamp: the time is only the claim of the signer
    crl = make_crl(p.root, [], last_update=NOW - timedelta(days=1))
    f = run(add_ltv(unstamped, crls=[crl]), p)
    assert f.timestamp == "NONE" and f.revocation_source == "NONE"


def test_a_revocation_before_the_signing_time_found_in_embedded_evidence_fails_the_signature() -> (
    None
):
    p = pki()
    xml, tsa = stamped(p)
    crl = make_crl(
        p.root,
        [(p.signer.cert, SIGNED_AT - timedelta(days=5))],
        last_update=SIGNED_AT + timedelta(days=1),
    )
    f = run(add_ltv(xml, crls=[crl]), p, tsa)
    assert f.revocation == "REVOKED" and f.revocation_source == "EMBEDDED"
    assert "CERTIFICATE_REVOKED" in f.problems


def test_a_revocation_after_the_signing_time_does_not_undo_an_old_signature() -> None:
    p = pki()
    xml, tsa = stamped(p)
    crl = make_crl(
        p.root,
        [(p.signer.cert, SIGNED_AT + timedelta(days=3))],
        last_update=SIGNED_AT + timedelta(days=5),
    )
    f = run(add_ltv(xml, crls=[crl]), p, tsa)
    assert f.revocation == "GOOD", "it was good when it signed"


def test_embedded_evidence_from_a_stranger_is_ignored() -> None:
    p, other = expired_signer_pki(), pki()
    xml, tsa = stamped(p)
    forged = make_crl(
        other.root,
        [(p.signer.cert, SIGNED_AT - timedelta(days=5))],
        last_update=SIGNED_AT + timedelta(days=1),
    )
    f = run(add_ltv(xml, crls=[forged]), p, tsa)
    assert f.revocation in {"PARTIAL", "NOT_CHECKED"} and f.revocation != "REVOKED"


def test_operator_evidence_comes_first_and_embedded_evidence_cannot_override_a_revocation() -> None:
    p = pki()
    xml, tsa = stamped(p)
    operator = make_crl(p.root, [(p.signer.cert, SIGNED_AT - timedelta(days=5))])
    clean_embedded = make_crl(p.root, [], last_update=SIGNED_AT + timedelta(days=1))
    f = run(add_ltv(xml, crls=[clean_embedded]), p, tsa, crls=(operator,))
    assert f.revocation == "REVOKED" and f.revocation_source == "OPERATOR"


def test_embedded_certificates_complete_a_chain_but_never_add_an_anchor() -> None:
    p = pki(intermediate=True)
    xml, tsa = stamped(p)
    with_embedded = add_ltv(xml, certs=[p.intermediate.cert])
    assert run(with_embedded, p, tsa).chain is cms.Chain.TRUSTED
    stranger = pki()
    g = xades.inspect_xml(with_embedded, anchors=(stranger.root.cert,), tsa_roots=(tsa.root,))[0]
    assert g.chain is cms.Chain.UNTRUSTED, "embedded certificates are never trust anchors"


def test_garbage_in_the_embedded_values_is_skipped_not_fatal() -> None:
    p = pki()
    xml, tsa = stamped(p)
    root = etree.fromstring(xml)
    usp = next(root.iter(f"{XA}UnsignedSignatureProperties"))
    crls = etree.SubElement(etree.SubElement(usp, f"{XA}RevocationValues"), f"{XA}CRLValues")
    etree.SubElement(crls, f"{XA}EncapsulatedCRLValue").text = "!!!not base64"
    etree.SubElement(crls, f"{XA}EncapsulatedCRLValue").text = "AAAA"
    f = run(etree.tostring(root), p, tsa)
    assert f.cryptographically_valid and f.revocation_source == "NONE"
