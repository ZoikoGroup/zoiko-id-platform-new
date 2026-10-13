"""Verifiable credential rules (ZID-VC-001 §0.2, §4, §7, §9-10, §13, §16; DEC-E2). Pure domain.

Separate checks, each its own reason code: proof, issuer trust (typed: per credential type and
jurisdiction; unknown = untrusted even when the signature is valid), holder binding, status,
claims. A presentation never creates authority; verified claims become policy inputs only.

SD-JWT (RFC 9901) disclosure processing lives here because it is pure computation over strings:
digest every disclosure, require each to be referenced exactly once from an `_sd` array of the
signed payload, reject duplicates, reserved names and claims that collide with plaintext ones,
and return only what was disclosed. Token Status List decoding (pinned draft) is pure too.
"""

from __future__ import annotations

import base64
import hashlib
import json
import zlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

# DEC-E2: what Zoiko may act as. Protocol support never implies the deferred roles.
ENABLED_ROLES = frozenset({"VERIFIER", "ISSUER_NON_QUALIFIED"})
DEFERRED_ROLES = frozenset(
    {"QUALIFIED_EAA_ISSUER", "WALLET_PROVIDER", "PID_PROVIDER", "QTSP", "BUSINESS_WALLET_PROVIDER"}
)
SD_JWT_ADAPTER = "sd-jwt-vc/draft-19+rfc9901"  # pinned: SD-JWT VC is not yet an RFC (§0.1)
STATUS_LIST_ADAPTER = "token-status-list/draft-21"
MDOC_ADAPTER = "iso18013-5:2021+oid4vp-1.0"
SD_JWT_TYPS = frozenset({"dc+sd-jwt"})
KB_TYP = "kb+jwt"
STATUS_LIST_TYP = "statuslist+jwt"
RESERVED = frozenset({"_sd", "...", "_sd_alg"})


class CredentialError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def require_role(role: str) -> None:
    if role in DEFERRED_ROLES or role not in ENABLED_ROLES:
        raise CredentialError("ROLE_NOT_ENABLED", role)  # FR-VC-008


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class Configuration:
    configuration_id: str
    credential_type: str
    format: str
    adapter_version: str
    required_claims: tuple[str, ...]
    optional_claims: tuple[str, ...]
    holder_binding_required: bool
    status_method: str
    status_mandatory: bool
    risk_class: int
    status: str
    assurance_profile_id: str | None = None
    issuance_enabled: bool = False


def minimize(
    requested: Sequence[str], config: Configuration, allowed_for_purpose: Sequence[str]
) -> tuple[str, ...]:
    """§7.1 / FR-VC-007: only claims the configuration defines AND the purpose permits."""
    if not requested:
        raise CredentialError("CLAIMS_REQUIRED")
    defined = set(config.required_claims) | set(config.optional_claims)
    extra = [c for c in requested if c not in defined]
    if extra:
        raise CredentialError("CLAIM_NOT_DEFINED", ",".join(extra))
    excess = [c for c in requested if c not in allowed_for_purpose]
    if excess:
        raise CredentialError("CLAIM_NOT_PERMITTED_FOR_PURPOSE", ",".join(excess))
    return tuple(dict.fromkeys(requested))


@dataclass(frozen=True, slots=True)
class IssuerTrust:
    issuer_identifier: str
    credential_types: frozenset[str]
    jurisdictions: frozenset[str]
    trust_source: str
    status: str
    valid_to: datetime


def trust_decision(
    trust: IssuerTrust | None, *, credential_type: str, jurisdiction: str | None, now: datetime
) -> None:
    """§4.1: no issuer is trusted for everything."""
    if trust is None:
        raise CredentialError("ISSUER_UNTRUSTED", "unknown issuer")
    if trust.status != "ACTIVE" or now >= trust.valid_to:
        raise CredentialError("ISSUER_UNTRUSTED", trust.status)
    if credential_type not in trust.credential_types:
        raise CredentialError("ISSUER_UNTRUSTED", "not trusted for this credential type")
    if jurisdiction is not None and trust.jurisdictions and jurisdiction not in trust.jurisdictions:
        raise CredentialError("ISSUER_UNTRUSTED", "not trusted for this jurisdiction")


# --------------------------------------------------------------------------------------- SD-JWT


@dataclass(frozen=True, slots=True)
class SdJwtParts:
    issuer_jwt: str
    disclosures: tuple[str, ...]
    kb_jwt: str | None
    sd_hash_input: str  # issuer JWT + "~" + each disclosure + "~" (what the KB-JWT signs over)


def split_presentation(presentation: str) -> SdJwtParts:
    if "~" not in presentation:
        raise CredentialError("SD_JWT_MALFORMED")
    parts = presentation.split("~")
    issuer_jwt, middle, last = parts[0], parts[1:-1], parts[-1]
    if issuer_jwt.count(".") != 2 or any(not d for d in middle):
        raise CredentialError("SD_JWT_MALFORMED")
    kb = last or None
    if kb is not None and kb.count(".") != 2:
        raise CredentialError("SD_JWT_MALFORMED", "key binding")
    return SdJwtParts(issuer_jwt, tuple(middle), kb, "~".join([issuer_jwt, *middle]) + "~")


