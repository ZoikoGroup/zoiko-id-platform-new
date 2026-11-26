"""WF08 services on top of the evidence ledger: a signed authority history, signed status lists, and disclosure-tier exports (CR-170).

Everything here is signed with the EVIDENCE key (never the access-token key) and says what it proves and what it does not.
"""

from __future__ import annotations

import base64
import gzip
import secrets
import uuid
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError
from zid_evidence import hash_json

from zid_platform.modules.evidence.application import replay
from zid_platform.modules.evidence.application.runtime import (
    current_runtime,
    sign_evidence,
    verify_evidence,
)
from zid_platform.modules.evidence.domain import authority_snapshot as snap
from zid_platform.modules.evidence.domain import export_tiers as tiers
from zid_platform.modules.evidence.domain import status_list as sl
from zid_platform.modules.evidence.infrastructure import receipt_store
from zid_platform.modules.evidence.infrastructure import wf08_store as store

SNAPSHOT_TYP = "zid-authority-snapshot+jwt"
EXPORT_TYP = "zid-export-manifest+jwt"
MAX_SLOT_DRAWS = 25


def _err(code: str, message: str, status: int = 422) -> ZidError:
    return ZidError(code, message, http_status=status)


# ------------------------------------------------------------------------------------------------------- authority history


async def record_authority_snapshot(
    conn: AsyncConnection, tenant_id: str, *, decision_ref: str, subject_ref: str, decision_at: datetime, edges: Sequence[Mapping[str, Any]], created_by: str | None,
    required: Sequence[str] = snap.REQUIRED,
) -> dict[str, Any]:  # fmt: skip
    """Write the authority history for one decision, once. A second call for the same decision returns the first: history is never replaced."""
    existing = await store.load_snapshot(conn, decision_ref)
    if existing is not None:
        return snapshot_view(existing)
    body = snap.assess(edges, decision_at, required=required)
    digest = str(hash_json(body, alg="sha-256"))
    signature = sign_evidence({"snapshot_digest": digest, "decision_ref": decision_ref, "subject_ref": subject_ref, "tenant_id": tenant_id, "verdict": body["verdict"]}, SNAPSHOT_TYP)
    await store.insert_snapshot(
        conn,
        {
            "snapshot_id": str(uuid.uuid4()), "tenant_id": tenant_id, "decision_ref": decision_ref, "subject_ref": subject_ref, "decision_at": decision_at, "verdict": body["verdict"],
            "reasons": body["reasons"], "body": body, "snapshot_digest": digest, "signature": signature, "signer_kid": str(current_runtime().signer.public_jwk()["kid"]), "created_by": created_by,
        },
    )  # fmt: skip
    row = await store.load_snapshot(conn, decision_ref)
    assert row is not None
    return snapshot_view(row)


def snapshot_view(row: Any) -> dict[str, Any]:
    """The snapshot as stored, with its own integrity re-checked from the body and the signature (a stored value is never taken on trust)."""
    body = dict(row.body)
    recomputed = str(hash_json(body, alg="sha-256"))
    problems: list[str] = []
    if recomputed != row.snapshot_digest:
        problems.append("BODY_DIFFERS_FROM_DIGEST")
    try:
        claims = verify_evidence(row.signature, SNAPSHOT_TYP)
        if claims.get("snapshot_digest") != row.snapshot_digest or claims.get("decision_ref") != row.decision_ref:
            problems.append("SIGNATURE_COVERS_ANOTHER_SNAPSHOT")
    except Exception:  # noqa: BLE001 - any failure to verify is the same answer
        problems.append("SIGNATURE_DOES_NOT_VERIFY")
    return {
        "snapshot_id": str(row.snapshot_id), "decision_ref": row.decision_ref, "subject_ref": row.subject_ref, "decision_at": row.decision_at.isoformat(), "verdict": row.verdict,
        "reasons": list(row.reasons), "snapshot_digest": row.snapshot_digest, "body": body, "signature": row.signature, "signer_kid": row.signer_kid,
        "integrity": "INVALID" if problems else "VALID", "integrity_problems": problems,
    }  # fmt: skip


# ----------------------------------------------------------------------------------------------------------- status lists


def _encode(bits: bytes) -> str:
    return base64.urlsafe_b64encode(gzip.compress(bits, mtime=0)).decode("ascii").rstrip("=")


