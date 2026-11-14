"""Evidence upload intake rules (ZID-INT-002 section 05): pure checks on the bytes that arrive.

"Short-lived signed upload; content-type sniffing, malware scanning, size caps, hashing, EXIF stripping where appropriate, encryption and
deletion controls." This module is the sniffing, size, hash and metadata-stripping part. The bytes must be exactly what was announced
(size and SHA-256), must BE the type they were declared as, and have their embedded metadata removed before anything is kept.
"""

from __future__ import annotations

import hashlib
import struct

from zid_platform.modules.evidence.domain.active_content import active_content
from zid_platform.modules.evidence.domain.documents import ALLOWED_TYPES, sniff

MAX_UPLOAD_BYTES = 5 * 1024 * 1024  # mirrors the table CHECK


class UploadError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def check_announcement(declared_type: str, size_bytes: int, sha256: str) -> None:
    """What may be announced at all: an allowed type, a positive size under the cap, a well-formed digest."""
    if declared_type not in ALLOWED_TYPES:
        raise UploadError("TYPE_NOT_ALLOWED", declared_type)
    if not 1 <= size_bytes <= MAX_UPLOAD_BYTES:
        raise UploadError("SIZE_NOT_ALLOWED", f"1 to {MAX_UPLOAD_BYTES} bytes")
    if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
        raise UploadError("DIGEST_INVALID", "a lowercase hex SHA-256 is required")


def check_content(content: bytes, *, declared_type: str, size_bytes: int, sha256: str) -> str:
    """The bytes that arrived must be what was announced. Returns the sniffed type. Checked in this order so the refusal is exact."""
    if len(content) != size_bytes:
        raise UploadError("SIZE_MISMATCH", f"announced {size_bytes}, received {len(content)}")
    if hashlib.sha256(content).hexdigest() != sha256:
        raise UploadError("DIGEST_MISMATCH")
    found = active_content(
        content
    )  # T41: before the type is even weighed, and before the scanner or any validator sees it
    if found:
        raise UploadError("ACTIVE_CONTENT", ",".join(found))
    sniffed = sniff(content)
    if sniffed is None or sniffed != declared_type:
        raise UploadError("TYPE_MISMATCH", f"declared {declared_type}, bytes are {sniffed}")
    return sniffed


def strip_jpeg_metadata(data: bytes) -> tuple[bytes, bool]:
    """Remove EXIF (APP1), XMP and other APPn/COM segments except the JFIF header (APP0) and the ICC colour profile (APP2). The image
    data is untouched. A file that does not parse as markers is returned unchanged rather than guessed at."""
    if not data.startswith(b"\xff\xd8"):
        return data, False
    out = bytearray(b"\xff\xd8")
    i, removed = 2, False
    while i + 4 <= len(data):
        if data[i] != 0xFF:
            return data, False
        marker = data[i + 1]
        if marker == 0xFF:  # fill byte
            i += 1
            continue
        if marker in (0xD9, 0x01) or 0xD0 <= marker <= 0xD7:  # EOI, TEM, RSTn: no length
            out += data[i : i + 2]
            i += 2
            if marker == 0xD9:
                break
            continue
        (length,) = struct.unpack(">H", data[i + 2 : i + 4])
        end = i + 2 + length
        if length < 2 or end > len(data):
            return data, False
        if marker == 0xDA:  # start of scan: the rest is entropy-coded image data up to the end
            out += data[i:]
            return bytes(out), removed
        keep = marker not in (0xE1, 0xED, 0xFE) and not (0xE3 <= marker <= 0xEF)
        if keep:
            out += data[i:end]
        else:
            removed = True
        i = end
    return bytes(out), removed


_PNG_DROP = {b"eXIf", b"tEXt", b"iTXt", b"zTXt", b"tIME"}


def strip_png_metadata(data: bytes) -> tuple[bytes, bool]:
    """Remove textual, EXIF and time chunks from a PNG; the image chunks (and their checksums) are untouched."""
    sig = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(sig):
        return data, False
    out, i, removed = bytearray(sig), len(sig), False
    while i + 12 <= len(data):
        (length,) = struct.unpack(">I", data[i : i + 4])
        kind, end = data[i + 4 : i + 8], i + 12 + length
        if end > len(data):
            return data, False
        if kind in _PNG_DROP:
            removed = True
        else:
            out += data[i:end]
        i = end
        if kind == b"IEND":
            break
    return bytes(out), removed


def strip_metadata(content: bytes, kind: str) -> tuple[bytes, bool]:
    """Strip image metadata where it applies. A PDF is left as it is: rewriting it would change what a signature covers."""
    if kind == "image/jpeg":
        return strip_jpeg_metadata(content)
    if kind == "image/png":
        return strip_png_metadata(content)
    return content, False
