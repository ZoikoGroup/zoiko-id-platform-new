"""Bulk evidence export with expiry (ZID-API-001 API-13).

The caller has already passed the R3 `evidence.export` gate. Each receipt is exported through the same path as a
single export, so each one is verified, signed and leaves its own chain-of-custody record; the bulk package wraps
them under one signed manifest. The package is kept only until `expires_at`: a read after that, or the sweep,
erases it and the export is refused from then on.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_crypto import sign_jws
from zid_evidence import canonicalize, hash_bytes, hash_json
from zid_outbox import emit

from zid_platform.modules.evidence.application import replay
from zid_platform.modules.evidence.application.runtime import current_runtime
from zid_platform.modules.evidence.application.vault import actor_handle
from zid_platform.modules.evidence.domain.bulk_export import MAX_RECEIPTS, available, expiry
from zid_platform.modules.evidence.domain.ledger import LedgerError
from zid_platform.modules.evidence.infrastructure import bulk_store

BULK_TYP = "zid-evidence-bulk-package+jwt"


async def create(
    conn: AsyncConnection,
    tenant_id: str,
    receipt_ids: list[str],
    *,
    mode: str,
    purpose: str,
    case_ref: str,
    custodian_principal_id: str,
    ttl_minutes: int | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    unique = list(dict.fromkeys(receipt_ids))
    if not unique or len(unique) > MAX_RECEIPTS:
        raise LedgerError(
            "EVIDENCE_SCHEMA_INVALID", f"export between 1 and {MAX_RECEIPTS} receipts"
        )
    rt = current_runtime()
    packages = [
        await replay.export_package(
            conn,
            tenant_id,
            rid,
            mode=mode,
            purpose=purpose,
            case_ref=case_ref,
            custodian_principal_id=custodian_principal_id,
            rt=rt,
        )
        for rid in unique
    ]
    export_id, expires_at = str(uuid7()), expiry(now, ttl_minutes)
    package: dict[str, Any] = {"packages": packages}
    package_hash = str(hash_bytes(canonicalize(package), alg="sha-256"))
    manifest = {
        "format": "zid-evidence-bulk-package/1",
        "export_id": export_id,
        "mode": mode,
        "receipt_ids": unique,
        "member_package_ids": [p["manifest"]["package_id"] for p in packages],
        "package_hash": package_hash,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    signature = await asyncio.to_thread(sign_jws, rt.signer, manifest, typ=BULK_TYP)
    package["manifest"], package["manifest_signature"] = manifest, signature
    handle = await actor_handle(conn, tenant_id, custodian_principal_id, rt=rt)
    await bulk_store.insert(
        conn,
        export_id=export_id,
        tenant_id=tenant_id,
        requested_by_handle=handle,
        purpose=purpose,
        case_commitment=str(hash_json({"tenant": tenant_id, "case": case_ref}, alg="sha-256")),
        export_mode=mode,
        receipt_count=len(unique),
        package_hash=package_hash,
        package=package,
        created_at=now,
        expires_at=expires_at,
    )
    await emit(
        conn,
        name="evidence.export.created",
        tenant_id=tenant_id,
        aggregate_type="evidence_package",
        aggregate_id=f"{tenant_id}:{export_id}",
        aggregate_version=1,
        data={
            "package_id": export_id,
            "bulk": True,
            "receipt_count": len(unique),
            "mode": mode,
            "bundle_hash": package_hash,
            "expires_at": expires_at.isoformat(),
            "evidence_class": "E3",
        },
    )
    return {
        "export_id": export_id,
        "expires_at": expires_at.isoformat(),
        "receipt_count": len(unique),
        **package,
    }


async def fetch(
    conn: AsyncConnection, export_id: str, *, now: datetime | None = None
) -> dict[str, Any]:
    """The package while it is available. Past its expiry it is erased on the spot and refused."""
    now = now or datetime.now(UTC)
    row = await bulk_store.load(conn, export_id)
    if row is None:
        raise LedgerError("NOT_FOUND", "unknown export")
    if not available(row["status"], row["expires_at"], now):
        await bulk_store.erase(conn, export_id, now)
        raise LedgerError("EVIDENCE_EXPORT_EXPIRED", "this export has expired and has been erased")
    await bulk_store.count_download(conn, export_id)
    return dict(row["package"])


async def sweep_expired(conn: AsyncConnection, now: datetime | None = None) -> int:
    """Worker pass: erase every package past its expiry, whether or not anyone asked for it."""
    now = now or datetime.now(UTC)
    ids = await bulk_store.expired_ids(conn, now)
    for export_id in ids:
        await bulk_store.erase(conn, export_id, now)
    return len(ids)