def sd_hash(text: str) -> str:
    return b64e(hashlib.sha256(text.encode("ascii")).digest())


def disclose(payload: Mapping[str, Any], disclosures: Sequence[str]) -> dict[str, Any]:
    """RFC 9901 §7.1-7.3 processing with sha-256 only; returns the disclosed claim set."""
    if payload.get("_sd_alg", "sha-256") != "sha-256":
        raise CredentialError("SD_JWT_ALG_UNSUPPORTED")
    by_digest: dict[str, list[Any]] = {}
    for d in disclosures:
        digest = b64e(hashlib.sha256(d.encode("ascii")).digest())
        if digest in by_digest:
            raise CredentialError("SD_JWT_DUPLICATE_DISCLOSURE")
        try:
            decoded = json.loads(b64d(d))
        except ValueError as e:
            raise CredentialError("SD_JWT_DISCLOSURE_INVALID") from e
        if not isinstance(decoded, list) or len(decoded) not in (2, 3):
            raise CredentialError("SD_JWT_DISCLOSURE_INVALID")
        by_digest[digest] = decoded
    used: set[str] = set()

    def walk(node: Any) -> Any:
        if isinstance(node, dict):
            out: dict[str, Any] = {}
            for k, v in node.items():
                if k in ("_sd", "_sd_alg"):
                    continue
                out[k] = walk(v)
            for digest in node.get("_sd", []) or []:
                if digest in used:
                    raise CredentialError("SD_JWT_DIGEST_REUSED")
                disclosure = by_digest.get(digest)
                if disclosure is None:
                    continue  # a decoy or an undisclosed claim: stays protected
                if len(disclosure) != 3:
                    raise CredentialError("SD_JWT_DISCLOSURE_INVALID", "object disclosure")
                used.add(digest)
                _, name, value = disclosure
                if not isinstance(name, str) or name in RESERVED or name in out:
                    raise CredentialError("SD_JWT_CLAIM_COLLISION", str(name))
                out[name] = walk(value)
            return out
        if isinstance(node, list):
            items = []
            for item in node:
                if isinstance(item, dict) and set(item) == {"..."}:
                    digest = item["..."]
                    if digest in used:
                        raise CredentialError("SD_JWT_DIGEST_REUSED")
                    disclosure = by_digest.get(digest)
                    if disclosure is None:
                        continue
                    if len(disclosure) != 2:
                        raise CredentialError("SD_JWT_DISCLOSURE_INVALID", "array disclosure")
                    used.add(digest)
                    items.append(walk(disclosure[1]))
                else:
                    items.append(walk(item))
            return items
        return node

    claims = walk(dict(payload))
    if set(by_digest) - used:
        raise CredentialError("SD_JWT_DISCLOSURE_UNREFERENCED")
    return claims  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------- Token Status List

STATUS_VALUES = {0: "VALID", 1: "REVOKED", 2: "SUSPENDED"}


def status_at(lst: str, bits: int, idx: int) -> str:
    """Draft Token Status List: zlib-deflated bit array, `bits` per entry, LSB first."""
    try:
        deflated = b64d(lst)
    except ValueError as e:
        raise CredentialError("STATUS_LIST_INVALID") from e
    return status_at_raw(deflated, bits, idx)


MAX_STATUS_LIST_BYTES = 16 * 1024 * 1024


def status_at_raw(deflated: bytes, bits: int, idx: int) -> str:
    """The same list as raw bytes (the CWT form carries `lst` as a byte string)."""
    if bits not in (1, 2, 4, 8) or idx < 0:
        raise CredentialError("STATUS_LIST_INVALID")
    try:
        inflater = zlib.decompressobj()
        raw = inflater.decompress(deflated, MAX_STATUS_LIST_BYTES)  # bounded: no zip bomb
    except zlib.error as e:
        raise CredentialError("STATUS_LIST_INVALID") from e
    if inflater.unconsumed_tail:
        raise CredentialError("STATUS_LIST_INVALID", "too large")
    per_byte = 8 // bits
    byte_index, slot = divmod(idx, per_byte)
    if byte_index >= len(raw):
        raise CredentialError("STATUS_LIST_INDEX_OUT_OF_RANGE")
    value = (raw[byte_index] >> (slot * bits)) & ((1 << bits) - 1)
    return STATUS_VALUES.get(value, "UNKNOWN")


def encode_status_list(statuses: bytes) -> str:
    return b64e(zlib.compress(statuses, 9))


def set_status(statuses: bytearray, bits: int, idx: int, value: int) -> None:
    per_byte = 8 // bits
    byte_index, slot = divmod(idx, per_byte)
    mask = ((1 << bits) - 1) << (slot * bits)
    statuses[byte_index] = (statuses[byte_index] & ~mask) | ((value << (slot * bits)) & mask)


def status_decision(status: str, config: Configuration) -> None:
    """§13.1: UNKNOWN/STALE never become VALID; mandatory status fails closed."""
    if status == "VALID":
        return
    if status in ("REVOKED", "SUSPENDED"):
        raise CredentialError(f"CREDENTIAL_{status}")
    if config.status_mandatory:
        raise CredentialError("CREDENTIAL_STATUS_UNAVAILABLE")
