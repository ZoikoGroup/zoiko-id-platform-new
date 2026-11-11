"""Evidence upload orchestration (ZID-INT-002 section 05; CR-071). Bytes are kept in the cell database, and only after a clean scan.

Flow: `announce` records what will arrive (type, size, SHA-256) and returns a short-lived SIGNED upload token bound to exactly that;
`receive` takes the bytes, checks they are what was announced and the type they claim, strips image metadata, scans, and keeps them only
if the scan says CLEAN. Any other outcome records a reason and keeps no bytes. Retention comes from a deployment setting; none configured
means no upload.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.application import scanner as sc
from zid_platform.modules.evidence.domain import intake
from zid_platform.modules.evidence.infrastructure import upload_store as store

TOKEN_TYP = "zid-upload+jwt"
TOKEN_LIFE = timedelta(minutes=5)


class Ring(Protocol):
    def sign(self, payload: Mapping[str, Any], *, typ: str) -> str: ...
    def verify(self, token: str, *, typ: str) -> tuple[dict[str, Any], dict[str, Any]]: ...


@dataclass(frozen=True, slots=True)
class Announced:
    upload_id: str
    token: str
    token_expires_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class Received:
    state: str  # READY | REJECTED
    reason: str | None
    scan_state: str
    exif_stripped: bool = False


async def announce(
    conn: AsyncConnection,
    ring: Ring,
    *,
    tenant_id: str,
    subject: str,
    uploaded_by: str,
    declared_type: str,
    size_bytes: int,
    sha256: str,
    retention_days: int | None,
    now: datetime,
) -> Announced:
    if not retention_days or retention_days < 1:
        raise intake.UploadError(
            "RETENTION_NOT_CONFIGURED"
        )  # the retention period is the privacy officer's decision
    intake.check_announcement(declared_type, size_bytes, sha256)
    upload_id = str(uuid.uuid4())
    token_expires = now + TOKEN_LIFE
    expires = now + timedelta(days=retention_days)
    await store.insert(
        conn,
        upload_id=upload_id,
        tenant_id=tenant_id,
        subject=subject,
        uploaded_by=uploaded_by,
        declared_type=declared_type,
        size_bytes=size_bytes,
        sha256=sha256,
        now=now,
        token_expires_at=token_expires,
        expires_at=expires,
    )
    token = ring.sign(
        {
            "typ": TOKEN_TYP,
            "upload_id": upload_id,
            "tenant_id": tenant_id,
            "size_bytes": size_bytes,
            "sha256": sha256,
            "exp": int(token_expires.timestamp()),
        },
        typ=TOKEN_TYP,
    )
    return Announced(upload_id, token, token_expires, expires)


def check_token(
    ring: Ring, token: str, *, upload_id: str, tenant_id: str, now: datetime
) -> dict[str, Any]:
    """The token must be ours, unexpired, and for exactly this upload and tenant. Anything else is refused."""
    try:
        _, claims = ring.verify(token, typ=TOKEN_TYP)
    except Exception as e:
        raise intake.UploadError("UPLOAD_TOKEN_INVALID") from e
    if claims.get("upload_id") != upload_id or claims.get("tenant_id") != tenant_id:
        raise intake.UploadError("UPLOAD_TOKEN_INVALID")
    if int(claims.get("exp", 0)) <= int(now.timestamp()):
        raise intake.UploadError("UPLOAD_TOKEN_EXPIRED")
    return claims


async def receive(
    conn: AsyncConnection, scanner: sc.Scanner | None, upload_id: str, content: bytes, now: datetime
) -> Received:
    """Take the bytes. Returns the recorded outcome; a refusal is recorded (with no bytes) rather than raised, so it survives the request."""
    row = await store.load(conn, upload_id, lock=True)
    if row is None:
        raise intake.UploadError("UPLOAD_NOT_FOUND")
    if row["state"] != "AWAITING_CONTENT":
        raise intake.UploadError("UPLOAD_NOT_AWAITING_CONTENT", row["state"])
    if now >= row["token_expires_at"]:
        await store.reject(conn, upload_id, reason="TOKEN_EXPIRED", now=now)
        return Received("REJECTED", "TOKEN_EXPIRED", "NOT_SCANNED")
    try:
        sniffed = intake.check_content(
            content,
            declared_type=row["declared_type"],
            size_bytes=row["size_bytes"],
            sha256=row["declared_sha256"],
        )
    except intake.UploadError as e:
        await store.reject(conn, upload_id, reason=e.code, now=now)
        return Received("REJECTED", e.code, "NOT_SCANNED")
    # scan EXACTLY what arrived, before anything is cleaned: stripping drops trailing bytes, and a marker hidden after an image's end
    # would otherwise vanish before the scanner ever saw it. Only the cleaned copy is kept, and only if the original scanned clean.
    verdict = await sc.scan(scanner, content)
    cleaned, stripped = intake.strip_metadata(content, sniffed)
    if verdict.state != "CLEAN":
        reason = "INFECTED" if verdict.state == "INFECTED" else "SCAN_ERROR"
        await store.reject(
            conn,
            upload_id,
            reason=reason,
            scan_state=verdict.state,
            scanner=verdict.scanner,
            sniffed_type=sniffed,
            now=now,
        )
        return Received("REJECTED", reason, verdict.state)
    import hashlib

    await store.accept(
        conn,
        upload_id,
        content=cleaned,
        sniffed_type=sniffed,
        stored_sha256=hashlib.sha256(cleaned).hexdigest(),
        exif_stripped=stripped,
        scanner=verdict.scanner,
        now=now,
    )
    return Received("READY", None, "CLEAN", stripped)


async def delete(conn: AsyncConnection, upload_id: str, now: datetime) -> bool:
    return await store.erase(conn, upload_id, state="DELETED", now=now)


async def purge_expired(conn: AsyncConnection, now: datetime) -> int:
    """Erase the bytes of every upload whose retention has run out. Returns how many."""
    n = 0
    for upload_id in await store.expired_ids(conn, now):
        if await store.erase(conn, upload_id, state="EXPIRED", now=now):
            n += 1
    return n


async def unusable(conn: AsyncConnection, refs: Sequence[str], now: datetime) -> list[str]:
    """The references that cannot back a check: unknown, not READY and CLEAN, or past retention. Empty means all are usable."""
    bad: list[str] = []
    for ref in refs:
        try:
            ident = str(uuid.UUID(ref.removeprefix("ev_")))
        except ValueError:
            bad.append(ref)
            continue
        row = await store.load(conn, ident)
        if (
            row is None
            or row["state"] != "READY"
            or row["scan_state"] != "CLEAN"
            or row["expires_at"] <= now
        ):
            bad.append(ref)
    return bad


async def read_usable(
    conn: AsyncConnection, ref: str, now: datetime
) -> tuple[bytes, dict[str, Any]] | None:
    """The bytes and metadata of a usable upload, or None."""
    if await unusable(conn, [ref], now):
        return None
    ident = str(uuid.UUID(ref.removeprefix("ev_")))
    content = await store.load_content(conn, ident)
    row = await store.load(conn, ident)
    return (content, row) if content is not None and row is not None else None


def manifest(row: Mapping[str, Any]) -> dict[str, Any]:
    """The restricted view of an upload: what it is and its state. Never the bytes."""
    return {
        "evidence_ref": f"ev_{row['upload_id']}",
        "state": row["state"],
        "reject_reason": row["reject_reason"],
        "declared_type": row["declared_type"],
        "sniffed_type": row["sniffed_type"],
        "size_bytes": row["size_bytes"],
        "sha256": row["stored_sha256"] or row["declared_sha256"],
        "metadata_stripped": row["exif_stripped"],
        "scan": {"state": row["scan_state"], "scanner": row["scanner"]},
        "expires_at": row["expires_at"].isoformat(),
        "erased_at": row["erased_at"].isoformat() if row["erased_at"] else None,
        "contains": "metadata only: the bytes are never part of a manifest",
    }
