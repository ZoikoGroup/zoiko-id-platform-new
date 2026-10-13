"""W3C Verifiable Credentials Data Model 2.0 secured with JOSE (ZID-VC-001 §9; VC-JOSE-COSE). Pure.

Adapter VCDM_ADAPTER, as a *verifier* only:
- a credential is a `vc+jwt` whose payload is the VC document itself; a presentation is a
  `vp+jwt` signed by the holder carrying it as an EnvelopedVerifiableCredential
  (`data:application/vc+jwt,<jwt>`). The VP's nonce/aud bind it to one transaction.
- JSON-LD is never processed and contexts are never fetched: @context must start with the VCDM
  2.0 base context and may only add contexts pinned here; an inline (object) context could
  redefine terms and is refused.
- Bitstring Status List v1.0: credentialStatus entries of type BitstringStatusListEntry point at
  a status list credential (itself a vc+jwt from the same issuer); the bit at statusListIndex,
  most significant bit first, in the GZIP-compressed multibase (base64url, 'u') encodedList.
"""

from __future__ import annotations

import gzip
import zlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from zid_platform.modules.credential.domain.credentials import CredentialError, b64d, b64e

VCDM_ADAPTER = "w3c-vcdm-2.0+vc-jose-cose"
VC_TYP, VP_TYP = "vc+jwt", "vp+jwt"
BASE_CONTEXT = "https://www.w3.org/ns/credentials/v2"
PINNED_CONTEXTS = frozenset({BASE_CONTEXT})
ENVELOPED_PREFIX = "data:application/vc+jwt,"
STATUS_ENTRY = "BitstringStatusListEntry"
STATUS_PURPOSES = {"revocation": "REVOKED", "suspension": "SUSPENDED"}
SKEW = timedelta(seconds=60)
MAX_LIST_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StatusEntry:
    purpose: str
    index: int
    list_credential: str


def _types(doc: Mapping[str, Any]) -> list[str]:
    t = doc.get("type")
    types = [t] if isinstance(t, str) else t
    if not isinstance(types, list) or not all(isinstance(x, str) for x in types):
        raise CredentialError("VC_MALFORMED", "type")
    return types


def check_contexts(doc: Mapping[str, Any]) -> None:
    contexts = doc.get("@context")
    if not isinstance(contexts, list) or not contexts or contexts[0] != BASE_CONTEXT:
        raise CredentialError("VC_CONTEXT_NOT_ALLOWED", "base context first")
    for c in contexts[1:]:
        if not isinstance(c, str) or c not in PINNED_CONTEXTS:
            raise CredentialError("VC_CONTEXT_NOT_ALLOWED", str(c)[:64])


def issuer_of(doc: Mapping[str, Any]) -> str:
    issuer = doc.get("issuer")
    if isinstance(issuer, Mapping):
        issuer = issuer.get("id")
    if not isinstance(issuer, str) or not issuer:
        raise CredentialError("VC_MALFORMED", "issuer")
    return issuer


