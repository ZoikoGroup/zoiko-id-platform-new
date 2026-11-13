"""ZID-INT-002 section 09 / DTV-03: qualified status comes only from an authenticated trusted list, never from a guess.

A trusted list counts only if its XML signature verifies against a certificate the operator pinned; a granted CA service on it, the QC
statements the signer certificate carries, and the status AT THE SIGNING TIME must all agree. Anything less is NOT_ASSESSED or NOT_QUALIFIED.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cryptography.hazmat.primitives.serialization import Encoding
from pdf_signing import (
    GRANTED,
    NOW,
    WITHDRAWN,
    Party,
    make_cert,
    pki,
    qc_statements,
    rsa_key,
    sign_pdf,
    trusted_list_xml,
)
from zid_platform.modules.evidence.application import cms
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

S = docs.Status


def list_signer() -> Party:
    return make_cert("EU List Signer", rsa_key())


def qualified_pki(kind: str | None = "esign", *, compliance: bool = True):
    return pki(signer_extensions=(qc_statements(kind, compliance=compliance),))


def chain_of(p, at=None):
    (finding,) = cms.inspect_pdf(sign_pdf(p), anchors=[p.root.cert], at=at)
    assert finding.chain is cms.Chain.TRUSTED
    return finding.chain_certificates


def authenticated(xml: bytes, signer: Party) -> tr.TrustedList:
    t = tr.parse_trusted_list(xml, [signer.cert], "eu.xml")
    assert t.authenticated, t.problem
    return t


# ---------------------------------------------------------------------------------------------------------- authentication of the list


def test_a_list_signed_by_a_pinned_signer_is_authenticated_and_its_services_are_read() -> None:
    p, signer = qualified_pki(), list_signer()
    t = authenticated(trusted_list_xml(p.root, sign_with=signer), signer)
    assert (
        len(t.services) == 1
        and t.services[0].certificate == p.root.cert
        and t.services[0].status.endswith("granted")
    )


def test_a_list_not_signed_by_a_pinned_signer_is_ignored_and_says_why() -> None:
    p, signer, stranger = qualified_pki(), list_signer(), list_signer()
    t = tr.parse_trusted_list(trusted_list_xml(p.root, sign_with=stranger), [signer.cert], "x.xml")
    assert (
        t.authenticated is False and t.services == () and t.problem == "LIST_SIGNATURE_NOT_VERIFIED"
    )


def test_an_unsigned_or_altered_list_is_never_authenticated() -> None:
    p, signer = qualified_pki(), list_signer()
    assert tr.parse_trusted_list(trusted_list_xml(p.root), [signer.cert]).authenticated is False
    signed = trusted_list_xml(p.root, sign_with=signer)
    altered = signed.replace(b"granted", b"withdrawn", 1)
    assert (
        altered != signed and tr.parse_trusted_list(altered, [signer.cert]).authenticated is False
    )
    assert tr.parse_trusted_list(b"not xml at all", [signer.cert]).problem == "NOT_XML_OR_UNSAFE"
    # a list that carries a DTD or an entity is refused outright (XXE and entity-expansion), signed or not
    bomb = b'<?xml version="1.0"?><!DOCTYPE a [<!ENTITY x "boom">]><a>&x;</a>'
    assert tr.parse_trusted_list(bomb, [signer.cert]).problem == "NOT_XML_OR_UNSAFE"
    external = b'<?xml version="1.0"?><!DOCTYPE a SYSTEM "file:///etc/passwd"><a/>'
    assert tr.parse_trusted_list(external, [signer.cert]).problem == "NOT_XML_OR_UNSAFE"
    assert tr.parse_trusted_list(signed, []).authenticated is False, (
        "with no pinned signer nothing can be authenticated"
    )


# ---------------------------------------------------------------------------------------------------------- qualified status


def status(p, xml: bytes, signer: Party, at=NOW):
    cfg = tr.TrustConfig(anchors=(p.root.cert,), trusted_lists=(authenticated(xml, signer),))
    return tr.qualified_status(chain_of(p), cfg, at)


def test_a_granted_qualified_ca_and_a_qc_compliant_certificate_are_qualified_for_signatures() -> (
    None
):
    p, signer = qualified_pki("esign"), list_signer()
    q = status(p, trusted_list_xml(p.root, sign_with=signer), signer)
    assert q.status == "QUALIFIED_ESIG" and q.reasons == ()


def test_a_seal_certificate_is_qualified_as_a_seal_not_a_signature() -> None:
    p, signer = qualified_pki("eseal"), list_signer()
    assert status(p, trusted_list_xml(p.root, sign_with=signer), signer).status == "QUALIFIED_ESEAL"


def test_a_certificate_without_qc_compliance_is_not_qualified_even_under_a_granted_service() -> (
    None
):
    p, signer = qualified_pki(compliance=False), list_signer()
    q = status(p, trusted_list_xml(p.root, sign_with=signer), signer)
    assert q.status == "NOT_QUALIFIED" and "CERTIFICATE_LACKS_QC_COMPLIANCE" in q.reasons
    plain = pki()  # no QcStatements at all
    q2 = tr.qualified_status(
        chain_of(plain),
        tr.TrustConfig(
            anchors=(plain.root.cert,),
            trusted_lists=(authenticated(trusted_list_xml(plain.root, sign_with=signer), signer),),
        ),
        NOW,
    )
    assert q2.status == "NOT_QUALIFIED"


def test_t21_one_pades_document_is_crypto_valid_and_not_qualified_when_the_certificate_lacks_qc_compliance() -> (
    None
):
    p, signer = qualified_pki(compliance=False), list_signer()
    pdf = sign_pdf(p)
    cfg = tr.TrustConfig(
        anchors=(p.root.cert,),
        trusted_lists=(authenticated(trusted_list_xml(p.root, sign_with=signer), signer),),
    )
    v = docs.validate(
        pdf, declared_type="application/pdf", expected_sha256=None, checks=docs.CHECKS
    )
    by = {r.check: r for r in sg.apply(v, pdf, cfg, at=NOW).results}
    # the cryptography is sound and chains to the configured anchor ...
    assert by["signature"].status is S.VERIFIED_LIMITED
    assert {"CRYPTOGRAPHICALLY_VALID", "CHAIN_TO_CONFIGURED_ANCHOR"} <= set(by["signature"].reasons)
    # ... and, on the very same document, the certificate is not qualified
    assert by["seal"].detail["qualified_status"] == "NOT_QUALIFIED"
    assert by["seal"].status is S.INDETERMINATE
    assert "CERTIFICATE_LACKS_QC_COMPLIANCE" in by["seal"].reasons


def test_a_ca_that_is_not_on_the_list_is_not_qualified() -> None:
    p, other, signer = qualified_pki(), qualified_pki(), list_signer()
    q = tr.qualified_status(
        chain_of(p),
        tr.TrustConfig(
            anchors=(p.root.cert,),
            trusted_lists=(authenticated(trusted_list_xml(other.root, sign_with=signer), signer),),
        ),
        NOW,
    )
    assert q.status == "NOT_QUALIFIED" and q.reasons == ("CA_NOT_ON_A_LIST",)


def test_a_service_withdrawn_now_but_granted_at_the_signing_time_was_qualified_then() -> None:
    p, signer = qualified_pki(), list_signer()
    withdrawn_on = datetime(2025, 6, 1, tzinfo=UTC)
    xml = trusted_list_xml(
        p.root,
        status=WITHDRAWN,
        since=withdrawn_on,
        history=[(GRANTED, datetime(2020, 1, 1, tzinfo=UTC))],
        sign_with=signer,
    )
    before = status(p, xml, signer, at=datetime(2024, 1, 1, tzinfo=UTC))
    after = status(p, xml, signer, at=NOW)
    assert before.status == "QUALIFIED_ESIG", "interpreted at the time of signing, not today"
    assert after.status == "NOT_QUALIFIED" and "SERVICE_NOT_GRANTED_AT_THE_TIME" in after.reasons


def test_a_service_that_did_not_exist_yet_at_the_signing_time_is_not_qualified() -> None:
    p, signer = qualified_pki(), list_signer()
    xml = trusted_list_xml(p.root, since=datetime(2024, 1, 1, tzinfo=UTC), sign_with=signer)
    assert status(p, xml, signer, at=datetime(2023, 1, 1, tzinfo=UTC)).status == "NOT_QUALIFIED"


def test_without_an_authenticated_list_qualified_status_is_not_assessed() -> None:
    p = qualified_pki()
    assert (
        tr.qualified_status(chain_of(p), tr.TrustConfig(anchors=(p.root.cert,)), NOW).status
        == "NOT_ASSESSED"
    )
    unauthenticated = tr.TrustedList("x.xml", False, problem="LIST_SIGNATURE_NOT_VERIFIED")
    assert (
        tr.qualified_status(
            chain_of(p), tr.TrustConfig(trusted_lists=(unauthenticated,)), NOW
        ).status
        == "NOT_ASSESSED"
    )


# ---------------------------------------------------------------------------------------------------------- the seal result


def test_the_seal_result_is_unavailable_without_a_list_and_verified_limited_with_one() -> None:
    p, signer = qualified_pki(), list_signer()
    pdf = sign_pdf(p)
    bare = tr.TrustConfig(anchors=(p.root.cert,))
    f = cms.inspect_pdf(pdf, anchors=bare.anchors)
    none = sg.seal_result(f, bare, NOW)
    assert (
        none.status is S.UNAVAILABLE
        and none.detail["qualified_status"] == "NOT_ASSESSED"
        and "NO_TRUST_LIST" in none.reasons
    )
    cfg = tr.TrustConfig(
        anchors=(p.root.cert,),
        trusted_lists=(authenticated(trusted_list_xml(p.root, sign_with=signer), signer),),
    )
    seal = sg.seal_result(cms.inspect_pdf(pdf, anchors=cfg.anchors), cfg, NOW)
    assert seal.status is S.VERIFIED_LIMITED and seal.detail["qualified_status"] == "QUALIFIED_ESIG"
    assert "REVOCATION_NOT_CHECKED" in seal.reasons and seal.detail["revocation_checked"] is False


def test_an_untrusted_signature_cannot_be_assessed_for_qualified_status() -> None:
    p, other, signer = qualified_pki(), qualified_pki(), list_signer()
    cfg = tr.TrustConfig(
        anchors=(other.root.cert,),
        trusted_lists=(authenticated(trusted_list_xml(p.root, sign_with=signer), signer),),
    )
    seal = sg.seal_result(cms.inspect_pdf(sign_pdf(p), anchors=cfg.anchors), cfg, NOW)
    assert seal.status is S.UNAVAILABLE and "NO_TRUSTED_SIGNATURE_TO_ASSESS" in seal.reasons


# ---------------------------------------------------------------------------------------------------------- loading from a folder


def test_the_loader_reads_anchors_signers_lists_tsa_roots_and_crls_and_an_empty_folder_trusts_nothing(
    tmp_path,
) -> None:
    p, signer = qualified_pki(), list_signer()
    empty = tr.load_trust(tmp_path / "nowhere")
    assert (
        empty.anchors == ()
        and empty.trusted_lists == ()
        and "NO_TRUST_ANCHORS_CONFIGURED" in empty.notes()
    )
    for folder in ("anchors", "tl-signers", "trusted-lists", "tsa-roots", "crls"):
        (tmp_path / folder).mkdir()
    (tmp_path / "anchors" / "root.pem").write_bytes(p.root.cert.public_bytes(Encoding.PEM))
    (tmp_path / "anchors" / "der.cer").write_bytes(pki().root.cert.public_bytes(Encoding.DER))
    (tmp_path / "anchors" / "junk.pem").write_bytes(b"not a certificate")
    (tmp_path / "tl-signers" / "s.pem").write_bytes(signer.pem())
    (tmp_path / "trusted-lists" / "eu.xml").write_bytes(trusted_list_xml(p.root, sign_with=signer))
    (tmp_path / "trusted-lists" / "forged.xml").write_bytes(
        trusted_list_xml(p.root, sign_with=list_signer())
    )
    cfg = tr.load_trust(tmp_path)
    assert len(cfg.anchors) == 2, "a file that is not a certificate is skipped, never trusted"
    assert {t.name: t.authenticated for t in cfg.trusted_lists} == {
        "eu.xml": True,
        "forged.xml": False,
    }
    assert cfg.notes() == ["NO_TSA_ROOTS_CONFIGURED", "NO_CRLS_SUPPLIED"]


def test_the_shipped_configuration_trusts_nothing() -> None:
    from zid_platform.modules.evidence.infrastructure.trust_config import CONFIG

    shipped = tr.load_trust(CONFIG / "trust")
    assert (
        shipped.anchors == ()
        and shipped.trusted_lists == ()
        and shipped.tsa_roots == ()
        and shipped.crls == ()
    )
    assert timedelta(0) < NOW - datetime(2000, 1, 1, tzinfo=UTC)
