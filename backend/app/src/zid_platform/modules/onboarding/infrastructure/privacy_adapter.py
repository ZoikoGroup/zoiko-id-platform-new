"""The sign-up records of a person, for the privacy data map (PRIV-001 §10, §15, §17; ZID-UX-WF-013A; slice L-11).

    platform.registration_account           keyed address hash to the account it made (the "is this address registered" index)
    platform.registration_intent            the requests that led to it (country, journey, locale, notice version; the address is long gone)
    platform.registration_invitation_index  a pending invitation routed from a verified address

They sit in the cell's registration tenant, not the person's own, so each step runs under that tenant's row-level security. An erased account
takes them with it: the index row (so no pseudonymous link to the person remains, and the address may register again), the requests and the
invitation routes. Only the erasure may do it: the database refuses these deletes unless the transaction is marked `app.registration_erasure`.
The data map never carries the address hash: an object is named by the request id, or by the account.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import in_tenant

from zid_platform.modules.privacy.ports import register_store

STORE = "platform.registration"
_ACCOUNT, _REQUEST, _INVITE = "account:", "request:", "invite:"


async def _locate(
    registration_tenant: str, conn: AsyncConnection, subject: str
) -> list[dict[str, Any]]:
    who = uuid.UUID(subject)
    out: list[dict[str, Any]] = []
    async with in_tenant(conn, registration_tenant):
        accounts = (
            await conn.execute(
                text(
                    "SELECT created_at, country FROM platform.registration_account WHERE account_principal_id = :p"
                ),
                {"p": who},
            )
        ).all()
        requests = (
            await conn.execute(
                text(
                    "SELECT intent_id, created_at, country FROM platform.registration_intent WHERE account_principal_id = :p"
                ),
                {"p": who},
            )
        ).all()
        invites = (
            await conn.execute(
                text(
                    "SELECT invite_tenant_id, created_at FROM platform.registration_invitation_index WHERE principal_id = :p"
                ),
                {"p": who},
            )
        ).all()
    for r in accounts:
        out.append(
            _entry(
                f"{_ACCOUNT}{subject}",
                "registration.account_index",
                "ACCOUNT",
                "ACCOUNT_LIFETIME",
                r.created_at,
            )
        )
    for r in requests:
        out.append(
            _entry(
                f"{_REQUEST}{r.intent_id}",
                "registration.request",
                "ACCOUNT",
                "REGISTRATION_REQUEST",
                r.created_at,
            )
        )
    for r in invites:
        out.append(
            _entry(
                f"{_INVITE}{r.invite_tenant_id}:{subject}",
                "registration.invitation_route",
                "ACCOUNT",
                "REGISTRATION_REQUEST",
                r.created_at,
            )
        )
    return out


def _entry(ref: str, kind: str, purpose: str, retention: str, at: Any) -> dict[str, Any]:
    return {
        "object_ref": ref,
        "object_type": kind,
        "purpose_code": purpose,
        "data_category": "D2",
        "trigger_at": at,
        "region": None,
        "retention_policy_code": retention,
    }


async def _export(
    registration_tenant: str, conn: AsyncConnection, entry: Row[Any]
) -> dict[str, Any]:
    """What the person is entitled to see: when and where they signed up, never the address hash, a token or anything about other people."""
    ref = str(entry.object_ref)
    async with in_tenant(conn, registration_tenant):
        if ref.startswith(_REQUEST):
            row = (
                await conn.execute(
                    text(
                        "SELECT country, journey, locale, notice_version, state, created_at "
                        "FROM platform.registration_intent WHERE intent_id = :i"
                    ),
                    {"i": uuid.UUID(ref[len(_REQUEST) :])},
                )
            ).first()
        elif ref.startswith(_ACCOUNT):
            row = (
                await conn.execute(
                    text(
                        "SELECT country, created_at FROM platform.registration_account WHERE account_principal_id = :p"
                    ),
                    {"p": uuid.UUID(ref[len(_ACCOUNT) :])},
                )
            ).first()
        else:
            tenant, _, principal = ref[len(_INVITE) :].partition(":")
            row = (
                await conn.execute(
                    text(
                        "SELECT role, status, created_at FROM platform.registration_invitation_index "
                        "WHERE invite_tenant_id = :t AND principal_id = :p"
                    ),
                    {"t": uuid.UUID(tenant), "p": uuid.UUID(principal)},
                )
            ).first()
    return dict(row._mapping) if row else {}


async def _dispose(
    registration_tenant: str, conn: AsyncConnection, entry: Row[Any], action: str
) -> None:
    """Delete one record, inside the marked transaction."""
    ref = str(entry.object_ref)
    async with in_tenant(conn, registration_tenant):
        await conn.execute(text("SELECT set_config('app.registration_erasure', 'on', true)"))
        if ref.startswith(_REQUEST):
            await conn.execute(
                text("DELETE FROM platform.registration_intent WHERE intent_id = :i"),
                {"i": uuid.UUID(ref[len(_REQUEST) :])},
            )
        elif ref.startswith(_ACCOUNT):
            await conn.execute(
                text("DELETE FROM platform.registration_account WHERE account_principal_id = :p"),
                {"p": uuid.UUID(ref[len(_ACCOUNT) :])},
            )
        elif ref.startswith(_INVITE):
            tenant, _, principal = ref[len(_INVITE) :].partition(":")
            await conn.execute(
                text(
                    "DELETE FROM platform.registration_invitation_index "
                    "WHERE invite_tenant_id = :t AND principal_id = :p"
                ),
                {"t": uuid.UUID(tenant), "p": uuid.UUID(principal)},
            )
        # cleared again at once, so nothing later in this transaction can use it; if a delete failed the transaction is gone and the mark with it
        await conn.execute(text("SELECT set_config('app.registration_erasure', '', true)"))


def register(registration_tenant: str) -> None:
    """Called at start-up when this cell has a registration tenant; a cell with sign-up off keeps no such records to register."""

    async def locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
        return await _locate(registration_tenant, conn, subject)

    async def export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
        return await _export(registration_tenant, conn, entry)

    async def dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
        await _dispose(registration_tenant, conn, entry, action)

    register_store(STORE, locator=locate, exporter=export, disposer=dispose)


__all__ = ["STORE", "register"]
