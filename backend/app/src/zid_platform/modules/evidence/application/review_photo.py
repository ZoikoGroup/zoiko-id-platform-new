"""Custody of the document photograph on a route that people decide (CR-125; ZID-VERIFY-001 VERIFY-22).

VERIFY-22 keeps raw identity documents out of the verification module: it keeps normalised results and commitments, and the raw material stays with whoever holds it. A route with no provider and no chip, decided by two
reviewers, has nothing for them to look at unless the platform itself holds the photograph. This module is the ONE place that does: the evidence module, which owns the vault, keeps it encrypted under the applicant's own erasable
key and gives it back only as an object of this kind. The verification module reaches it only through these two ports and never touches the vault itself; opening it is an `evidence.object.read` act (R3, E3), taken by the API
before this is called, and a reviewer's time-limited grant stands on top of that.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_evidence import Digest

from zid_platform.modules.evidence.application.vault import read_object, store_object
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.infrastructure import vault_store as store

OBJECT_TYPE = "idv.document_photo"
CLASSIFICATION = "D4"  # an identity document image: the most sensitive class the vault knows
RETENTION_CLASS = "idv_document_photo"


async def keep(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    content: bytes,
    subject_principal_id: str,
    expires_at: datetime,
) -> tuple[str, Digest]:
    """Store the photograph, encrypted under the subject's key, with the date retention ends. The retention sweep erases the bytes at that date."""
    return await store_object(
        conn, tenant_id, content=content, object_type=OBJECT_TYPE, classification=CLASSIFICATION, retention_class=RETENTION_CLASS,
        subject_principal_id=subject_principal_id, expires_at=expires_at,
    )  # fmt: skip


async def read(conn: AsyncConnection, tenant_id: str, object_id: str) -> bytes:
    """The photograph, and only an object kept by `keep`: asking for any other object by this door is NOT_FOUND, so this port cannot be used to read the vault at large."""
    row = await store.load_object(conn, object_id)
    if row is None or row.object_type != OBJECT_TYPE:
        raise LedgerError("NOT_FOUND", "unknown evidence object")
    return await read_object(conn, tenant_id, object_id)
