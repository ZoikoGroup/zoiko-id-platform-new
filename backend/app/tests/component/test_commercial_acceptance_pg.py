"""ZID-COM-001 §31 acceptance rows with no earlier home: COM-02 identity merge, COM-03 cross-region usage.

COM-02: a person whose identities are merged counts once as a monthly active human, before or after the merge,
and a duplicate that already happened is reported for an approved adjustment, never silently erased.
COM-03: where an event was earned never changes what it costs (the tenant's entitlement and price book govern),
and the ledger holds no raw identity attributes.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import budget_service, recorder
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.infrastructure import catalogue_store

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
NOW = datetime.now(UTC)
MONTH = b.window_month(NOW)
M_START = datetime(MONTH.year, MONTH.month, 1, tzinfo=UTC)
AT = M_START + timedelta(days=10)


def uid() -> str:
    return str(uuid.uuid4())


async def mah_total(conn: Any) -> int:
    sql = "SELECT coalesce(sum(quantity), 0) FROM commercial.billable_event WHERE meter_id = 'MAH'"
    return int((await conn.execute(text(sql))).scalar_one())


def test_com02_a_merged_person_counts_once_and_a_past_duplicate_is_reported(
    rt_url: str, owner_url: str
) -> None:
    tenant, other = uid(), uid()
    a, b_, c, lone = uid(), uid(), uid(), uid()

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as conn:
                # merged before anyone is counted: the merged-away identity counts as the survivor
                merged = await recorder.record_identity_merge(
                    conn, tenant_id=tenant, from_principal_id=a, into_principal_id=b_
                )
                assert merged == []
                assert await recorder.record_active_human(
                    conn, tenant_id=tenant, principal_id=a, at=AT
                )
                assert not await recorder.record_active_human(
                    conn, tenant_id=tenant, principal_id=b_, at=AT
                )  # same person, already counted
                assert await mah_total(conn) == 1

                # the survivor is merged again (A -> B -> C): still one person, no new count
                await recorder.record_identity_merge(
                    conn, tenant_id=tenant, from_principal_id=b_, into_principal_id=c
                )
                for who in (a, b_, c):
                    assert not await recorder.record_active_human(
                        conn, tenant_id=tenant, principal_id=who, at=AT
                    )
                assert await mah_total(conn) == 1

                # a different person is still counted
                assert await recorder.record_active_human(
                    conn, tenant_id=tenant, principal_id=lone, at=AT
                )
                assert await mah_total(conn) == 2

                # refused merges: into itself, the same principal twice, and a loop
                for kw in (
                    {"from_principal_id": lone, "into_principal_id": lone},
                    {"from_principal_id": a, "into_principal_id": lone},
                    {"from_principal_id": c, "into_principal_id": a},
                ):
                    with pytest.raises(b.BillableError) as e:
                        await recorder.record_identity_merge(conn, tenant_id=tenant, **kw)
                    assert e.value.code == "MERGE_INVALID"

            # both identities already counted before the merge is known: reported, never erased
            async with tenant_transaction(eng, ctx(other)) as conn:
                x, y = uid(), uid()
                assert await recorder.record_active_human(
                    conn, tenant_id=other, principal_id=x, at=AT
                )
                assert await recorder.record_active_human(
                    conn, tenant_id=other, principal_id=y, at=AT
                )
                months = await recorder.record_identity_merge(
                    conn, tenant_id=other, from_principal_id=x, into_principal_id=y
                )
                assert months == [MONTH]  # the caller requests an approved usage adjustment for it
                assert await mah_total(conn) == 2  # insert-only: the original events stay

            # the merge map is tenant-scoped
            async with tenant_transaction(eng, ctx(tenant)) as conn:
                sql = "SELECT count(*) FROM commercial.mah_merge WHERE tenant_id = :t"
                assert (await conn.execute(text(sql), {"t": uuid.UUID(other)})).scalar_one() == 0

    asyncio.run(run())


def test_com03_region_never_changes_the_price_and_the_ledger_holds_no_identity(
    rt_url: str, owner_url: str
) -> None:
    uk, eu = uid(), uid()

    async def earn(eng: Any, tenant: str, region: str, book: str) -> None:
        recorder.configure(environment="test", region=region)
        async with tenant_transaction(eng, ctx(tenant)) as c:
            await catalogue_store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="BUSINESS",
                price_book_id=book,
                actor=None,
                now=M_START - timedelta(days=35),
            )
            # 10,000 over the 50,000 that Business includes
            await recorder.record(
                c,
                tenant_id=tenant,
                meter="MAH",
                key=f"mah:{tenant}",
                at=M_START + timedelta(days=2),
                quantity=60_000,
            )

    async def regions(c: Any) -> set[str]:
        rows = await c.execute(text("SELECT DISTINCT region FROM commercial.billable_event"))
        return {r[0] for r in rows}

    async def run() -> None:
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                draft = await catalogue_store.create_draft(
                    c, copy_of=SEED_BOOK, created_by=uid(), note="com03"
                )
                await catalogue_store.approve(
                    c,
                    draft["price_book_id"],
                    approver=uid(),
                    effective_from=M_START - timedelta(days=40),
                    now=datetime.now(UTC),
                )
            try:
                await earn(eng, uk, "uk-1", draft["price_book_id"])
                await earn(eng, eu, "eu-1", draft["price_book_id"])
            finally:
                recorder.configure(environment="local", region="unknown")

            bills = []
            for tenant, region in ((uk, "uk-1"), (eu, "eu-1")):
                async with tenant_transaction(eng, ctx(tenant)) as c:
                    preview = await budget_service.preview(c, connections={}, now=AT)
                    bills.append(preview["bill_to_date"])
                    assert await regions(c) == {region}  # each event records where it was earned
            assert (
                bills[0] == bills[1]
            )  # same plan and price book: the region does not change the bill
            assert Decimal(bills[0]) == Decimal(1499) + Decimal(10_000) * Decimal("0.015")

            # the ledger has no column that could hold a raw identity attribute
            async with eng.begin() as c:
                sql = (
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'commercial' AND table_name = 'billable_event'"
                )
                cols = {r[0] for r in await c.execute(text(sql))}
            assert "region" in cols
            assert not cols & {"principal_id", "email", "name", "subject", "claims", "phone"}

    asyncio.run(run())
