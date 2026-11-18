"""A principal's own security email changes only when the new address confirms it (ZID-DTV-WF-05 DTV-21, QA-014, A04; ZID-EM-017 to 019; CR-114).

    request   store the new address encrypted, cancel any earlier pending change, and return a single-use link secret for ZID-EM-017 (to the new
              address); the old address, if any, gets ZID-EM-018. Nothing changes yet.
    confirm   the link: at most 5 tries, 30 minutes, once. On success the new address replaces the old one and ZID-EM-019 goes to both.

The link token is the change id (32 hex) followed by a 43-character random secret; only a keyed hash of the secret is kept.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.communication.application import contacts
from zid_platform.modules.evidence.ports import current_runtime, read_object, store_object

TTL = timedelta(minutes=30)
MAX_ATTEMPTS = 5
_TOKEN = re.compile(r"^(?P<id>[0-9a-f]{32})(?P<secret>[A-Za-z0-9_-]{43})$")


class ChangeError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class Requested:
    change_id: str
    token: str  # goes only into the ZID-EM-017 link
    new_address: str
    old_address: str | None
    expires_at: datetime


async def _key() -> bytes:
    mac = current_runtime().mac_key
    return await asyncio.to_thread(mac.mac, b"zid.email-contact-change.v1")


def _hash(key: bytes, change_id: str, secret: str) -> bytes:
    return hmac.new(key, f"{change_id}|{secret}".encode(), hashlib.sha256).digest()


async def request(
    conn: AsyncConnection, tenant_id: str, principal_id: str, address: str, *, now: datetime
) -> Requested:
    new = contacts.normalize_address(address)
    old = await contacts.resolve_principal(conn, tenant_id, principal_id)
    await conn.execute(
        text(
            "UPDATE platform.email_contact_change SET state = 'CANCELLED', decided_at = :n "
            "WHERE tenant_id = :t AND principal_id = :p AND state = 'PENDING'"
        ),
        {"n": now, "t": uuid.UUID(tenant_id), "p": uuid.UUID(principal_id)},
    )
    object_id, _ = await store_object(
        conn, tenant_id, content=new.encode("utf-8"), object_type="communication_contact", classification="D3",
        retention_class="contact", subject_principal_id=principal_id,
    )  # fmt: skip
    change = uuid.uuid4()
    secret = secrets.token_urlsafe(32)
    await conn.execute(
        text(
            "INSERT INTO platform.email_contact_change (change_id, tenant_id, principal_id, address_hash, vault_object_id, token_hash, state, "
            "expires_at, created_at) VALUES (:id, :t, :p, :h, :o, :th, 'PENDING', :ex, :at)"
        ),
        {
            "id": change,
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "h": await contacts.address_hash(new),
            "o": object_id,
            "th": _hash(await _key(), change.hex, secret),
            "ex": now + TTL,
            "at": now,
        },
    )
    return Requested(
        change.hex, change.hex + secret, new, old[0].address if old else None, now + TTL
    )


async def confirm(
    conn: AsyncConnection, tenant_id: str, principal_id: str, token: str, *, now: datetime
) -> tuple[str, str | None]:
    """(new address, old address) once applied. Every refusal is the same code, whatever went wrong."""
    m = _TOKEN.match(token or "")
    if not m:
        raise ChangeError("EMAIL_CHANGE_INVALID")
    row = (
        await conn.execute(
            text(
                "SELECT change_id, vault_object_id, token_hash, state, attempt_count, expires_at FROM platform.email_contact_change "
                "WHERE change_id = :id AND tenant_id = :t AND principal_id = :p FOR UPDATE"
            ),
            {"id": uuid.UUID(m["id"]), "t": uuid.UUID(tenant_id), "p": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None or row.state != "PENDING":
        raise ChangeError("EMAIL_CHANGE_INVALID")
    if now >= row.expires_at:
        await _decide(conn, row.change_id, "EXPIRED", row.attempt_count, now)
        raise ChangeError("EMAIL_CHANGE_INVALID")
    attempts = row.attempt_count + 1
    if not hmac.compare_digest(_hash(await _key(), m["id"], m["secret"]), bytes(row.token_hash)):
        if attempts >= MAX_ATTEMPTS:
            await _decide(conn, row.change_id, "CANCELLED", attempts, now)
        else:
            await conn.execute(
                text(
                    "UPDATE platform.email_contact_change SET attempt_count = :a WHERE change_id = :id"
                ),
                {"a": attempts, "id": row.change_id},
            )
        raise ChangeError("EMAIL_CHANGE_INVALID")
    new = (await read_object(conn, tenant_id, row.vault_object_id)).decode("utf-8")
    old = await contacts.resolve_principal(conn, tenant_id, principal_id)
    await contacts.set_principal_contact(conn, tenant_id, principal_id, new, actor=principal_id)
    await _decide(conn, row.change_id, "APPLIED", attempts, now)
    return new, old[0].address if old else None


async def _decide(
    conn: AsyncConnection, change_id: uuid.UUID, state: str, attempts: int, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.email_contact_change SET state = :s, attempt_count = :a, decided_at = :n WHERE change_id = :id"
        ),
        {"s": state, "a": attempts, "n": now, "id": change_id},
    )


__all__ = ["MAX_ATTEMPTS", "TTL", "ChangeError", "Requested", "confirm", "request"]
