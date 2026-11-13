"""Public pricing page acceptance row P23 (ZID-UX-PRICE-004; CR-088): a screening check and a monitoring period are two separate usage meters.

A screening decision is one SCREEN_CHECK, recorded once per decision. A case under continuous monitoring is one SCREEN_MONITOR_PERIOD per UTC month, however many rescreens run in it. Both are
usage facts only: nothing is rated or invoiced because no SKU or price stands behind them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta

from conftest import ctx, engine
from sqlalchemy import text
from test_screening_pg import CFG, GRAPH, NOW, OFFICER, load_lists, sign, subjects
from zid_db import tenant_transaction
from zid_platform.modules.commercial.ports import screening_usage_sink
from zid_platform.modules.screening.application import screening as service


def uid() -> str:
    return str(uuid.uuid4())


async def counts(c, tenant: str) -> dict[str, int]:
    rows = (
        await c.execute(
            text(
                "SELECT meter_id, count(*) AS n FROM commercial.billable_event WHERE tenant_id = :t AND meter_id LIKE 'SCREEN_%' GROUP BY meter_id"
            ),
            {"t": uuid.UUID(tenant)},
        )
    ).all()
    return {r.meter_id: int(r.n) for r in rows}


def test_a_check_and_a_monitoring_period_are_two_meters_each_counted_once_and_never_rated(
    rt_url: str,
) -> None:
    service.configure_usage_sink(screening_usage_sink)  # what the composition root does at startup
    tenant, requester = uid(), uid()
    key = f"scr-{uid()[:12]}"

    async def run() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                v = await service.open_case(
                    c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                    requested_by=requester, idempotency_key=key, cfg=CFG, sign=sign, now=NOW,
                )  # fmt: skip
                assert await counts(c, tenant) == {"SCREEN_CHECK": 1}, (
                    "opening a case is one check and no monitoring period"
                )
                # the same key is the same case: no second check
                again = await service.open_case(
                    c, tenant, country="GB", subjects=subjects(), graph=GRAPH, authority=OFFICER,
                    requested_by=requester, idempotency_key=key, cfg=CFG, sign=sign, now=NOW,
                )  # fmt: skip
                assert again.case_id == v.case_id and await counts(c, tenant) == {"SCREEN_CHECK": 1}

                await service.rescreen(
                    c, tenant, v.case_id, cfg=CFG, sign=sign, now=NOW + timedelta(days=1)
                )
                assert await counts(c, tenant) == {"SCREEN_CHECK": 2, "SCREEN_MONITOR_PERIOD": 1}
                # another rescreen in the same UTC month is another check but the same monitoring period
                await service.rescreen(
                    c, tenant, v.case_id, cfg=CFG, sign=sign, now=NOW + timedelta(days=2)
                )
                assert await counts(c, tenant) == {"SCREEN_CHECK": 3, "SCREEN_MONITOR_PERIOD": 1}
                # the next month is the next period
                await service.rescreen(
                    c, tenant, v.case_id, cfg=CFG, sign=sign, now=NOW + timedelta(days=40)
                )
                assert await counts(c, tenant) == {"SCREEN_CHECK": 4, "SCREEN_MONITOR_PERIOD": 2}

            async with tenant_transaction(eng, ctx(uid())) as c:
                assert await counts(c, tenant) == {}, "another tenant sees none of it"

    try:
        asyncio.run(run())
    finally:
        service.configure_usage_sink(None)
