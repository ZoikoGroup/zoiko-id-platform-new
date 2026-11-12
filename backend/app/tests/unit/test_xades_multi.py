"""CR-073 A3: more than one signature per XML document, and the signature-wrapping attacks that make this dangerous.

signxml verifies only the FIRST signature it finds, so documents with several are judged by `dsig`, a strict engine of our own, with signxml
kept as an independent cross-check on the first. Every attack below changes exactly one property of a validly signed document.
"""

from __future__ import annotations

from pdf_signing import pki
from xades_signing import sign_xml
from zid_platform.modules.evidence.application import cms, dsig, xades
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs

BODY = '<data Id="payload">v1</data><other Id="other">x</other>'


def two(p, q, *, body: str = BODY) -> bytes:
    first = sign_xml(p.signer, body=body, over_id="payload")
    return sign_xml(q.signer, base=first, n=2, over_id="payload")


def run(xml: bytes, *pkis) -> list[cms.Finding]:
    return xades.inspect_xml(xml, anchors=tuple(x.root.cert for x in pkis))


def test_two_independent_signatures_are_each_judged_on_their_own_terms() -> None:
    p, q = pki(), pki()
    found = run(two(p, q), p, q)
    assert [f.index for f in found] == [0, 1]
    assert all(f.cryptographically_valid and f.chain is cms.Chain.TRUSTED for f in found)
    assert not any(
        f.problems and f.problems != ("REFERENCE_DOES_NOT_COVER_DOCUMENT",) for f in found
    )
    assert found[0].covered_ids == ("payload", "sp-1") and found[1].covered_ids == (
        "payload",
        "sp-2",
    )
    assert found[0].signer_serial != found[1].signer_serial


def test_a_signature_over_one_element_is_never_reported_as_covering_the_document() -> None:
    p, q = pki(), pki()
    found = run(two(p, q), p, q)
    assert all(not f.covers_whole_file for f in found)
    r = sg.signature_result(found, tr.TrustConfig(anchors=(p.root.cert, q.root.cert)))
    assert (
        r.status is docs.Status.INDETERMINATE and "REFERENCE_DOES_NOT_COVER_DOCUMENT" in r.reasons
    )
    assert r.detail["signatures"][0]["covered_ids"] == [
        "payload",
        "sp-1",
    ]  # the caller can see what was signed


def test_one_untrusted_signer_does_not_hide_behind_a_trusted_one() -> None:
    p, q = pki(), pki()
    found = run(two(p, q), p)  # only p's root is configured
    assert found[0].chain is cms.Chain.TRUSTED and found[1].chain is cms.Chain.UNTRUSTED


def test_changing_the_signed_element_breaks_every_signature_over_it() -> None:
    p, q = pki(), pki()
    xml = two(p, q).replace(b">v1<", b">v2<")
    assert all(not f.digest_ok for f in run(xml, p, q))


def test_changing_one_signatures_own_properties_breaks_only_that_signature() -> None:
    p, q = pki(), pki()
    xml = two(p, q)
    at = xml.index(b'Id="sp-2"')
    tampered = xml[:at] + xml[at:].replace(b"Z</", b"Y</", 1)  # the second signature's SigningTime
    first, second = run(tampered, p, q)
    assert first.cryptographically_valid and not second.digest_ok


def test_a_flipped_value_in_the_second_signature_is_caught() -> None:
    p, q = pki(), pki()
    xml = bytearray(two(p, q))
    at = bytes(xml).index(b'Id="sigvalue-2"')
    start = bytes(xml).index(b">", at) + 1
    xml[start + 10] = ord("A") if xml[start + 10] != ord("A") else ord("B")
    first, second = run(bytes(xml), p, q)
    assert first.cryptographically_valid and not second.signature_ok


# ------------------------------------------------------------------------------------------------ wrapping attacks


