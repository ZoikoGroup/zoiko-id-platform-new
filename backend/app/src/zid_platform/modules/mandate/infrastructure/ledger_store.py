"""PostgreSQL mandate ledger (DEC-C5; ZID-MANDATE-001 §11-15, §19; ZID-DATA-001 §8.2-8.3; P0-03).

Every operation runs inside the caller's tenant transaction (`zid_db.tenant_transaction`), so RLS
scopes it to the server-derived tenant. Inside it:

1. writer fence: this cell must hold the tenant's current writer epoch (`FOR SHARE`), or the call
   is rejected with WRITER_EPOCH_STALE (split-brain protection, §19.1);
2. the balance row is locked `FOR UPDATE` (and the mandate `FOR SHARE`), which serializes every
   reserve/commit/release on one mandate in its home cell;
3. the domain transition in `mandate.domain.ledger` decides the outcome and the error code;
4. a conditional UPDATE re-checks the limits in SQL and the ledger_version, so even a bug in step
   3 cannot push committed + reserved past a limit.

There is no writable "remaining" column; it is derived on read.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Balance,
    Budget,
    LedgerError,
    MandateStatus,
    Reservation,
    ReservationStatus,
    ReserveRequest,
    SideEffect,
    commit,
    expire_or_reconcile,
    is_exhausted,
    release,
    reserve,
)

DEFAULT_LEASE = timedelta(minutes=5)


def digest(value: str) -> str:
    """Hex SHA-256; idempotency keys and transaction contexts are stored only as hashes."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class MandateRecord:
    mandate_id: uuid.UUID
    tenant_id: uuid.UUID
    authority_grant_id: uuid.UUID
    represented_principal_id: uuid.UUID
    executor_principal_id: uuid.UUID
    purpose_id: uuid.UUID
    resource_scope_id: uuid.UUID
    allowed_actions: tuple[str, ...]
    budget: Budget
    home_cell_id: uuid.UUID
    status: MandateStatus
    valid_from: datetime
    valid_to: datetime
    created_by_principal_id: uuid.UUID


async def create_mandate(conn: AsyncConnection, m: MandateRecord) -> None:
    """Insert a mandate and its zero balance in one transaction."""
    b = m.budget
    await conn.execute(
        text("""
            INSERT INTO agent.mandate (
                mandate_id, tenant_id, authority_grant_id, represented_principal_id,
                executor_principal_id, purpose_id, resource_scope_id, allowed_actions,
                value_limit, per_transaction_value_limit, currency, count_limit, data_limit_bytes,
                home_cell_id, status, valid_from, valid_to, created_by_principal_id)
            VALUES (:mid, :tid, :gid, :rep, :exe, :pur, :res, :acts, :vl, :ptv, :cur, :cl, :dl,
                    :cell, CAST(:status AS agent.mandate_status), :vf, :vt, :by)
        """),
        {
            "mid": m.mandate_id,
            "tid": m.tenant_id,
            "gid": m.authority_grant_id,
            "rep": m.represented_principal_id,
            "exe": m.executor_principal_id,
            "pur": m.purpose_id,
            "res": m.resource_scope_id,
            "acts": list(m.allowed_actions),
            "vl": b.value_limit,
            "ptv": b.per_transaction_value_limit,
            "cur": b.currency,
            "cl": b.count_limit,
            "dl": b.data_limit_bytes,
            "cell": m.home_cell_id,
            "status": m.status.value,
            "vf": m.valid_from,
            "vt": m.valid_to,
            "by": m.created_by_principal_id,
        },
    )
    await conn.execute(
        text("INSERT INTO agent.mandate_balance (mandate_id, tenant_id) VALUES (:mid, :tid)"),
        {"mid": m.mandate_id, "tid": m.tenant_id},
    )


async def check_writer(
    conn: AsyncConnection, tenant_id: uuid.UUID, cell_id: uuid.UUID, epoch: int
) -> None:
    row = (
        await conn.execute(
            text("SELECT cell_id, epoch FROM agent.writer_fence WHERE tenant_id = :t FOR SHARE"),
            {"t": tenant_id},
        )
    ).first()
    if row is None or row.cell_id != cell_id or row.epoch != epoch:
        raise LedgerError("WRITER_EPOCH_STALE")


@dataclass(frozen=True, slots=True)
class _Locked:
    budget: Budget
    balance: Balance
    status: MandateStatus
    valid_to: datetime


