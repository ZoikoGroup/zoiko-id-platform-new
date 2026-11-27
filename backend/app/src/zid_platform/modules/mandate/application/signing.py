"""Putting a name to a mandate's exact terms (ZID-CP-WF07 v2.0 .04; A07-03; CR-169).

The grantor reads the canonical terms, then confirms THEIR HASH with a fresh strong sign-in (the route asks the policy decision point for a catalogued R3 action, which refuses a stale or weak sign-in). For a mandate that can
spend, an independent second person does the same. Each confirmation is signed by Zoiko iD and kept append-only. With signing switched to enforce, a mandate cannot be activated until every required confirmation exists for its
exact current terms. A confirmation of anything else counts for nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import peek_header

from zid_platform.modules.mandate.domain import canonical as c
from zid_platform.modules.mandate.domain.ledger import LedgerError
from zid_platform.modules.mandate.infrastructure import mandate_store as store

Sign = Callable[[Mapping[str, Any], str], str]
Verify = Callable[[str], Mapping[str, Any]]


async def _load(
    conn: AsyncConnection, mandate_id: str
) -> tuple[dict[str, Any], dict[str, Any], str]:
    row = await store.canonical_row(conn, mandate_id)
    if row is None:
        raise LedgerError("MANDATE_NOT_FOUND")
    terms = c.canonical_terms(row)
    return row, terms, c.canonical_hash(terms)


def _sig_view(
    s: Mapping[str, Any], current: str, mandate_id: str, verify: Verify | None
) -> dict[str, Any]:
    out = {
        "signature_id": str(s["signature_id"]), "role": s["role"], "canonical_hash": s["canonical_hash"], "signer": str(s["signer_principal_id"]), "assurance_level": s["assurance_level"],
        "auth_time": s["auth_time"].isoformat() if s["auth_time"] else None, "signed_at": s["signed_at"].isoformat(), "key_id": s["key_id"], "signature": s["signature"],
        "current": s["canonical_hash"] == current,
    }  # fmt: skip
    if verify is not None:
        try:
            claims = verify(s["signature"])
            out["signature_valid"] = (
                claims.get("canonical_hash") == s["canonical_hash"]
                and claims.get("mandate_id") == mandate_id
                and claims.get("role") == s["role"]
                and claims.get("signer") == str(s["signer_principal_id"])
            )
        except Exception:  # noqa: BLE001 - one that does not verify is reported as such
            out["signature_valid"] = False
    return out


async def view(
    conn: AsyncConnection, mandate_id: str, verify: Verify | None = None
) -> dict[str, Any]:
    _row, terms, h = await _load(conn, mandate_id)
    sigs = await store.signatures_of(conn, mandate_id)
    return {
        "mandate_id": mandate_id, "canonical_version": c.CANONICAL_VERSION, "canonical_terms": terms, "canonical_json": c.canonical_json(terms), "canonical_hash": h,
        "signing": c.signed_state(c.required_roles(terms), sigs, h), "signatures": [_sig_view(s, h, mandate_id, verify) for s in sigs],
        "note": "A platform signature over a grantor's confirmed hash. It is not a qualified electronic signature.",
    }  # fmt: skip


async def sign(
    conn: AsyncConnection, tenant_id: str, *, mandate_id: str, role: c.Role, signer: str, presented_hash: str | None, assurance_level: int, auth_time: datetime | None, signer_fn: Sign, issuer: str, verify: Verify | None = None, now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    now = now or datetime.now(UTC)
    row, terms, h = await _load(conn, mandate_id)
    c.check_hash(presented_hash, h)
    prior = await store.signatures_of(conn, mandate_id)
    c.check_signer(
        role,
        signer=signer,
        creator=str(row["created_by_principal_id"]),
        executor=str(row["executor_principal_id"]),
        represented=str(row["represented_principal_id"]),
        prior=[p for p in prior if p["canonical_hash"] == h],
    )
    if role is c.Role.APPROVER and c.Role.APPROVER not in c.required_roles(terms):
        raise LedgerError(
            "MANDATE_NO_SECOND_CONFIRMATION_NEEDED",
            "this mandate cannot spend, so it needs only its grantor's confirmation",
        )
    claims = {
        "typ": c.SIGNATURE_TYP,
        "iss": issuer,
        "mandate_id": mandate_id,
        "tenant_id": tenant_id,
        "canonical_hash": h,
        "role": role.value,
        "signer": signer,
        "assurance_level": assurance_level,
        "iat": int(now.timestamp()),
    }
    token = signer_fn(claims, c.SIGNATURE_TYP)
    await store.add_signature(
        conn, tenant_id, mandate_id=mandate_id, role=role.value, canonical_hash=h, signer=signer, assurance_level=assurance_level, auth_time=auth_time, signature=token,
        key_id=str(peek_header(token).get("kid")) if token.count(".") == 2 else None, signed_at=now,
    )  # fmt: skip
    return await view(conn, mandate_id, verify)


async def require_signed(conn: AsyncConnection, mandate_id: str) -> None:
    """Called before activation when signing is enforced: every confirmation this mandate needs must exist for its exact current terms."""
    _row, terms, h = await _load(conn, mandate_id)
    state = c.signed_state(c.required_roles(terms), await store.signatures_of(conn, mandate_id), h)
    if not state["complete"]:
        raise LedgerError(
            "MANDATE_NOT_SIGNED",
            "this mandate has not been confirmed in full: "
            + ", ".join(state["missing"]).lower()
            + " still to confirm its exact terms",
        )
