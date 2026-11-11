"""The complete normalized credential assertion (ZID-GAP-001 v2.0 section 08.1) and its signature. Pure.

The domain assertion (`claims.Assertion`) holds the five separate facts. This module completes it into the contract a relying party
receives: identifiers, the consumer, the consent it was made under, the revocation status, the evidence receipts, how the subject was
matched, and a signature that binds all of it.

The signature is a compact JWS (typ `zid-assertion+jwt`) over a SMALL claim set: the check id, the assertion id, the SHA-256 of the
canonical body, and the time. A holder or verifier recomputes the digest from the body they were given; any change to any field, in any
order, fails. Keys `signature` and `superseded` are not part of the body (the second says whether a newer check replaced this one).
Verification of the JWS itself is the caller's (`zid_crypto.verify_jws`, or the SDKs): this module only does the binding.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Any

SIGNATURE_TYP = "zid-assertion+jwt"
NOT_PART_OF_THE_BODY = frozenset({"signature", "superseded"})
Signer = Callable[
    [dict[str, Any]], str
]  # claims -> compact JWS signed with the platform's credential key

REVOCATION = {
    "ACTIVE": "GOOD",
    "REVOKED": "REVOKED",
    "SUSPENDED": "SUSPENDED",
    "EXPIRED": "EXPIRED",
}


def revocation_status(credential_status: str) -> str:
    """The section 08.1 `revocation_status`: GOOD only for an ACTIVE credential; an unknown status is UNKNOWN, never GOOD."""
    return REVOCATION.get(credential_status, "UNKNOWN")


def match_method(source_result: str, tier_name: str, source_id: str) -> str:
    """How the subject was matched: by the primary source, by a reviewed issuer answer, or not at all."""
    if source_result == "NOT_QUERIED":
        return "NOT_CHECKED"
    if source_id.startswith("outreach"):
        return "ISSUER_OUTREACH"
    if source_result == "RESPONDED" and tier_name == "PRIMARY_SOURCE_CONFIRMED":
        return "PRIMARY_SOURCE"
    return "NONE"


def _normalize(body: Mapping[str, Any]) -> dict[str, Any]:
    """Round-trip through JSON so the digest covers exactly what a reader will see (dates as strings, tuples as lists)."""
    return json.loads(json.dumps(body, default=str, sort_keys=True))  # type: ignore[no-any-return]


def canonical(body: Mapping[str, Any]) -> bytes:
    stripped = {k: v for k, v in _normalize(body).items() if k not in NOT_PART_OF_THE_BODY}
    return json.dumps(stripped, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def body_digest(body: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical(body)).hexdigest()


def complete(
    contract: Mapping[str, Any],
    *,
    check_id: str,
    subject: str,
    consumer: str,
    consent_ref: str | None,
    receipts: Sequence[str],
    pack_id: str,
    pack_version: int,
    source_result: str,
    source_id: str,
) -> dict[str, Any]:
    """Add the section 08.1 fields. The subject appears only as an opaque reference, never a name or a date."""
    body = dict(contract)
    decision = (body.get("subject_match") or {}).get("decision", "NOT_CHECKED")
    body.update(
        {
            "check_id": check_id,
            "assertion_id": f"asrt_{check_id}",
            "actor_ref": f"sub_{subject}",
            "consumer": consumer,
            "consent_ref": consent_ref,
            "policy_ref": {"id": pack_id, "version": pack_version},
            "revocation_status": revocation_status(str(body.get("credential_status", ""))),
            "evidence_receipts": [f"rcpt_{r}" for r in receipts],
            "subject_match": {
                "decision": decision,
                "method": match_method(
                    source_result, str(body.get("assurance_tier", "")), source_id
                ),
            },
        }
    )
    return body


def seal(body: Mapping[str, Any], signer: Signer | None, now: datetime) -> dict[str, Any]:
    """The body plus its signature. With no signer the signature is null (local use); the API always supplies one."""
    out = {k: v for k, v in body.items() if k not in NOT_PART_OF_THE_BODY}
    if signer is None:
        out["signature"] = None
        return out
    claims = {
        "typ": "zid-assertion",
        "check_id": out["check_id"],
        "assertion_id": out["assertion_id"],
        "body_sha256": body_digest(out),
        "iat": int(now.timestamp()),
    }
    out["signature"] = signer(claims)
    return out


def binds(contract: Mapping[str, Any], signed_claims: Mapping[str, Any]) -> bool:
    """Do the (already signature-verified) claims bind THIS body? Any change to the body fails."""
    return (
        signed_claims.get("typ") == "zid-assertion"
        and signed_claims.get("check_id") == contract.get("check_id")
        and signed_claims.get("assertion_id") == contract.get("assertion_id")
        and signed_claims.get("body_sha256") == body_digest(contract)
    )
