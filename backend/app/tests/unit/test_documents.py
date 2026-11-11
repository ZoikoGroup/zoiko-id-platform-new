"""ZID-INT-002 section 09 (T26): document checks report each result on its own and never one positive status."""

from __future__ import annotations

import hashlib

import pytest
from zid_platform.modules.evidence.domain import documents as d

PLAIN_PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"
SIGNED_PDF = (
    b"%PDF-1.7\n1 0 obj << /Type /Sig /Filter /Adobe.PPKLite /SubFilter /ETSI.CAdES.detached "
    b"/ByteRange [0 100 200 50] /Contents <00> >> endobj\n%%EOF\n"
)
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
CT = {"pdf": "application/pdf", "png": "image/png", "jpeg": "image/jpeg"}


def run(
    content: bytes, kind: str = "application/pdf", expected: str | None = None, checks=d.CHECKS
):
    return {
        r.check: r
        for r in d.validate(
            content, declared_type=kind, expected_sha256=expected, checks=checks
        ).results
    }


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def test_the_type_is_what_the_bytes_are_not_what_the_caller_says() -> None:
    assert (
        d.sniff(PLAIN_PDF) == "application/pdf"
        and d.sniff(PNG) == "image/png"
        and d.sniff(JPEG) == "image/jpeg"
    )
    assert d.sniff(b"MZ\x90\x00 an executable") is None and d.sniff(b"") is None
    r = run(PNG, "application/pdf")["integrity"]
    assert r.status is d.Status.NOT_VERIFIED and "TYPE_MISMATCH" in r.reasons


def test_integrity_means_unchanged_bytes_and_says_nothing_more() -> None:
    ok = run(PLAIN_PDF, expected=sha(PLAIN_PDF))["integrity"]
    assert (
        ok.status is d.Status.VERIFIED
        and ok.reasons == ("BYTES_UNCHANGED_ONLY",)
        and ok.detail["unchanged_bytes_only"] is True
    )
    assert run(PLAIN_PDF + b" ", expected=sha(PLAIN_PDF))["integrity"].reasons == (
        "DIGEST_MISMATCH",
    )
    none = run(PLAIN_PDF)["integrity"]
    assert none.status is d.Status.INDETERMINATE and none.reasons == (
        "NO_REFERENCE_DIGEST",
    )  # nothing to compare is not a pass
    assert run(PLAIN_PDF, expected=sha(PLAIN_PDF).upper())["integrity"].status is d.Status.VERIFIED


def test_t26_a_signature_is_detected_but_never_validated_so_the_document_is_never_issuer_authentic() -> (
    None
):
    r = run(SIGNED_PDF)
    sig = r["signature"]
    assert sig.status is d.Status.INDETERMINATE and "SIGNATURE_PRESENT_NOT_VALIDATED" in sig.reasons
    assert sig.detail["signatures_detected"] == 1
    assert (
        sig.detail["chain_checked"] is False
        and sig.detail["revocation_checked"] is False
        and sig.detail["signing_time_checked"] is False
    )
    assert r["issuer"].status is d.Status.UNAVAILABLE and "NO_ISSUER_SOURCE" in r["issuer"].reasons
    seal = r["seal"]
    assert (
        seal.status is d.Status.UNAVAILABLE
        and "NO_TRUST_LIST" in seal.reasons
        and seal.detail["qualified_status"] == "NOT_ASSESSED"
    )
    assert seal.detail["etsi_subfilter_named"] is True


@pytest.mark.parametrize("content", [PLAIN_PDF, SIGNED_PDF, PNG, JPEG])
def test_no_check_about_authority_is_ever_verified(content: bytes) -> None:
    kind = d.sniff(content) or ""
    for name in ("issuer", "signature", "seal"):
        assert run(content, kind)[name].status is not d.Status.VERIFIED


def test_an_unsigned_pdf_and_an_image_are_reported_for_what_they_are() -> None:
    assert run(PLAIN_PDF)["signature"].reasons == ("NO_SIGNATURE_FOUND",)
    assert run(PNG, "image/png")["signature"].reasons == ("NOT_APPLICABLE_TO_TYPE",)
    assert run(PNG, "image/png")["seal"].reasons == ("NOT_APPLICABLE_TO_TYPE",)


def test_a_signature_word_without_a_byterange_is_not_a_signature() -> None:
    assert d.signatures_present(b"%PDF-1.7 /Type /Sig but no range") == (0, False)


def test_only_the_requested_checks_run_once_each() -> None:
    v = d.validate(
        PLAIN_PDF,
        declared_type="application/pdf",
        expected_sha256=None,
        checks=["issuer", "integrity", "issuer"],
    )
    assert [r.check for r in v.results] == ["issuer", "integrity"]


@pytest.mark.parametrize(
    ("content", "kind", "checks", "code"),
    [
        (b"", "application/pdf", ["integrity"], "EMPTY_DOCUMENT"),
        (PLAIN_PDF, "application/zip", ["integrity"], "TYPE_NOT_ALLOWED"),
        (PLAIN_PDF, "application/pdf", ["authentic"], "CHECK_NOT_SUPPORTED"),
        (PLAIN_PDF, "application/pdf", [], "CHECK_NOT_SUPPORTED"),
    ],
)
def test_refusals_come_before_any_result(
    content: bytes, kind: str, checks: list[str], code: str
) -> None:
    with pytest.raises(d.DocumentError) as e:
        d.validate(content, declared_type=kind, expected_sha256=None, checks=checks)
    assert e.value.code == code


def test_a_document_over_the_cap_is_refused() -> None:
    with pytest.raises(d.DocumentError) as e:
        d.validate(
            PLAIN_PDF,
            declared_type="application/pdf",
            expected_sha256=None,
            checks=["integrity"],
            max_bytes=10,
        )
    assert e.value.code == "DOCUMENT_TOO_LARGE"
