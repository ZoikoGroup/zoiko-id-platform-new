"""Document analysis: tamper and template signals (ZID-INT-002 section 09, "document analysis"; T19's own-process half). Pure: bytes in, signals out.

A signal is an inconsistency or a feature a person may want to look at. The rules the document sets, kept here:

- "AI may flag inconsistencies; it cannot confirm an official issuer alone". Nothing here decides. The check's status is INDETERMINATE whether signals
  are found or not, because a document with no signals is not thereby genuine, and a document with signals is not thereby forged;
- a signal says what was seen and what it does and does not mean, in plain words. NOTICE signals are worth a look, INFO ones are facts;
- nothing is repaired or inferred: if a date cannot be read it is simply not compared.

Deterministic and free of any model or service. It reads the bytes, nothing else.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

EDITING_TOOLS = (
    "photoshop",
    "gimp",
    "inkscape",
    "canva",
    "pixlr",
    "paint.net",
    "pdfescape",
    "sejda",
    "ilovepdf",
    "smallpdf",
    "foxit phantom",
    "nitro",
    "snapseed",
    "picsart",
)
LIMITATION = "These are signals for a person to look at. None is a finding of forgery, and a document with no signals is not thereby genuine: only the issuer can confirm it."


@dataclass(frozen=True, slots=True)
class Signal:
    code: str
    severity: str  # INFO | NOTICE
    detail: str
    meaning: str

    def public(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "detail": self.detail,
            "meaning": self.meaning,
        }


def _info(key: bytes, data: bytes) -> str | None:
    m = re.search(rb"/" + key + rb"\s*\(((?:\\.|[^\\)])*)\)", data)
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")[:120]
    return raw.decode("latin-1")[:120]


def _pdf_date(text: str | None) -> datetime | None:
    if not text:
        return None
    m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?", text)
    if not m:
        return None
    parts = [int(g) if g else d for g, d in zip(m.groups(), (0, 1, 1, 0, 0, 0), strict=True)]
    try:
        return datetime(*parts)
    except ValueError:
        return None


def pdf_signals(pdf: bytes) -> list[Signal]:
    out: list[Signal] = []
    eof = len(re.findall(rb"%%EOF", pdf))
    if eof > 1:
        out.append(
            Signal(
                "INCREMENTAL_UPDATES",
                "NOTICE",
                f"{eof} end-of-file markers",
                "The file was saved more than once without rewriting it. That is normal for signed or annotated files and is also how an edit is appended.",
            )
        )
    for needle, code, meaning in (
        (
            rb"/JavaScript|/JS\s*[(<]",
            "ACTIVE_CONTENT_JAVASCRIPT",
            "The file contains script. Official documents rarely need it.",
        ),
        (
            rb"/Launch\b",
            "ACTIVE_CONTENT_LAUNCH_ACTION",
            "The file asks the viewer to open something else.",
        ),
        (
            rb"/OpenAction\b|/AA\s*<<",
            "AUTOMATIC_ACTION_ON_OPEN",
            "The file runs an action when opened.",
        ),
        (rb"/EmbeddedFile\b", "EMBEDDED_FILES", "Other files are carried inside this one."),
        (rb"/XFA\b", "XFA_FORM", "The file uses an XFA form, which is not read here."),
    ):
        if re.search(needle, pdf):
            out.append(Signal(code, "NOTICE", needle.decode("latin-1")[:30], meaning))
    if re.search(rb"/Encrypt\b", pdf):
        out.append(
            Signal(
                "ENCRYPTED",
                "INFO",
                "an /Encrypt entry",
                "The file is encrypted; its content could not be fully examined.",
            )
        )
    producer, creator = _info(b"Producer", pdf), _info(b"Creator", pdf)
    for label, value in (("Producer", producer), ("Creator", creator)):
        if value:
            hit = next((t for t in EDITING_TOOLS if t in value.lower()), None)
            out.append(
                Signal(
                    "EDITING_TOOL_NAMED" if hit else "PRODUCER_NAMED",
                    "NOTICE" if hit else "INFO",
                    f"{label}: {value}",
                    "An image or PDF editor is named. Many genuine documents pass through editors, and an official one usually comes from the issuer's own system."
                    if hit
                    else "The software that produced the file, as the file states it. It can be set to anything.",
                )
            )
    created, changed = _pdf_date(_info(b"CreationDate", pdf)), _pdf_date(_info(b"ModDate", pdf))
    if created and changed and (changed - created).days >= 1:
        out.append(
            Signal(
                "MODIFIED_AFTER_CREATION",
                "NOTICE",
                f"created {created.date()}, modified {changed.date()}",
                "The file says it was changed after it was created. Dates in a file are set by whoever wrote it.",
            )
        )
    return out


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    out, pos = [], 8
    while pos + 8 <= len(data):
        length = int.from_bytes(data[pos : pos + 4], "big")
        kind = data[pos + 4 : pos + 8]
        out.append((kind, data[pos + 8 : pos + 8 + length]))
        pos += 12 + length
        if kind == b"IEND":
            break
    return out


def image_signals(data: bytes) -> list[Signal]:
    out: list[Signal] = []
    low = data[:65536].lower()
    tool = next((t for t in EDITING_TOOLS if t.encode() in low), None)
    if tool:
        out.append(
            Signal(
                "EDITING_TOOL_IN_METADATA",
                "NOTICE",
                f"the name of an editing tool ({tool}) appears in the image's metadata",
                "The image says it passed through an editor. Metadata can be removed or set to anything, so its absence says nothing.",
            )
        )
    if data.startswith(b"\x89PNG"):
        chunks = _png_chunks(data)
        end = sum(12 + len(body) for _kind, body in chunks) + 8
        if chunks and chunks[-1][0] == b"IEND" and len(data) - end > 0:
            out.append(
                Signal(
                    "DATA_AFTER_IMAGE_END",
                    "NOTICE",
                    f"{len(data) - end} bytes after the end of the image",
                    "Extra bytes follow the image. They can hide content.",
                )
            )
        if any(k == b"eXIf" for k, _ in chunks):
            out.append(
                Signal(
                    "EXIF_PRESENT",
                    "INFO",
                    "an EXIF block",
                    "The image carries camera or software metadata.",
                )
            )
    elif data.startswith(b"\xff\xd8\xff"):
        end = data.rfind(b"\xff\xd9")
        if end != -1 and len(data) - (end + 2) > 16:
            out.append(
                Signal(
                    "DATA_AFTER_IMAGE_END",
                    "NOTICE",
                    f"{len(data) - end - 2} bytes after the end of the image",
                    "Extra bytes follow the image. They can hide content.",
                )
            )
        if b"Exif\x00\x00" in data[:4096]:
            out.append(
                Signal(
                    "EXIF_PRESENT",
                    "INFO",
                    "an EXIF block",
                    "The image carries camera or software metadata.",
                )
            )
    return out


def analyse(content: bytes, kind: str | None) -> tuple[list[Signal], str]:
    """(the signals, what the document was read as). Types that are not analysed say so rather than returning an empty list that looks clean."""
    if kind == "application/pdf":
        return pdf_signals(content), "pdf"
    if kind in {"image/png", "image/jpeg"}:
        return image_signals(content), "image"
    return [], "not_analysed"
