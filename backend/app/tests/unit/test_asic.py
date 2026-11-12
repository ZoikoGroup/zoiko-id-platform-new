"""ASiC-S and ASiC-E containers with CAdES signatures, one broken property at a time (ETSI EN 319 162; ZID-UAT-005 DOC-01 to DOC-09)."""

from __future__ import annotations

import base64
import hashlib
import io
import zipfile

from pdf_signing import Pki, pki
from test_cades import detached
from zid_platform.modules.evidence.application import asic
from zid_platform.modules.evidence.application import signatures as sg
from zid_platform.modules.evidence.application import trust as tr
from zid_platform.modules.evidence.domain import documents as docs
from zid_platform.modules.evidence.domain import intake

DATA = b"The agreement text, signed inside a container.\n" * 10


def container(
    kind: str,
    files: list[tuple[str, bytes]],
    *,
    mimetype_first: bool = True,
    compress_mimetype: bool = False,
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        order = (
            [("mimetype", kind.encode()), *files]
            if mimetype_first
            else [*files, ("mimetype", kind.encode())]
        )
        for name, body in order:
            ctype = (
                zipfile.ZIP_DEFLATED
                if (name != "mimetype" or compress_mimetype)
                else zipfile.ZIP_STORED
            )
            z.writestr(zipfile.ZipInfo(name), body, compress_type=ctype)
    return buf.getvalue()


def asic_s(
    p: Pki,
    data: bytes = DATA,
    *,
    sign: bytes | None = None,
    extra: list[tuple[str, bytes]] | None = None,
) -> bytes:
    sig = detached(p, DATA) if sign is None else sign
    return container(
        asic.ASIC_S, [("doc.txt", data), ("META-INF/signature.p7s", sig), *(extra or [])]
    )


def manifest(
    refs: list[tuple[str, bytes]],
    algorithm: str = "http://www.w3.org/2001/04/xmlenc#sha256",
    digest=hashlib.sha256,
) -> bytes:
    items = "".join(
        f'<DataObjectReference URI="{n}"><DigestMethod Algorithm="{algorithm}"/><DigestValue>{base64.b64encode(digest(b).digest()).decode()}</DigestValue></DataObjectReference>'
        for n, b in refs
    )
    return f'<?xml version="1.0"?><ASiCManifest xmlns="http://uri.etsi.org/02918/v1.2.1#">{items}</ASiCManifest>'.encode()


def asic_e(p: Pki, files: dict[str, bytes], signed: dict[str, bytes] | None = None, **mk) -> bytes:
    man = manifest(list((signed if signed is not None else files).items()), **mk)
    return container(
        asic.ASIC_E,
        [
            *files.items(),
            ("META-INF/ASiCManifest.xml", man),
            ("META-INF/signature.p7s", detached(p, man)),
        ],
    )


def validate(content: bytes, kind: str, p: Pki | None):
    v = docs.validate(
        content, declared_type=kind, expected_sha256=None, checks=["integrity", "signature", "seal"]
    )
    cfg = tr.TrustConfig(anchors=(p.root.cert,)) if p else tr.TrustConfig()
    out = sg.apply(v, content, cfg)
    return {r.check: r for r in out.results}


def test_an_asic_is_recognised_by_its_stored_mimetype_entry_and_a_plain_zip_is_not() -> None:
    p = pki()
    assert docs.sniff(asic_s(p)) == asic.ASIC_S
    assert docs.sniff(asic_e(p, {"a.txt": b"x"})) == asic.ASIC_E
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", b"x")
    assert docs.sniff(plain.getvalue()) is None
    assert asic.mimetype_of(asic_s(p)) == asic.ASIC_S


def test_an_asic_is_a_validation_type_but_never_an_upload_type() -> None:
    assert asic.ASIC_S in docs.VALIDATION_TYPES and asic.ASIC_E in docs.VALIDATION_TYPES
    assert asic.ASIC_S not in docs.ALLOWED_TYPES and asic.ASIC_E not in docs.ALLOWED_TYPES
    assert asic.ASIC_S not in intake.ALLOWED_TYPES if hasattr(intake, "ALLOWED_TYPES") else True


def test_a_valid_asic_s_is_verified_at_the_level_its_chain_supports() -> None:
    p = pki()
    r = validate(asic_s(p), asic.ASIC_S, p)
    assert (
        r["integrity"].status is docs.Status.INDETERMINATE
        and "TYPE_MISMATCH" not in r["integrity"].reasons
    )
    assert r["signature"].status is docs.Status.VERIFIED_LIMITED, r["signature"]
    assert r["signature"].detail["container"] == asic.ASIC_S and r["signature"].detail[
        "data_files"
    ] == ["doc.txt"]


def test_with_no_anchor_the_asic_signature_is_sound_but_not_relied_on() -> None:
    assert (
        validate(asic_s(pki()), asic.ASIC_S, None)["signature"].status is docs.Status.INDETERMINATE
    )


def test_a_changed_data_file_in_an_asic_s_is_not_verified() -> None:
    p = pki()
    r = validate(asic_s(p, b"The agreement text, edited after signing.\n"), asic.ASIC_S, p)
    assert r["signature"].status is docs.Status.NOT_VERIFIED


def test_an_asic_s_with_two_data_files_or_no_signature_is_refused_by_name() -> None:
    p = pki()
    two = validate(asic_s(p, extra=[("other.txt", b"y")]), asic.ASIC_S, p)["signature"]
    assert (
        two.status is docs.Status.NOT_VERIFIED
        and "ASIC_S_NEEDS_EXACTLY_ONE_DATA_FILE" in two.reasons
    )
    none = container(asic.ASIC_S, [("doc.txt", DATA)])
    r = validate(none, asic.ASIC_S, p)["signature"]
    assert r.status is docs.Status.NOT_VERIFIED and "NO_SIGNATURE_IN_CONTAINER" in r.reasons


def test_a_valid_asic_e_signs_a_manifest_and_every_listed_file_must_match_its_digest() -> None:
    p = pki()
    files = {"a.txt": b"first file", "b.txt": b"second file"}
    r = validate(asic_e(p, files), asic.ASIC_E, p)["signature"]
    assert r.status is docs.Status.VERIFIED_LIMITED, r
    changed = validate(
        asic_e(p, {**files, "b.txt": b"second file, edited"}, signed=files), asic.ASIC_E, p
    )["signature"]
    assert (
        changed.status is docs.Status.NOT_VERIFIED and "DATA_FILE_CHANGED:b.txt" in changed.reasons
    )


def test_a_data_file_no_manifest_names_is_reported_unsigned_never_assumed_covered() -> None:
    p = pki()
    r = validate(
        asic_e(p, {"a.txt": b"signed", "extra.txt": b"smuggled"}, signed={"a.txt": b"signed"}),
        asic.ASIC_E,
        p,
    )["signature"]
    assert r.status is docs.Status.NOT_VERIFIED and "DATA_FILE_NOT_SIGNED:extra.txt" in r.reasons


def test_a_manifest_naming_a_missing_file_a_sha1_digest_or_a_doctype_is_refused() -> None:
    p = pki()
    missing = validate(
        asic_e(p, {"a.txt": b"x"}, signed={"a.txt": b"x", "gone.txt": b"y"}), asic.ASIC_E, p
    )["signature"]
    assert "REFERENCED_FILE_MISSING:gone.txt" in missing.reasons
    sha1 = validate(
        asic_e(
            p,
            {"a.txt": b"x"},
            algorithm="http://www.w3.org/2000/09/xmldsig#sha1",
            digest=hashlib.sha1,
        ),
        asic.ASIC_E,
        p,
    )["signature"]
    assert (
        sha1.status is docs.Status.NOT_VERIFIED
        and "DIGEST_ALGORITHM_UNSUPPORTED:a.txt" in sha1.reasons
    )
    evil = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e "boom">]><ASiCManifest>&e;</ASiCManifest>'
    c = container(
        asic.ASIC_E,
        [
            ("a.txt", b"x"),
            ("META-INF/ASiCManifest.xml", evil),
            ("META-INF/signature.p7s", detached(p, evil)),
        ],
    )
    unsafe = validate(c, asic.ASIC_E, p)["signature"]
    assert unsafe.status is docs.Status.NOT_VERIFIED and any(
        x.startswith("MANIFEST_UNSAFE") for x in unsafe.reasons
    )


