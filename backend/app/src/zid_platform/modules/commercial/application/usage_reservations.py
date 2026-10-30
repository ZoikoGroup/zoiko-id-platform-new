"""Reserve, commit and release a meter's quantity (ZID-ENG-DEC-008 section 05; slice L-8).

Idempotent by key: a retry, a provider callback or a concurrent request with the same key is the same reservation and consumes
nothing twice; the same key with other terms is refused. Reservations of one meter in one period are serialized under an advisory
lock, so two concurrent requests cannot both fit under a cap.

A meter has a cap only where the register says `hard_quota` and a limit can be read (today only `auth.mau`, from the plan's
included active humans). A hard-quota meter with no readable limit is a HOLD for a manual commercial decision, never a silent
charge and never an unlimited allowance. Crossing a cap returns `QUOTA_REACHED` with the limit, the use and the reset date and
refuses only that operation. Nothing here writes an invoice line or invents a price: it is the quota ledger.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ErrorDetail, ZidError

from zid_platform.modules.commercial.application import events, recorder
from zid_platform.modules.commercial.domain.catalogue import DEFAULT_PLAN
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure import reservation_store as store
from zid_platform.modules.commercial.infrastructure.bands import (
    MeterDef,
    band_matrix,
    meter_register,
)

PLAN_LIMIT_COLUMN = {
    "auth.mau": "mah_included"
}  # the meters whose included quantity the Estate's plan carries


def period_of(now: datetime) -> str:
    return now.strftime("%Y-%m")


def reset_at(now: datetime) -> datetime:
    year, month = (now.year + 1, 1) if now.month == 12 else (now.year, now.month + 1)
    return datetime(year, month, 1, tzinfo=UTC)


def _meter(register: Mapping[str, MeterDef], meter: str) -> MeterDef:
    m = register.get(meter)
    if m is None:
        raise ZidError("METER_UNKNOWN", "No such meter.", http_status=422)
    return m


async def limit_for(conn: AsyncConnection, m: MeterDef) -> int | None | str:
    """The cap, None for no cap, or "UNDEFINED" when the register demands a cap and none can be read."""
    if not m.hard_quota:
        return None
    column = PLAN_LIMIT_COLUMN.get(m.key)
    if column is None:
        return "UNDEFINED"
    current = await catalogue_store.active_entitlement(conn)
    plan = await catalogue_store.plan(conn, str(current["plan_code"]) if current else DEFAULT_PLAN)
    if plan is None:
        return "UNDEFINED"
    value = plan.get(column)
    return None if value is None else int(value)  # a NULL included quantity is negotiated, not zero


async def reserve(
    conn: AsyncConnection,
    tenant_id: str,
    meter: str,
    quantity: int,
    key: str,
    *,
    now: datetime | None = None,
    register: Mapping[str, MeterDef] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    m = _meter(register or meter_register(), meter)
    period = period_of(now)
    await store.lock(conn, tenant_id, meter, period)
    existing = await store.by_key(conn, key)
    if existing is not None:
        if existing["meter"] != meter or existing["quantity"] != quantity:
            raise ZidError(
                "IDEMPOTENCY_CONFLICT",
                "This key was used for a different reservation.",
                http_status=409,
            )
        return {**existing, "replayed": True}
    limit = await limit_for(conn, m)
    committed, reserved = await store.totals(conn, meter, period)
    if limit == "UNDEFINED":
        raise ZidError(
            "QUOTA_UNDEFINED",
            "This meter has a quota but no limit is defined; a manual commercial decision is needed.",
            http_status=503,
            details=[ErrorDetail(field=meter, issue="MANUAL_COMMERCIAL_DECISION")],
        )
    if isinstance(limit, int) and committed + reserved + quantity > limit:
        raise ZidError(
            "QUOTA_REACHED",
            "The quota for this meter is reached; only this operation is refused.",
            http_status=429,
            details=[
                ErrorDetail(field="limit", issue=str(limit)),
                ErrorDetail(field="used", issue=str(committed)),
                ErrorDetail(field="reserved", issue=str(reserved)),
                ErrorDetail(field="resets_at", issue=reset_at(now).isoformat()),
            ],
        )
    row = await store.insert(
        conn,
        tenant_id=tenant_id,
        meter=meter,
        quantity=quantity,
        key=key,
        period=period,
        catalog_version=band_matrix().version,
        now=now,
    )
    await events.usage_reserved(conn, row)
    remaining = None if not isinstance(limit, int) else limit - committed - reserved - quantity
    if remaining is not None and isinstance(limit, int) and remaining <= 0:
        # the cap is reached by this reservation (not crossed: that is refused above)
        await events.quota_threshold_reached(conn, row, limit=limit)
    return {
        **row,
        "replayed": False,
        "limit": limit if isinstance(limit, int) else None,
        "remaining": remaining,
    }


async def _resolve(
    conn: AsyncConnection,
    reservation_id: str,
    to: str,
    outcome: str | None,
    now: datetime,
    register: Mapping[str, MeterDef] | None,
) -> dict[str, Any]:
    current = await store.by_id(conn, reservation_id)
    if current is None:
        raise ZidError("RESERVATION_NOT_FOUND", "No such reservation.", http_status=404)
    await store.lock(conn, current_tenant(current), current["meter"], current["period"])
    done = await store.resolve(conn, reservation_id, to, outcome, now)
    if done is not None:
        if to == "COMMITTED":
            await events.usage_committed(conn, done)
            await _bill(conn, done, now, register or meter_register())
        else:
            await events.usage_released(conn, done)
        return {**done, "replayed": False}
    again = await store.by_id(conn, reservation_id)
    assert again is not None
    if again["state"] == to:  # the same resolution again is the same answer
        return {**again, "replayed": True}
    raise ZidError(
        "RESERVATION_ALREADY_RESOLVED",
        f"This reservation is already {again['state']}.",
        http_status=409,
    )


async def _bill(
    conn: AsyncConnection, done: Mapping[str, Any], now: datetime, register: Mapping[str, MeterDef]
) -> None:
    """Only when the owners have ruled that this API is the recording point for the meter (`bill_on_commit`): one billable event,
    keyed by the reservation, so a retried commit and a replayed callback record nothing twice. A released reservation never reaches
    here, so a failed, duplicate or incomplete operation is never charged."""
    m = register.get(str(done["meter"]))
    if m is None or not m.bill_on_commit or not m.existing_meter:
        return
    await recorder.record(
        conn,
        tenant_id=str(done["tenant_id"]),
        meter=m.existing_meter,
        key=f"reservation:{done['reservation_id']}",
        at=now,
        quantity=int(done["quantity"]),
    )


def current_tenant(row: Mapping[str, Any]) -> str:
    return str(row.get("tenant_id", ""))


async def commit(
    conn: AsyncConnection,
    reservation_id: str,
    *,
    now: datetime | None = None,
    outcome: str | None = None,
    register: Mapping[str, MeterDef] | None = None,
) -> dict[str, Any]:
    return await _resolve(
        conn, reservation_id, "COMMITTED", outcome, now or datetime.now(UTC), register
    )


async def release(
    conn: AsyncConnection,
    reservation_id: str,
    *,
    now: datetime | None = None,
    outcome: str | None = None,
) -> dict[str, Any]:
    """An operation that failed, was a duplicate or never completed gives its quantity back. A committed use is not released."""
    return await _resolve(conn, reservation_id, "RELEASED", outcome, now or datetime.now(UTC), None)


async def summary(
    conn: AsyncConnection,
    *,
    now: datetime | None = None,
    register: Mapping[str, MeterDef] | None = None,
) -> dict[str, Any]:
    now = now or datetime.now(UTC)
    reg = register or meter_register()
    period = period_of(now)
    used = await store.per_meter(conn, period)
    meters = []
    for key, m in reg.items():
        u = used.get(key, {"committed": 0, "reserved": 0, "released": 0})
        limit = await limit_for(conn, m)
        capped = isinstance(limit, int)
        meters.append(
            {
                "meter": key,
                "counts": m.counts,
                "committed": u["committed"],
                "reserved": u["reserved"],
                "released": u["released"],
                "limit": limit if capped else None,
                "remaining": (limit - u["committed"] - u["reserved"])
                if isinstance(limit, int)
                else None,
                "quota_defined": limit != "UNDEFINED",
                "priced": m.priced,
            }
        )
    return {
        "period": period,
        "resets_at": reset_at(now).isoformat(),
        "meters": meters,
        "treatment": {
            "incomplete": "A reservation released before it completes gives its quantity back and is not charged.",
            "duplicate": "A retry with the same key is the same reservation and is counted once.",
            "unpriced": "A meter without an approved price is recorded here and never billed silently.",
        },
    }
