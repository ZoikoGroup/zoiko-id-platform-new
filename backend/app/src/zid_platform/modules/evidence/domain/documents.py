"""Document checks that need no outside authority (ZID-INT-002 section 09). Pure: bytes in, separate results out.

What this CAN say, and what it cannot:
- INTEGRITY: the SHA-256 of the exact bytes, whether it matches a digest the caller already holds, and whether the bytes are the type
  they claim to be. "A hash shows unchanged bytes, not genuine issuing authority."
- SIGNATURE and SEAL: whether a PDF CONTAINS an embedded signature (a /ByteRange with a /Sig dictionary) is detected and reported. It
  is NOT validated: chain building, revocation, signing-time status and trusted lists need a PAdES/XAdES validator and a trust-list
  source, and neither exists in this repository. The answer is therefore never VERIFIED.
- ISSUER: needs a registry lookup of the issuer the document names. None ran, so the answer is UNAVAILABLE.

So there is no single positive status: a document can be intact and still have no authenticated issuer, and the result says so
(`authentic` is never offered). A valid signature would still not mean lawful authority for the purpose.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

ALLOWED_TYPES = ("application/pdf", "image/png", "image/jpeg")
MAX_BYTES = 2 * 1024 * 1024  # validated in memory and never stored
CHECKS = ("integrity", "issuer", "signature", "seal")


class Status(StrEnum):
    VERIFIED = "VERIFIED"
    NOT_VERIFIED = "NOT_VERIFIED"
    INDETERMINATE = "INDETERMINATE"
    UNAVAILABLE = "UNAVAILABLE"


class DocumentError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True, slots=True)
class CheckResult:
    check: str
    status: Status
    reasons: tuple[str, ...] = ()
    detail: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Validation:
    sha256: str
    sniffed_type: str | None
    size_bytes: int
    results: tuple[CheckResult, ...]


def sniff(content: bytes) -> str | None:
    """The type the bytes ARE, from their leading bytes; None if it is not an allowed type."""
    if content.startswith(b"%PDF-"):
        return "application/pdf"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


_BYTERANGE = re.compile(rb"/ByteRange\s*\[")
_SIG = re.compile(rb"/Type\s*/Sig\b|/FT\s*/Sig\b")
_SEAL = re.compile(rb"/SubFilter\s*/(ETSI\.CAdES\.detached|ETSI\.RFC3161)")


def signatures_present(content: bytes) -> tuple[int, bool]:
    """(how many embedded signature dictionaries a PDF appears to contain, whether any names an ETSI sub-filter). A detection only."""
    if not content.startswith(b"%PDF-"):
        return 0, False
    return min(len(_BYTERANGE.findall(content)), len(_SIG.findall(content))), bool(
        _SEAL.search(content)
    )


def validate(
    content: bytes,
    *,
    declared_type: str,
    expected_sha256: str | None,
    checks: Sequence[str],
    max_bytes: int = MAX_BYTES,
) -> Validation:
    """Run the requested checks. Each has its own result; nothing here can produce an overall positive."""
    if not content:
        raise DocumentError("EMPTY_DOCUMENT")
    if len(content) > max_bytes:
        raise DocumentError("DOCUMENT_TOO_LARGE", f"limit {max_bytes} bytes")
    if declared_type not in ALLOWED_TYPES:
        raise DocumentError("TYPE_NOT_ALLOWED", declared_type)
    wanted = list(dict.fromkeys(checks))
    unknown = [c for c in wanted if c not in CHECKS]
    if unknown or not wanted:
        raise DocumentError("CHECK_NOT_SUPPORTED", ",".join(unknown) or "none requested")
    digest = hashlib.sha256(content).hexdigest()
    kind = sniff(content)
    count, etsi = signatures_present(content)
    out: list[CheckResult] = []
    for name in wanted:
        if name == "integrity":
            out.append(_integrity(digest, kind, declared_type, expected_sha256))
        elif name == "issuer":
            out.append(
                CheckResult(
                    "issuer",
                    Status.UNAVAILABLE,
                    ("NO_ISSUER_SOURCE",),
                    {"note": "the issuer was not looked up in any registry"},
                )
            )
        elif name == "signature":
            out.append(_signature(kind, count))
        else:
            out.append(_seal(kind, count, etsi))
    return Validation(digest, kind, len(content), tuple(out))


def _integrity(digest: str, kind: str | None, declared: str, expected: str | None) -> CheckResult:
    detail: dict[str, object] = {"sha256": digest, "unchanged_bytes_only": True}
    if kind != declared:
        return CheckResult(
            "integrity", Status.NOT_VERIFIED, ("TYPE_MISMATCH",), {**detail, "sniffed_type": kind}
        )
    if expected is None:
        return CheckResult("integrity", Status.INDETERMINATE, ("NO_REFERENCE_DIGEST",), detail)
    if expected.lower() != digest:
        return CheckResult("integrity", Status.NOT_VERIFIED, ("DIGEST_MISMATCH",), detail)
    return CheckResult("integrity", Status.VERIFIED, ("BYTES_UNCHANGED_ONLY",), detail)


def _signature(kind: str | None, count: int) -> CheckResult:
    if kind != "application/pdf":
        return CheckResult(
            "signature", Status.UNAVAILABLE, ("NOT_APPLICABLE_TO_TYPE",), {"signatures_detected": 0}
        )
    if count == 0:
        return CheckResult(
            "signature", Status.INDETERMINATE, ("NO_SIGNATURE_FOUND",), {"signatures_detected": 0}
        )
    return CheckResult(
        "signature",
        Status.INDETERMINATE,
        ("SIGNATURE_PRESENT_NOT_VALIDATED", "NO_SIGNATURE_VALIDATOR"),
        {
            "signatures_detected": count,
            "chain_checked": False,
            "revocation_checked": False,
            "signing_time_checked": False,
        },
    )


def _seal(kind: str | None, count: int, etsi: bool) -> CheckResult:
    if kind != "application/pdf":
        return CheckResult("seal", Status.UNAVAILABLE, ("NOT_APPLICABLE_TO_TYPE",), {})
    reasons = (
        ("SEAL_NOT_VALIDATED", "NO_TRUST_LIST")
        if (count and etsi)
        else ("NO_SEAL_VALIDATOR", "NO_TRUST_LIST")
    )
    return CheckResult(
        "seal",
        Status.UNAVAILABLE,
        reasons,
        {"etsi_subfilter_named": etsi, "qualified_status": "NOT_ASSESSED"},
    )
