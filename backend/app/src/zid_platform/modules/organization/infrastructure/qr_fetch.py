"""Fetch what a QR code on a registry extract points at, so it can be compared with the extract (ZID-DTV-WF-02 K03; acceptance T07).

Only an address the issuer is recorded as using is ever requested (`qr_destination_problem` runs first, so a QR cannot make the platform call an arbitrary host), over https, with no redirect followed
(a redirect is how a mirror gets in), a short timeout and a size ceiling. Any failure is `None`, which the rules read as "the issuer could not be reached": never a finding about the file."""

from __future__ import annotations

import hashlib

import httpx

from zid_platform.modules.organization.domain.extract_authenticity import (
    Issuer,
    qr_destination_problem,
)

TIMEOUT_S = 8.0
MAX_BYTES = 5 * 1024 * 1024


async def fetch_digest(
    url: str, issuer: Issuer, *, transport: httpx.AsyncBaseTransport | None = None
) -> str | None:
    """`sha256:<hex>` of the body the issuer's own address returns, or None when it was not asked or did not answer cleanly."""
    if qr_destination_problem(url, issuer) is not None:
        return None  # not the issuer's address: nothing is requested
    try:
        async with httpx.AsyncClient(
            transport=transport, timeout=TIMEOUT_S, follow_redirects=False, verify=True
        ) as client:
            r = await client.get(url, headers={"accept": "application/pdf, application/json, */*"})
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content or len(r.content) > MAX_BYTES:
        return None
    return "sha256:" + hashlib.sha256(r.content).hexdigest()
