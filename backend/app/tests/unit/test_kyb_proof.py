"""Proof documents for a company claim: the rules, the policy and the scanner hook (WF-13B AT-12). The routes are in test_org_claim_proofs_pg."""

from __future__ import annotations

import asyncio
import struct
from collections.abc import Awaitable, Callable

import pytest
from zid_platform.modules.organization.domain.proof import (
    ProofError,
    ProofPolicy,
    check_proof,
    sanitize_name,
    sniff,
)
from zid_platform.modules.organization.infrastructure.proof_config import parse, proof_policy
from zid_platform.modules.organization.infrastructure.proof_scan import (
    ClamdScanner,
    ScanStatus,
    from_address,
)

POLICY = ProofPolicy(
    "t",
    1024,
    ("application/pdf", "image/png", "image/jpeg"),
    ("CERTIFICATE_OF_INCORPORATION", "OTHER"),
    3,
)
PDF = b"%PDF-1.7\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< /Root 1 0 R >>\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
KIND = "CERTIFICATE_OF_INCORPORATION"


def check(
    content: bytes, declared: str | None = None, kind: str = KIND, policy: ProofPolicy = POLICY
) -> str:
    return check_proof(content, declared_type=declared, kind=kind, policy=policy)


def refused(content: bytes, **kw: object) -> str:
    with pytest.raises(ProofError) as caught:
        check(content, **kw)  # type: ignore[arg-type]
    return caught.value.code


def test_the_type_comes_from_the_files_own_bytes() -> None:
    assert (sniff(PDF), sniff(PNG), sniff(JPEG), sniff(b"MZ\x90\x00")) == (
        "application/pdf",
        "image/png",
        "image/jpeg",
        None,
    )
    assert (
        check(PDF) == "application/pdf"
        and check(PNG) == "image/png"
        and check(JPEG) == "image/jpeg"
    )
    # a declared type that agrees (with or without parameters) is fine; one that disagrees is not
    assert (
        check(PDF, "application/pdf")
        == check(PDF, "Application/PDF; charset=binary")
        == "application/pdf"
    )
    assert refused(PNG, declared="application/pdf") == "PROOF_TYPE_MISMATCH"


def test_a_program_or_archive_renamed_to_a_pdf_is_refused() -> None:
    for body in (
        b"MZ\x90\x00" + b"\x00" * 60,
        b"PK\x03\x04" + b"\x00" * 60,
        b"#!/bin/sh\nrm -rf /\n",
        b"<html><script>x</script></html>",
        b"GIF89a" + b"\x00" * 20,
    ):
        assert refused(body, declared="application/pdf") == "PROOF_TYPE_NOT_ALLOWED", body[:6]


def test_a_type_the_policy_does_not_list_is_refused_even_if_it_is_a_known_signature() -> None:
    only_pdf = ProofPolicy("t", 1024, ("application/pdf",), (KIND,), 3)
    assert refused(PNG, policy=only_pdf) == "PROOF_TYPE_NOT_ALLOWED"  # type: ignore[arg-type]


def test_empty_oversized_and_unknown_kind_are_refused_in_a_fixed_order() -> None:
    assert refused(b"") == "PROOF_EMPTY"
    assert refused(PDF + b"x" * 1100) == "PROOF_TOO_LARGE"
    assert refused(PDF, kind="SELFIE") == "PROOF_KIND_UNKNOWN"
    assert (
        refused(b"", kind="SELFIE") == "PROOF_KIND_UNKNOWN"
    )  # the kind is judged before the bytes


@pytest.mark.parametrize(
    "name",
    [
        "/JavaScript",
        "/JS",
        "/Launch",
        "/EmbeddedFile",
        "/OpenAction",
        "/AA",
        "/RichMedia",
        "/XFA",
        "/SubmitForm",
    ],
)
def test_a_pdf_that_can_run_something_is_refused(name: str) -> None:
    active = PDF.replace(b"/Type /Catalog", b"/Type /Catalog " + name.encode() + b" 2 0 R")
    assert refused(active) == "PROOF_ACTIVE_CONTENT"