async def _lock(conn: AsyncConnection, mandate_id: uuid.UUID) -> _Locked:
    row = (
        await conn.execute(
            text("""
                SELECT m.status, m.valid_to, m.currency, m.value_limit, m.per_transaction_value_limit,
                       m.count_limit, m.data_limit_bytes,
                       b.committed_value, b.reserved_value, b.committed_count, b.reserved_count,
                       b.committed_bytes, b.reserved_bytes, b.ledger_version
                FROM agent.mandate m JOIN agent.mandate_balance b USING (mandate_id)
                WHERE m.mandate_id = :mid
                FOR UPDATE OF b FOR SHARE OF m
            """),
            {"mid": mandate_id},
        )
    ).first()
    if row is None:
        raise LedgerError("MANDATE_NOT_FOUND")
    return _Locked(
        budget=Budget(
            currency=row.currency.strip() if row.currency else None,
            value_limit=row.value_limit,
            count_limit=row.count_limit,
            data_limit_bytes=row.data_limit_bytes,
            per_transaction_value_limit=row.per_transaction_value_limit,
        ),
        balance=Balance(
            committed=Amounts(row.committed_value, row.committed_count, row.committed_bytes),
            reserved=Amounts(row.reserved_value, row.reserved_count, row.reserved_bytes),
            ledger_version=row.ledger_version,
        ),
        status=MandateStatus(row.status),
        valid_to=row.valid_to,
    )


async def _write_balance(
    conn: AsyncConnection, mandate_id: uuid.UUID, old: Balance, new: Balance
) -> None:
    result = await conn.execute(
        text("""
            UPDATE agent.mandate_balance b
            SET committed_value = :cv, reserved_value = :rv, committed_count = :cc,
                reserved_count = :rc, committed_bytes = :cb, reserved_bytes = :rb,
                ledger_version = :nv
            FROM agent.mandate m
            WHERE b.mandate_id = :mid AND m.mandate_id = b.mandate_id AND b.ledger_version = :ov
              AND (m.value_limit IS NULL OR CAST(:cv AS numeric) + CAST(:rv AS numeric) <= m.value_limit)
              AND (m.count_limit IS NULL OR CAST(:cc AS bigint) + CAST(:rc AS bigint) <= m.count_limit)
              AND (m.data_limit_bytes IS NULL OR CAST(:cb AS bigint) + CAST(:rb AS bigint) <= m.data_limit_bytes)
        """),
        {
            "mid": mandate_id,
            "ov": old.ledger_version,
            "nv": new.ledger_version,
            "cv": new.committed.value,
            "rv": new.reserved.value,
            "cc": new.committed.count,
            "rc": new.reserved.count,
            "cb": new.committed.data_bytes,
            "rb": new.reserved.data_bytes,
        },
    )
    if result.rowcount != 1:
        raise LedgerError("LEDGER_GUARD_REJECTED", "database limit/version guard refused the write")


def _reservation(row: object) -> Reservation:
    r = row
    return Reservation(
        reservation_id=str(r.reservation_id),  # type: ignore[attr-defined]
        idempotency_key=bytes(r.idempotency_key_hash).hex(),  # type: ignore[attr-defined]
        transaction_context_hash=bytes(r.transaction_context_hash).hex(),  # type: ignore[attr-defined]
        amounts=Amounts(r.requested_value, r.requested_count, r.requested_bytes),  # type: ignore[attr-defined]
        status=ReservationStatus(r.reservation_status),  # type: ignore[attr-defined]
        expires_at=r.expires_at,  # type: ignore[attr-defined]
    )


_RES_COLS = """reservation_id, idempotency_key_hash, transaction_context_hash, requested_value,
               requested_count, requested_bytes, reservation_status, expires_at, mandate_id"""


async def reserve_capacity(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    mandate_id: uuid.UUID,
    idempotency_key: str,
    transaction_context: str,
    amounts: Amounts,
    currency: str | None,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
    lease: timedelta = DEFAULT_LEASE,
) -> Reservation:
    """Atomic reserve (§12.1). A same-key retry returns the original reservation."""
    await check_writer(conn, tenant_id, cell_id, writer_epoch)
    locked = await _lock(conn, mandate_id)
    key_hash = digest(idempotency_key)
    existing_row = (
        await conn.execute(
            text(
                f"SELECT {_RES_COLS} FROM agent.mandate_reservation "
                "WHERE mandate_id = :mid AND idempotency_key_hash = :k"
            ),
            {"mid": mandate_id, "k": bytes.fromhex(key_hash)},
        )
    ).first()
    req = ReserveRequest(key_hash, digest(transaction_context), amounts, currency)
    new_balance, res = reserve(
        locked.budget,
        locked.balance,
        req,
        existing=_reservation(existing_row) if existing_row else None,
        new_reservation_id=str(uuid7()),
        mandate_status=locked.status,
        now=now,
        valid_to=locked.valid_to,
        expires_at=now + lease,
    )
    if existing_row is not None:
        return res
    await conn.execute(
        text("""
            INSERT INTO agent.mandate_reservation (
                reservation_id, tenant_id, mandate_id, idempotency_key_hash, transaction_context_hash,
                requested_value, requested_count, requested_bytes, currency, reservation_status, expires_at)
            VALUES (:rid, :tid, :mid, :k, :ctx, :v, :c, :b, :cur, 'RESERVED', :exp)
        """),
        {
            "rid": uuid.UUID(res.reservation_id),
            "tid": tenant_id,
            "mid": mandate_id,
            "k": bytes.fromhex(key_hash),
            "ctx": bytes.fromhex(req.transaction_context_hash),
            "v": amounts.value,
            "c": amounts.count,
            "b": amounts.data_bytes,
            "cur": currency,
            "exp": res.expires_at,
        },
    )
    await _write_balance(conn, mandate_id, locked.balance, new_balance)
    return res


