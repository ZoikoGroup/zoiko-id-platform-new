"""An issued invoice records the merchant route and matrix version it was issued under (ZID-COM-WF-2026-11 INV-04): only when the active matrix is unambiguous, never a guess,
immutable with the invoice, and the invoice document is unchanged."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_finance_authority_pg import ENTITY, billed
from test_market_matrix_pg import full_row, signed
from test_purchase_order_pg import issue, price_book, rated_draft
from zid_db import tenant_transaction
from zid_platform.modules.commercial.ports import market_matrix_store as matrices


def uid() -> str:
    return str(uuid.uuid4())


async def activate(eng, rows) -> int:
    author, activator = uid(), uid()
    async with eng.begin() as c:
        mid = (
            await matrices.create_draft(c, copy_of=None, created_by=author, note="invoice route")
        )["matrix_id"]
        for r in rows:
            await matrices.put_row(c, mid, r)
        await signed(c, mid, author)
        now = datetime.now(UTC) - timedelta(seconds=5)
        await matrices.activate(c, mid, activator=activator, effective_from=now, now=now)
        return int((await matrices.matrix(c, mid))["version"])  # type: ignore[index]


async def invoice_for(eng, book: str, tenant: str) -> dict:
    async with tenant_transaction(eng, ctx(tenant)) as c:
        await billed(c, tenant)  # a direct account in GB, invoiced in USD
        return await issue(c, await rated_draft(eng, tenant, book, c))


def test_the_merchant_route_is_pinned_only_when_the_active_matrix_is_unambiguous(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        t1, t2, t3 = uid(), uid(), uid()
        async with engine(rt_url) as eng:
            book = await price_book(eng)
            gb = replace(
                full_row("API", "GB"),
                seller_entity=ENTITY,
                currency="USD",
                merchant_route="stripe-uk",
            )

            # no matrix names this seller: the invoice records no route, and nothing is guessed
            first = await invoice_for(eng, book, t1)
            assert first["status"] == "ISSUED" and first["merchant_route"] is None

            v1 = await activate(eng, [gb])
            second = await invoice_for(eng, book, t2)
            assert second["merchant_route"] == "stripe-uk" and second["market_matrix_version"] == v1

            # two routes for the same seller, country and currency: ambiguous, so none is recorded (the matrix version still is)
            v2 = await activate(eng, [gb, replace(gb, family="DOC", merchant_route="adyen-uk")])
            third = await invoice_for(eng, book, t3)
            assert third["merchant_route"] is None and third["market_matrix_version"] == v2

        # issued with the invoice, then immutable with it; a hash chain and document were not touched by the new columns
        async with engine(owner_url) as eng:
            with pytest.raises(DBAPIError, match="immutable"):
                async with eng.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE commercial.invoice SET merchant_route = 'changed' WHERE status = 'ISSUED'"
                        )
                    )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t2)) as c:
            row = (
                await c.execute(
                    text(
                        "SELECT document_digest, chain_hash, merchant_route FROM commercial.invoice"
                    )
                )
            ).one()
            assert len(row[0]) == 64 and row[1] and row[2] == "stripe-uk"

    asyncio.run(run())
