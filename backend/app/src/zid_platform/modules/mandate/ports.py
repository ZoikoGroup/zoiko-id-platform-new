"""Public interface of the mandate module. Other modules import ONLY this.

DR (G-W7; RESILIENCE §17): `advance_writer(conn, tenant, new_cell)` moves the single C3 writer to a
new cell under a strictly higher epoch (requests carrying the old epoch are refused everywhere);
`quarantine_open_reservations` marks every RESERVED reservation RECONCILIATION_REQUIRED - DR never
frees authority to improve availability.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.mandate.application.decision_check import MandateCheck, check_for_decision
from zid_platform.modules.mandate.domain.subagent import CascadeEffect
from zid_platform.modules.mandate.infrastructure.mandate_store import (
    MandateMetadata,
    list_mandates,
    mandate_metadata_page,
    pause_or_revoke_for_grants,
)


async def advance_writer(conn: AsyncConnection, tenant_id: str, new_cell: uuid.UUID) -> int:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1) "
                "ON CONFLICT (tenant_id) DO UPDATE SET cell_id = EXCLUDED.cell_id, "
                "epoch = agent.writer_fence.epoch + 1, updated_at = now() RETURNING epoch"
            ),
            {"t": uuid.UUID(tenant_id), "c": new_cell},
        )
    ).one()
    return int(row.epoch)


async def quarantine_open_reservations(conn: AsyncConnection) -> int:
    r = await conn.execute(
        text(
            "UPDATE agent.mandate_reservation SET reservation_status = 'RECONCILIATION_REQUIRED' "
            "WHERE reservation_status = 'RESERVED'"
        )
    )
    return int(r.rowcount or 0)


async def metadata_projection(conn: AsyncConnection, *, limit: int) -> list[dict[str, Any]]:
    """The minimal, read-only view of the tenant's mandates that an access-readiness review needs (ZID-AG-WF-001 section 06): the
    mandate's own id, state, end date and the actions it allows, plus what its source grant allows and whether that grant is still
    live. No represented person, no resource scope, no value, no purpose text: nothing a reviewer or a model could be misled by.
    The caller owns pagination: it asks for one more than it will keep so an overflow is never silent."""
    from zid_platform.modules.authority.ports import (
        load_grant,  # here, not at the top: authority imports this module
    )

    out: list[dict[str, Any]] = []
    for m in await list_mandates(conn, limit=limit):
        grant = await load_grant(conn, m.authority_grant_id)
        allowed = grant.authority.actions if grant is not None else None
        out.append(
            {
                "mandate_id": m.terms.mandate_id,
                "status": m.terms.status.value,
                "valid_to": m.terms.valid_to,
                "actions": sorted(m.terms.actions),
                "approved_actions": sorted(allowed) if isinstance(allowed, frozenset) else None,
                "grant_found": grant is not None,
                "grant_status": grant.status.value if grant is not None else None,
            }
        )
    return out


__all__ = [
    "CascadeEffect",
    "MandateCheck",
    "MandateMetadata",
    "advance_writer",
    "check_for_decision",
    "mandate_metadata_page",
    "metadata_projection",
    "pause_or_revoke_for_grants",
    "quarantine_open_reservations",
]


async def reservation_state(conn: AsyncConnection, reservation_id: str) -> dict[str, Any] | None:
    """The mandate and state of one reservation: what a tool action needs to know about the capacity it holds. Read only."""
    row = (
        await conn.execute(
            text("SELECT mandate_id, reservation_status::text AS status, expires_at FROM agent.mandate_reservation WHERE reservation_id = :r"), {"r": uuid.UUID(reservation_id)}
        )
    ).first()
    return dict(row._mapping) if row else None


async def settle_for_action(
    conn: AsyncConnection, *, tenant_id: str, reservation_id: str, effect: str, cell_id: uuid.UUID, writer_epoch: int, now: datetime
) -> tuple[str, str | None]:
    """What a tool server's report does to the capacity held for its action: COMMIT after it ran, RELEASE after it did not, HOLD while nobody knows. A held reservation is never freed because a lease ran out
    (an unknown outcome of a spend stays blocked until reconciled). Returns (state afterwards, the ledger's refusal code or None): a refusal leaves the capacity as it was and is reported, never hidden."""
    from zid_platform.modules.mandate.domain.ledger import LedgerError, SideEffect
    from zid_platform.modules.mandate.infrastructure import ledger_store as ls

    tid, rid = uuid.UUID(tenant_id), uuid.UUID(reservation_id)
    try:
        if effect == "COMMIT":
            res = await ls.commit_reservation(conn, tenant_id=tid, reservation_id=rid, cell_id=cell_id, writer_epoch=writer_epoch, now=now, recheck_passed=True)
        elif effect == "RELEASE":
            res = await ls.release_reservation(conn, tenant_id=tid, reservation_id=rid, cell_id=cell_id, writer_epoch=writer_epoch, now=now)
        else:
            res = await ls.sweep_reservation(conn, tenant_id=tid, reservation_id=rid, cell_id=cell_id, writer_epoch=writer_epoch, now=now, side_effect=SideEffect.UNKNOWN, high_risk=True)
    except LedgerError as e:
        current = await reservation_state(conn, reservation_id)
        return (str(current["status"]) if current else "UNKNOWN"), str(e.code)
    return str(res.status.value), None


async def mandates_executed_by(conn: AsyncConnection, agent_ids: list[str], *, limit: int) -> list[dict[str, Any]]:
    """Live mandates whose executor is one of these agents: what a stop of those agents would reach. Read only."""
    rows = await conn.execute(
        text(
            "SELECT mandate_id, executor_principal_id, status::text AS status, valid_to FROM agent.mandate WHERE executor_principal_id = ANY(:a) AND status IN ('ACTIVE', 'PAUSED', 'PENDING_APPROVAL') "
            "ORDER BY created_at DESC LIMIT :n"
        ),
        {"a": [uuid.UUID(x) for x in agent_ids], "n": limit},
    )
    return [dict(x._mapping) for x in rows]


async def open_reservation_count(conn: AsyncConnection, mandate_ids: list[str]) -> int:
    """Capacity still held (reserved, or waiting to be reconciled) on these mandates. A stop does not free it."""
    return int(
        (
            await conn.execute(
                text("SELECT count(*) FROM agent.mandate_reservation WHERE mandate_id = ANY(:m) AND reservation_status IN ('RESERVED', 'RECONCILIATION_REQUIRED')"),
                {"m": [uuid.UUID(x) for x in mandate_ids]},
            )
        ).scalar_one()
    )


async def capacity_check(conn: AsyncConnection, mandate_id: str, *, value: tuple[str, str] | None, now: datetime) -> dict[str, Any]:
    """Would one more spend of this size fit the mandate right now? Read only: nothing is reserved. Returns what is left and, if it would not fit, the ledger's own reason. A concurrent spend can still change the answer;
    only a reservation holds capacity."""
    from decimal import Decimal, InvalidOperation

    from zid_platform.modules.mandate.domain.ledger import Amounts, LedgerError, ReserveRequest, check_reserve, remaining
    from zid_platform.modules.mandate.infrastructure.ledger_store import digest, read_balance
    from zid_platform.modules.mandate.infrastructure.mandate_store import load_terms

    try:
        terms, budget, _grant = await load_terms(conn, mandate_id)
        balance = await read_balance(conn, uuid.UUID(mandate_id))
    except (LedgerError, ValueError):
        return {"available": False, "reason": "MANDATE_NOT_FOUND"}
    try:
        amount = Decimal(value[0]) if value else Decimal(0)
    except InvalidOperation:
        return {"available": False, "reason": "MANDATE_VALUE_INVALID"}
    req = ReserveRequest(digest("preflight"), digest("preflight"), Amounts(amount, 1 if value else 0, 0), value[1] if value else None)
    why = check_reserve(budget, balance, req, mandate_status=terms.status, now=now, valid_to=getattr(terms, "valid_to", None))
    left = {k: (str(v) if isinstance(v, Decimal) else v) for k, v in remaining(budget, balance).items()}
    return {"available": why is None, "reason": why, "remaining": left, "currency": budget.currency, "ledger_version": balance.ledger_version, "reserved": False}


async def canonical_hash_of(conn: AsyncConnection, mandate_id: str) -> str | None:
    """The hash of a mandate's exact terms, or None when there is no such mandate. A receipt names it so it can be matched to what was confirmed."""
    from zid_platform.modules.mandate.domain.canonical import canonical_hash, canonical_terms
    from zid_platform.modules.mandate.infrastructure.mandate_store import canonical_row

    try:
        row = await canonical_row(conn, mandate_id)
    except ValueError:
        return None
    return canonical_hash(canonical_terms(row)) if row else None


async def pause_mandates_of_executors(conn: AsyncConnection, agent_ids: list[str]) -> int:
    """A stop of an agent also pauses the live mandates it executes, so nothing can be reserved against them while it is stopped. Returns how many were paused. A paused mandate is started again through its own gates."""
    if not agent_ids:
        return 0
    res = await conn.execute(
        text("UPDATE agent.mandate SET status = 'PAUSED' WHERE executor_principal_id = ANY(:a) AND status = 'ACTIVE'"), {"a": [uuid.UUID(x) for x in agent_ids]}
    )
    return int(res.rowcount or 0)