async def _settle(
    conn: AsyncConnection,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
    transition: Callable[[Balance, Reservation], tuple[Balance, Reservation]],
    execution_id: str | None = None,
) -> Reservation:
    await check_writer(conn, tenant_id, cell_id, writer_epoch)
    mid_row = (
        await conn.execute(
            text("SELECT mandate_id FROM agent.mandate_reservation WHERE reservation_id = :r"),
            {"r": reservation_id},
        )
    ).first()
    if mid_row is None:
        raise LedgerError("RESERVATION_NOT_FOUND")
    locked = await _lock(
        conn, mid_row.mandate_id
    )  # balance first, then the reservation: one lock order
    row = (
        await conn.execute(
            text(
                f"SELECT {_RES_COLS} FROM agent.mandate_reservation WHERE reservation_id = :r FOR UPDATE"
            ),
            {"r": reservation_id},
        )
    ).first()
    res = _reservation(row)
    new_balance, new_res = transition(locked.balance, res)
    if new_res == res and new_balance == locked.balance:
        return new_res  # idempotent replay: terminal state returned, nothing written
    await conn.execute(
        text("""
            UPDATE agent.mandate_reservation
            SET reservation_status = CAST(:s AS agent.reservation_status),
                settled_at = CASE WHEN :s IN ('COMMITTED', 'RELEASED', 'EXPIRED') THEN :now ELSE settled_at END,
                execution_id = coalesce(:eid, execution_id)
            WHERE reservation_id = :r
        """),
        {"s": new_res.status.value, "now": now, "eid": execution_id, "r": reservation_id},
    )
    await _write_balance(conn, mid_row.mandate_id, locked.balance, new_balance)
    if new_res.status is ReservationStatus.COMMITTED and is_exhausted(locked.budget, new_balance):
        await conn.execute(
            text(
                "UPDATE agent.mandate SET status = 'EXHAUSTED', row_version = row_version + 1 "
                "WHERE mandate_id = :m AND status = 'ACTIVE'"
            ),
            {"m": mid_row.mandate_id},
        )
    return new_res


async def commit_reservation(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
    recheck_passed: bool,
    execution_id: str | None = None,
) -> Reservation:
    """Commit after the protected side effect (§13). Repeats return the terminal state."""
    return await _settle(
        conn,
        tenant_id,
        reservation_id,
        cell_id,
        writer_epoch,
        now,
        lambda bal, res: commit(bal, res, now=now, recheck_passed=recheck_passed),
        execution_id,
    )


async def release_reservation(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
) -> Reservation:
    return await _settle(conn, tenant_id, reservation_id, cell_id, writer_epoch, now, release)


async def sweep_reservation(
    conn: AsyncConnection,
    *,
    tenant_id: uuid.UUID,
    reservation_id: uuid.UUID,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
    side_effect: SideEffect,
    high_risk: bool,
) -> Reservation:
    """Lease expiry / crash reconciliation for one reservation (§14)."""
    return await _settle(
        conn,
        tenant_id,
        reservation_id,
        cell_id,
        writer_epoch,
        now,
        lambda bal, res: expire_or_reconcile(
            bal, res, now=now, side_effect=side_effect, high_risk=high_risk
        ),
    )


async def read_balance(conn: AsyncConnection, mandate_id: uuid.UUID) -> Balance:
    row = (
        await conn.execute(
            text(
                "SELECT committed_value, reserved_value, committed_count, reserved_count, "
                "committed_bytes, reserved_bytes, ledger_version FROM agent.mandate_balance "
                "WHERE mandate_id = :m"
            ),
            {"m": mandate_id},
        )
    ).first()
    if row is None:
        raise LedgerError("MANDATE_NOT_FOUND")
    return Balance(
        Amounts(Decimal(row.committed_value), row.committed_count, row.committed_bytes),
        Amounts(Decimal(row.reserved_value), row.reserved_count, row.reserved_bytes),
        row.ledger_version,
    )
