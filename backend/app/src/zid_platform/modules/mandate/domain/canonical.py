"""A mandate, written down exactly, and who has put their name to that exact text (ZID-CP-WF07 v2.0 .04; A07-03; CR-169). Pure domain, no I/O.

A mandate is a person's own authority handed on, so what it says must be fixed and what a person agreed to must be provable. The canonical form is every term that gives or limits power, in one fixed order, with sets sorted and
numbers and times written one way. Its SHA-256 is the mandate's hash. The grantor confirms that hash (with a fresh strong sign-in), and for a mandate that can spend, a second person confirms it too. Zoiko iD then signs each
confirmation. A mandate's terms can never be edited (the database refuses), so a different term is a different mandate with a different hash and needs its own confirmations.

This is a platform signature over a grantor's confirmed hash. It is not a qualified electronic signature and does not claim to be one.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from zid_platform.modules.mandate.domain.ledger import LedgerError

SIGNATURE_TYP = "zid-mandate-signature+jwt"
CANONICAL_VERSION = "1"
MODES = ("off", "enforce")


class Role(StrEnum):
    GRANTOR = "GRANTOR"  # the person whose authority is handed on, confirming exactly what they hand on
    APPROVER = "APPROVER"  # an independent second person, for a mandate that can spend


def _dec(v: Decimal | None) -> str | None:
    return None if v is None else format(v.normalize(), "f") if v != 0 else "0"


def _at(v: datetime) -> str:
    return v.astimezone(UTC).isoformat()


def canonical_terms(row: Mapping[str, Any]) -> dict[str, Any]:
    """Every term that gives or limits power. Anything that is only bookkeeping (status, row version, timestamps of creation) is left out, so pausing a mandate does not change what was agreed."""
    return {
        "v": CANONICAL_VERSION,
        "mandate_id": str(row["mandate_id"]),
        "tenant_id": str(row["tenant_id"]),
        "authority_grant_id": str(row["authority_grant_id"]),
        "represented_principal_id": str(row["represented_principal_id"]),
        "executor_principal_id": str(row["executor_principal_id"]),
        "purpose_id": str(row["purpose_id"]),
        "resource_scope_id": str(row["resource_scope_id"]),
        "allowed_actions": sorted(row["allowed_actions"]),
        "resource_scopes": sorted(row["resource_scopes"] or []),
        "tools": sorted(row["tools"] or []),
        "jurisdictions": sorted(row["jurisdictions"] or []),
        "currency": (row["currency"] or "").strip() or None,
        "value_limit": _dec(row["value_limit"]),
        "per_transaction_value_limit": _dec(row["per_transaction_value_limit"]),
        "count_limit": row["count_limit"],
        "data_limit_bytes": row["data_limit_bytes"],
        "valid_from": _at(row["valid_from"]),
        "valid_to": _at(row["valid_to"]),
        "delegation_allowed": bool(row["delegation_allowed"]),
        "max_delegation_depth": row["max_delegation_depth"],
        "budget_mode": row["budget_mode"],
        "parent_mandate_id": str(row["parent_mandate_id"]) if row["parent_mandate_id"] else None,
        "created_by_principal_id": str(row["created_by_principal_id"]),
    }


def canonical_json(terms: Mapping[str, Any]) -> str:
    return json.dumps(terms, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def canonical_hash(terms: Mapping[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(terms).encode()).hexdigest()


def can_spend(terms: Mapping[str, Any]) -> bool:
    """A mandate that can move value or count is high-risk enough to need a second person."""
    return any(terms.get(k) is not None for k in ("value_limit", "per_transaction_value_limit", "count_limit"))


def required_roles(terms: Mapping[str, Any]) -> tuple[Role, ...]:
    return (Role.GRANTOR, Role.APPROVER) if can_spend(terms) else (Role.GRANTOR,)


def check_signer(role: Role, *, signer: str, creator: str, executor: str, represented: str, prior: Sequence[Mapping[str, Any]]) -> None:
    """Who may confirm. The grantor is the person who handed their authority on; the approver is someone else entirely, never the agent, never the person it acts for and never someone who already signed."""
    if role is Role.GRANTOR:
        if signer != creator:
            raise LedgerError("MANDATE_SIGNER_NOT_GRANTOR", "only the person who created this mandate from their own authority can confirm it as its grantor")
        return
    if signer in (creator, executor, represented) or any(str(p["signer_principal_id"]) == signer for p in prior):
        raise LedgerError("MANDATE_SIGNER_NOT_INDEPENDENT", "the second confirmation must come from someone who is not the grantor, the agent or the person it acts for, and has not signed")


def check_hash(presented: str | None, actual: str) -> None:
    if presented != actual:
        raise LedgerError("MANDATE_HASH_MISMATCH", "the terms you looked at are not the terms this mandate has now; read them again")


def signed_state(required: Sequence[Role], signatures: Sequence[Mapping[str, Any]], current_hash: str) -> dict[str, Any]:
    """Which required confirmations exist for THESE exact terms. A confirmation of any other hash counts for nothing."""
    have = {Role(str(s["role"])) for s in signatures if s["canonical_hash"] == current_hash}
    missing = [r.value for r in required if r not in have]
    return {"required": [r.value for r in required], "have": sorted(r.value for r in have), "missing": missing, "complete": not missing, "stale": sorted({str(s["canonical_hash"]) for s in signatures if s["canonical_hash"] != current_hash})}
