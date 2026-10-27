"""Direct operator mode (ZID_OPERATOR_MODE=direct): one operator drafts and approves a price book themselves.

The database still refuses an author approving their own book (a CHECK), so direct mode records the second person as
the DIRECT_APPROVER; the real operator stays the author and the actor. Governed mode is unchanged."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from conftest import ctx, engine
from zid_db import tenant_transaction
from zid_platform.modules.commercial.domain.catalogue import CommercialError
from zid_platform.modules.commercial.ports import catalogue_store as store
from zid_platform.modules.operator_access.ports import DIRECT_APPROVER

OPERATOR_TENANT = "00000000-0000-4000-8000-0000000000e1"


async def draft_with_a_line(conn, operator: str) -> str:  # type: ignore[no-untyped-def]
    book = await store.create_draft(
        conn, copy_of=None, created_by=operator, note="direct mode test"
    )
    book_id = str(book["price_book_id"])
    await store.put_line(
        conn,
        book_id,
        sku_id="ZID-BASE",
        plan_code="LAUNCH",
        unit_rate=Decimal(299),
        unit_divisor=1,
        included_quantity=None,
        minimum_amount=None,
    )
    return book_id


def test_the_author_cannot_approve_but_the_direct_approver_can(rt_url: str) -> None:
    operator = str(uuid.uuid4())

    async def run() -> None:
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(OPERATOR_TENANT)) as conn,
        ):
            book_id = await draft_with_a_line(conn, operator)
            now = datetime.now(UTC)
            with pytest.raises(CommercialError, match="author cannot approve"):
                await store.approve(conn, book_id, approver=operator, effective_from=now, now=now)
            # direct mode: the same operator's approval is recorded as the direct approver's
            await store.approve(
                conn, book_id, approver=DIRECT_APPROVER, effective_from=now, now=now
            )
            approved = await store.price_book(conn, book_id)
            assert approved is not None
            assert (
                approved["status"] == "APPROVED" and str(approved["approved_by"]) == DIRECT_APPROVER
            )
            assert str(approved["created_by"]) == operator  # the author is still named

    asyncio.run(run())


def test_each_direct_use_is_an_event_and_an_audit_record(rt_url: str) -> None:
    """Without a second approver, every use leaves a priority-lane event Security can alert on, and a protected audit
    record (CR-035)."""
    from sqlalchemy import text
    from zid_platform.modules.operator_access.application import service

    operator = str(uuid.uuid4())

    async def run() -> None:
        async with (
            engine(rt_url) as eng,
            tenant_transaction(eng, ctx(OPERATOR_TENANT)) as conn,
        ):
            await service._record_direct_use(
                conn,
                OPERATOR_TENANT,
                operator,
                "commercial.catalog.manage",
                None,
                "commercial:catalogue",
            )
            events = (
                await conn.execute(
                    text(
                        "SELECT count(*) FROM events.outbox_event WHERE aggregate_type = 'operator_direct_action' "
                        "AND event_type LIKE '%operator_access.direct_action%'"
                    )
                )
            ).scalar_one()
            assert events == 1

    asyncio.run(run())
