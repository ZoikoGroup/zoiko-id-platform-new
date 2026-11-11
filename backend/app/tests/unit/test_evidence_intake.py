"""ZID-INT-002 section 05: upload intake rules, metadata stripping, the scanner protocol, and the upload token (all without a database)."""

from __future__ import annotations

import asyncio
import hashlib
import struct
from datetime import UTC, datetime, timedelta

import pytest
from zid_crypto import KeyRing, LocalSigner
from zid_platform.modules.evidence.application import scanner as sc
from zid_platform.modules.evidence.application import uploads as up
from zid_platform.modules.evidence.domain import intake as it

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
PDF = b"%PDF-1.7\n1 0 obj << /Type /Catalog >> endobj\n%%EOF\n"


def sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def seg(marker: int, body: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(body) + 2) + body


def jpeg(*segments: bytes) -> bytes:
    return b"\xff\xd8" + b"".join(segments) + seg(0xDA, b"\x00\x01\x02") + b"\x12\x34\x56\xff\xd9"


def chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body)) + kind + body + struct.pack(">I", 0)
    )  # checksum is not checked by the stripper


def png(*chunks: bytes) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", b"\x00" * 13)
        + b"".join(chunks)
        + chunk(b"IDAT", b"\x01\x02")
        + chunk(b"IEND", b"")
    )


def run(coro):
    return asyncio.run(coro)


# --------------------------------------------------------------------------------------------------- announcement


@pytest.mark.parametrize(
    ("kind", "size", "digest", "code"),
    [
        ("application/zip", 10, "a" * 64, "TYPE_NOT_ALLOWED"),
        ("application/pdf", 0, "a" * 64, "SIZE_NOT_ALLOWED"),
        ("application/pdf", it.MAX_UPLOAD_BYTES + 1, "a" * 64, "SIZE_NOT_ALLOWED"),
        ("application/pdf", 10, "A" * 64, "DIGEST_INVALID"),
        ("application/pdf", 10, "a" * 63, "DIGEST_INVALID"),
    ],
)
def test_an_announcement_is_refused_before_anything_is_stored(
    kind: str, size: int, digest: str, code: str
) -> None:
    with pytest.raises(it.UploadError) as e:
        it.check_announcement(kind, size, digest)
    assert e.value.code == code
    it.check_announcement("application/pdf", 10, "a" * 64)


# --------------------------------------------------------------------------------------------------- the bytes


def test_the_bytes_must_be_what_was_announced_in_size_digest_and_type() -> None:
    assert (
        it.check_content(PDF, declared_type="application/pdf", size_bytes=len(PDF), sha256=sha(PDF))
        == "application/pdf"
    )
    for kwargs, code in (
        ({"size_bytes": len(PDF) + 1, "sha256": sha(PDF)}, "SIZE_MISMATCH"),
        ({"size_bytes": len(PDF), "sha256": sha(b"other")}, "DIGEST_MISMATCH"),
    ):
        with pytest.raises(it.UploadError) as e:
            it.check_content(PDF, declared_type="application/pdf", **kwargs)
        assert e.value.code == code
    with pytest.raises(
        it.UploadError
    ) as e:  # right size and digest, but the bytes are a PDF and the caller said PNG
        it.check_content(PDF, declared_type="image/png", size_bytes=len(PDF), sha256=sha(PDF))
    assert e.value.code == "TYPE_MISMATCH"
    exe = b"MZ\x90\x00" + b"\x00" * 20
    with pytest.raises(it.UploadError):
        it.check_content(exe, declared_type="application/pdf", size_bytes=len(exe), sha256=sha(exe))


# --------------------------------------------------------------------------------------------------- stripping


def test_jpeg_exif_xmp_and_comments_go_and_the_picture_and_colour_profile_stay() -> None:
    exif = seg(0xE1, b"Exif\x00\x00GPS 51.5N 0.12W")
    original = jpeg(
        seg(0xE0, b"JFIF\x00\x01\x01"),
        exif,
        seg(0xE2, b"ICC_PROFILE\x00\x01\x01"),
        seg(0xFE, b"a comment"),
        seg(0xED, b"Photoshop"),
    )
    cleaned, removed = it.strip_jpeg_metadata(original)
    assert (
        removed is True
        and b"GPS" not in cleaned
        and b"a comment" not in cleaned
        and b"Photoshop" not in cleaned
    )
    assert b"JFIF" in cleaned and b"ICC_PROFILE" in cleaned
    assert cleaned.endswith(b"\x12\x34\x56\xff\xd9") and cleaned.startswith(b"\xff\xd8")


def test_a_jpeg_with_nothing_to_remove_is_unchanged_and_a_broken_one_is_left_alone() -> None:
    clean = jpeg(seg(0xE0, b"JFIF\x00\x01\x01"))
    assert it.strip_jpeg_metadata(clean) == (clean, False)
    broken = b"\xff\xd8\xff\xe1\x00\x10 truncated"
    assert it.strip_jpeg_metadata(broken) == (broken, False)
    assert it.strip_jpeg_metadata(b"not a jpeg") == (b"not a jpeg", False)


def test_png_text_exif_and_time_chunks_go_and_the_image_chunks_stay() -> None:
    original = png(
        chunk(b"tEXt", b"Author\x00Dana Lee"),
        chunk(b"eXIf", b"GPS"),
        chunk(b"tIME", b"\x00" * 7),
        chunk(b"gAMA", b"\x00\x00\x00\x00"),
    )
    cleaned, removed = it.strip_png_metadata(original)
    assert removed is True and b"Dana Lee" not in cleaned and b"GPS" not in cleaned
    assert b"IHDR" in cleaned and b"IDAT" in cleaned and b"IEND" in cleaned and b"gAMA" in cleaned
    assert it.strip_png_metadata(png()) == (png(), False)


