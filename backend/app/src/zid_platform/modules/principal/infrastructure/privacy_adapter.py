"""The principal module's personal data, for the privacy data map (PRIV-001 §10, §15, §17; F-W3).

    core.principal             the account itself (display label, lifecycle)
    core.principal_identifier  identifiers (only hashes and encrypted references are stored)

Erasure keeps the principal row as an ERASED tombstone (references from receipts, grants and
mandates stay structurally valid) and removes everything identifying: the display label, the
identifier hash (replaced by random bytes, so the old value can no longer be matched) and the
encrypted value reference.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

PRINCIPAL_STORE = "core.principal"
IDENTIFIER_STORE = "core.principal_identifier"


async def _locate_principal(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    row = (
        await conn.execute(
            text("SELECT principal_id, home_cell, created_at, status::text AS status FROM core.principal WHERE principal_id = :p"),
            {"p": uuid.UUID(subject)},
        )
    ).first()
    if row is None or row.status == "ERASED":
        return []
    return [
        {
            "object_ref": str(row.principal_id),
            "object_type": "principal.account",
            "purpose_code": "ACCOUNT",
            "data_category": "D1",
            "trigger_at": row.created_at,
            "region": row.home_cell,
            "retention_policy_code": "ACCOUNT_LIFETIME",
        }
    ]


async def _export_principal(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT principal_type::text AS type, status::text AS status, display_label, home_cell, "
                "created_at FROM core.principal WHERE principal_id = :p"
            ),
            {"p": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose_principal(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE core.principal SET status = 'ERASED', display_label = NULL,
                security_revision = security_revision + 1, row_version = row_version + 1,
                disabled_at = coalesce(disabled_at, now())
            WHERE principal_id = :p AND status <> 'ERASED'
        """),
        {"p": uuid.UUID(entry.object_ref)},
    )


async def _locate_identifiers(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT identifier_id, namespace, recorded_at FROM core.principal_identifier "
                "WHERE principal_id = :p AND superseded_at IS NULL"
            ),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.identifier_id),
            "object_type": f"principal.identifier.{r.namespace}".lower()[:64],
            "purpose_code": "AUTHENTICATE_USER",
            "data_category": "D2",
            "trigger_at": r.recorded_at,
            "region": None,
            "retention_policy_code": "ACCOUNT_LIFETIME",
        }
        for r in rows
    ]


async def _export_identifier(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT namespace, issuer, verification_state::text AS verification_state, valid_from, valid_to "
                "FROM core.principal_identifier WHERE identifier_id = :i"
            ),
            {"i": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose_identifier(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("""
            UPDATE core.principal_identifier SET normalized_value_hash = :rnd, encrypted_value_ref = NULL,
                superseded_at = coalesce(superseded_at, now()), valid_to = coalesce(valid_to, now())
            WHERE identifier_id = :i
        """),
        {"i": uuid.UUID(entry.object_ref), "rnd": os.urandom(32)},
    )


def register() -> None:
    register_store(IDENTIFIER_STORE, locator=_locate_identifiers, exporter=_export_identifier, disposer=_dispose_identifier)
    register_store(PRINCIPAL_STORE, locator=_locate_principal, exporter=_export_principal, disposer=_dispose_principal)


__all__ = ["register"]
