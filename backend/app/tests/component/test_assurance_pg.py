"""Revenue assurance: change log, month-end close, detectors, journals, audit pack and KPIs (CR-030).

Every commercial change lands in one append-only hash chain; a close resumes where it stopped; detectors turn
leaks (an entitlement no order backs, a month rated but never invoiced, a session never billed) into exceptions
with an owner; journals are derived from issued documents and always balance; MRR excludes intercompany.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import (
    assurance_service as assurance,
)
from zid_platform.modules.commercial.application import (
    invoicing_service,
    orders_service,
    recorder,
    vendor_service,
)
from zid_platform.modules.commercial.application.adapters import NoTaxConfigured, RecordingPayments
from zid_platform.modules.commercial.application.change_log import log_change, verify_change_log
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.orders import QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
NOW = datetime.now(UTC)
LAST = b.window_month(NOW.replace(day=1) - timedelta(days=1))
L_START = datetime(LAST.year, LAST.month, 1, tzinfo=UTC)
ENTITY = f"A{uuid.uuid4().hex[:8].upper()}"


def uid() -> str:
    return str(uuid.uuid4())


async def approved_book(rt_url: str) -> str:
    async with engine(rt_url) as eng, eng.begin() as c:
        d = await catalogue_store.create_draft(
            c, copy_of=SEED_BOOK, created_by=uid(), note="assurance"
        )
        await catalogue_store.approve(
            c,
            d["price_book_id"],
            approver=uid(),
            effective_from=L_START - timedelta(days=40),
            now=datetime.now(UTC),
        )
    return str(d["price_book_id"])


def test_close_detectors_journal_and_audit_pack(rt_url: str, owner_url: str) -> None:
    tenant, drafter, issuer = uid(), uid(), uid()

    async def run() -> None:
        book = await approved_book(rt_url)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await catalogue_store.set_entitlement(
                    c,
                    tenant_id=tenant,
                    plan_code="BUSINESS",
                    price_book_id=book,
                    actor=None,
                    now=L_START - timedelta(days=30),
                )
                await recorder.record(
                    c,
                    tenant_id=tenant,
                    meter="MAH",
                    key=f"mah:{tenant}",
                    at=L_START + timedelta(days=3),
                    quantity=60_000,
                )

            # the close: freeze, rate, draft; a second run does nothing new
            async with tenant_transaction(eng, ctx(tenant)) as c:
                first = await assurance.close_month(
                    c,
                    tenant_id=tenant,
                    month=LAST,
                    connections={},
                    issuing_entity=ENTITY,
                    actor=drafter,
                )
                assert first["done_now"] == ["FROZEN", "RATED", "DRAFTED"] and first["complete"]
                again = await assurance.close_month(
                    c,
                    tenant_id=tenant,
                    month=LAST,
                    connections={},
                    issuing_entity=ENTITY,
                    actor=drafter,
                )
                assert again["done_now"] == [] and again["invoice_id"] == first["invoice_id"]

            # detectors: an entitlement no order backs, a computed rating never invoiced, a session never billed
            async def fetch_sessions(
                conn: object, start: datetime, end: datetime
            ) -> list[tuple[str, datetime]]:
                return [("session-without-event", L_START + timedelta(days=5))]

            sources = {"AUTH_EVENT": (fetch_sessions, b.key_auth, "HUMAN")}
            async with tenant_transaction(eng, ctx(tenant)) as c:
                found = await assurance.detect_tenant(c, tenant, sources=sources, month=LAST)
                kinds = sorted(f["kind"] for f in found)
                assert kinds == ["ENTITLEMENT_WITHOUT_ORDER", "METER_LOSS", "UNINVOICED_RUN"], kinds
                assert (
                    await assurance.detect_tenant(c, tenant, sources=sources, month=LAST) == []
                )  # one open each

                # issue and pay: the leak closes, journals balance
                out = await invoicing_service.issue(
                    c,
                    first["invoice_id"],
                    issuer=issuer,
                    tax=NoTaxConfigured(),
                    payment_terms_days=30,
                    allow_zero_tax=True,
                    region="uk-1",
                )
                assert out["gross"] == "1649"  # 1,499 + 10,000 MAH over 50,000 x 0.015
                await invoicing_service.collect(
                    c,
                    first["invoice_id"],
                    payments=RecordingPayments(),
                    idempotency_key="key-asr-0001",
                )
                j = await assurance.journal(c, NOW - timedelta(days=1), NOW + timedelta(days=1))
                assert j["balanced"]
                accounts = [(ln["account"], ln["debit"], ln["credit"]) for ln in j["lines"]]
                assert ("RECEIVABLES", "1649", "0") in accounts and (
                    "CASH",
                    "1649",
                    "0",
                ) in accounts
                assert ("REVENUE_SUBSCRIPTION", "0", "1499") in accounts and (
                    "REVENUE_USAGE",
                    "0",
                    "150",
                ) in accounts

                pack = await assurance.audit_pack(c, tenant, LAST)
                assert pack == await assurance.audit_pack(c, tenant, LAST)  # deterministic
                assert pack["rating"] and len(pack["invoices"]) == 1 and pack["meter_windows"]
                assert {e["kind"] for e in pack["change_log"]} >= {"INVOICE_ISSUED"}
                assert tenant not in str(pack["tenant"])  # alias only

            open_ = [e for e in await _exceptions(rt_url) if e["tenant_id"] == tenant]
            assert {e["kind"] for e in open_} == {
                "ENTITLEMENT_WITHOUT_ORDER",
                "METER_LOSS",
                "UNINVOICED_RUN",
            }
            async with eng.begin() as c:
                target = next(e for e in open_ if e["kind"] == "UNINVOICED_RUN")
                assert await assurance.resolve(
                    c, target["exception_id"], owner=issuer, resolution="issued INV"
                )
                assert not await assurance.resolve(
                    c, target["exception_id"], owner=issuer, resolution="again"
                )

        # the change log is a whole chain and cannot be edited
        async with engine(rt_url) as eng, eng.begin() as c:
            v = await verify_change_log(c)
            assert v["intact"] and v["entries"] >= 1
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.change_log SET reason = 'x'",
                "DELETE FROM commercial.change_log",
            ):
                with pytest.raises(DBAPIError, match="immutable"):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())


async def _exceptions(rt_url: str) -> list[dict[str, object]]:
    async with engine(rt_url) as eng, eng.begin() as c:
        return await assurance.exceptions(c)


def test_change_log_entries_chain_in_order(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            seqs = []
            for i in range(5):
                async with eng.begin() as c:
                    seqs.append(
                        await log_change(
                            c,
                            kind="TEST_ENTRY",
                            ref=f"r{i}",
                            tenant_id=None,
                            actor=None,
                            detail={"i": i},
                        )
                    )
            assert seqs == list(range(seqs[0], seqs[0] + 5))
            async with eng.begin() as c:
                assert (await verify_change_log(c))["intact"]

    asyncio.run(run())


def test_platform_detectors_and_kpis(rt_url: str, owner_url: str) -> None:
    actor, other = uid(), uid()

    async def run() -> None:
        book = await approved_book(rt_url)
        async with engine(rt_url) as eng, eng.begin() as c:
            before = Decimal((await assurance.kpis(c))["mrr"])
            external = await orders_service.create_account(
                c,
                legal_name="Ext Ltd",
                kind="DIRECT",
                country="GB",
                currency="USD",
                reseller_of=None,
                actor=actor,
            )
            internal = await orders_service.create_account(
                c,
                legal_name="Zoiko Affiliate",
                kind="INTERNAL",
                country="GB",
                currency="USD",
                reseller_of=None,
                actor=actor,
            )
            await c.execute(
                text(
                    "INSERT INTO commercial.region_release (region, sre_gate_passed, evidence_ref, recorded_by) "
                    "VALUES ('uk-1', true, 'PRR', :b) ON CONFLICT DO NOTHING"
                ),
                {"b": uuid.uuid4()},
            )
            for account in (external, internal):
                t = uid()
                await orders_service.link_tenant(c, account, t)
                q = await orders_service.create_quote(
                    c,
                    account_id=account,
                    price_book_id=book,
                    region="uk-1",
                    term_months=12,
                    lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", Decimal(1))],
                    prepaid=False,
                    prepaid_discount=Decimal(0),
                    liability_capped=True,
                    residency_commitment=None,
                    pilot=False,
                    actor=actor,
                )
                o = await orders_service.accept(
                    c,
                    q["quote_id"],
                    tenant_id=t,
                    signer_ref="buyer",
                    signed_document_digest="b" * 64,
                    effective_from=datetime.now(UTC),
                    amends_order_id=None,
                    actor=actor,
                )
                await orders_service.activate(c, o["order_id"], actor=other)
            after = await assurance.kpis(c)
            assert Decimal(after["mrr"]) - before == Decimal(
                1499
            )  # the affiliate's order is not external revenue
            assert Decimal(after["arr"]) == Decimal(after["mrr"]) * 12

            vid = f"kyb-{uuid.uuid4().hex[:8]}"
            await vendor_service.register(
                c,
                vendor_id=vid,
                display_name="KYB Co",
                legal_role="SUBPROCESSOR",
                adapter_id=None,
                sku_id="ZID-KYB",
                spike_threshold_pct=None,
                actor=actor,
            )
            await c.execute(
                text(
                    "INSERT INTO commercial.vendor_dispute (dispute_id, vendor_id, invoice_ref, supplier_ref, kind) "
                    "VALUES (:i, :v, 'INV-9', 'r1', 'UNKNOWN_ON_INVOICE')"
                ),
                {"i": uuid.uuid4(), "v": vid},
            )
            found = await assurance.detect_platform(c)
            assert {"kind": "SUPPLIER_DISPUTE_OPEN", "ref": f"{vid}:INV-9"} in found

    asyncio.run(run())
