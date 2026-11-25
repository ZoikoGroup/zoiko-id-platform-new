"""Two-person approval of a production credential (CR-162; ZID-EXT-IDV-001 WF-05, section 05).

One company administrator asks for an approval, a DIFFERENT one approves it, and the person who asked spends it once, within a day, to create or rotate a production credential. The approval names the application, the action,
the credential being rotated and (for a private-key credential) the fingerprint of the exact public key, so it cannot be spent on anything else. A sandbox application never needs one.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.application import certificates as certs
from zid_platform.modules.verify_api.application import observe
from zid_platform.modules.verify_api.domain import approvals as a
from zid_platform.modules.verify_api.domain import credential_options as opts
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import approval_store as db
from zid_platform.modules.verify_api.infrastructure import store


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def view(r: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    status = r["status"]
    if status in (a.Status.PENDING.value, a.Status.APPROVED.value) and r["expires_at"] <= now:
        status = "EXPIRED"
    return {
        "approval_id": str(r["approval_id"]),
        "application_id": str(r["application_id"]),
        "action": r["action"],
        "credential_id": str(r["credential_id"]) if r["credential_id"] else None,
        "with_key": r["key_digest"] is not None,
        "status": status,
        "requested_by": str(r["requested_by"]),
        "decided_by": str(r["decided_by"]) if r["decided_by"] else None,
        "note": r["note"],
        "created_at": _iso(r["created_at"]),
        "expires_at": _iso(r["expires_at"]),
        "decided_at": _iso(r["decided_at"]),
    }


async def list_view(conn: AsyncConnection) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {"items": [view(r, now) for r in await db.list_approvals(conn)]}


async def request(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    body: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    action = a.check_action(body.get("action"))
    credential_id = body.get("credential_id") or None
    a.check_shape(action, credential_id=credential_id)
    app = await store.get_application(conn, application_id)
    if app is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if app["environment"] != "PRODUCTION":
        raise m.PartnerError(
            "VALIDATION_FAILED",
            "Only a production application needs an approval. Credentials for the sandbox are made directly.",
        )
    if app["status"] != "ACTIVE":
        raise m.PartnerError(
            "APPLICATION_STATE_INVALID", "Approvals are for an active application."
        )
    if (
        credential_id is not None
        and await store.get_credential(conn, application_id, credential_id) is None
    ):
        raise m.PartnerError("NOT_FOUND", "No such credential.")
    jwk = body.get("public_jwk")
    pem = body.get("certificate_pem")
    if jwk is not None and pem is not None:
        raise m.PartnerError("VALIDATION_FAILED", "Name a public key or a certificate, not both.")
    digest = a.key_digest(opts.check_public_jwk(jwk)) if jwk is not None else None
    if pem is not None:
        digest = certs.check_certificate(pem, now=now).sha256
    approval_id = str(uuid.uuid4())
    await db.insert_approval(
        conn,
        approval_id=approval_id,
        tenant_id=tenant_id,
        application_id=application_id,
        action=action.value,
        credential_id=credential_id,
        key_digest=digest,
        requested_by=principal,
        expires_at=now + a.APPROVAL_TTL,
    )
    observe.APPROVALS.inc(event="requested")
    made = await db.get_approval(conn, approval_id)
    assert made is not None
    return view(made, now)


async def _open(conn: AsyncConnection, approval_id: str, now: datetime) -> dict[str, Any]:
    r = await db.get_approval(conn, approval_id, lock=True)
    if r is None:
        raise m.PartnerError("NOT_FOUND", "No such approval.")
    if r["status"] != a.Status.PENDING.value:
        raise m.PartnerError(
            "APPROVAL_STATE_INVALID", f"A {r['status'].lower()} approval cannot be decided again."
        )
    if r["expires_at"] <= now:
        raise m.PartnerError(
            "APPROVAL_STATE_INVALID", "This approval request has expired. Ask again."
        )
    return r


async def approve(
    conn: AsyncConnection,
    approval_id: str,
    decider: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _open(conn, approval_id, now)
    a.check_decider(str(r["requested_by"]), decider)
    await db.decide(
        conn, approval_id, a.Status.APPROVED.value, decider, a.check_note(note, required=False), now
    )
    observe.APPROVALS.inc(event="approved")
    fresh = await db.get_approval(conn, approval_id)
    assert fresh is not None
    return view(fresh, now)


async def reject(
    conn: AsyncConnection,
    approval_id: str,
    decider: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    r = await _open(conn, approval_id, now)
    a.check_decider(str(r["requested_by"]), decider)
    await db.decide(
        conn, approval_id, a.Status.REJECTED.value, decider, a.check_note(note, required=True), now
    )
    observe.APPROVALS.inc(event="rejected")
    fresh = await db.get_approval(conn, approval_id)
    assert fresh is not None
    return view(fresh, now)


async def cancel(
    conn: AsyncConnection, approval_id: str, principal: str, now: datetime | None = None
) -> dict[str, Any]:
    """Only the person who asked can take the request back, while it waits."""
    now = now or datetime.now(UTC)
    r = await _open(conn, approval_id, now)
    if str(r["requested_by"]) != principal:
        raise m.PartnerError("NOT_FOUND", "No such approval.")
    await db.decide(conn, approval_id, a.Status.CANCELED.value, None, None, now)
    observe.APPROVALS.inc(event="canceled")
    fresh = await db.get_approval(conn, approval_id)
    assert fresh is not None
    return view(fresh, now)


async def spend(
    conn: AsyncConnection,
    approval_id: str | None,
    *,
    principal: str,
    application_id: str,
    action: a.Action,
    credential_id: str | None,
    key: dict[str, Any] | None = None,
    digest: bytes | None = None,
    now: datetime,
) -> None:
    """Called when a production credential is about to be made: the approval must be approved, unexpired, the caller's own, and for exactly this. It is spent here, once, in the same transaction as the credential."""
    if not approval_id:
        raise m.PartnerError(
            "APPROVAL_REQUIRED",
            "A production credential needs a second administrator's approval. Ask for one, have another administrator approve it, then use it within 24 hours.",
        )
    try:
        uuid.UUID(approval_id)
    except ValueError as e:
        raise m.PartnerError("APPROVAL_REQUIRED", "That is not an approval.") from e
    r = await db.get_approval(conn, approval_id, lock=True)
    if r is None:
        raise m.PartnerError("APPROVAL_REQUIRED", "That is not an approval.")
    a.check_usable(
        r,
        principal=principal,
        application_id=application_id,
        action=action,
        credential_id=credential_id,
        digest=a.key_digest(key) if key is not None else digest,
        now=now,
    )
    if not await db.consume(conn, approval_id, now):
        raise m.PartnerError("APPROVAL_REQUIRED", "That approval has already been used.")
    observe.APPROVALS.inc(event="spent")