def test_a_truncated_pdf_is_refused_but_an_ordinary_one_passes() -> None:
    assert refused(PDF.replace(b"%%EOF\n", b"")) == "PROOF_MALFORMED"
    assert (
        check(b"%PDF-1.4\n/Type /Page /Annots []\n%%EOF") == "application/pdf"
    )  # words that merely start like an active name


def test_the_applicants_file_name_is_never_kept_as_given() -> None:
    assert sanitize_name("../../etc/passwd") == "passwd"
    assert (
        sanitize_name("C:\\Users\\a\\Certificate of Incorporation.pdf")
        == "Certificate of Incorporation.pdf"
    )
    assert (
        sanitize_name('evil"><script>alert(1)</script>.pdf') == "script.pdf"
    )  # the slash made it a path: only the last part is kept
    assert (
        sanitize_name("\x00\n\r") == "document"
        and sanitize_name(None) == "document"
        and sanitize_name("...") == "document"
    )
    assert len(sanitize_name("a" * 500 + ".pdf")) <= 100


def test_the_shipped_policy_is_loaded_and_a_bad_one_is_refused() -> None:
    p = proof_policy()
    assert p.max_bytes == 10 * 1024 * 1024 and set(p.types) == {
        "application/pdf",
        "image/png",
        "image/jpeg",
    }
    assert "OTHER" in p.kinds and p.per_case_limit >= 1
    for bad in (
        {"types": [], "kinds": ["X"], "max_bytes": 1},
        {"types": ["a"], "kinds": [], "max_bytes": 1},
        {"types": ["a"], "kinds": ["X"], "max_bytes": 0},
    ):
        with pytest.raises(ProofError):
            parse(bad)


# ---- the scanner hook, against a stand-in for clamd's INSTREAM protocol


async def with_clamd(
    reply: bytes | None, run: Callable[[int], Awaitable[object]]
) -> tuple[object, bytes]:
    """Serve one INSTREAM scan, answer with `reply` (None: close without answering), return the result and what the server received."""
    received = bytearray()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        assert await reader.readexactly(10) == b"zINSTREAM\0"
        while True:
            (n,) = struct.unpack("!I", await reader.readexactly(4))
            if n == 0:
                break
            received.extend(await reader.readexactly(n))
        if reply is not None:
            writer.write(reply)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        return await run(port), bytes(received)
    finally:
        server.close()
        await server.wait_closed()


def scan(reply: bytes | None, content: bytes) -> tuple[ScanStatus, bytes]:
    async def go() -> tuple[object, bytes]:
        return await with_clamd(
            reply, lambda port: ClamdScanner("127.0.0.1", port, timeout_s=3).scan(content)
        )

    status, received = asyncio.run(go())
    assert isinstance(status, ScanStatus)
    return status, received


def test_the_scanner_streams_the_whole_file_and_only_ok_is_clean() -> None:
    big = b"A" * (ClamdScanner.CHUNK * 2 + 123)  # more than one chunk
    assert scan(b"stream: OK\0", big) == (ScanStatus.CLEAN, big)
    assert scan(b"stream: Eicar-Signature FOUND\0", b"x")[0] is ScanStatus.INFECTED
    assert (
        scan(b"INSTREAM size limit exceeded. ERROR\0", b"x")[0] is ScanStatus.UNAVAILABLE
    )  # an error reply is never a pass
    assert scan(b"gibberish\0", b"x")[0] is ScanStatus.UNAVAILABLE
    assert scan(None, b"x")[0] is ScanStatus.UNAVAILABLE  # the connection closed without an answer


def test_a_scanner_that_cannot_be_reached_is_unavailable_never_clean() -> None:
    free = asyncio.run(_free_port())
    assert (
        asyncio.run(ClamdScanner("127.0.0.1", free, timeout_s=2).scan(b"x"))
        is ScanStatus.UNAVAILABLE
    )
    assert from_address(None) is None and from_address("") is None
    s = from_address("scanner.internal:3311")
    assert isinstance(s, ClamdScanner) and (s.host, s.port) == ("scanner.internal", 3311)
    d = from_address("scanner.internal")
    assert isinstance(d, ClamdScanner) and d.port == 3310


async def _free_port() -> int:
    server = await asyncio.start_server(lambda r, w: None, "127.0.0.1", 0)
    port: int = server.sockets[0].getsockname()[1]
    server.close()
    await server.wait_closed()
    return port