def test_a_second_element_with_the_same_id_is_refused_not_chosen_between() -> None:
    p = pki()
    xml = sign_xml(p.signer, body=BODY, over_id="payload")
    evil = xml.replace(b"</doc>", b'<data Id="payload">EVIL</data></doc>')
    f = run(evil, p)[0]
    assert not f.digest_ok and "DUPLICATE_ID" in f.problems


def test_moving_the_signed_element_aside_and_putting_another_in_its_place_is_never_verified_as_the_document() -> (
    None
):
    """The classic wrap: the signed element is hidden (here, inside the signature's Object) and a forged one sits where the application
    reads. The signature itself still verifies, but it covers one element, never the document, and the result says which."""
    p = pki()
    xml = sign_xml(p.signer, body=BODY, over_id="payload")
    wrapped = xml.replace(b'<data Id="payload">v1</data>', b'<data Id="forged">FORGED</data>', 1)
    # the original element is re-added where it cannot be mistaken for the document body
    wrapped = wrapped.replace(
        b"</ds:Object>", b'<hidden><data Id="payload">v1</data></hidden></ds:Object>', 1
    )
    f = run(wrapped, p)[0]
    assert f.digest_ok and f.signature_ok  # the cryptography cannot tell where the element sits ...
    assert not f.covers_whole_file and f.covered_ids == (
        "payload",
        "sp-1",
    )  # ... so the result must, and does
    r = sg.signature_result([f], tr.TrustConfig(anchors=(p.root.cert,)))
    assert r.status is docs.Status.INDETERMINATE
    assert "forged" not in str(r.detail["signatures"][0]["covered_ids"])


def test_a_reference_to_an_id_that_is_not_there_fails() -> None:
    p = pki()
    xml = sign_xml(p.signer, body=BODY, over_id="payload").replace(
        b'Id="payload"', b'Id="moved"', 1
    )
    f = run(xml, p)[0]
    assert not f.digest_ok and "REFERENCE_TARGET_NOT_FOUND" in f.problems


def test_xpath_and_other_transforms_are_refused() -> None:
    p = pki()
    xml = sign_xml(p.signer, body=BODY, over_id="payload")
    xpath = xml.replace(
        b'http://www.w3.org/2001/10/xml-exc-c14n#"/></ds:Transforms>',
        b'http://www.w3.org/TR/1999/REC-xpath-19991116"/></ds:Transforms>',
        1,
    )
    assert xpath != xml
    f = run(xpath, p)[0]
    assert not f.algorithm_ok and "UNSUPPORTED_TRANSFORM" in f.problems


def test_an_external_reference_is_refused() -> None:
    p = pki()
    xml = sign_xml(p.signer, body=BODY, over_id="payload").replace(
        b'URI="#payload"', b'URI="http://attacker.example/x.xml"', 1
    )
    f = run(xml, p)[0]
    assert not f.algorithm_ok and "UNSUPPORTED_REFERENCE_URI" in f.problems


def test_signed_properties_that_are_not_among_the_signed_ids_make_the_signature_unreliable() -> (
    None
):
    p = pki()
    f = run(sign_xml(p.signer, body=BODY, over_id="payload", sign_properties=False), p)[0]
    assert "SIGNED_PROPERTIES_NOT_SIGNED" in f.problems and not f.cryptographically_valid


def test_the_engine_and_the_independent_library_agree_on_a_whole_document_signature() -> None:
    p = pki()
    xml = sign_xml(p.signer)
    f = xades.inspect_xml(xml, anchors=(p.root.cert,))[0]
    assert f.cryptographically_valid and "VERIFIER_DISAGREEMENT" not in f.problems
    # and they agree it is broken when it is
    broken = xades.inspect_xml(xml.replace(b"hello", b"HELLO"), anchors=(p.root.cert,))[0]
    assert not broken.digest_ok


def test_dsig_refuses_a_signature_index_that_does_not_exist() -> None:
    p = pki()
    from lxml import etree

    root = etree.fromstring(sign_xml(p.signer))
    assert dsig.verify(root, 5, p.signer.cert).problems == ("SIGNATURE_NOT_FOUND",)
