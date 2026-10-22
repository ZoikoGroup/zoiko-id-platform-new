"""PostgreSQL persistence of cell state, readiness, recovery operations and exercises (G-W7)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.resilience.domain.runbooks import (
    Kind,
    OpStatus,
    Readiness,
    ResilienceError,
)


async def cell_status(conn: AsyncConnection, cell_id: str) -> str:
    row = (
        await conn.execute(
            text("SELECT status FROM platform.cell_state WHERE cell_id = :c"), {"c": cell_id}
        )
    ).first()
    return str(row.status) if row else "ACTIVE"


async def accepts_placement(conn: AsyncConnection, cell_id: str) -> bool:
    """A QUARANTINED or DRAINING cell takes no new tenant (RESILIENCE §28; cell.quarantined)."""
    return await cell_status(conn, cell_id) not in ("QUARANTINED", "DRAINING")


async def set_cell_status(
    conn: AsyncConnection, cell_id: str, status: str, *, reason: str | None, actor: str | None
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.cell_state (cell_id, status, reason_code, changed_by, changed_at)
            VALUES (:c, :s, :r, :a, now())
            ON CONFLICT (cell_id) DO UPDATE SET status = EXCLUDED.status, reason_code = EXCLUDED.reason_code,
                changed_by = EXCLUDED.changed_by, changed_at = now()
        """),
        {"c": cell_id, "s": status, "r": reason, "a": uuid.UUID(actor) if actor else None},
    )


