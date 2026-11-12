"""A marked export: intended claims only, signed, recipient-bound and expiring (ZID-PRI-004 sections 04 and 22; AT-047). Pure apart from the signer.

What the mark guarantees, each with a test:

- only the claims the caller NAMED are included; everything else in the source is dropped, and a name that is not in the source is reported, not invented;
- a claim whose name says it is raw biometric or document material (an image, a template, an embedding, a document number) is refused outright, so an
  export can never carry what the ordinary APIs are forbidden to return;
- the export names its recipient and purpose and carries an issue time and an expiry (never longer than MAX_TTL), so a copy that travels is still
  tied to who it was made for and when it stops being good;
- the token is a compact JWS under the evidence signing key with its own explicit type (signing and signature verification are in
  `application/export_signing.py`, so this module stays free of crypto). Verification checks the signature, the type, the expiry and, when the
  verifier says who it is, that it is the named recipient. A changed byte anywhere fails.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

EXPORT_TYP = "zid-export+jwt"
MAX_TTL = timedelta(days=7)
MIN_TTL = timedelta(minutes=1)
FORBIDDEN = re.compile(
    r"(^|_)(image|photo|selfie|template|embedding|vector|frame|video|audio|document_number|passport_number|id_number|raw|biometric)(_|$)",
    re.IGNORECASE,
)


class ExportError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def build(
    source: Mapping[str, Any],
    include: Sequence[str],
    *,
    recipient: str,
    purpose: str,
    ttl: timedelta,
    tenant_id: str,
    now: datetime,
) -> tuple[dict[str, Any], list[str]]:
    """(payload, names asked for that the source does not have)."""
    if not include:
        raise ExportError("NO_CLAIMS_NAMED", "an export names the claims it is meant to carry")
    refused = sorted(k for k in include if FORBIDDEN.search(k))
    if refused:
        raise ExportError("CLAIM_NOT_EXPORTABLE", ", ".join(refused))
    if not recipient.strip() or not purpose.strip():
        raise ExportError("RECIPIENT_AND_PURPOSE_REQUIRED")
    if ttl < MIN_TTL or ttl > MAX_TTL:
        raise ExportError("TTL_OUT_OF_RANGE", f"between {MIN_TTL} and {MAX_TTL}")
    missing = sorted(k for k in set(include) if k not in source)
    claims = {k: source[k] for k in sorted(set(include)) if k in source}
    payload = {
        "export_id": f"exp_{uuid.uuid4()}",
        "tenant_id": tenant_id,
        "recipient": recipient,
        "purpose": purpose,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "claims": claims,
    }
    return payload, missing


def check(
    payload: Mapping[str, Any], *, now: datetime, expected_recipient: str | None = None
) -> dict[str, Any]:
    """The checks that come after the signature has been verified: not expired, and for the named recipient when the verifier says who it is."""
    if int(payload.get("exp", 0)) <= int(now.timestamp()):
        raise ExportError("EXPORT_EXPIRED")
    if expected_recipient is not None and payload.get("recipient") != expected_recipient:
        raise ExportError("EXPORT_NOT_FOR_THIS_RECIPIENT")
    return dict(payload)
