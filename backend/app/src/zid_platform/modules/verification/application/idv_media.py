"""The document photograph on a reviewed route: kept, shown to a reviewer under control, and never anywhere else (ZID-DTV-WF-03 browser-only route; CR-124).

A case on a reviewed (non-chip) route is decided by people, so the people must be able to see what they sign. The photograph is encrypted in the evidence vault under the applicant's own erasable key and held until
the market's retention ends (a market without a retention period keeps none, and the route refuses the photograph rather than keep it for ever). A reviewer is never the applicant, asks with a reason, is given a grant
that lasts minutes and reads at most three times, and every grant is a stored record. The image is never in a log, a list or a general screen.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import review_photo
from zid_platform.modules.verification.application.idv_case import Case, CaseError

GRANT_MINUTES = 5
MAX_READS = 3
TYPES = {b"\xff\xd8\xff": "image/jpeg", b"\x89PNG": "image/png"}


def sniff(content: bytes) -> str | None:
    """The image type from its first bytes, never from what the client said."""
    for magic, kind in TYPES.items():
        if content.startswith(magic):
            return kind
    return None


async def save(
    conn: AsyncConnection, tenant_id: str, case: Case, content: bytes, *, content_type: str, checks: dict[str, str],
    captured_at: datetime, retention_days: int | None, now: datetime,
) -> str:  # fmt: skip
    if not retention_days:
        raise CaseError(
            "MEDIA_RETENTION_NOT_SET",
            "This way of verifying keeps a photograph for review, and no retention period is set for this market.",
        )
    await supersede(conn, case.case_id, now)
    expires = now + timedelta(days=retention_days)
    object_id, digest = await review_photo.keep(
        conn, tenant_id, content=content, subject_principal_id=case.applicant, expires_at=expires
    )
    media_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO verify.idv_case_media (media_id, tenant_id, case_id, vault_object_id, sha256, content_type, byte_length, checks, captured_at, expires_at, created_at) "
            "VALUES (:m, :t, :c, :o, :h, :ct, :n, CAST(:ch AS jsonb), :cap, :exp, :now)"
        ),
        {
            "m": media_id, "t": uuid.UUID(tenant_id), "c": uuid.UUID(case.case_id), "o": uuid.UUID(object_id), "h": str(digest).split(":")[-1],
            "ct": content_type, "n": len(content), "ch": json.dumps(checks), "cap": captured_at, "exp": expires, "now": now,
        },
    )  # fmt: skip
    return str(media_id)


async def supersede(conn: AsyncConnection, case_id: str, now: datetime) -> None:
    """A newer photograph (or a request for one) retires the one before: it can no longer be opened."""
    await conn.execute(
        text(
            "UPDATE verify.idv_case_media SET superseded_at = :now WHERE case_id = :c AND superseded_at IS NULL"
        ),
        {"now": now, "c": uuid.UUID(case_id)},
    )


async def _latest(conn: AsyncConnection, case_id: str) -> Any:
    return (
        await conn.execute(
            text(
                "SELECT media_id, vault_object_id, content_type, byte_length, checks, captured_at, expires_at FROM verify.idv_case_media "
                "WHERE case_id = :c AND superseded_at IS NULL ORDER BY created_at DESC LIMIT 1"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).first()


async def describe(conn: AsyncConnection, case_id: str, *, now: datetime) -> dict[str, Any]:
    """What a reviewer's list may say about the photograph: that there is one, when it was taken, until when it is kept, and what was measured. Never the image."""
    row = await _latest(conn, case_id)
    if row is None:
        return {"has_photo": False}
    return {
        "has_photo": True, "expired": row.expires_at <= now, "content_type": row.content_type, "byte_length": row.byte_length,
        "checks": dict(row.checks), "captured_at": row.captured_at.isoformat(), "expires_at": row.expires_at.isoformat(),
    }  # fmt: skip


async def open_grant(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    reviewer: str,
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    text_reason = reason.strip()
    if not 10 <= len(text_reason) <= 500:
        raise CaseError(
            "REASON_REQUIRED", "Say why you need to see the photograph (10 to 500 characters)."
        )
    case = (
        await conn.execute(
            text("SELECT applicant, state FROM verify.idv_case WHERE case_id = :c"),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    if case is None:
        raise CaseError("CASE_NOT_FOUND")
    if str(case.applicant) == reviewer:
        raise CaseError(
            "REVIEWER_IS_THE_APPLICANT",
            "A person cannot look at their own photograph as a reviewer.",
        )
    if case.state == "CLOSED":
        raise CaseError(
            "CASE_CLOSED", "The case has a final decision; its photograph is no longer opened."
        )
    media = await _latest(conn, case_id)
    if media is None:
        raise CaseError("MEDIA_NOT_FOUND", "There is no photograph on this case.")
    if media.expires_at <= now:
        raise CaseError("MEDIA_EXPIRED", "The retention period for this photograph has ended.")
    grant = uuid.uuid4()
    expires = now + timedelta(minutes=GRANT_MINUTES)
    await conn.execute(
        text(
            "INSERT INTO verify.idv_media_grant (grant_id, tenant_id, media_id, reviewer, reason, created_at, expires_at) VALUES (:g, :t, :m, :r, :why, :now, :exp)"
        ),
        {"g": grant, "t": uuid.UUID(tenant_id), "m": media.media_id, "r": uuid.UUID(reviewer), "why": text_reason, "now": now, "exp": expires},
    )  # fmt: skip
    return {"grant_id": str(grant), "expires_at": expires.isoformat(), "reads_left": MAX_READS}


async def read_with_grant(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    grant_id: str,
    *,
    reviewer: str,
    now: datetime,
) -> dict[str, Any]:
    """The photograph, once per call, while the grant is the reviewer's own, unexpired and has reads left. Each read is counted before the bytes are decrypted."""
    try:
        key = uuid.UUID(grant_id)
    except ValueError as e:
        raise CaseError("GRANT_INVALID", "That permission is not valid.") from e
    got = (
        await conn.execute(
            text(
                "UPDATE verify.idv_media_grant g SET reads = reads + 1 WHERE g.grant_id = :g AND g.reviewer = :r AND g.expires_at > :now AND g.reads < :max "
                "AND g.media_id IN (SELECT media_id FROM verify.idv_case_media WHERE case_id = :c AND superseded_at IS NULL) RETURNING g.media_id, g.reads"
            ),
            {
                "g": key,
                "r": uuid.UUID(reviewer),
                "now": now,
                "max": MAX_READS,
                "c": uuid.UUID(case_id),
            },
        )
    ).first()
    if got is None:
        raise CaseError(
            "GRANT_INVALID", "That permission has expired, been used up, or is not yours."
        )
    media = (
        await conn.execute(
            text(
                "SELECT vault_object_id, content_type, checks, expires_at FROM verify.idv_case_media WHERE media_id = :m"
            ),
            {"m": got.media_id},
        )
    ).one()
    if media.expires_at <= now:
        raise CaseError("MEDIA_EXPIRED", "The retention period for this photograph has ended.")
    content = await review_photo.read(conn, tenant_id, str(media.vault_object_id))
    return {
        "content_type": media.content_type,
        "content": content,
        "checks": dict(media.checks),
        "reads_left": MAX_READS - got.reads,
    }
