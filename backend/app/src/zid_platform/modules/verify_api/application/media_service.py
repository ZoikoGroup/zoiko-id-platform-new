"""Photographs of a partner's customer, kept only when the deployment decided to (ZID_VERIFY_KEEP_PHOTOS_DAYS), and shown to people at the company only under a reason that is recorded.

Off by default (the number of days is 0): then nothing is kept and the hosted page says so. On, each photograph of an attempt is encrypted in the evidence vault under the session's own pseudonymous key, with the
date retention ends; the vault's retention sweep erases the bytes at that date, and erasing the session's key erases them at once. A person at the company lists what exists freely, and OPENS one only with a typed
reason, which is stored in an append-only record with who and when. The image comes back once, in that answer, and is never in a log or a list.
"""

from __future__ import annotations

import base64
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import review_photo
from zid_platform.modules.verification.ports import partner_capture
from zid_platform.modules.verify_api.domain import model as m
from zid_platform.modules.verify_api.infrastructure import media_store as db

MAX_DAYS = 90
_TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


def keep_days() -> int:
    """How many days a photograph is kept; 0 (the default) keeps none. Read on each call, bounded to 1..90."""
    try:
        days = int(os.environ.get("ZID_VERIFY_KEEP_PHOTOS_DAYS", "0").strip() or "0")
    except ValueError:
        return 0
    return max(0, min(days, MAX_DAYS))


def _type_of(content: bytes) -> str | None:
    """The image type from its first bytes, never from what the client said."""
    for magic, kind in _TYPES.items():
        if content.startswith(magic):
            return kind
    return None


async def keep_capture(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    document: bytes,
    selfie: bytes,
    captured_at: datetime,
    now: datetime,
) -> int:
    """Keep the two photographs of this attempt. Returns how many were kept. Never raises: a photograph that cannot be kept must not undo the verification, so a failure keeps nothing for this attempt."""
    days = keep_days()
    if days <= 0:
        return 0
    try:
        async with conn.begin_nested():  # a failure undoes only this, never the verification
            attempt = await db.next_attempt(conn, session_id)
            subject = partner_capture.applicant_for(session_id)
            expires = now + timedelta(days=days)
            kept = 0
            for kind, content in (("DOCUMENT", document), ("SELFIE", selfie)):
                content_type = _type_of(content)
                if content_type is None:
                    continue
                object_id, _digest = await review_photo.keep(
                    conn,
                    tenant_id,
                    content=content,
                    subject_principal_id=subject,
                    expires_at=expires,
                )
                await db.add(
                    conn, tenant_id=tenant_id, session_id=session_id, kind=kind, attempt=attempt, vault_object_id=object_id,
                    content_type=content_type, byte_length=len(content), captured_at=captured_at, expires_at=expires,
                )  # fmt: skip
                kept += 1
            return kept
    except Exception:  # noqa: BLE001 - see the docstring
        return 0


def _view(r: dict[str, Any], now: datetime) -> dict[str, Any]:
    return {
        "media_id": r["media_id"],
        "kind": r["kind"],
        "attempt": r["attempt"],
        "content_type": r["content_type"],
        "byte_length": r["byte_length"],
        "captured_at": r["captured_at"].isoformat(),
        "expires_at": r["expires_at"].isoformat(),
        "available": r["expires_at"] > now,
    }


async def listing(
    conn: AsyncConnection, session_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    items = [_view(r, now) for r in await db.list_for_session(conn, session_id)]
    opened = [
        {
            "access_id": a["access_id"],
            "media_id": a["media_id"],
            "reason": a["reason"],
            "opened_at": a["opened_at"].isoformat(),
            "by": str(a["principal_id"]),
        }
        for a in await db.accesses(conn, session_id)
    ]
    return {"keeping_days": keep_days(), "items": items, "opened": opened}


async def open_one(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    session_id: str,
    media_id: str,
    principal_id: str,
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return one photograph, after recording who asked and why. A reason of fewer than five characters is refused before anything is read."""
    now = now or datetime.now(UTC)
    clean = (reason or "").strip()
    if not 5 <= len(clean) <= 300:
        raise m.PartnerError(
            "VALIDATION_FAILED", "Say why you are opening this photograph, in 5 to 300 characters."
        )
    row = await db.get(conn, session_id, media_id)
    if row is None:
        raise m.PartnerError("NOT_FOUND", "No such photograph for this verification.")
    if row["expires_at"] <= now:
        raise m.PartnerError("NOT_FOUND", "That photograph was deleted when its retention ended.")
    try:
        content = await review_photo.read(conn, tenant_id, row["vault_object_id"])
    except Exception as e:
        raise m.PartnerError("NOT_FOUND", "That photograph is no longer available.") from e
    await db.record_access(
        conn,
        tenant_id=tenant_id,
        session_id=session_id,
        media_id=media_id,
        principal_id=principal_id,
        reason=clean,
    )  # recorded BEFORE the image leaves
    return {**_view(row, now), "image_base64": base64.b64encode(content).decode("ascii")}
