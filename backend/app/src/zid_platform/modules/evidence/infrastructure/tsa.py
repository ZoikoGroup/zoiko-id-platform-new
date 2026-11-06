"""Optional RFC 3161 (as updated by RFC 5816) timestamps over checkpoint manifests (DEC-F2 §1, §3).

`TimestampAuthority` is the port; `Rfc3161Client` is a minimal HTTP client that posts a DER
TimeStampReq (`application/timestamp-query`) and keeps the DER TimeStampResp as the checkpoint's
`external_timestamp_ref` (`rfc3161:<base64>`) only after `tsa_token.TimestampVerifier` has proved the
token against the configured TSA trust list and checked revocation (a forged, mismatched, expired,
untrusted or revoked answer is `TimestampRejected` and the checkpoint stays unanchored). The TSA network isolation of the Deployment
spec §23 comes with the deployed TSA profile. A TSA outage or a rejected answer never blocks sealing by
default; the checkpoint is stored without a timestamp and anchored on a later worker pass.
"""

from __future__ import annotations

import base64
import logging
import secrets
from typing import TYPE_CHECKING, Protocol

from zid_evidence import Digest

if TYPE_CHECKING:
    from zid_platform.modules.evidence.infrastructure.tsa_token import TimestampVerifier

log = logging.getLogger("zid.evidence.tsa")

# id-sha256 / id-sha384 / id-sha512 (NIST hash algorithm OIDs), DER-encoded OBJECT IDENTIFIER values
_HASH_OIDS = {
    "sha-256": bytes.fromhex("608648016503040201"),
    "sha-384": bytes.fromhex("608648016503040202"),
    "sha-512": bytes.fromhex("608648016503040203"),
}


class TimestampError(Exception):
    pass


class TimestampAuthority(Protocol):
    async def timestamp(self, digest: Digest) -> str:
        """Return an opaque reference to a timestamp token over `digest`; raise TimestampError."""
        ...


def _tlv(tag: int, body: bytes) -> bytes:
    n = len(body)
    if n < 0x80:
        length = bytes([n])
    else:
        raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
        length = bytes([0x80 | len(raw)]) + raw
    return bytes([tag]) + length + body


def _integer(value: int) -> bytes:
    raw = value.to_bytes(value.bit_length() // 8 + 1, "big")
    return _tlv(0x02, raw)


def timestamp_request(digest: Digest, nonce: int) -> bytes:
    """DER TimeStampReq (RFC 3161 §2.4.1): version 1, messageImprint, nonce, certReq TRUE."""
    algorithm = _tlv(0x30, _tlv(0x06, _HASH_OIDS[digest.alg]) + b"\x05\x00")
    imprint = _tlv(0x30, algorithm + _tlv(0x04, digest.value))
    return _tlv(0x30, _integer(1) + imprint + _integer(nonce) + _tlv(0x01, b"\xff"))


class Rfc3161Client:
    """Asks a TSA for a timestamp and returns a reference only if the answer proves it.

    The `verifier` (a `tsa_token.TimestampVerifier`) is required: a response from an unverified authority, or one whose
    certificates are revoked, is never stored as an anchor.
    """

    def __init__(self, url: str, *, verifier: TimestampVerifier, timeout_s: float = 10.0) -> None:
        self.url = url
        self.verifier = verifier
        self.timeout_s = timeout_s

    async def timestamp(self, digest: Digest) -> str:
        import httpx

        from zid_platform.modules.evidence.infrastructure.tsa_token import TimestampRejected

        nonce = secrets.randbits(63)
        body = timestamp_request(digest, nonce)
        try:
            async with httpx.AsyncClient(timeout=self.timeout_s) as http:
                r = await http.post(
                    self.url,
                    content=body,
                    headers={"Content-Type": "application/timestamp-query"},
                )
        except httpx.HTTPError as e:
            raise TimestampError(f"TSA unreachable: {type(e).__name__}") from e
        if r.status_code != 200 or r.headers.get("content-type") != "application/timestamp-reply":
            raise TimestampError(f"TSA answered {r.status_code}")
        try:
            await self.verifier.verify_response(r.content, digest, nonce=nonce)
        except TimestampRejected:
            log.exception(
                "TSA response rejected; the checkpoint stays unanchored", extra={"tsa": self.url}
            )
            raise
        return "rfc3161:" + base64.b64encode(r.content).decode("ascii")