def test_an_asic_e_with_no_manifest_or_a_manifest_with_no_signature_is_refused() -> None:
    p = pki()
    assert (
        "ASIC_E_HAS_NO_MANIFEST"
        in validate(container(asic.ASIC_E, [("a.txt", b"x")]), asic.ASIC_E, p)["signature"].reasons
    )
    man = manifest([("a.txt", b"x")])
    c = container(asic.ASIC_E, [("a.txt", b"x"), ("META-INF/ASiCManifest.xml", man)])
    assert (
        "MANIFEST_HAS_NO_SIGNATURE:META-INF/ASiCManifest.xml"
        in validate(c, asic.ASIC_E, p)["signature"].reasons
    )


def test_xades_and_timestamp_tokens_in_a_container_are_reported_unsupported_not_guessed_at() -> (
    None
):
    p = pki()
    c = container(asic.ASIC_S, [("doc.txt", DATA), ("META-INF/signatures.xml", b"<x/>")])
    r = validate(c, asic.ASIC_S, p)["signature"]
    assert r.status is docs.Status.INDETERMINATE and "XADES_IN_ASIC_NOT_SUPPORTED" in r.reasons
    tst = container(asic.ASIC_S, [("doc.txt", DATA), ("META-INF/timestamp.tst", b"\x30\x00")])
    assert (
        "ASIC_TIMESTAMP_TOKEN_NOT_SUPPORTED" in validate(tst, asic.ASIC_S, p)["signature"].reasons
    )


