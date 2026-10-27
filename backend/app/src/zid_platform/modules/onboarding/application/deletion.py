"""Deleting a closed company's data (TENANCY-001 §23; PRIV-001; TEN-25).

Closing a tenant (offboarding) stops it authenticating; it does not delete anything. Deletion is a separate, recorded
request on a CLOSED tenant, carried out by the worker:

- every subject in the evidence vault is crypto-erased through the same legal-hold evaluation as a single person's
  erasure; a subject under hold is kept and reported, and the tenant's dedicated key is then left alone, so nothing a
  hold protects is destroyed;
- connector secrets are destroyed, cached security state and routing for the tenant are dropped;
- the receipts, hashes and checkpoints that prove what was decided stay valid: the ledger is retained per policy;
- two dates remain owed and are recorded: when backups holding the data age out, and when the scheduled destruction of
  the dedicated key completes.

Idempotent: a repeated pass re-reads the same state and the record is updated, never duplicated.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import erase_tenant
from zid_platform.modules.integration.ports import purge_connector_secrets
from zid_platform.modules.signals.ports import forget_tenant as forget_security_state
from zid_platform.modules.tenant.ports import TenantStatus, load_tenant
from zid_platform.modules.tenant.ports import forget as forget_routing

BACKUP_RETENTION = timedelta(
    days=35
)  # backups are overwritten on this cycle (ZID-SRE-001 backup policy)
KEY_DESTRUCTION_WINDOW = timedelta(days=30)  # the longest pending window the key service allows


class DeletionError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


async def request_deletion(
    conn: AsyncConnection, tenant_id: str, *, requested_by: str, now: datetime
) -> None:
    """Record the request. Only a CLOSED tenant can have its data deleted: closing drains it first."""
    tenant = await load_tenant(conn, tenant_id, lock=True)
    if tenant is None:
        raise DeletionError("TENANT_NOT_FOUND", "no such tenant")
    if tenant.status is not TenantStatus.CLOSED:
        raise DeletionError(
            "TENANT_NOT_CLOSED", "close the tenant (offboarding) before deleting its data"
        )
    await conn.execute(
        text(
            "INSERT INTO platform.tenant_deletion (tenant_id, requested_by, requested_at, status) "
            "VALUES (:t, :by, :at, 'REQUESTED') ON CONFLICT (tenant_id) DO NOTHING"
        ),
        {"t": tenant_id, "by": requested_by, "at": now},
    )


async def deletion_status(conn: AsyncConnection, tenant_id: str) -> dict[str, object] | None:
    """The record of a deletion: the report, and the dates still owed (backup age-out, key destruction)."""
    row = (
        await conn.execute(
            text(
                "SELECT status, requested_by, requested_at, report, backup_erasure_due, key_destruction_due, "
                "completed_at FROM platform.tenant_deletion WHERE tenant_id = :t"
            ),
            {"t": tenant_id},
        )
    ).first()
    if row is None:
        return None

    def iso(v: datetime | None) -> str | None:
        return v.isoformat() if v else None

    return {
        "tenant_id": tenant_id,
        "status": row.status,
        "requested_at": iso(row.requested_at),
        "report": row.report,
        "backup_erasure_due": iso(row.backup_erasure_due),
        "key_destruction_due": iso(row.key_destruction_due),
        "completed_at": iso(row.completed_at),
    }


async def delete_tenant_material(
    conn: AsyncConnection, tenant_id: str, *, now: datetime
) -> dict[str, object]:
    row = (
        await conn.execute(
            text("SELECT status FROM platform.tenant_deletion WHERE tenant_id = :t FOR UPDATE"),
            {"t": tenant_id},
        )
    ).first()
    if row is None or row.status == "COMPLETE":
        return {"tenant_id": tenant_id, "skipped": True}
    vault = await erase_tenant(conn, tenant_id, request_ref=f"tenant-deletion:{tenant_id}", now=now)
    secrets = await purge_connector_secrets(conn, tenant_id)
    cached = forget_security_state(tenant_id)
    forget_routing(tenant_id)
    held = vault.partial > 0
    report = {
        "subjects": vault.subjects,
        "erased": vault.erased,
        "held_back": vault.partial,
        "objects_erased": vault.objects_erased,
        "objects_retained": vault.objects_retained,
        "connector_secrets_destroyed": secrets,
        "cache_entries_dropped": cached,
        "dedicated_key_scheduled": vault.dedicated_key_scheduled,
    }
    await conn.execute(
        text(
            "UPDATE platform.tenant_deletion SET status = :s, report = CAST(:r AS jsonb), "
            "backup_erasure_due = :b, key_destruction_due = :k, completed_at = :c WHERE tenant_id = :t"
        ),
        {
            "t": tenant_id,
            "s": "HELD" if held else "COMPLETE",
            "r": json.dumps(report),
            "b": now + BACKUP_RETENTION,
            "k": now + KEY_DESTRUCTION_WINDOW if vault.dedicated_key_scheduled else None,
            "c": None if held else now,
        },
    )
    return {"tenant_id": tenant_id, "status": "HELD" if held else "COMPLETE", **report}


async def deletion_pass(conn: AsyncConnection, now: datetime) -> list[dict[str, object]]:
    """Worker pass (BYPASSRLS): requested deletions, and held ones (a lifted hold lets the next pass finish)."""
    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id FROM platform.tenant_deletion WHERE status IN ('REQUESTED', 'HELD')"
            )
        )
    ).all()
    out = []
    for r in rows:
        tenant = str(r.tenant_id)
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        out.append(await delete_tenant_material(conn, tenant, now=now))
    return out