def decode_list(encoded: str, size_bits: int) -> bytes:
    bits = gzip.decompress(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
    if len(bits) * 8 != size_bits:
        raise sl.StatusListError("list size differs from its claim")
    return bits


async def allocate_slot(conn: AsyncConnection, tenant_id: str, purpose: str, subject_ref: str) -> dict[str, Any]:
    """A random position for this credential or agent. Positions are never handed out in order, so a position says nothing about when it was issued."""
    if purpose not in sl.PURPOSES:
        raise _err("STATUS_LIST_PURPOSE_UNKNOWN", "purpose must be revocation or suspension")
    lst = await store.ensure_list(conn, tenant_id, purpose, sl.MIN_BITS)
    have = await store.slot_for(conn, lst.list_id, subject_ref)
    if have is not None:
        return {"list_id": str(lst.list_id), "position": int(have.position), "purpose": purpose, "size_bits": int(lst.size_bits)}
    for _ in range(MAX_SLOT_DRAWS):
        position = secrets.randbelow(int(lst.size_bits))
        if await store.insert_slot(conn, tenant_id, lst.list_id, position, subject_ref):
            return {"list_id": str(lst.list_id), "position": position, "purpose": purpose, "size_bits": int(lst.size_bits)}
    raise _err("STATUS_LIST_FULL", "no free position was found; start a new list", 409)


async def change_status(conn: AsyncConnection, tenant_id: str, purpose: str, subject_ref: str, *, revoke: bool, reason: str, by: str | None) -> dict[str, Any]:
    lst = await store.ensure_list(conn, tenant_id, purpose, sl.MIN_BITS)
    slot = await store.slot_for(conn, lst.list_id, subject_ref)
    if slot is None:
        raise _err("STATUS_SLOT_UNKNOWN", "this subject has no position in the list", 404)
    await store.insert_change(conn, tenant_id, slot.slot_id, "SET" if revoke else "REINSTATE", reason, by)
    return {"list_id": str(lst.list_id), "purpose": purpose, "change": "SET" if revoke else "REINSTATE", "published": False, "note": "Relying parties see this only after the next publication."}


async def publish_list(conn: AsyncConnection, tenant_id: str, purpose: str, *, issuer: str, environment: str, now: datetime | None = None, ttl: timedelta = sl.TTL) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    lst = await store.ensure_list(conn, tenant_id, purpose, sl.MIN_BITS)
    bits = sl.new_bits(int(lst.size_bits))
    for position in await store.marked_positions(conn, lst.list_id):
        sl.set_bit(bits, position)
    version = await store.next_version(conn, lst.list_id)
    claims = sl.list_claims(list_id=str(lst.list_id), purpose=purpose, version=version, encoded=_encode(bytes(bits)), size_bits=int(lst.size_bits), issuer=issuer, environment=environment, now=now, ttl=ttl)
    jws = sign_evidence(claims, sl.TYP)
    await store.insert_publication(conn, tenant_id, lst.list_id, version, jws, now, datetime.fromisoformat(claims["expires_at"]))
    return {"list_id": str(lst.list_id), "version": version, "issued_at": claims["issued_at"], "expires_at": claims["expires_at"], "jws": jws}


def check_published(jws: str, position: int, *, now: datetime, max_age: timedelta | None, issuer: str, environment: str) -> str:
    """What a relying party does: verify the list's signature, then look its own position up locally."""
    try:
        claims = dict(verify_evidence(jws, sl.TYP))
        bits = decode_list(str(claims["encoded_list"]), int(claims["size_bits"]))
    except Exception:  # noqa: BLE001
        return "UNKNOWN"
    return sl.lookup(claims, bits, position, now=now, max_age=max_age, expected_issuer=issuer, expected_environment=environment)


# ------------------------------------------------------------------------------------------------------------------ exports


def row_view(r: Any) -> dict[str, Any]:
    return {
        "request_id": str(r.request_id), "receipt_id": str(r.receipt_id), "tier": r.tier, "recipient": dict(r.recipient), "purpose": r.purpose, "legal_basis": r.legal_basis,
        "retention_days": r.retention_days, "recipient_country": r.recipient_country, "cross_border": r.cross_border, "needs_second_person": r.needs_second_person, "state": r.state,
        "requested_by": str(r.requested_by), "requested_at": r.requested_at.isoformat(), "decided_by": str(r.decided_by) if r.decided_by else None,
        "decision_reason": r.decision_reason, "issued_at": r.issued_at.isoformat() if r.issued_at else None, "expires_at": r.expires_at.isoformat() if r.expires_at else None,
        "package_digest": r.package_digest,
    }  # fmt: skip


async def request_export(
    conn: AsyncConnection, tenant_id: str, *, receipt_id: str, tier: str, recipient: Mapping[str, Any] | None, purpose: str, legal_basis: str | None, retention_days: int,
    recipient_country: str | None, tenant_country: str, requested_by: str, authenticated: bool = True,
) -> dict[str, Any]:  # fmt: skip
    problems = tiers.request_problems(tier=tier, authenticated=authenticated, recipient=recipient, purpose=purpose, legal_basis=legal_basis, retention_days=retention_days, recipient_country=recipient_country)
    if problems:
        raise _err("EXPORT_TIER_REQUEST_REFUSED", "; ".join(problems))
    cross = tiers.is_cross_border(tenant_country, recipient_country) if tier != "PUBLIC_MINIMAL" else False
    request_id = str(uuid.uuid4())
    await store.insert_export_request(
        conn,
        {
            "request_id": request_id, "tenant_id": tenant_id, "receipt_id": receipt_id, "tier": tier, "recipient": dict(recipient or {}), "purpose": purpose.strip(), "legal_basis": legal_basis,
            "retention_days": retention_days, "recipient_country": recipient_country, "cross_border": cross, "needs_second_person": tiers.needs_second_person(tier, cross_border=cross),
            "requested_by": requested_by,
        },
    )  # fmt: skip
    row = await store.load_export_request(conn, request_id)
    assert row is not None
    return row_view(row)


async def decide_export(conn: AsyncConnection, request_id: str, *, approve: bool, decided_by: str, reason: str | None) -> dict[str, Any]:
    row = await store.load_export_request(conn, request_id)
    if row is None:
        raise _err("NOT_FOUND", "unknown export request", 404)
    if str(row.requested_by) == decided_by:
        raise _err("EXPORT_SECOND_PERSON_REQUIRED", "the person who asked cannot decide the request", 403)
    if not approve and not (reason or "").strip():
        raise _err("EXPORT_REFUSAL_NEEDS_REASON", "a refusal needs a reason")
    if not await store.decide_export_request(conn, request_id, approve=approve, decided_by=decided_by, reason=reason):
        raise _err("EXPORT_REQUEST_NOT_OPEN", "the request is no longer waiting for a decision", 409)
    row = await store.load_export_request(conn, request_id)
    assert row is not None
    return row_view(row)


async def issue_export(conn: AsyncConnection, tenant_id: str, request_id: str, *, issued_by: str) -> dict[str, Any]:
    """Build the package for the tier, sign a manifest naming the recipient, and record both. Refused until the second person has approved where one is needed."""
    row = await store.load_export_request(conn, request_id)
    if row is None:
        raise _err("NOT_FOUND", "unknown export request", 404)
    if row.state == "ISSUED":
        raise _err("EXPORT_ALREADY_ISSUED", "this request was already issued", 409)
    if row.state == "REFUSED":
        raise _err("EXPORT_REQUEST_REFUSED", "this request was refused", 409)
    if row.needs_second_person and row.state != "APPROVED":
        raise _err("EXPORT_SECOND_PERSON_REQUIRED", "this tier needs a second person's approval before it is issued", 403)
    receipt = await receipt_store.load_receipt(conn, str(row.receipt_id))
    if receipt is None:
        raise _err("NOT_FOUND", "unknown receipt", 404)
    withheld = tiers.withheld_for(row.tier, dict(receipt.body))
    package = await replay.export_package(
        conn, tenant_id, str(row.receipt_id), mode=tiers.EXPORT_MODE[row.tier], purpose=row.purpose or "public integrity proof", case_ref=f"export-request:{request_id}", custodian_principal_id=issued_by,
        redact=withheld or None,
    )  # fmt: skip
    now = datetime.now(UTC)
    expires = now + timedelta(days=int(row.retention_days))
    digest = str(hash_json(package, alg="sha-256"))
    manifest = {
        "request_id": request_id, "receipt_id": str(row.receipt_id), "tier": row.tier, "recipient": {k: v for k, v in dict(row.recipient).items() if k in ("name", "kind")},
        "purpose": row.purpose, "legal_basis": row.legal_basis, "recipient_country": row.recipient_country, "cross_border": row.cross_border, "package_digest": digest,
        "issued_at": now.isoformat(), "expires_at": expires.isoformat(), "second_person": str(row.decided_by) if row.decided_by else None,
        "withheld_fields": withheld,
    }  # fmt: skip
    jws = sign_evidence(manifest, EXPORT_TYP)
    if not await store.issue_export_request(conn, request_id, package_digest=digest, manifest_jws=jws, expires_at=expires):
        raise _err("EXPORT_REQUEST_NOT_OPEN", "the request changed while it was being issued", 409)
    done = await store.load_export_request(conn, request_id)
    assert done is not None
    return {"request": row_view(done), "manifest": manifest, "manifest_jws": jws, "package": package}