def test_a_hostile_container_is_refused_before_anything_in_it_is_read() -> None:
    p = pki()
    late = container(
        asic.ASIC_S,
        [("doc.txt", DATA), ("META-INF/signature.p7s", detached(p, DATA))],
        mimetype_first=False,
    )
    assert docs.sniff(late) is None, (
        "the type is read from the first entry: a late mimetype is not an ASiC"
    )
    escape = container(asic.ASIC_S, [("../evil.txt", b"x"), ("META-INF/signature.p7s", b"x")])
    assert (
        validate(escape, asic.ASIC_S, p)["signature"].reasons[0]
        == "ENTRY_NAME_ESCAPES_THE_CONTAINER"
    )
    bomb = container(
        asic.ASIC_S, [("doc.txt", b"\x00" * (3 * 1024 * 1024)), ("META-INF/signature.p7s", b"x")]
    )
    assert validate(bomb, asic.ASIC_S, p)["signature"].reasons[0] == "COMPRESSION_RATIO_TOO_HIGH"
    many = container(asic.ASIC_S, [(f"f{i}.txt", b"x") for i in range(asic.MAX_ENTRIES + 5)])
    assert validate(many, asic.ASIC_S, p)["signature"].reasons[0] == "TOO_MANY_ENTRIES"


def test_an_asic_declared_as_something_else_or_a_zip_declared_as_an_asic_is_a_type_mismatch() -> (
    None
):
    p = pki()
    v = docs.validate(
        asic_s(p), declared_type="application/pdf", expected_sha256=None, checks=["integrity"]
    )
    assert v.results[0].reasons == ("TYPE_MISMATCH",)
    plain = io.BytesIO()
    with zipfile.ZipFile(plain, "w") as z:
        z.writestr("a.txt", b"x")
    v2 = docs.validate(
        plain.getvalue(), declared_type=asic.ASIC_S, expected_sha256=None, checks=["integrity"]
    )
    assert v2.results[0].reasons == ("TYPE_MISMATCH",)
