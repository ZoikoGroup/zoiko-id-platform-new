"""External identity links in PostgreSQL (ZID-DATA-001 §5.2; ZID-AUTH-001 §9.1).

Creating or removing a link is security-significant: it increments the principal's security
revision (every session observing the old revision must re-authenticate) and emits
federation.link.created / federation.link.revoked.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.authentication.domain.authenticator import AuthnError
from zid_platform.modules.principal.ports import bump_security_revision

_COLS = (
    "link_id, tenant_id, principal_id, issuer, subject, connection_id, link_assurance, status, "
    "created_at, last_asserted_at"
)


@dataclass(frozen=True, slots=True)
class Link:
    link_id: str
    tenant_id: str
    principal_id: str
    issuer: str
    subject: str
    connection_id: str | None
    link_assurance: str
    status: str
    created_at: datetime
    last_asserted_at: datetime | None


def _link(r: object) -> Link:
    m = r._mapping  # type: ignore[attr-defined]
    return Link(
        link_id=str(m["link_id"]),
        tenant_id=str(m["tenant_id"]),
        principal_id=str(m["principal_id"]),
        issuer=m["issuer"],
        subject=m["subject"],
        connection_id=str(m["connection_id"]) if m["connection_id"] else None,
        link_assurance=m["link_assurance"],
        status=m["status"],
        created_at=m["created_at"],
        last_asserted_at=m["last_asserted_at"],
    )


async def find_active(conn: AsyncConnection, issuer: str, subject: str) -> Link | None:
    row = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.external_identity_link "
                "WHERE issuer = :i AND subject = :s AND status = 'ACTIVE'"
            ),
            {"i": issuer, "s": subject},
        )
    ).first()
    return _link(row) if row else None


async def for_principal(conn: AsyncConnection, principal_id: str) -> list[Link]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_COLS} FROM authn.external_identity_link WHERE principal_id = :p "
                "ORDER BY created_at"
            ),
            {"p": uuid.UUID(principal_id)},
        )
    ).all()
    return [_link(r) for r in rows]


async def create_link(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    issuer: str,
    subject: str,
    connection_id: str | None,
    link_assurance: str,
    now: datetime,
) -> Link:
    link = Link(
        str(uuid.uuid4()),
        tenant_id,
        principal_id,
        issuer,
        subject,
        connection_id,
        link_assurance,
        "ACTIVE",
        now,
        now,
    )
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    f"INSERT INTO authn.external_identity_link ({_COLS}) VALUES (:id, :t, :p, :i, "
                    ":s, :c, :a, 'ACTIVE', :now, :now)"
                ),
                {
                    "id": uuid.UUID(link.link_id),
                    "t": uuid.UUID(tenant_id),
                    "p": uuid.UUID(principal_id),
                    "i": issuer,
                    "s": subject,
                    "c": uuid.UUID(connection_id) if connection_id else None,
                    "a": link_assurance,
                    "now": now,
                },
            )
    except IntegrityError as e:
        raise AuthnError("FEDERATION_LINK_CONFLICT") from e
    await emit(
        conn,
        name="federation.link.created",
        tenant_id=tenant_id,
        aggregate_type="external_identity_link",
        aggregate_id=link.link_id,
        aggregate_version=1,
        data={
            "link_id": link.link_id,
            "principal_id": principal_id,
            "connection_id": connection_id,
            "link_assurance": link_assurance,
        },
    )
    await bump_security_revision(conn, principal_id, reason="federation_link_created")
    return link


async def touch(conn: AsyncConnection, link_id: str, now: datetime) -> None:
    await conn.execute(
        text("UPDATE authn.external_identity_link SET last_asserted_at = :now WHERE link_id = :l"),
        {"l": uuid.UUID(link_id), "now": now},
    )


async def revoke(conn: AsyncConnection, link: Link, *, reason: str, now: datetime) -> bool:
    result = await conn.execute(
        text(
            "UPDATE authn.external_identity_link SET status = 'REVOKED', revoked_at = :now, "
            "revoked_reason = :r WHERE link_id = :l AND status <> 'REVOKED'"
        ),
        {"l": uuid.UUID(link.link_id), "r": reason, "now": now},
    )
    if result.rowcount != 1:
        return False
    await emit(
        conn,
        name="federation.link.revoked",
        tenant_id=link.tenant_id,
        aggregate_type="external_identity_link",
        aggregate_id=link.link_id,
        aggregate_version=2,
        data={"link_id": link.link_id, "principal_id": link.principal_id, "reason": reason},
    )
    await bump_security_revision(conn, link.principal_id, reason="federation_link_revoked")
    return True
