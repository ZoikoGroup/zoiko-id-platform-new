"""Recording billable events inside the operation that earns them (ZID-COM-001 §7; CR-025).

The insert happens on the caller's connection, in a savepoint, in the same transaction as the business
effect: if the operation rolls back no event exists, and if it commits exactly one does however often it is
retried. A failure to write is counted and logged and never raised: **billing accuracy is repaired after the
fact (reconciliation); identity availability is never traded for it.**
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Iterable
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.infrastructure import billable_store as store

log = logging.getLogger("zid.commercial.ledger")

# Process-level counters, exposed for the operations dashboard and the tests.
STATS: Counter[str] = Counter()

_environment = "local"
_region = "unknown"


def configure(*, environment: str, region: str) -> None:
    """Set once at start-up by the composition root: where and in which environment events are earned."""
    global _environment, _region
    _environment, _region = environment, region


async def record(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    meter: str,
    key: str,
    at: datetime | None = None,
    quantity: Decimal | int = 1,
    principal_class: str | None = None,
    source_request_id: str | None = None,
    recovered: bool = False,
    legal_payer_id: str | None = None,
    origin: str | None = None,
    partner_id: str | None = None,
    transaction_type: str | None = None,
    billable_result: str | None = None,
    finality: str | None = None,
    market_policy: str | None = None,
    supplier_rate_version: str | None = None,
) -> bool:
    """Record one chargeable operation. True when this call created the event, False when it was already
    recorded or could not be written (counted in `STATS`). Never raises."""
    at = at or datetime.now(UTC)
    month = b.window_month(at)
    event = b.BillableEvent(
        event_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        deduplication_key=key,
        meter_id=meter,
        quantity=Decimal(quantity),
        occurred_at=at,
        window_month=month,
        environment_id=_environment,
        region=_region,
        principal_class=principal_class,
        source_request_id=source_request_id,
        recovered=recovered,
        legal_payer_id=legal_payer_id,
        origin=origin,
        partner_id=partner_id,
        transaction_type=transaction_type,
        billable_result=billable_result,
        finality=finality,
        market_policy=market_policy,
        supplier_rate_version=supplier_rate_version,
    )
    try:
        event.validate()
        try:
            async with conn.begin_nested():
                created = await store.insert_event(conn, event)
        except DBAPIError as e:
            if "meter window frozen" not in str(e):
                raise
            # a late event for a window that is already closed lands in the next open window, flagged
            late = replace(event, window_month=b.next_month(month), late=True)
            async with conn.begin_nested():
                created = await store.insert_event(conn, late)
            STATS["late"] += 1
    except Exception:  # never block the operation that was being billed
        STATS["write_failed"] += 1
        log.exception("METERING_WRITE_FAILED meter=%s", meter)
        return False
    STATS["recorded" if created else "duplicate"] += 1
    return created


async def screening_usage_sink(
    conn: AsyncConnection,
    tenant_id: str,
    kind: str,
    case_id: str,
    version: int,
    at: datetime,
    human: bool,
) -> None:
    """Records what the screening module reports (CR-104): a CHECK is one SCREEN_CHECK per decision, a PERIOD is one SCREEN_MONITOR_PERIOD per case per UTC month.
    Usage facts only: nothing is rated or invoiced. Wired to the screening module by the composition root."""
    if kind == "CHECK":
        await record(
            conn, tenant_id=tenant_id, meter="SCREEN_CHECK", key=b.key_screen_check(case_id, version), at=at,
            principal_class="HUMAN" if human else "NON_HUMAN",
        )  # fmt: skip
    elif kind == "PERIOD":
        await record(
            conn, tenant_id=tenant_id, meter="SCREEN_MONITOR_PERIOD", key=b.key_screen_period(case_id, b.window_month(at)), at=at,
            principal_class="NON_HUMAN",
        )  # fmt: skip


async def credential_usage_sink(
    conn: AsyncConnection,
    tenant_id: str,
    kind: str,
    check_id: str,
    at: datetime,
    human: bool,
) -> None:
    """Records what the credential module reports (CR-070): a CHECK is one CREDENTIAL_CHECK per completed claim check, a SENSITIVE_CHECK one
    CREDENTIAL_SENSITIVE_CHECK. Usage facts only: nothing is rated or invoiced, and a check never depends on this. Wired by the composition root."""
    meter, key = (
        ("CREDENTIAL_SENSITIVE_CHECK", b.key_credential_sensitive_check(check_id))
        if kind == "SENSITIVE_CHECK"
        else ("CREDENTIAL_CHECK", b.key_credential_check(check_id))
    )
    await record(
        conn,
        tenant_id=tenant_id,
        meter=meter,
        key=key,
        at=at,
        principal_class="HUMAN" if human else "NON_HUMAN",
    )


async def idv_usage_sink(
    conn: AsyncConnection, tenant_id: str, session_ref: str, at: datetime
) -> None:
    """Records what the verification module reports (ZID-CP-WF01 Q33): one IDV_SUBMISSION per hosted session the person submitted. A usage fact only: nothing is rated
    or invoiced, and the submission never depends on this. Wired by the composition root."""
    await record(
        conn,
        tenant_id=tenant_id,
        meter="IDV_SUBMISSION",
        key=b.key_idv_submission(session_ref),
        at=at,
        principal_class="HUMAN",
        transaction_type="SUBMITTED",
    )


async def record_active_human(
    conn: AsyncConnection, *, tenant_id: str, principal_id: str, at: datetime | None = None
) -> bool:
    """The first qualifying activity of a human principal in a UTC month counts once as MAH."""
    at = at or datetime.now(UTC)
    month = b.window_month(at)
    try:
        async with conn.begin_nested():
            first = await store.mark_mah(conn, tenant_id, month, principal_id)
    except Exception:
        STATS["write_failed"] += 1
        log.exception("METERING_WRITE_FAILED meter=MAH")
        return False
    if not first:
        return False
    return await record(
        conn,
        tenant_id=tenant_id,
        meter="MAH",
        key=b.key_mah(month, principal_id),
        at=at,
        principal_class="HUMAN",
    )


async def record_identity_merge(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    from_principal_id: str,
    into_principal_id: str,
    at: datetime | None = None,
) -> list[date]:
    """A person's identities were merged: whoever merges principals calls this so they count once as MAH.

    From now on activity of the merged-away principal counts as the survivor's, so no new duplicate can
    arise. If both were already counted in a month before the merge was recorded, that month's duplicate is
    *not* silently removed (events are insert-only): the months are returned so the caller can request an
    approved usage adjustment for them. Merging a principal twice, into itself, or in a loop is refused."""
    if from_principal_id == into_principal_id:
        raise b.BillableError("MERGE_INVALID", "a principal cannot be merged into itself")
    survivor = await store.canonical_principal(conn, tenant_id, into_principal_id)
    if survivor == from_principal_id:
        raise b.BillableError("MERGE_INVALID", "this merge would create a loop")
    if not await store.insert_merge(
        conn, tenant_id, from_principal_id, survivor, at or datetime.now(UTC)
    ):
        raise b.BillableError("MERGE_INVALID", "this principal was already merged")
    return await store.double_counted_months(conn, tenant_id, from_principal_id, survivor)


async def reconcile(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    meter: str,
    sources: Iterable[tuple[str, datetime, str | None]],
) -> int:
    """Insert the events a source says happened but the ledger does not have, marked `recovered`.

    `sources` are (deduplication key, occurred at, principal class) taken from the authoritative rows
    (sessions, credentials). An event is never invented without a source row, and an existing one is
    never touched. Returns how many were recovered."""
    n = 0
    for key, at, pclass in sources:
        if await record(
            conn,
            tenant_id=tenant_id,
            meter=meter,
            key=key,
            at=at,
            principal_class=pclass,
            recovered=True,
        ):
            n += 1
    return n


async def freeze_month(conn: AsyncConnection, tenant_id: str, month: date) -> list[dict[str, Any]]:
    """Close every meter of one tenant for one past UTC month. Idempotent per meter."""
    if month >= b.window_month(datetime.now(UTC)):
        raise b.BillableError("WINDOW_NOT_CLOSED", "only a finished month can be frozen")
    done = await store.frozen_windows(conn, month)
    out = []
    for meter in b.METERS:
        if meter in done:
            continue
        out.append(await store.freeze(conn, tenant_id, month, meter))
    return out


async def ledger_summary(conn: AsyncConnection, month: date) -> dict[str, Any]:
    events = await store.event_totals(conn, month)
    adj = await store.adjustment_totals(conn, month)
    net = b.net_quantities({m: q for m, (_, q) in events.items()}, adj)
    frozen = {w["meter_id"]: w for w in await store.windows(conn, month)}
    return {
        "month": month.isoformat(),
        "meters": [
            {
                "meter": m,
                "events": events.get(m, (0, Decimal(0)))[0],
                "quantity": format(events.get(m, (0, Decimal(0)))[1].normalize(), "f"),
                "adjustments": format(adj.get(m, Decimal(0)).normalize(), "f"),
                "net": format(net.get(m, Decimal(0)).normalize(), "f"),
                "frozen": m in frozen,
                "hash_total": frozen[m]["hash_total"] if m in frozen else None,
            }
            for m in b.METERS
        ],
    }
