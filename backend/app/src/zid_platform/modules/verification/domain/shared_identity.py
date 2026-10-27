"""A reusable verified identity relied on from another cell (ZID-TENANCY-001 §10; TEN-24). Pure domain.

The identity's own home cell is authoritative for it. A relying tenant in another cell receives an approved assertion
or commitment, never the source profile: no provider, provider reference, document evidence, reason codes or the
identity's profile attributes. The payload is built field by field from an allowlist, so a column added to the
assertion row later cannot leak by being copied along, and it is re-checked against the forbidden names before it
leaves the cell.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime
from typing import Any

from zid_platform.modules.verification.domain.ida import IAL_RANK

SHARED_FIELDS = (
    "assertion_id",
    "claim_type",
    "assurance_level",
    "performed_at",
    "valid_until",
    "commitment",
    "home_cell_id",
)
# What belongs to the source profile and must never cross: who proved it, how it was evidenced, and why it was decided.
SOURCE_PROFILE_FIELDS = frozenset(
    {
        "provider_id",
        "provider_reference",
        "evidence_types",
        "evidence_commitment",
        "verification_method",
        "reason_codes",
        "session_id",
        "subject_principal_id",
        "claim_value",
        "claim_value_commitment",
        "jurisdiction",
        "legal_name",
        "email",
        "phone",
    }
)


class SharedIdentityError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def assertion_for_reliance(a: Mapping[str, Any], home_cell_id: str) -> dict[str, Any]:
    """One assertion as the relying cell may see it: its level and validity, and the commitment it can compare a
    presented value against (a hash, so it proves a value without revealing one)."""
    commitment = a.get("claim_value_commitment")
    return {
        "assertion_id": str(a["assertion_id"]),
        "claim_type": a["claim_type"],
        "assurance_level": a["assurance_level"],
        "performed_at": a["performed_at"].isoformat(),
        "valid_until": a["valid_until"].isoformat() if a.get("valid_until") else None,
        "commitment": bytes(commitment).hex() if commitment is not None else None,
        "home_cell_id": home_cell_id,
    }


def shareable(
    assertions: Sequence[Mapping[str, Any]],
    *,
    home_cell_id: str,
    purpose: str,
    requested: Collection[str],
    now: datetime,
    min_level: str = "IAL-Z1",
    allowlist: Sequence[str] = (),
) -> list[dict[str, Any]]:
    """Only VALID, unexpired assertions of a requested claim type, whose profile lists the purpose for reliance and
    that meet the level asked for. `allowlist` narrows the fields further (an approved transfer rule's field list)."""
    keep = [f for f in SHARED_FIELDS if not allowlist or f in allowlist]
    out: list[dict[str, Any]] = []
    for a in assertions:
        if a["claim_type"] not in requested or a["status"] != "VALID":
            continue
        if a.get("valid_until") is not None and a["valid_until"] <= now:
            continue
        if purpose not in (a.get("purposes") or []):
            continue
        if IAL_RANK.get(a["assurance_level"], 0) < IAL_RANK[min_level]:
            continue
        built = assertion_for_reliance(a, home_cell_id)
        out.append({k: built[k] for k in keep})
    forbidden = {k for item in out for k in item} & SOURCE_PROFILE_FIELDS
    if forbidden:  # defence in depth: the allowlist above already makes this unreachable
        raise SharedIdentityError("SOURCE_PROFILE_DISCLOSURE", ", ".join(sorted(forbidden)))
    return out
