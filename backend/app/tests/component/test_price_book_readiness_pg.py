"""A price book says what it still lacks before an approver approves it."""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal

from conftest import engine
from zid_platform.modules.commercial.ports import catalogue_store as store


def test_an_empty_draft_lists_every_unpriced_pair_and_a_complete_one_is_ready(rt_url: str) -> None:
    async def run() -> tuple[bool, int, bool, int]:
        async with engine(rt_url) as eng, eng.begin() as c:
            draft = await store.create_draft(
                c, copy_of=None, created_by=str(uuid.uuid4()), note="readiness"
            )
            did = draft["price_book_id"]
            empty = await store.readiness(c, did)
            for pair in empty["missing"]:
                await store.put_line(
                    c,
                    did,
                    sku_id=pair["sku"],
                    plan_code=pair["plan"],
                    unit_rate=Decimal(0),
                    unit_divisor=1,
                    included_quantity=None,
                    minimum_amount=None,
                )
            done = await store.readiness(c, did)
            return empty["ready"], len(empty["missing"]), done["ready"], len(done["free"])

    empty_ready, missing, done_ready, free = asyncio.run(run())
    assert empty_ready is False and missing > 0, "a book with no lines is never ready"
    assert done_ready is True
    assert free == missing, (
        "lines priced at zero are listed as free so an approver sees they were chosen"
    )
