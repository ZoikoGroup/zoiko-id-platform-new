"""A partner's logo on the verification page (CR-166; docs/specs/verify-api-mtls-domains-logos-decisions.md).

Uploaded, scanned and re-encoded (never linked). A sandbox application's logo shows at once; a production application's shows only after an operator has approved that exact image (by digest). The person's page asks for
the APPROVED image only, by the session's link, and falls back to the partner's name as text when there is none.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.ports import CaptureError, PartnerServices, partner_capture
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import logo_store as db
from zid_platform.modules.verify_api.infrastructure import store

MAX_UPLOAD = 2 * 1024 * 1024
MAX_STORED = 300 * 1024
NOTE_MAX = 1000


def view(meta: dict[str, Any] | None, environment: str) -> dict[str, Any]:
    if meta is None or meta["status"] == "REMOVED":
        return {"status": "NONE", "shown": False}
    return {
        "status": meta["status"],
        "shown": meta["status"] == "APPROVED"
        or (environment == "SANDBOX" and meta["status"] != "REJECTED"),
        "digest": bytes(meta["digest"]).hex(),
        "uploaded_at": meta["uploaded_at"].isoformat(),
        "note": meta["note"],
    }


async def get(conn: AsyncConnection, application_id: str) -> dict[str, Any]:
    a = await store.get_application(conn, application_id)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    return view(await db.meta(conn, application_id), a["environment"])


async def upload(
    conn: AsyncConnection,
    tenant_id: str,
    principal: str,
    application_id: str,
    image_base64: str,
    sv: PartnerServices,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    a = await store.get_application(conn, application_id, lock=True)
    if a is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    if a["status"] == "RETIRED":
        raise m.PartnerError(
            "APPLICATION_STATE_INVALID", "A retired application cannot be changed."
        )
    try:
        raw = base64.b64decode(image_base64, validate=True)
    except (binascii.Error, ValueError) as e:
        raise m.PartnerError("VALIDATION_FAILED", "The logo is not valid.") from e
    if not 100 <= len(raw) <= MAX_UPLOAD:
        raise m.PartnerError("VALIDATION_FAILED", "The logo must be a JPEG or PNG under 2 MB.")
    if not raw.startswith((b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")):
        raise m.PartnerError("VALIDATION_FAILED", "The logo must be a JPEG or PNG.")
    try:
        clean = await partner_capture.clean_image(sv, raw)
    except CaptureError as e:
        raise m.PartnerError(
            "CAPTURE_REFUSED" if e.status < 500 else "CAPTURE_UNAVAILABLE", e.detail
        ) from e
    if len(clean) > MAX_STORED:
        raise m.PartnerError(
            "VALIDATION_FAILED",
            "That logo is too detailed. Use a simpler or smaller picture (under 300 KB once prepared).",
        )
    status = "APPROVED" if a["environment"] == "SANDBOX" else "PENDING"
    await db.put(
        conn,
        application_id=application_id,
        tenant_id=tenant_id,
        image=clean,
        digest=hashlib.sha256(clean).digest(),
        status=status,
        uploaded_by=principal,
        now=now,
    )
    return await get(conn, application_id)


async def remove(conn: AsyncConnection, application_id: str) -> dict[str, Any]:
    if await store.get_application(conn, application_id) is None:
        raise m.PartnerError("NOT_FOUND", "No such application.")
    await db.remove(conn, application_id)
    return await get(conn, application_id)


async def preview(conn: AsyncConnection, application_id: str) -> bytes:
    """The company's own view of its uploaded logo, whatever its status."""
    img = await db.image(conn, application_id, approved_only=False)
    if img is None:
        raise m.PartnerError("NOT_FOUND", "No logo.")
    return img


async def shown_image(conn: AsyncConnection, application_id: str, environment: str) -> bytes | None:
    """For the person's page, inside `auth_scope`: the image only if it may be shown (approved; or any non-rejected one in the sandbox)."""
    meta = await db.meta(conn, application_id)
    if meta is None or not view(meta, environment)["shown"]:
        return None
    return await db.image(conn, application_id, approved_only=False)


# ------------------------------------------------------------------------------- the operator (inside `go_live_store.operator_scope`)


async def operator_pending(conn: AsyncConnection) -> list[dict[str, Any]]:
    return [
        {
            "application_id": str(r["application_id"]),
            "tenant_id": str(r["tenant_id"]),
            "application_name": r["application_name"],
            "digest": bytes(r["digest"]).hex(),
            "uploaded_at": r["uploaded_at"].isoformat(),
            "image_base64": base64.b64encode(bytes(r["image"])).decode(),
        }
        for r in await db.pending(conn)
    ]


async def operator_decide(
    conn: AsyncConnection,
    application_id: str,
    digest_hex: str,
    approve: bool,
    operator: str,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    clean = " ".join((note or "").split())
    if len(clean) > NOTE_MAX or (not approve and len(clean) < 3):
        raise m.PartnerError(
            "NOTE_REQUIRED", "Give a reason the company can read when you reject a logo."
        )
    try:
        digest = bytes.fromhex(digest_hex)
    except ValueError as e:
        raise m.PartnerError("VALIDATION_FAILED", "The digest is not valid.") from e
    if not await db.decide(
        conn, application_id, digest, "APPROVED" if approve else "REJECTED", operator, clean, now
    ):
        raise m.PartnerError(
            "LOGO_CHANGED",
            "That logo is no longer the one you looked at, or is no longer waiting. Reload and look again.",
        )
    return {"application_id": application_id, "status": "APPROVED" if approve else "REJECTED"}
