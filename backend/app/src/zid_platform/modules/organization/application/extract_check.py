"""Is this uploaded registry extract the issuer's word? (ZID-DTV-WF-02 K03; CR-109). Orchestration over `domain/extract_authenticity.py`.

An extract carries one of two markers: a signature (checked against the trust list's keys) or a QR address (the issuer's own host must return this exact content). With neither, or with an issuer not in the
trust list, the answer is NOT_PROVEN and the file goes to a person: nothing here rejects a company and nothing here verifies one.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import UTC, datetime

import httpx

from zid_platform.modules.organization.domain import extract_authenticity as ea
from zid_platform.modules.organization.infrastructure import qr_fetch
from zid_platform.modules.organization.infrastructure.extract_verifier import (
    verify_with_cryptography,
)


def digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


async def check(
    content: bytes,
    *,
    issuer_id: str,
    issuers: Mapping[str, ea.Issuer],
    signature: bytes | None = None,
    key_id: str | None = None,
    algorithm: str | None = None,
    qr_url: str | None = None,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> ea.Finding:
    now = now or datetime.now(UTC)
    if signature is not None and key_id and algorithm:
        return ea.verify_signed_extract(
            content,
            signature,
            claimed_issuer=issuer_id,
            key_id=key_id,
            algorithm=algorithm,
            issuers=issuers,
            now=now,
            verifier=verify_with_cryptography,
        )
    if qr_url:
        issuer = issuers.get(issuer_id)
        fetched = (
            await qr_fetch.fetch_digest(qr_url, issuer, transport=transport)
            if issuer is not None
            else None
        )
        return ea.judge_qr(qr_url, issuer, extract_digest=digest(content), fetched_digest=fetched)
    return ea.Finding(ea.Authenticity.NOT_PROVEN, ("NO_AUTHENTICITY_MARKER",))
