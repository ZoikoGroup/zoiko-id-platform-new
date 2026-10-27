"""Organization identity links, legal-name history and source quarantine (ZID-ROOT-001 §4-6, §18, §22).

- Identifier links (ROOT-01): one canonical organization is reachable by every identifier it is known by (national
  number, EUID, LEI). An identifier belongs to one organization; a second claim on it is a conflict that stops the
  establishment, never a silent merge of two organizations.
- Legal-name history (ROOT-03): a rename closes the current name row and opens a new one. The organization id, its
  roots and its grants are not touched.
- Source quarantine (ROOT-25): while a registry or credential source is QUARANTINED no new root is established from
  it, the refresh worker does not trust it, and the active roots it established are suspended. Everything it
  observed stays as evidence. Releasing a source lets new roots be established from it again; a suspended root is
  not revived by the release, it is re-established.
"""

from __future__ import annotations

import dataclasses
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.organization.domain.registry import (
    GLOBAL_NAMESPACES,
    RegistryError,
    check_identifier,
)
from zid_platform.modules.organization.domain.roots import RootStatus
from zid_platform.modules.organization.infrastructure import root_store


def _jurisdiction(namespace: str, jurisdiction: str) -> str:
    """LEI and EUID are global (stored with no jurisdiction); a national number needs its own."""
    return "" if namespace in GLOBAL_NAMESPACES else jurisdiction.strip().upper()


async def link_identifier(
    conn: AsyncConnection,
    tenant_id: str,
    organization_id: str,
    *,
    namespace: str,
    identifier: str,
    source_id: str,
    now: datetime,
    jurisdiction: str = "",
) -> bool:
    """Attach an identifier to the organization. True when newly linked, False when it already was.

    Raises ORGANIZATION_IDENTIFIER_CONFLICT when the identifier belongs to another organization."""
    value = check_identifier(namespace, identifier)
    scope = _jurisdiction(namespace, jurisdiction)
    inserted = await conn.execute(
        text("""
            INSERT INTO org.organization_identifier
                (tenant_id, organization_id, namespace, jurisdiction, identifier, source_id, linked_at)
            VALUES (:t, :o, :ns, :j, :id, :src, :at) ON CONFLICT DO NOTHING
        """),
        {
            "t": uuid.UUID(tenant_id),
            "o": uuid.UUID(organization_id),
            "ns": namespace,
            "j": scope,
            "id": value,
            "src": source_id,
            "at": now,
        },
    )
    if inserted.rowcount == 1:
        return True
    owner = await resolve_organization(
        conn, namespace=namespace, identifier=value, jurisdiction=scope
    )
    if owner != organization_id:
        raise RegistryError(
            "ORGANIZATION_IDENTIFIER_CONFLICT",
            f"this {namespace} identifier already identifies a different organization; nothing was merged",
        )
    return False


async def resolve_organization(
    conn: AsyncConnection, *, namespace: str, identifier: str, jurisdiction: str = ""
) -> str | None:
    """The canonical organization an identifier points to (RLS scopes it to the caller's tenant)."""
    row = (
        await conn.execute(
            text(
                "SELECT organization_id FROM org.organization_identifier "
                "WHERE namespace = :ns AND jurisdiction = :j AND identifier = :id"
            ),
            {
                "ns": namespace,
                "j": _jurisdiction(namespace, jurisdiction),
                "id": identifier.strip().upper(),
            },
        )
    ).first()
    return str(row.organization_id) if row else None


async def identifiers_of(conn: AsyncConnection, organization_id: str) -> list[tuple[str, str]]:
    rows = await conn.execute(
        text(
            "SELECT namespace, identifier FROM org.organization_identifier "
            "WHERE organization_id = :o ORDER BY namespace, identifier"
        ),
        {"o": uuid.UUID(organization_id)},
    )
    return [(r.namespace, r.identifier) for r in rows]


