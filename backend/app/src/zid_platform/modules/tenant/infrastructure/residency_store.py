"""PostgreSQL access for platform.tenant_residency and platform.residency_transfer_rule (CR-014)."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.residency import Residency, ResidencyError, TransferRule


def _residency(r: Row[Any]) -> Residency:
    return Residency(
        str(r.tenant_id),
        r.residency_profile_id,
        r.home_cell_id,
        tuple(r.permitted_recovery_cells),
        tuple(r.prohibited_cells),
        tuple(r.operator_jurisdictions),
        r.key_profile,
        r.jurisdiction_pack_id,
        int(r.placement_epoch),
        int(r.revision),
    )


def _rule(r: Row[Any]) -> TransferRule:
    return TransferRule(
        str(r.rule_id),
        r.data_class,
        r.destination_cell_id,
        r.purpose_code,
        r.mechanism,
        r.jurisdiction_pack_id,
        tuple(r.field_allowlist),
        r.valid_from,
        r.valid_to,
        r.status,
    )


async def load(conn: AsyncConnection, tenant_id: str, *, lock: bool = False) -> Residency | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.tenant_residency WHERE tenant_id = :t"
                + (" FOR UPDATE" if lock else "")
            ),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    return _residency(row) if row else None


async def save(conn: AsyncConnection, v: Mapping[str, Any]) -> Residency:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.tenant_residency (tenant_id, residency_profile_id,
                    home_cell_id, permitted_recovery_cells, prohibited_cells,
                    operator_jurisdictions, key_profile, jurisdiction_pack_id, approval_ref,
                    updated_by)
                VALUES (:t, :p, :home, :rec, :pro, :ops, :key, :pack, :approval, :by)
                ON CONFLICT (tenant_id) DO UPDATE SET
                    residency_profile_id = EXCLUDED.residency_profile_id,
                    home_cell_id = EXCLUDED.home_cell_id,
                    permitted_recovery_cells = EXCLUDED.permitted_recovery_cells,
                    prohibited_cells = EXCLUDED.prohibited_cells,
                    operator_jurisdictions = EXCLUDED.operator_jurisdictions,
                    key_profile = EXCLUDED.key_profile,
                    jurisdiction_pack_id = EXCLUDED.jurisdiction_pack_id,
                    approval_ref = EXCLUDED.approval_ref,
                    updated_by = EXCLUDED.updated_by,
                    placement_epoch = platform.tenant_residency.placement_epoch
                        + CASE WHEN platform.tenant_residency.home_cell_id <> EXCLUDED.home_cell_id
                          THEN 1 ELSE 0 END,
                    revision = platform.tenant_residency.revision + 1,
                    updated_at = now()
                RETURNING *
            """),
            {
                "t": uuid.UUID(v["tenant_id"]),
                "p": v["residency_profile_id"],
                "home": v["home_cell_id"],
                "rec": list(v["permitted_recovery_cells"]),
                "pro": list(v["prohibited_cells"]),
                "ops": list(v["operator_jurisdictions"]),
                "key": v["key_profile"],
                "pack": v["jurisdiction_pack_id"],
                "approval": v["approval_ref"],
                "by": uuid.UUID(v["updated_by"]),
            },
        )
    ).one()
    return _residency(row)


async def insert_rule(conn: AsyncConnection, v: Mapping[str, Any]) -> TransferRule:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.residency_transfer_rule (rule_id, tenant_id, data_class,
                    destination_cell_id, purpose_code, mechanism, jurisdiction_pack_id,
                    field_allowlist, approval_ref, created_by, approved_by, valid_from, valid_to)
                VALUES (:id, :t, :dc, :dest, :purpose, :mech, :pack, :fields, :approval, :by,
                    :approver, :from, :to)
                RETURNING *
            """),
            {
                "id": uuid.UUID(v["rule_id"]),
                "t": uuid.UUID(v["tenant_id"]),
                "dc": v["data_class"],
                "dest": v["destination_cell_id"],
                "purpose": v["purpose_code"],
                "mech": v["mechanism"],
                "pack": v["jurisdiction_pack_id"],
                "fields": list(v["field_allowlist"]),
                "approval": v["approval_ref"],
                "by": uuid.UUID(v["created_by"]),
                "approver": uuid.UUID(v["approved_by"]),
                "from": v["valid_from"],
                "to": v["valid_to"],
            },
        )
    ).one()
    return _rule(row)


async def rules(conn: AsyncConnection, *, active_only: bool = False) -> list[TransferRule]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.residency_transfer_rule "
                + ("WHERE status = 'ACTIVE' " if active_only else "")
                + "ORDER BY created_at"
            )
        )
    ).all()
    return [_rule(r) for r in rows]


async def set_rule_status(conn: AsyncConnection, rule_id: str, status: str) -> TransferRule | None:
    row = (
        await conn.execute(
            text(
                "UPDATE platform.residency_transfer_rule SET status = :s WHERE rule_id = :id "
                "AND status <> 'RETIRED' RETURNING *"
            ),
            {"s": status, "id": uuid.UUID(rule_id)},
        )
    ).first()
    return _rule(row) if row else None


async def advance_placement(
    conn: AsyncConnection, tenant_id: str, *, new_home: str, actor: str
) -> int:
    """placement_epoch + 1 and the new home cell, inside a governed recovery operation only (the
    transaction-local `app.governed_placement` flag lets tenant_guard accept the home change)."""
    await conn.execute(text("SELECT set_config('app.governed_placement', 'on', true)"))
    row = (
        await conn.execute(
            text(
                "UPDATE platform.tenant_residency SET placement_epoch = placement_epoch + 1, "
                "home_cell_id = :home, revision = revision + 1, updated_by = :by, updated_at = now() "
                "WHERE tenant_id = :t RETURNING placement_epoch"
            ),
            {"t": uuid.UUID(tenant_id), "home": new_home, "by": uuid.UUID(actor)},
        )
    ).first()
    await conn.execute(
        text(
            "UPDATE platform.tenant SET home_cell_id = :home, row_version = row_version + 1 WHERE tenant_id = :t"
        ),
        {"t": uuid.UUID(tenant_id), "home": new_home},
    )
    await conn.execute(text("SELECT set_config('app.governed_placement', '', true)"))
    if row is None:
        raise ResidencyError(
            "RESIDENCY_PLACEMENT_REQUIRED", "The tenant has no placement decision."
        )
    return int(row.placement_epoch)