async def report_readiness(conn: AsyncConnection, cell_id: str, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.cell_readiness (cell_id, replica_lag_s, backup_age_s, recovery_keys_ready,
                one_az_loss_headroom, last_restore_verified, observed_at)
            VALUES (:c, :lag, :backup, :keys, :head, :restore, now())
            ON CONFLICT (cell_id) DO UPDATE SET replica_lag_s = EXCLUDED.replica_lag_s,
                backup_age_s = EXCLUDED.backup_age_s, recovery_keys_ready = EXCLUDED.recovery_keys_ready,
                one_az_loss_headroom = EXCLUDED.one_az_loss_headroom,
                last_restore_verified = coalesce(EXCLUDED.last_restore_verified,
                    platform.cell_readiness.last_restore_verified),
                observed_at = now()
        """),
        {
            "c": cell_id,
            "lag": v.get("replica_lag_s"),
            "backup": v.get("backup_age_s"),
            "keys": bool(v.get("recovery_keys_ready", False)),
            "head": v.get("one_az_loss_headroom"),
            "restore": v.get("last_restore_verified"),
        },
    )


async def readiness(conn: AsyncConnection, cell_id: str) -> Readiness | None:
    row = (
        await conn.execute(
            text("SELECT * FROM platform.cell_readiness WHERE cell_id = :c"), {"c": cell_id}
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    return Readiness(
        cell_id,
        float(m["replica_lag_s"]) if m["replica_lag_s"] is not None else None,
        float(m["backup_age_s"]) if m["backup_age_s"] is not None else None,
        bool(m["recovery_keys_ready"]),
        float(m["one_az_loss_headroom"]) if m["one_az_loss_headroom"] is not None else None,
        await cell_status(conn, cell_id),
    )


@dataclass(frozen=True, slots=True)
class Operation:
    operation_id: str
    tenant_id: str | None
    kind: Kind
    source_cell_id: str
    target_cell_id: str
    status: OpStatus
    steps_done: int
    blocked_code: str | None
    declared_by: str
    epoch_before: int | None
    epoch_after: int | None
    declared_at: datetime
    completed_at: datetime | None
    observed_rto_s: float | None


def _op(r: Any) -> Operation:
    m = r._mapping
    return Operation(
        str(m["operation_id"]),
        str(m["tenant_id"]) if m["tenant_id"] else None,
        Kind(m["kind"]),
        m["source_cell_id"],
        m["target_cell_id"],
        OpStatus(m["status"]),
        int(m["steps_done"]),
        m["blocked_code"],
        str(m["declared_by"]),
        m["epoch_before"],
        m["epoch_after"],
        m["declared_at"],
        m["completed_at"],
        float(m["observed_rto_s"]) if m["observed_rto_s"] is not None else None,
    )


async def insert_operation(conn: AsyncConnection, v: Mapping[str, Any]) -> Operation:
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("""
                        INSERT INTO platform.recovery_operation (operation_id, tenant_id, kind,
                            source_cell_id, target_cell_id, declared_by, jit_grant_id, epoch_before)
                        VALUES (:id, :t, :k, :src, :dst, :by, :jit, :ep) RETURNING *
                    """),
                    {
                        "id": uuid.uuid4(),
                        "t": uuid.UUID(v["tenant"]) if v.get("tenant") else None,
                        "k": v["kind"],
                        "src": v["source"],
                        "dst": v["target"],
                        "by": uuid.UUID(v["actor"]),
                        "jit": uuid.UUID(v["jit"]) if v.get("jit") else None,
                        "ep": v.get("epoch"),
                    },
                )
            ).one()
    except IntegrityError as e:
        raise ResilienceError(
            "RECOVERY_OPERATION_IN_PROGRESS", "This tenant already has an open recovery operation."
        ) from e
    return _op(row)


async def load_operation(
    conn: AsyncConnection, operation_id: str, *, lock: bool = False
) -> Operation | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.recovery_operation WHERE operation_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(operation_id)},
        )
    ).first()
    return _op(row) if row else None


async def operations(conn: AsyncConnection, limit: int = 100) -> list[Operation]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.recovery_operation ORDER BY declared_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_op(r) for r in rows]


async def update_operation(conn: AsyncConnection, operation_id: str, **fields: Any) -> Operation:
    allowed = {
        "status",
        "steps_done",
        "blocked_code",
        "epoch_after",
        "completed_at",
        "observed_rto_s",
    }
    assert set(fields) <= allowed
    sets = ", ".join(f"{k} = :{k}" for k in fields)
    row = (
        await conn.execute(
            text(
                f"UPDATE platform.recovery_operation SET {sets} WHERE operation_id = :id RETURNING *"
            ),
            {**fields, "id": uuid.UUID(operation_id)},
        )
    ).one()
    return _op(row)


async def log_step(
    conn: AsyncConnection,
    operation_id: str,
    *,
    step: str,
    outcome: str,
    actor: str | None,
    evidence_ref: str | None,
    report: Sequence[Mapping[str, Any]],
) -> int:
    seq = int(
        (
            await conn.execute(
                text(
                    "SELECT coalesce(max(seq), 0) + 1 FROM platform.recovery_step WHERE operation_id = :id"
                ),
                {"id": uuid.UUID(operation_id)},
            )
        ).scalar_one()
    )
    await conn.execute(
        text("""
            INSERT INTO platform.recovery_step (operation_id, seq, step, outcome, actor, evidence_ref, report)
            VALUES (:id, :seq, :step, :outcome, :actor, :ref, CAST(:report AS jsonb))
        """),
        {
            "id": uuid.UUID(operation_id),
            "seq": seq,
            "step": step,
            "outcome": outcome,
            "actor": uuid.UUID(actor) if actor else None,
            "ref": evidence_ref,
            "report": json.dumps(list(report), default=str),
        },
    )
    return seq


async def steps(conn: AsyncConnection, operation_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.recovery_step WHERE operation_id = :id ORDER BY seq"),
            {"id": uuid.UUID(operation_id)},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def insert_exercise(conn: AsyncConnection, v: Mapping[str, Any]) -> str:
    eid = uuid.uuid4()
    await conn.execute(
        text("""
            INSERT INTO platform.dr_exercise (exercise_id, kind, cell_id, scenario, operation_id,
                observed_rto_s, observed_rpo_s, target_rto_s, target_rpo_s, passed, findings,
                recorded_by, started_at, completed_at)
            VALUES (:id, :kind, :cell, :scenario, :op, :rto, :rpo, :trto, :trpo, :passed,
                CAST(:findings AS jsonb), :by, :start, :end)
        """),
        {**v, "id": eid, "findings": json.dumps(list(v.get("findings") or []))},
    )
    return str(eid)


async def exercises(conn: AsyncConnection, limit: int = 100) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.dr_exercise ORDER BY completed_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [dict(r._mapping) for r in rows]


async def cell_view(conn: AsyncConnection, cell_id: str) -> dict[str, Any]:
    r = await readiness(conn, cell_id)
    return (
        r.view()
        if r
        else {"cell_id": cell_id, "status": await cell_status(conn, cell_id), "reported": False}
    )


async def operation_in_progress(conn: AsyncConnection, cell_id: str) -> bool:
    """An OPEN or BLOCKED recovery operation moving a tenant out of or into this cell."""
    row = (
        await conn.execute(
            text(
                "SELECT 1 FROM platform.recovery_operation WHERE status IN ('OPEN', 'BLOCKED') "
                "AND (source_cell_id = :c OR target_cell_id = :c) LIMIT 1"
            ),
            {"c": cell_id},
        )
    ).first()
    return row is not None
