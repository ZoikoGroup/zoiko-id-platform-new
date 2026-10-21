"""The credential module's personal data for the privacy data map: presentation records where the
subject is known. Disclosed claim values are personal data (D3); erasure removes them and keeps
only the claim commitments and the outcome the relying party relied on."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.privacy.ports import register_store

STORE = "cred.presentation_record"


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT presentation_id, created_at FROM cred.presentation_record WHERE subject_principal_id = :p"),
            {"p": uuid.UUID(subject)},
        )
    ).all()
    return [
        {
            "object_ref": str(r.presentation_id),
            "object_type": "credential.presentation",
            "purpose_code": "VERIFY_CREDENTIAL",
            "data_category": "D3",
            "trigger_at": r.created_at,
            "region": None,
            "retention_policy_code": "PRESENTATION_RECORD",
        }
        for r in rows
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT relying_client_id, purpose, requested_claims, status, disclosed_claims, created_at, "
                "completed_at FROM cred.presentation_record WHERE presentation_id = :p"
            ),
            {"p": uuid.UUID(entry.object_ref)},
        )
    ).first()
    return dict(row._mapping) if row else {}


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await conn.execute(
        text("UPDATE cred.presentation_record SET disclosed_claims = NULL WHERE presentation_id = :p"),
        {"p": uuid.UUID(entry.object_ref)},
    )


def register() -> None:
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)


__all__ = ["register"]
