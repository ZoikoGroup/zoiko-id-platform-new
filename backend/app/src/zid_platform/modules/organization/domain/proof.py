"""Proof documents for a company claim (WF-13B section 06, AT-12; ZID-ED-03). Pure rules, no I/O.

A proof is something the applicant supplies to support a claim: a certificate of incorporation, a letter of authority, an ownership
chart. It is evidence for a reviewer, never a verification: a proof never changes a company's status by itself. What is accepted is
decided here and nowhere else:

- the type is read from the file's own first bytes, not from its name or the declared type, and the declared type must agree;
- only the types the policy lists are accepted (PDF, PNG and JPEG), up to the policy's size, and never an empty file;
- a PDF that carries active content (scripts, launch actions, embedded files, form actions) is refused, because a reviewer opens it;
- the applicant's file name is never stored as given; only a short, plain label is kept.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


class ProofError(Exception):
    def __init__(self, code: str, message: str = "") -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message or code


@dataclass(frozen=True, slots=True)
class ProofPolicy:
    version: str
    max_bytes: int
    types: tuple[str, ...]  # accepted media types, as sniffed
    kinds: tuple[str, ...]  # what the applicant says the document is
    per_case_limit: int  # how many proofs one claim may hold


_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
)
# a PDF a reviewer opens must not be able to run anything; these names only appear in PDFs that can
_ACTIVE_PDF = re.compile(
    rb"/(JavaScript|JS|Launch|EmbeddedFile|OpenAction|AA|RichMedia|XFA|SubmitForm|ImportData)\b"
)
_PDF_TAIL = b"%%EOF"
_NAME = re.compile(r"[^A-Za-z0-9._ -]")


def sniff(content: bytes) -> str | None:
    """The media type the bytes themselves show, or None."""
    for magic, media in _SIGNATURES:
        if content.startswith(magic):
            return media
    return None


def sanitize_name(name: str | None) -> str:
    """A short plain label: no path, no control or markup characters, never empty."""
    base = (name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _NAME.sub("", base).strip(" .")[:100]
    return cleaned or "document"


def check_proof(
    content: bytes, *, declared_type: str | None, kind: str, policy: ProofPolicy
) -> str:
    """Return the sniffed media type, or raise ProofError. Order: kind, size, type, agreement, active content."""
    if kind not in policy.kinds:
        raise ProofError("PROOF_KIND_UNKNOWN", "Say what kind of document this is.")
    if not content:
        raise ProofError("PROOF_EMPTY", "The file is empty.")
    if len(content) > policy.max_bytes:
        raise ProofError(
            "PROOF_TOO_LARGE", f"The file is larger than {policy.max_bytes // (1024 * 1024)} MB."
        )
    media = sniff(content)
    if media is None or media not in policy.types:
        raise ProofError("PROOF_TYPE_NOT_ALLOWED", "Upload a PDF, PNG or JPEG file.")
    declared = (declared_type or "").split(";")[0].strip().lower()
    if declared and declared != media:
        raise ProofError("PROOF_TYPE_MISMATCH", "The file is not the type it says it is.")
    if media == "application/pdf":
        if _ACTIVE_PDF.search(content):
            raise ProofError(
                "PROOF_ACTIVE_CONTENT",
                "That PDF contains scripts or embedded content and cannot be accepted.",
            )
        if _PDF_TAIL not in content[-2048:]:
            raise ProofError("PROOF_MALFORMED", "That PDF is incomplete or damaged.")
    return media
