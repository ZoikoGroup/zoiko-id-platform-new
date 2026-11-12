"""What a supplied piece of evidence can and cannot establish, for the authority and property checks (ZID-INT-002 sections 10 and 11).

A mandate, a title document or a permission is evidence somebody supplied. This reads each one that is usable (uploaded, scanned clean, in its
retention period) and runs it through the same document and signature checks as `/v2/documents/validate`. It reports what that proves, which is
narrow:

- a signature that is sound, chains to a trust anchor THIS deployment configured, and was not followed by any change, shows the document is as
  signed by the holder of that certificate;
- it does NOT show the signatory is entitled to bind the company or to let the property (that needs an officer or title register, a source
  nobody has configured), and it never shows the facts the document states are true;
- a document with no signature, or one that cannot be validated, proves nothing about who issued it: it is reported as unsigned or
  indeterminate, never repaired and never promoted.

Pure of any outside service: it reads this cell's own upload store and the operator's trust configuration.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.application import signatures, trust, uploads
from zid_platform.modules.evidence.domain import documents as docs

REF = re.compile(r"^ev_[0-9a-fA-F-]{36}$")


@dataclass(frozen=True, slots=True)
class Assessment:
    evidence_ref: str
    usable: bool
    sniffed_type: str | None = None
    sha256: str | None = None
    signature_status: str = "NOT_APPLICABLE"  # VERIFIED | VERIFIED_LIMITED | NOT_VERIFIED | INDETERMINATE | NOT_APPLICABLE
    signature_reasons: tuple[str, ...] = ()
    signed_and_unchanged: bool = False  # the signature is sound, chains to a configured anchor and nothing was added after it

    def public(self) -> dict[str, object]:
        return {
            "evidence_ref": self.evidence_ref,
            "usable": self.usable,
            "type": self.sniffed_type,
            "sha256": self.sha256,
            "signature_status": self.signature_status,
            "signature_reasons": list(self.signature_reasons),
            "signed_and_unchanged": self.signed_and_unchanged,
        }


async def assess(
    conn: AsyncConnection,
    refs: Sequence[str],
    config: trust.TrustConfig,
    now: datetime,
) -> list[Assessment]:
    """One assessment per reference, in order. A reference that is malformed, unknown, not scanned clean, deleted or past retention is
    reported as not usable: the same answer for every one of those, so nothing is learned about an upload that is not the caller's."""
    out: list[Assessment] = []
    for ref in refs:
        held = await uploads.read_usable(conn, ref, now) if REF.match(ref) else None
        if held is None:
            out.append(Assessment(ref, False))
            continue
        content, _row = held
        kind = docs.sniff(content)
        if kind is None or kind not in docs.ALLOWED_TYPES:
            out.append(Assessment(ref, True, kind))
            continue
        v = docs.validate(
            content, declared_type=kind, expected_sha256=None, checks=["integrity", "signature"]
        )
        v = signatures.apply(v, content, config, at=now)
        sig = next(r for r in v.results if r.check == "signature")
        unchanged = (
            sig.status in {docs.Status.VERIFIED, docs.Status.VERIFIED_LIMITED}
            and "CONTENT_ADDED_AFTER_SIGNING" not in sig.reasons
        )
        out.append(
            Assessment(
                ref,
                True,
                kind,
                v.sha256,
                "NOT_APPLICABLE"
                if sig.reasons == ("NOT_APPLICABLE_TO_TYPE",)
                else sig.status.value,
                tuple(sig.reasons),
                unchanged,
            )
        )
    return out
