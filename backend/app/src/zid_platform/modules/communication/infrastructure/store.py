"""SQL for the delivery log, suppression, consent and contacts (Communication Estate §03).

Nothing here sees an address, a subject, a body, a merge value, a token or a code: callers pass recipient
hashes and the vault object id of an address, and rows hold only those.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.communication.domain.policy import Suppression


@dataclass(frozen=True, slots=True)
class MessageRow:
    message_id: str
    status: str
    attempts: int
    updated_at: datetime


async def reserve_message(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    template_id: str,
    template_version: str,
    message_class: str,
    event_id: str,
    event_type: str,
    recipient_hash: str,
    correlation_id: str | None,
) -> tuple[MessageRow, bool]:
    """Create the row for this (event, template, recipient), or return the existing one. True when created."""
    message_id = uuid.uuid4()
    created = (
        await conn.execute(
            text(
                "INSERT INTO platform.email_message (message_id, tenant_id, template_id, template_version, "
                "message_class, event_id, event_type, recipient_hash, correlation_id) "
                "VALUES (:m, :t, :tpl, :v, :c, :e, :et, :h, :corr) "
                "ON CONFLICT (tenant_id, event_id, template_id, recipient_hash) DO NOTHING "
                "RETURNING message_id, status, attempts, updated_at"
            ),
            {
                "m": message_id,
                "t": uuid.UUID(tenant_id),
                "tpl": template_id,
                "v": template_version,
                "c": message_class,
                "e": event_id,
                "et": event_type,
                "h": recipient_hash,
                "corr": correlation_id,
            },
        )
    ).first()
    if created is not None:
        return MessageRow(str(created[0]), created[1], created[2], created[3]), True
    row = (
        await conn.execute(
            text(
                "SELECT message_id, status, attempts, updated_at FROM platform.email_message "
                "WHERE tenant_id = :t AND event_id = :e AND template_id = :tpl AND recipient_hash = :h"
            ),
            {"t": uuid.UUID(tenant_id), "e": event_id, "tpl": template_id, "h": recipient_hash},
        )
    ).one()
    return MessageRow(str(row[0]), row[1], row[2], row[3]), False


async def begin_attempt(conn: AsyncConnection, message_id: str, now: datetime) -> None:
    await conn.execute(
        text(
            "UPDATE platform.email_message SET status = 'PENDING', attempts = attempts + 1, updated_at = :n "
            "WHERE message_id = :m"
        ),
        {"m": uuid.UUID(message_id), "n": now},
    )


async def finish_message(
    conn: AsyncConnection,
    message_id: str,
    *,
    status: str,
    reason: str | None,
    provider_ref: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.email_message SET status = :s, reason = :r, provider_ref = :p, updated_at = :n, "
            "sent_at = CASE WHEN :s = 'SENT' THEN :n ELSE sent_at END WHERE message_id = :m"
        ),
        {"m": uuid.UUID(message_id), "s": status, "r": reason, "p": provider_ref, "n": now},
    )


async def set_attempts(conn: AsyncConnection, message_id: str, attempts: int) -> None:
    await conn.execute(
        text("UPDATE platform.email_message SET attempts = :a WHERE message_id = :m"),
        {"m": uuid.UUID(message_id), "a": attempts},
    )


async def sent_last_hour(
    conn: AsyncConnection,
    tenant_id: str,
    recipient_hash: str,
    since: datetime,
    template_id: str | None,
    *,
    exclude_message_id: str | None = None,
) -> int:
    """Other messages to this recipient that were sent, or are being sent, since `since` (flood protection).
    The message being prepared is excluded: it has its own row but is not yet part of the flood."""
    return int(
        (
            await conn.execute(
                text(
                    "SELECT count(*) FROM platform.email_message WHERE tenant_id = :t AND recipient_hash = :h "
                    "AND created_at >= :s AND status IN ('SENT', 'PENDING') "
                    "AND (CAST(:tpl AS text) IS NULL OR template_id = :tpl) "
                    "AND (CAST(:me AS uuid) IS NULL OR message_id <> :me)"
                ),
                {
                    "t": uuid.UUID(tenant_id),
                    "h": recipient_hash,
                    "s": since,
                    "tpl": template_id,
                    "me": uuid.UUID(exclude_message_id) if exclude_message_id else None,
                },
            )
        ).scalar_one()
    )


async def suppressions(
    conn: AsyncConnection, recipient_hash: str, list_id: str = ""
) -> set[Suppression]:
    rows = (
        await conn.execute(
            text(
                "SELECT reason FROM platform.email_suppression WHERE recipient_hash = :h "
                "AND (list_id = '' OR list_id = :l)"
            ),
            {"h": recipient_hash, "l": list_id},
        )
    ).scalars()
    return {Suppression(r) for r in rows}


async def add_suppression(
    conn: AsyncConnection,
    recipient_hash: str,
    reason: Suppression,
    *,
    source: str,
    list_id: str = "",
) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.email_suppression (recipient_hash, reason, list_id, source) "
            "VALUES (:h, :r, :l, :s) ON CONFLICT DO NOTHING"
        ),
        {"h": recipient_hash, "r": reason.value, "l": list_id, "s": source},
    )


async def consent_status(conn: AsyncConnection, recipient_hash: str, list_id: str) -> str | None:
    return (
        await conn.execute(
            text(
                "SELECT status FROM platform.email_consent WHERE recipient_hash = :h AND list_id = :l"
            ),
            {"h": recipient_hash, "l": list_id},
        )
    ).scalar_one_or_none()


async def record_consent(
    conn: AsyncConnection, recipient_hash: str, list_id: str, status: str, basis_public: str
) -> None:
    await conn.execute(
        text(
            "INSERT INTO platform.email_consent (recipient_hash, list_id, status, basis_public) "
            "VALUES (:h, :l, :s, :b) ON CONFLICT (recipient_hash, list_id) DO UPDATE "
            "SET status = EXCLUDED.status, basis_public = EXCLUDED.basis_public, recorded_at = now()"
        ),
        {"h": recipient_hash, "l": list_id, "s": status, "b": basis_public},
    )


# ------------------------------------------------------------------------------- contacts


async def supersede_principal_contact(
    conn: AsyncConnection, tenant_id: str, principal_id: str
) -> None:
    await conn.execute(
        text(
            "UPDATE platform.email_contact SET superseded_at = now() WHERE tenant_id = :t "
            "AND principal_id = :p AND superseded_at IS NULL"
        ),
        {"t": uuid.UUID(tenant_id), "p": uuid.UUID(principal_id)},
    )


async def insert_contact(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    purpose: str,
    principal_id: str | None,
    address_hash: str,
    vault_object_id: str,
    created_by: str | None,
) -> str:
    contact_id = uuid.uuid4()
    await conn.execute(
        text(
            "INSERT INTO platform.email_contact (contact_id, tenant_id, purpose, principal_id, address_hash, "
            "vault_object_id, created_by) VALUES (:c, :t, :p, :pr, :h, :o, :u)"
        ),
        {
            "c": contact_id,
            "t": uuid.UUID(tenant_id),
            "p": purpose,
            "pr": uuid.UUID(principal_id) if principal_id else None,
            "h": address_hash,
            "o": uuid.UUID(vault_object_id),
            "u": uuid.UUID(created_by) if created_by else None,
        },
    )
    return str(contact_id)


async def contacts_for(
    conn: AsyncConnection, tenant_id: str, *, purpose: str, principal_id: str | None = None
) -> list[dict[str, Any]]:
    rows = (
        (
            await conn.execute(
                text(
                    "SELECT contact_id, purpose, principal_id, address_hash, vault_object_id, created_at "
                    "FROM platform.email_contact WHERE tenant_id = :t AND purpose = :p AND superseded_at IS NULL "
                    "AND (CAST(:pr AS uuid) IS NULL OR principal_id = :pr) ORDER BY created_at"
                ),
                {
                    "t": uuid.UUID(tenant_id),
                    "p": purpose,
                    "pr": uuid.UUID(principal_id) if principal_id else None,
                },
            )
        )
        .mappings()
        .all()
    )
    return [dict(r) for r in rows]


async def retire_contact(conn: AsyncConnection, tenant_id: str, contact_id: str) -> bool:
    r = await conn.execute(
        text(
            "UPDATE platform.email_contact SET superseded_at = now() WHERE tenant_id = :t "
            "AND contact_id = :c AND superseded_at IS NULL"
        ),
        {"t": uuid.UUID(tenant_id), "c": uuid.UUID(contact_id)},
    )
    return bool(r.rowcount)
