"""Tenant locator: accept signed directory records from cells; resolve tenants (DEC-G3; G-W6).

`publish` verifies a cell's JWS against that cell's *pinned* public keys (never fetched), applies
the record rules (PII gate, home-cell-only, epoch fencing) and upserts the record and its routing
aliases in one transaction. `resolve` answers by tenant id or routing alias; the discovery module
signs what it returns.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_crypto import CryptoError, peek_header, verify_jws

from zid_platform.modules.locator.domain.record import (
    RECORD_TYP,
    DirectoryRecord,
    LocatorError,
    check_supersedes,
    parse_record,
)

CellKeys = Mapping[
    str, Sequence[Mapping[str, Any]]
]  # cell id -> pinned public JWKs ("*": any cell)


def _row(r: Any) -> DirectoryRecord:
    m = r._mapping
    return DirectoryRecord(
        str(m["tenant_id"]),
        m["tenant_status"],
        m["home_cell_id"],
        m["recovery_cell_id"],
        int(m["placement_epoch"]),
        m["residency_profile_id"],
        m["tenant_key_profile_ref"],
        tuple(m["routing_aliases"]),
        tuple(m["service_entitlements"]),
        tuple(m["policy_release_refs"]),
        int(m["record_revision"]),
    )


async def load(
    conn: AsyncConnection, tenant_id: str, *, lock: bool = False
) -> DirectoryRecord | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM global_directory.tenant_directory_record WHERE tenant_id = :t"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return _row(row) if row else None


def verify_publication(token: str, cell_keys: CellKeys) -> tuple[str, dict[str, Any]]:
    """(publishing cell, record payload). The cell is the JWS `iss`; its keys are pinned."""
    try:
        kid = str(peek_header(token).get("kid") or "")
    except CryptoError as e:
        raise LocatorError("PUBLICATION_SIGNATURE_INVALID", e.code) from e
    # The key (by kid) decides which cell signed; the payload's iss must then be that same cell.
    owners = [cell for cell, keys in cell_keys.items() if any(k.get("kid") == kid for k in keys)]
    if len(owners) != 1:
        raise LocatorError("PUBLICATION_SIGNATURE_INVALID", f"no single pinned cell key {kid!r}")
    owner = owners[0]
    try:
        _, payload = verify_jws(token, keys=list(cell_keys[owner]), typ=RECORD_TYP)
    except CryptoError as e:
        raise LocatorError("PUBLICATION_SIGNATURE_INVALID", e.code) from e
    iss = str(payload.get("iss") or "")
    if owner != "*" and iss != owner:
        raise LocatorError("PUBLICATION_SIGNATURE_INVALID", "the key belongs to another cell")
    record = payload.get("record")
    if not isinstance(record, dict):
        raise LocatorError("VALIDATION_FAILED", "record")
    return iss, record


async def publish(conn: AsyncConnection, token: str, cell_keys: CellKeys) -> DirectoryRecord:
    cell, raw = verify_publication(token, cell_keys)
    rec = parse_record(raw)
    current = await load(conn, rec.tenant_id, lock=True)
    check_supersedes(rec, current, publisher=cell)
    await conn.execute(
        text("""
            INSERT INTO global_directory.tenant_directory_record (tenant_id, tenant_status, home_cell_id,
                recovery_cell_id, placement_epoch, residency_profile_id, tenant_key_profile_ref,
                routing_aliases, service_entitlements, policy_release_refs, record_revision,
                published_by_cell)
            VALUES (:t, :st, :home, :rec, :ep, :prof, :key, :aliases, :ent, :pol, :rev, :cell)
            ON CONFLICT (tenant_id) DO UPDATE SET tenant_status = EXCLUDED.tenant_status,
                home_cell_id = EXCLUDED.home_cell_id, recovery_cell_id = EXCLUDED.recovery_cell_id,
                placement_epoch = EXCLUDED.placement_epoch,
                residency_profile_id = EXCLUDED.residency_profile_id,
                tenant_key_profile_ref = EXCLUDED.tenant_key_profile_ref,
                routing_aliases = EXCLUDED.routing_aliases,
                service_entitlements = EXCLUDED.service_entitlements,
                policy_release_refs = EXCLUDED.policy_release_refs,
                record_revision = EXCLUDED.record_revision,
                published_by_cell = EXCLUDED.published_by_cell,
                state_changed_at = CASE WHEN global_directory.tenant_directory_record.tenant_status
                    <> EXCLUDED.tenant_status THEN now()
                    ELSE global_directory.tenant_directory_record.state_changed_at END
        """),
        {
            "t": uuid.UUID(rec.tenant_id),
            "st": rec.tenant_status,
            "home": rec.home_cell_id,
            "rec": rec.recovery_cell_id,
            "ep": rec.placement_epoch,
            "prof": rec.residency_profile_id,
            "key": rec.tenant_key_profile_ref,
            "aliases": list(rec.routing_aliases),
            "ent": list(rec.service_entitlements),
            "pol": list(rec.policy_release_refs),
            "rev": rec.record_revision,
            "cell": cell,
        },
    )
    await conn.execute(
        text(
            "DELETE FROM global_directory.routing_alias WHERE tenant_id = :t AND NOT (alias = ANY(:a))"
        ),
        {"t": uuid.UUID(rec.tenant_id), "a": list(rec.routing_aliases)},
    )
    for alias in rec.routing_aliases:
        try:
            async with conn.begin_nested():
                await conn.execute(
                    text(
                        "INSERT INTO global_directory.routing_alias (alias, tenant_id) VALUES (:a, :t) "
                        "ON CONFLICT (alias) DO NOTHING"
                    ),
                    {"a": alias, "t": uuid.UUID(rec.tenant_id)},
                )
        except IntegrityError as e:  # pragma: no cover - ON CONFLICT covers it
            raise LocatorError("ROUTING_ALIAS_TAKEN", alias) from e
        owner = (
            await conn.execute(
                text("SELECT tenant_id FROM global_directory.routing_alias WHERE alias = :a"),
                {"a": alias},
            )
        ).scalar_one()
        if str(owner) != rec.tenant_id:
            raise LocatorError("ROUTING_ALIAS_TAKEN", f"{alias!r} routes to another tenant")
    return rec


async def resolve(
    conn: AsyncConnection, *, tenant_id: str | None = None, alias: str | None = None
) -> DirectoryRecord | None:
    if tenant_id:
        try:
            return await load(conn, str(uuid.UUID(tenant_id)))
        except ValueError:
            return None
    if alias:
        row = (
            await conn.execute(
                text("SELECT tenant_id FROM global_directory.routing_alias WHERE alias = :a"),
                {"a": alias.lower()},
            )
        ).first()
        return await load(conn, str(row.tenant_id)) if row else None
    return None
