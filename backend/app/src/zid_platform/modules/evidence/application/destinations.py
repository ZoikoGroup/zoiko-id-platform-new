"""Deliverable destinations another module must reach later (an email address or an E.164 number of a WF-05 case contact; CR-114, CR-115).

Encrypted in the vault like any contact; the caller keeps only the object id. Not raw evidence: a destination is read back only at send time.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.application.vault import read_object, store_object


async def keep_destination(conn: AsyncConnection, tenant_id: str, value: str) -> str:
    object_id, _ = await store_object(
        conn,
        tenant_id,
        content=value.encode("utf-8"),
        object_type="communication_contact",
        classification="D3",
        retention_class="contact",
    )
    return object_id


async def read_destination(conn: AsyncConnection, tenant_id: str, object_id: str) -> str:
    return (await read_object(conn, tenant_id, object_id)).decode("utf-8")


__all__ = ["keep_destination", "read_destination"]