def _instant(value: Any, name: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise CredentialError("VC_MALFORMED", name)
    try:
        t = datetime.fromisoformat(value)  # Python 3.11+ accepts a trailing Z
    except ValueError as e:
        raise CredentialError("VC_MALFORMED", name) from e
    if t.tzinfo is None:
        raise CredentialError("VC_MALFORMED", f"{name} without offset")
    return t.astimezone(UTC)


def check_validity(doc: Mapping[str, Any], now: datetime) -> None:
    valid_from = _instant(doc.get("validFrom"), "validFrom")
    valid_until = _instant(doc.get("validUntil"), "validUntil")
    if valid_from is not None and valid_from > now + SKEW:
        raise CredentialError("CREDENTIAL_NOT_YET_VALID")
    if valid_until is not None and now >= valid_until + SKEW:
        raise CredentialError("CREDENTIAL_EXPIRED")


def check_credential(
    doc: Mapping[str, Any], *, credential_type: str, now: datetime
) -> dict[str, Any]:
    """The claims of the single credentialSubject (its `id` excluded)."""
    check_contexts(doc)
    types = _types(doc)
    if "VerifiableCredential" not in types:
        raise CredentialError("VC_MALFORMED", "not a VerifiableCredential")
    if credential_type not in types:
        raise CredentialError("CREDENTIAL_TYPE_MISMATCH")
    check_validity(doc, now)
    subject = doc.get("credentialSubject")
    if not isinstance(subject, Mapping):
        raise CredentialError("VC_MALFORMED", "exactly one credentialSubject")
    return {k: v for k, v in subject.items() if k != "id"}


def enveloped_credential(vp: Mapping[str, Any]) -> str:
    """The one vc+jwt a vp+jwt envelopes."""
    check_contexts(vp)
    if "VerifiablePresentation" not in _types(vp):
        raise CredentialError("VP_MALFORMED", "not a VerifiablePresentation")
    items = vp.get("verifiableCredential")
    items = [items] if isinstance(items, Mapping) else items
    if not isinstance(items, list) or len(items) != 1 or not isinstance(items[0], Mapping):
        raise CredentialError("VP_MALFORMED", "exactly one credential")
    item = items[0]
    ident = item.get("id")
    if "EnvelopedVerifiableCredential" not in _types(item) or not isinstance(ident, str):
        raise CredentialError("VP_MALFORMED", "enveloped credential expected")
    if not ident.startswith(ENVELOPED_PREFIX):
        raise CredentialError("VP_MALFORMED", "vc+jwt data URL expected")
    return ident[len(ENVELOPED_PREFIX) :]


def status_entries(doc: Mapping[str, Any]) -> list[StatusEntry]:
    raw = doc.get("credentialStatus")
    if raw is None:
        return []
    entries = [raw] if isinstance(raw, Mapping) else raw
    if not isinstance(entries, list):
        raise CredentialError("VC_MALFORMED", "credentialStatus")
    out = []
    for e in entries:
        if not isinstance(e, Mapping) or e.get("type") != STATUS_ENTRY:
            continue  # other status mechanisms are not understood: they never count as VALID
        purpose, index, url = (
            e.get("statusPurpose"),
            e.get("statusListIndex"),
            e.get("statusListCredential"),
        )
        if purpose not in STATUS_PURPOSES or not isinstance(url, str):
            raise CredentialError("VC_MALFORMED", "credentialStatus entry")
        try:
            idx = int(str(index))
        except ValueError as ex:
            raise CredentialError("VC_MALFORMED", "statusListIndex") from ex
        if idx < 0:
            raise CredentialError("VC_MALFORMED", "statusListIndex")
        out.append(StatusEntry(str(purpose), idx, url))
    return out


def status_list_bits(
    doc: Mapping[str, Any], *, issuer: str, url: str, purpose: str, now: datetime
) -> bytes:
    """The decompressed bitstring of a verified BitstringStatusListCredential."""
    check_contexts(doc)
    if "BitstringStatusListCredential" not in _types(doc) or issuer_of(doc) != issuer:
        raise CredentialError("STATUS_LIST_INVALID", "type/issuer")
    if doc.get("id") not in (None, url):
        raise CredentialError("STATUS_LIST_INVALID", "id")
    check_validity(doc, now)
    subject = doc.get("credentialSubject")
    if not isinstance(subject, Mapping) or subject.get("type") != "BitstringStatusList":
        raise CredentialError("STATUS_LIST_INVALID", "subject")
    if subject.get("statusPurpose") != purpose:
        raise CredentialError("STATUS_LIST_INVALID", "purpose")
    encoded = subject.get("encodedList")
    if not isinstance(encoded, str) or not encoded.startswith("u"):
        raise CredentialError("STATUS_LIST_INVALID", "encodedList multibase")
    try:
        compressed = b64d(encoded[1:])
        inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)  # gzip framing, bounded output
        bits = inflater.decompress(compressed, MAX_LIST_BYTES)
    except (ValueError, zlib.error) as e:
        raise CredentialError("STATUS_LIST_INVALID", "encodedList") from e
    if inflater.unconsumed_tail:
        raise CredentialError("STATUS_LIST_INVALID", "too large")
    return bits


def bit_at(bits: bytes, index: int) -> int:
    byte, offset = divmod(index, 8)
    if byte >= len(bits):
        raise CredentialError("STATUS_LIST_INDEX_OUT_OF_RANGE")
    return (bits[byte] >> (7 - offset)) & 1  # most significant bit first


def encode_list(bits: bytes) -> str:
    """For issuers and tests: multibase base64url of the GZIP-compressed bitstring."""
    return "u" + b64e(gzip.compress(bits))
