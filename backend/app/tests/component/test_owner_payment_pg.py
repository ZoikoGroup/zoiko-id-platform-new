"""The owner sees what is due and pays it with the saved card (CR-034): amount due, payment history, Pay now."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from decimal import Decimal

import httpx
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_invoicing_pg import ENTITY, LAST, rated_month, uid
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service
from zid_platform.modules.commercial.application.adapters import (
    StripePayments,
    TaxableLine,
    TaxLine,
)
from zid_platform.settings import Settings


class NoTax:
    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        return {ln.seq: TaxLine("VAT0", Decimal(0)) for ln in lines if ln.net > 0}


def test_the_owner_sees_the_amount_due_pays_it_and_sees_the_history(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])
    drafter, issuer = uid(), uid()
    charged: list[str] = []

    def stripe(req: httpx.Request) -> httpx.Response:
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "cus_x",
                    "invoice_settings": {"default_payment_method": "pm_card_test"},
                },
            )
        charged.append(req.content.decode())
        return httpx.Response(200, json={"id": "pi_owner_1", "status": "succeeded"})

    async def prepare() -> str:
        await rated_month(rt_url, o.tenant)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                account = await orders_service.create_account(
                    c,
                    legal_name="Owner Pay Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=drafter,
                )
                await orders_service.link_tenant(c, account, o.tenant)
                await inv.set_payment_customer(
                    c, account, provider="stripe", customer_ref="cus_OwnerPay123", actor=drafter
                )
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                d = await inv.create_draft(
                    c, tenant_id=o.tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
                )
                await inv.issue(
                    c,
                    d["invoice_id"],
                    issuer=issuer,
                    tax=NoTax(),
                    payment_terms_days=14,
                    allow_zero_tax=False,
                    region="uk-1",
                )
                return str(d["invoice_id"])

    invoice_id = asyncio.run(prepare())
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_id="uk-1",
        ),
        keyring=o.ring,
    )
    with TestClient(app) as api:
        api.app.state.payment_port = StripePayments(
            "sk_test_pg", base_url="https://stripe.test", transport=httpx.MockTransport(stripe)
        )
        h = o.bearer(o.owner)
        before = api.get("/v1/billing/payment", headers=h).json()
        assert before["can_manage"] is True and before["payments"] == []
        assert [d["currency"] for d in before["amount_due"]] == ["USD"]
        assert Decimal(before["amount_due"][0]["amount"]) > 0
        assert [i["invoice_id"] for i in before["open_invoices"]] == [invoice_id]

        paid = api.post(
            f"/v1/invoices/{invoice_id}/pay", json={"idempotency_key": "click-0001-abcd"}, headers=h
        )
        assert paid.status_code == 200 and paid.json() == {
            "status": "CAPTURED",
            "failure_code": None,
        }
        again = api.post(
            f"/v1/invoices/{invoice_id}/pay", json={"idempotency_key": "click-0001-abcd"}, headers=h
        )
        assert again.status_code == 200 and len(charged) == 1  # the same click never charges twice

        after = api.get("/v1/billing/payment", headers=h).json()
        assert after["amount_due"] == [] and after["open_invoices"] == []
        assert [p["status"] for p in after["payments"]] == ["CAPTURED"]
        assert after["payments"][0]["invoice_number"].startswith(ENTITY)

        settled = api.post(
            f"/v1/invoices/{invoice_id}/pay", json={"idempotency_key": "click-0002-abcd"}, headers=h
        )
        assert settled.status_code == 409  # nothing is outstanding any more
        assert (
            api.post(
                f"/v1/invoices/{uid()}/pay", json={"idempotency_key": "click-0003-abcd"}, headers=h
            ).status_code
            == 404
        )
        assert (
            api.post(
                f"/v1/invoices/{invoice_id}/pay", json={"idempotency_key": "click-0004-abcd"}
            ).status_code
            == 401
        )
