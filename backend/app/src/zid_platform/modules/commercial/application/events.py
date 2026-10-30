"""Commercial events through the transactional outbox (CR-024). Ids and revisions only."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit


async def entitlement_changed(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    entitlement_id: str,
    version: int,
    plan: str,
    previous_plan: str | None,
    price_book_version: int | None,
    effective_from: datetime,
) -> str:
    return await emit(
        conn,
        name="commercial.entitlement.changed",
        tenant_id=tenant_id,
        aggregate_type="entitlement",
        aggregate_id=entitlement_id,
        aggregate_version=version,
        data={
            "plan": plan,
            "previous_plan": previous_plan,
            "price_book_version": price_book_version,
            "effective_from": effective_from.isoformat(),
        },
    )


async def meter_window_frozen(
    conn: AsyncConnection, *, tenant_id: str, month: str, meters: int
) -> str:
    return await emit(
        conn,
        name="commercial.meter_window.frozen",
        tenant_id=tenant_id,
        aggregate_type="meter_window",
        aggregate_id=f"{tenant_id}:{month}",
        aggregate_version=1,
        data={"month": month, "meters_frozen": meters},
    )


async def billable_event_adjusted(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    adjustment_id: str,
    meter: str,
    month: str,
    state: str,
) -> str:
    return await emit(
        conn,
        name="commercial.billable_event.adjusted",
        tenant_id=tenant_id,
        aggregate_type="billable_adjustment",
        aggregate_id=adjustment_id,
        aggregate_version=2,
        data={"meter": meter, "month": month, "state": state},
    )


async def billing_state_changed(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    version: int,
    from_state: str,
    to_state: str,
    reason: str,
) -> str:
    return await emit(
        conn,
        name="commercial.billing_state.changed",
        tenant_id=tenant_id,
        aggregate_type="billing_account",
        aggregate_id=tenant_id,
        aggregate_version=version,
        data={"from": from_state, "to": to_state, "reason": reason},
    )


async def invoice_issued(
    conn: AsyncConnection, *, tenant_id: str, invoice_id: str, number: str, period: str
) -> str:
    return await emit(
        conn,
        name="commercial.invoice.issued",
        tenant_id=tenant_id,
        aggregate_type="invoice",
        aggregate_id=invoice_id,
        aggregate_version=1,
        data={"invoice_id": invoice_id, "number": number, "period": period},
    )


async def credit_note_issued(
    conn: AsyncConnection, *, tenant_id: str, credit_note_id: str, invoice_id: str
) -> str:
    return await emit(
        conn,
        name="commercial.credit_note.issued",
        tenant_id=tenant_id,
        aggregate_type="credit_note",
        aggregate_id=credit_note_id,
        aggregate_version=1,
        data={"invoice_id": invoice_id},
    )


async def payment_recorded(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    attempt_id: str,
    invoice_id: str,
    status: str,
    amount: str | None = None,
    currency: str | None = None,
) -> str:
    return await emit(
        conn,
        name="commercial.payment.recorded",
        tenant_id=tenant_id,
        aggregate_type="payment_attempt",
        aggregate_id=attempt_id,
        aggregate_version=1,
        data={"invoice_id": invoice_id, "status": status, "amount": amount, "currency": currency},
    )


# A reservation's events share one aggregate, so each has its own version: reserved 1, quota reached 2, then committed or released 3.
async def _usage_event(
    conn: AsyncConnection, name: str, version: int, reservation: dict[str, object], **extra: object
) -> str:
    return await emit(
        conn,
        name=name,
        tenant_id=str(reservation["tenant_id"]),
        aggregate_type="usage_reservation",
        aggregate_id=str(reservation["reservation_id"]),
        aggregate_version=version,
        data={
            "meter": reservation["meter"],
            "quantity": reservation["quantity"],
            "period": reservation["period"],
            "catalog_version": reservation["catalog_version"],
            **extra,
        },
    )


async def usage_reserved(conn: AsyncConnection, reservation: dict[str, object]) -> str:
    return await _usage_event(conn, "commercial.usage.reserved", 1, reservation)


async def usage_committed(conn: AsyncConnection, reservation: dict[str, object]) -> str:
    return await _usage_event(conn, "commercial.usage.committed", 3, reservation)


async def usage_released(conn: AsyncConnection, reservation: dict[str, object]) -> str:
    return await _usage_event(conn, "commercial.usage.released", 3, reservation)


async def quota_threshold_reached(
    conn: AsyncConnection, reservation: dict[str, object], *, limit: int
) -> str:
    return await _usage_event(
        conn, "commercial.quota.threshold_reached", 2, reservation, limit=limit
    )


async def entitlement_snapshot_published(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    revision: int,
    plan: str,
    band: str | None,
    subscription_state: str,
    catalog_version: str,
) -> str:
    return await emit(
        conn,
        name="commercial.entitlement.snapshot_published",
        tenant_id=tenant_id,
        aggregate_type="entitlement_snapshot",
        aggregate_id=tenant_id,
        aggregate_version=revision,
        data={
            "plan": plan,
            "band": band,
            "subscription_state": subscription_state,
            "catalog_version": catalog_version,
            "revision": revision,
        },
    )
