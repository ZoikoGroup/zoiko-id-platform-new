"""Acceptance tests of ZID-COM-WF-2026-11 section 15 that had no test, on Postgres: CAT-26 (a pilot expires without converting), CAT-30 (another payer's invoice is not
reachable by its id) and CAT-40 (a rollout, rollback or new matrix leaves contracts, meters and invoices as they were).

Engineering evidence, not independent execution: gate G5 still needs Independent QA to run them.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_finance_authority_pg import NOW, cast, issued_invoice, pilot, sign
from test_market_matrix_pg import full_row, go_live, signed
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import recorder
from zid_platform.modules.commercial.domain.invoicing import InvoicingError
from zid_platform.modules.commercial.ports import market_matrix_store as matrices
from zid_platform.modules.commercial.ports import rollout_store


def uid() -> str:
    return str(uuid.uuid4())


def test_cat26_a_pilot_expires_without_a_conversion_or_a_hidden_charge(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        tenant = uid()
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                req = await pilot(c, p["REVOPS"], tenant, "500.00", days=30)
                await sign(c, req, "CCO", p["CCO"])
                await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"])
                ran = await finance.execute(
                    c,
                    req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="pilot-run-1",
                    environment="test",
                )
                assert (
                    ran["state"] == "APPLIED"
                    and ran["result"]["classification"] == "PILOT_NON_BILLABLE"
                )

        async def footprint() -> dict[str, int]:
            async with engine(owner_url) as eng, eng.begin() as c:
                out = {}
                for table in (
                    "sales_order",
                    "activation_payment",
                    "invoice",
                    "rating_run",
                    "rated_usage",
                    "entitlement",
                    "billable_event",
                ):
                    out[table] = (
                        await c.execute(
                            text(
                                f"SELECT count(*) FROM commercial.{table} WHERE tenant_id = :t"
                                if table != "sales_order"
                                else "SELECT count(*) FROM commercial.sales_order WHERE tenant_id = :t"
                            ),
                            {"t": uuid.UUID(tenant)},
                        )
                    ).scalar_one()
                return out

        before = await footprint()
        # the pilot is a recorded, non-billable grant with an end date; it has no purchase, plan change or charge attached
        async with engine(owner_url) as eng, eng.begin() as c:
            grant = (
                await c.execute(
                    text(
                        "SELECT ends_at, starts_at FROM commercial.finance_pilot_credit WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).one()
            assert grant[0] - grant[1] <= timedelta(days=90), (
                "a pilot is finite (the proposed maximum is 90 days)"
            )
            # the pilot cannot be extended or edited in place, and never deleted
            for sql in (
                "UPDATE commercial.finance_pilot_credit SET ends_at = ends_at + interval '365 days'",
                "DELETE FROM commercial.finance_pilot_credit",
            ):
                with pytest.raises(Exception):  # noqa: B017
                    async with c.begin_nested():
                        await c.execute(text(sql))
        # time passes beyond the end date: nothing happens by itself
        await asyncio.sleep(0)
        after = await footprint()
        assert after == before, (
            "expiry created no order, payment, invoice, rating run, plan change or usage charge"
        )
        assert (
            before["sales_order"]
            == before["activation_payment"]
            == before["invoice"]
            == before["entitlement"]
            == 0
        )

    asyncio.run(run())


def test_cat30_another_payers_invoice_is_not_reachable_by_its_id(rt_url: str) -> None:
    async def run() -> None:
        owner, other = uid(), uid()
        invoice_id = await issued_invoice(rt_url, owner)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(owner)) as c:
                mine = await inv.view(c, invoice_id)
                assert mine["status"] == "ISSUED"
            # a different company, holding the right invoice id, is told there is no such invoice: no document, no totals, no payer details
            async with tenant_transaction(eng, ctx(other)) as c:
                with pytest.raises(InvoicingError) as e:
                    await inv.view(c, invoice_id)
                assert e.value.code == "INVOICE_NOT_FOUND"
                assert (
                    await c.execute(text("SELECT count(*) FROM commercial.invoice"))
                ).scalar_one() == 0
                assert (
                    await c.execute(
                        text("SELECT count(*) FROM commercial.invoice WHERE invoice_id = :i"),
                        {"i": uuid.UUID(invoice_id)},
                    )
                ).scalar_one() == 0

    asyncio.run(run())


def test_cat40_a_rollout_a_rollback_and_a_new_matrix_leave_meters_and_invoices_as_they_were(
    rt_url: str,
) -> None:
    async def run() -> None:
        tenant = uid()
        author, activator = uid(), uid()
        async with engine(rt_url) as eng:
            invoice_id = await issued_invoice(rt_url, tenant)

            async def evidence(c) -> tuple[object, object]:
                events = (
                    await c.execute(
                        text(
                            "SELECT count(*), string_agg(row_hash, ',' ORDER BY row_hash) FROM commercial.billable_event"
                        )
                    )
                ).one()
                doc = (
                    await c.execute(
                        text(
                            "SELECT status, document_digest, chain_hash, gross FROM commercial.invoice WHERE invoice_id = :i"
                        ),
                        {"i": uuid.UUID(invoice_id)},
                    )
                ).one()
                return tuple(events), tuple(doc)

            async with tenant_transaction(eng, ctx(tenant)) as c:
                await recorder.record(
                    c, tenant_id=tenant, meter="AUTH_EVENT", key="auth:cat40", at=NOW
                )
                before = await evidence(c)
                assert before[0][0] >= 1

            async with eng.begin() as c:
                first = (
                    await matrices.create_draft(c, copy_of=None, created_by=author, note="first")
                )["matrix_id"]
                await matrices.put_row(c, first, full_row("API", "IE"))
                await signed(c, first, author)
                now = datetime.now(UTC) - timedelta(seconds=10)
                await matrices.activate(c, first, activator=activator, effective_from=now, now=now)
                await go_live(c, "API", "IE", through="PRODUCTION")
                await rollout_store.move(
                    c,
                    family="API",
                    country="IE",
                    target="ROLLED_BACK",
                    decider=uid(),
                    reason="provider rights lapsed",
                )
                second = (
                    await matrices.create_draft(c, copy_of=first, created_by=author, note="second")
                )["matrix_id"]
                await signed(c, second, author)
                later = now + timedelta(seconds=5)
                await matrices.activate(
                    c, second, activator=activator, effective_from=later, now=later
                )

            async with tenant_transaction(eng, ctx(tenant)) as c:
                assert await evidence(c) == before, (
                    "the meter and the issued invoice are exactly as they were"
                )
            async with eng.begin() as c:
                old = await matrices.matrix(c, first)
                assert old is not None and old["status"] == "SUPERSEDED"
                assert [r.country for r in await matrices.rows(c, first)] == ["IE"], (
                    "the superseded matrix and its rows remain as evidence"
                )
                history = await rollout_store.history(c, "API", "IE")
                assert [h["stage"] for h in history][-2:] == ["PRODUCTION", "ROLLED_BACK"], (
                    "the rollout log is intact"
                )

    asyncio.run(run())