def test_a_pdf_is_never_rewritten_because_that_would_change_what_a_signature_covers() -> None:
    assert it.strip_metadata(PDF, "application/pdf") == (PDF, False)


# --------------------------------------------------------------------------------------------------- scanning


def test_the_simulator_finds_only_the_eicar_test_string() -> None:
    s = sc.SimulatorScanner()
    assert run(s.scan(b"hello")).state == "CLEAN"
    v = run(s.scan(b"prefix " + sc.EICAR + b" suffix"))
    assert v.state == "INFECTED" and v.signature == "Eicar-Test-Signature"


@pytest.mark.parametrize(
    ("reply", "state", "signature"),
    [
        ("stream: OK", "CLEAN", None),
        ("stream: Win.Test.EICAR_HDB-1 FOUND", "INFECTED", "Win.Test.EICAR_HDB-1"),
        ("INSTREAM size limit exceeded. ERROR", "SCAN_ERROR", None),
        ("stream: lstat() failed ERROR", "SCAN_ERROR", None),
        ("", "SCAN_ERROR", None),
        ("OK", "SCAN_ERROR", None),
    ],
)
def test_only_an_explicit_ok_is_clean(reply: str, state: str, signature: str | None) -> None:
    v = sc.parse_reply(reply, "clamd")
    assert (v.state, v.signature) == (state, signature)


async def _fake_clamd(reply: bytes, seen: list[bytes], *, hang: bool = False):
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        command = await reader.readuntil(b"\0")
        data = b""
        while True:
            (n,) = struct.unpack(">I", await reader.readexactly(4))
            if n == 0:
                break
            data += await reader.readexactly(n)
        seen.append(command + data)
        if hang:
            await asyncio.sleep(5)
        writer.write(reply)
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    return server, server.sockets[0].getsockname()[1]


def test_the_clamd_scanner_speaks_instream_and_reads_the_verdict() -> None:
    async def go() -> None:
        content = b"A" * 70_000 + b"B" * 10  # more than one chunk
        for reply, expected in (
            (b"stream: OK\0", "CLEAN"),
            (b"stream: Eicar-Signature FOUND\0", "INFECTED"),
            (b"stream: x ERROR\0", "SCAN_ERROR"),
        ):
            seen: list[bytes] = []
            server, port = await _fake_clamd(reply, seen)
            async with server:
                v = await sc.ClamdScanner("127.0.0.1", port).scan(content)
            assert v.state == expected
            assert seen == [b"zINSTREAM\0" + content], (
                "the exact bytes were streamed after the command"
            )

    run(go())


def test_a_scanner_that_is_down_or_slow_is_an_error_never_clean() -> None:
    async def go() -> None:
        assert (
            await sc.ClamdScanner("127.0.0.1", 1, timeout=1).scan(b"x")
        ).state == "SCAN_ERROR"  # nothing listening
        server, port = await _fake_clamd(b"stream: OK\0", [], hang=True)
        async with server:
            assert (
                await sc.ClamdScanner("127.0.0.1", port, timeout=0.3).scan(b"x")
            ).state == "SCAN_ERROR"  # too slow

    run(go())


def test_no_scanner_or_a_scanner_that_breaks_is_never_clean() -> None:
    class Broken:
        name = "broken"

        async def scan(self, content: bytes):
            raise RuntimeError("boom")

    class Lies:
        name = "liar"

        async def scan(self, content: bytes):
            return sc.Verdict("PROBABLY_FINE", "liar")

    assert run(sc.scan(None, b"x")).state == "SCAN_ERROR"
    assert run(sc.scan(Broken(), b"x")).state == "SCAN_ERROR"
    assert run(sc.scan(Lies(), b"x")).state == "SCAN_ERROR"


# --------------------------------------------------------------------------------------------------- the token


def test_the_upload_token_is_ours_unexpired_and_for_exactly_this_upload_and_tenant() -> None:
    ring = KeyRing(LocalSigner("upload-test"))
    claims = {
        "typ": up.TOKEN_TYP,
        "upload_id": "u1",
        "tenant_id": "t1",
        "size_bytes": 5,
        "sha256": "a" * 64,
        "exp": int((NOW + timedelta(minutes=5)).timestamp()),
    }
    token = ring.sign(claims, typ=up.TOKEN_TYP)
    assert (
        up.check_token(ring, token, upload_id="u1", tenant_id="t1", now=NOW)["sha256"] == "a" * 64
    )
    for kwargs, code in (
        ({"upload_id": "u2", "tenant_id": "t1", "now": NOW}, "UPLOAD_TOKEN_INVALID"),
        ({"upload_id": "u1", "tenant_id": "t2", "now": NOW}, "UPLOAD_TOKEN_INVALID"),
        (
            {"upload_id": "u1", "tenant_id": "t1", "now": NOW + timedelta(minutes=6)},
            "UPLOAD_TOKEN_EXPIRED",
        ),
    ):
        with pytest.raises(it.UploadError) as e:
            up.check_token(ring, token, **kwargs)
        assert e.value.code == code
    other = KeyRing(LocalSigner("someone-else"))
    for bad in (
        other.sign(claims, typ=up.TOKEN_TYP),
        ring.sign(claims, typ="zid-decision+jwt"),
        "not-a-token",
        token[:-3] + "abc",
    ):
        with pytest.raises(it.UploadError) as e:
            up.check_token(ring, bad, upload_id="u1", tenant_id="t1", now=NOW)
        assert e.value.code == "UPLOAD_TOKEN_INVALID"
