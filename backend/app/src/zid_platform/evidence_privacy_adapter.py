"""Uploaded evidence in the privacy data map (CR-071). Wired at the application level, not inside the evidence module: the privacy module
already depends on evidence, so an adapter inside evidence that imported privacy would make a module cycle.

An uploaded document is the most sensitive thing the cell can hold about a person (D3), so it is registered like every other personal
data store: found by subject, exported as a readable summary (what, when, scan result, retention; never the bytes or a hash), and
disposed of by deleting the row, bytes included, when the privacy module erases the person after its own legal-hold checks.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence import ports as evidence
from zid_platform.modules.privacy.ports import register_store

STORE = "evidence.upload"


async def _locate(conn: AsyncConnection, subject: str) -> list[dict[str, Any]]:
    return [
        {
            "object_ref": str(r["upload_id"]),
            "object_type": "evidence.upload",
            "purpose_code": "EVIDENCE_DOCUMENT",
            "data_category": "D3",
            "trigger_at": r["created_at"],
            "region": None,
            "retention_policy_code": None,  # the DPO defines it; the privacy module then applies its default
        }
        for r in await evidence.uploads_by_subject(conn, subject)  # type: ignore[no-untyped-call]
    ]


async def _export(conn: AsyncConnection, entry: Row[Any]) -> dict[str, Any]:
    row = await evidence.upload_summary(conn, entry.object_ref)  # type: ignore[no-untyped-call]
    return row or {}


async def _dispose(conn: AsyncConnection, entry: Row[Any], action: str) -> None:
    await evidence.erase_uploads(conn, str(entry.tenant_id), str(entry.subject_principal_id))  # type: ignore[no-untyped-call]


def register() -> None:
    register_store(STORE, locator=_locate, exporter=_export, disposer=_dispose)


__all__ = ["register"]