async def record_legal_name(
    conn: AsyncConnection,
    tenant_id: str,
    organization_id: str,
    *,
    legal_name: str,
    source_id: str,
    source_revision: str,
    now: datetime,
) -> bool:
    """Open a new name row when the registered name changed. True when the name changed (or is the first)."""
    name = legal_name.strip()
    current = (
        await conn.execute(
            text(
                "SELECT name_id, legal_name FROM org.organization_name "
                "WHERE organization_id = :o AND valid_to IS NULL FOR UPDATE"
            ),
            {"o": uuid.UUID(organization_id)},
        )
    ).first()
    if current is not None and current.legal_name == name:
        return False
    if current is not None:
        await conn.execute(
            text("UPDATE org.organization_name SET valid_to = :at WHERE name_id = :id"),
            {"at": now, "id": current.name_id},
        )
    await conn.execute(
        text("""
            INSERT INTO org.organization_name (name_id, tenant_id, organization_id, legal_name,
                source_id, source_revision, valid_from)
            VALUES (:id, :t, :o, :n, :src, :rev, :at)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "o": uuid.UUID(organization_id),
            "n": name,
            "src": source_id,
            "rev": source_revision[:256],
            "at": now,
        },
    )
    return True


async def legal_name_history(
    conn: AsyncConnection, organization_id: str
) -> list[tuple[str, datetime, datetime | None]]:
    rows = await conn.execute(
        text(
            "SELECT legal_name, valid_from, valid_to FROM org.organization_name "
            "WHERE organization_id = :o ORDER BY valid_from"
        ),
        {"o": uuid.UUID(organization_id)},
    )
    return [(r.legal_name, r.valid_from, r.valid_to) for r in rows]


async def source_is_quarantined(conn: AsyncConnection, tenant_id: str, source_id: str) -> bool:
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM org.source_quarantine "
                "WHERE tenant_id = :t AND source_id = :s AND status = 'QUARANTINED'"
            ),
            {"t": uuid.UUID(tenant_id), "s": source_id},
        )
    ).first()
    return row is not None


async def quarantine_source(
    conn: AsyncConnection, tenant_id: str, source_id: str, *, reason: str, now: datetime
) -> list[str]:
    """Stop trusting a source: record it, then suspend the active roots it established. Returns their ids.

    The registry observations stay (they are evidence of what the source said and when)."""
    await conn.execute(
        text("""
            INSERT INTO org.source_quarantine (tenant_id, source_id, status, reason, quarantined_at)
            VALUES (:t, :s, 'QUARANTINED', :r, :at)
            ON CONFLICT (tenant_id, source_id) DO UPDATE SET status = 'QUARANTINED',
                reason = EXCLUDED.reason, quarantined_at = EXCLUDED.quarantined_at, released_at = NULL
        """),
        {"t": uuid.UUID(tenant_id), "s": source_id, "r": reason[:200], "at": now},
    )
    established = (
        await conn.execute(
            text("""
                SELECT DISTINCT o.organization_id, o.root_assertion_id
                FROM org.registry_observation o
                WHERE o.tenant_id = :t AND o.source_id = :s AND o.root_assertion_id IS NOT NULL
            """),
            {"t": uuid.UUID(tenant_id), "s": source_id},
        )
    ).all()
    suspended: list[str] = []
    organizations: dict[str, Any] = {}
    for row in established:
        organizations.setdefault(str(row.organization_id), set()).add(str(row.root_assertion_id))
    for organization_id, root_ids in organizations.items():
        roots = [
            dataclasses.replace(r, status=RootStatus.SUSPENDED)
            for r in await root_store.load_roots(conn, organization_id)
            if r.root_id in root_ids and r.status is RootStatus.ACTIVE
        ]
        if not roots:
            continue
        await root_store.persist_statuses(conn, tenant_id, roots)
        await conn.execute(
            text(
                "UPDATE org.organization SET authority_root_revision = authority_root_revision + 1 "
                "WHERE organization_id = :o"
            ),
            {"o": uuid.UUID(organization_id)},
        )
        suspended += [r.root_id for r in roots]
    return sorted(suspended)


async def release_source(
    conn: AsyncConnection, tenant_id: str, source_id: str, *, now: datetime
) -> bool:
    """Allow new roots from the source again. Suspended roots stay suspended: they are re-established."""
    done = await conn.execute(
        text(
            "UPDATE org.source_quarantine SET status = 'RELEASED', released_at = :at "
            "WHERE tenant_id = :t AND source_id = :s AND status = 'QUARANTINED'"
        ),
        {"t": uuid.UUID(tenant_id), "s": source_id, "at": now},
    )
    return done.rowcount == 1
