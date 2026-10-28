"""The owner pays an invoice on Stripe's page, the way through when a bank asks for confirmation (CR-041)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import httpx
from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from test_invoicing_pg import ENTITY, LAST, rated_month, uid
from test_owner_payment_pg import NoTax
from test_stripe_webhook_pg import signed
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service
from zid_platform.modules.commercial.application.adapters import StripeCustomers
from zid_platform.settings import Settings

CUSTOMER = "cus_OnlinePay123"
SESSION = "cs_test_online12345"


def test_pay_on_stripe_then_return_records_once_and_the_webhook_adds_nothing(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])
    drafter, issuer = uid(), uid()

    async def prepare() -> str:
        await rated_month(rt_url, o.tenant)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                account = await orders_service.create_account(
                    c,
                    legal_name="Online Pay Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=drafter,
                )
                await orders_service.link_tenant(c, account, o.tenant)
                await inv.set_payment_customer(
                    c, account, provider="stripe", customer_ref=CUSTOMER, actor=drafter
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
    state: dict[str, int] = {"minor": 0}

    def stripe(req: httpx.Request) -> httpx.Response:
        if req.method == "POST":
            return httpx.Response(
                200, json={"id": SESSION, "url": "https://checkout.stripe.test/pay"}
            )
        return httpx.Response(
            200,
            json={
                "mode": "payment",
                "customer": CUSTOMER,
                "payment_status": "paid",
                "payment_intent": "pi_online_1",
                "amount_total": state["minor"],
                "currency": "usd",
                "metadata": {"invoice_id": invoice_id},
            },
        )

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
        api.app.state.payment_setup = StripeCustomers(
            "sk_test_pg", base_url="https://stripe.test", transport=httpx.MockTransport(stripe)
        )
        api.app.state.billing_return_origin = "https://console.test"
        api.app.state.stripe_webhook_secrets = ["whsec_online"]
        h = o.bearer(o.owner)
        due = api.get("/v1/billing/payment", headers=h).json()["amount_due"][0]
        state["minor"] = int((Decimal(due["amount"]) * 100).to_integral_value())

        started = api.post(f"/v1/invoices/{invoice_id}/pay-online", headers=h)
        assert started.status_code == 200 and started.json()["url"].startswith("https://checkout")
        assert api.post(f"/v1/invoices/{uid()}/pay-online", headers=h).status_code == 404
        assert api.post(f"/v1/invoices/{invoice_id}/pay-online").status_code == 401

        done = api.post(
            "/v1/billing/payment/online-complete", json={"session_id": SESSION}, headers=h
        )
        assert done.status_code == 200 and done.json() == {
            "recorded": True,
            "already_recorded": False,
        }
        again = api.post(
            "/v1/billing/payment/online-complete", json={"session_id": SESSION}, headers=h
        )
        assert again.json() == {"recorded": True, "already_recorded": True}
        after = api.get("/v1/billing/payment", headers=h).json()
        assert after["amount_due"] == [] and [p["status"] for p in after["payments"]] == [
            "CAPTURED"
        ]

        # Stripe's own report of the same payment arrives afterwards and changes nothing
        pi = {
            "id": "pi_online_1",
            "customer": CUSTOMER,
            "currency": "usd",
            "amount": state["minor"],
            "metadata": {"invoice_id": invoice_id},
        }
        late = api.post(
            "/v1/provider-events/stripe",
            **signed(
                "payment_intent.succeeded", pi, event_id="evt_online001", secret="whsec_online"
            ),
        )
        assert late.json()["outcome"] == "IGNORED"
        assert len(api.get("/v1/billing/payment", headers=h).json()["payments"]) == 1

        # a value that is not a session is refused outright
        bad = api.post(
            "/v1/billing/payment/online-complete", json={"session_id": "not-a-session"}, headers=h
        )
        assert bad.status_code == 422
