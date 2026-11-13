"""Purchase orders on Postgres (ZID-COM-WF-2026-11 CW-11): an invoice for a company with POs on record is issued only against one that fits, the draw is exact and
append-only, and a company with none is untouched."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_finance_authority_pg import ENTITY, L_START, LAST, SEED_BOOK
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import rating_service, recorder
from zid_platform.modules.commercial.application.adapters import NoTaxConfigured
from zid_platform.modules.commercial.domain.invoicing import InvoicingError
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.ports import purchase_order_store as pos


def uid() -> str:
    return str(uuid.uuid4())


async def price_book(eng) -> str:
    async with eng.begin() as c:
        draft = await catalogue_store.create_draft(
            c, copy_of=SEED_BOOK, created_by=uid(), note="po"
        )
        await catalogue_store.approve(
            c,
            draft["price_book_id"],
            approver=uid(),
            effective_from=L_START - timedelta(days=1),
            now=datetime.now(UTC),
        )
    return str(draft["price_book_id"])


async def rated_draft(eng, tenant: str, book: str, c) -> str:
    await catalogue_store.set_entitlement(
        c,
        tenant_id=tenant,
        plan_code="BUSINESS",
        price_book_id=book,
        actor=None,
        now=L_START - timedelta(days=3),
    )
    await recorder.record(
        c,
        tenant_id=tenant,
        meter="MAH",
        key=f"mah:{tenant}",
        at=L_START + timedelta(days=4),
        quantity=68_000,
    )
    await recorder.freeze_month(c, tenant, LAST)
    await rating_service.run(c, tenant_id=tenant, month=LAST, connections={"saml_sso": 1})
    d = await inv.create_draft(c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=uid())
    return str(d["invoice_id"])


async def issue(c, invoice_id: str):
    return await inv.issue(
        c,
        invoice_id,
        issuer=uid(),
        tax=NoTaxConfigured(),
        payment_terms_days=30,
        allow_zero_tax=True,
        region="uk-1",
    )


def test_an_invoice_is_issued_only_against_a_purchase_order_that_fits(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        buyer, plain = uid(), uid()
        today = date.today()
        async with engine(rt_url) as eng:
            book = await price_book(eng)

            # a company with no PO on record is untouched
            async with tenant_transaction(eng, ctx(plain)) as c:
                out = await issue(c, await rated_draft(eng, plain, book, c))
                assert out["status"] == "ISSUED"
                assert await pos.for_tenant(c, plain) == []

            async with tenant_transaction(eng, ctx(buyer)) as c:
                draft = await rated_draft(eng, buyer, book, c)
                gross = Decimal(
                    (
                        await c.execute(
                            text("SELECT subtotal FROM commercial.invoice WHERE invoice_id = :i"),
                            {"i": uuid.UUID(draft)},
                        )
                    ).scalar_one()
                )

                # the company has a PO, but it has lapsed: not issued, and nothing is drawn
                await pos.record(
                    c,
                    tenant_id=buyer,
                    po_number="PO-OLD",
                    currency="USD",
                    value="100000",
                    valid_from=today - timedelta(days=90),
                    valid_until=today - timedelta(days=1),
                    created_by=uid(),
                )
                with pytest.raises(InvoicingError) as e:
                    async with c.begin_nested():
                        await issue(c, draft)
                assert e.value.code == "PO_REQUIRED"

                # an open PO in the right currency with too little left: named as insufficient
                await pos.record(
                    c,
                    tenant_id=buyer,
                    po_number="PO-SMALL",
                    currency="USD",
                    value="1",
                    valid_from=today - timedelta(days=1),
                    valid_until=today + timedelta(days=60),
                    created_by=uid(),
                )
                with pytest.raises(InvoicingError) as e:
                    async with c.begin_nested():
                        await issue(c, draft)
                assert e.value.code == "PO_INSUFFICIENT"
                assert (
                    await c.execute(text("SELECT count(*) FROM commercial.purchase_order_draw"))
                ).scalar_one() == 0

                # a PO that fits: issued, and exactly the gross is drawn, against the PO that expires first among those that fit
                await pos.record(
                    c,
                    tenant_id=buyer,
                    po_number="PO-FIT",
                    currency="USD",
                    value="1000000",
                    valid_from=today - timedelta(days=1),
                    valid_until=today + timedelta(days=200),
                    created_by=uid(),
                )
                out = await issue(c, draft)
                assert out["status"] == "ISSUED"
                rows = (
                    await c.execute(
                        text(
                            "SELECT p.po_number, d.amount FROM commercial.purchase_order_draw d JOIN commercial.purchase_order p USING (po_id)"
                        )
                    )
                ).all()
                assert [(r[0], Decimal(r[1])) for r in rows] == [("PO-FIT", Decimal(out["gross"]))]
                assert Decimal(out["gross"]) >= gross
                mine = {r["po_number"]: r for r in await pos.for_tenant(c, buyer)}
                assert (
                    Decimal(mine["PO-FIT"]["drawn"]) == Decimal(out["gross"])
                    and Decimal(mine["PO-SMALL"]["drawn"]) == 0
                )

        # POs and draws are never edited or deleted, and a PO changes only by being closed
        async with engine(owner_url) as eng:
            for sql in (
                "DELETE FROM commercial.purchase_order",
                "UPDATE commercial.purchase_order SET value = 1",
                "UPDATE commercial.purchase_order_draw SET amount = 1",
                "DELETE FROM commercial.purchase_order_draw",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())


def test_a_po_cannot_be_drawn_beyond_its_value_even_if_the_code_is_bypassed(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        t = uid()
        today = date.today()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(t)) as c:
            po = await pos.record(
                c,
                tenant_id=t,
                po_number="PO-1",
                currency="EUR",
                value="100",
                valid_from=today,
                valid_until=today + timedelta(days=5),
                created_by=uid(),
            )
            await c.execute(
                text(
                    "INSERT INTO commercial.purchase_order_draw (invoice_id, po_id, tenant_id, amount) VALUES (:i, :p, :t, 60)"
                ),
                {"i": uuid.uuid4(), "p": uuid.UUID(po), "t": uuid.UUID(t)},
            )
            with pytest.raises(DBAPIError, match="beyond its value"):
                async with c.begin_nested():
                    await c.execute(
                        text(
                            "INSERT INTO commercial.purchase_order_draw (invoice_id, po_id, tenant_id, amount) VALUES (:i, :p, :t, 41)"
                        ),
                        {"i": uuid.uuid4(), "p": uuid.UUID(po), "t": uuid.UUID(t)},
                    )
            await pos.close(c, po)
            with pytest.raises(Exception) as e:
                await pos.close(c, po)
            assert getattr(e.value, "code", "") == "PO_NOT_OPEN"
            # a company sees only its own POs
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
            assert (
                await c.execute(text("SELECT count(*) FROM commercial.purchase_order"))
            ).scalar_one() == 0

    asyncio.run(run())
