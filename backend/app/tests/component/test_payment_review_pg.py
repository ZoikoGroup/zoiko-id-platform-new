"""An operator works the payment review queue: a refund becomes a credit note once (CR-041)."""

from __future__ import annotations

import asyncio
from decimal import Decimal

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import ISS, onboard
from operator_fixture import activate_grant, operators
from test_invoicing_pg import ENTITY, LAST, rated_month, settings, uid
from test_owner_payment_pg import NoTax
from test_stripe_webhook_pg import signed
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service
from zid_platform.modules.operator_access.domain.model import OperatorRole

CUSTOMER = "cus_ReviewPay123"
SECRET = "whsec_review"


def test_a_refund_is_credited_once_and_leaves_the_queue(rt_url: str) -> None:
    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    alice, dave, bob, carol = uid(), uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            dave: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )

    async def prepare() -> str:
        await rated_month(rt_url, o.tenant)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                account = await orders_service.create_account(
                    c,
                    legal_name="Review Pay Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=alice,
                )
                await orders_service.link_tenant(c, account, o.tenant)
                await inv.set_payment_customer(
                    c, account, provider="stripe", customer_ref=CUSTOMER, actor=alice
                )
            async with tenant_transaction(eng, ctx(o.tenant)) as c:
                d = await inv.create_draft(
                    c, tenant_id=o.tenant, month=LAST, issuing_entity=ENTITY, actor=alice
                )
                await inv.issue(
                    c,
                    d["invoice_id"],
                    issuer=bob,
                    tax=NoTax(),
                    payment_terms_days=14,
                    allow_zero_tax=False,
                    region="uk-1",
                )
                return str(d["invoice_id"])

    invoice_id = asyncio.run(prepare())
    app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    with TestClient(app, base_url=ISS) as api:
        api.app.state.stripe_webhook_secrets = [SECRET]
        charge = {
            "id": "ch_review_1",
            "customer": CUSTOMER,
            "currency": "usd",
            "amount": 5000,
            "amount_refunded": 1500,
            "metadata": {"invoice_id": invoice_id},
        }
        hook = api.post(
            "/v1/provider-events/stripe",
            **signed("charge.refunded", charge, event_id="evt_review001", secret=SECRET),
        )
        assert hook.json()["outcome"] == "NEEDS_REVIEW"

        # no grant, no queue
        assert (
            api.get("/v1/commercial/payment-events", headers=ops.bearer(alice)).status_code == 403
        )
        catalog = {
            "capability": "commercial.catalog.manage",
            "resource_scope": "commercial:catalogue",
        }
        activate_grant(api, ops, alice, (bob, carol), **catalog)
        activate_grant(api, ops, dave, (bob, carol), **catalog)
        queue = api.get("/v1/commercial/payment-events", headers=ops.bearer(alice)).json()["items"]
        mine = next(i for i in queue if i["event_id"] == "evt_review001")
        assert (mine["tenant_id"], mine["invoice_id"], mine["amount"]) == (
            o.tenant,
            invoice_id,
            "15.00",
        )

        credit = "/v1/commercial/payment-events/evt_review001:credit-note"
        # the billing grant for this company is also needed to issue a credit note
        assert (
            api.post(credit, json={"amount": "15.00"}, headers=ops.bearer(dave)).status_code == 403
        )
        billing = {
            "capability": "commercial.billing.manage",
            "resource_scope": f"tenant:{o.tenant}",
            "target_tenant_id": o.tenant,
        }
        activate_grant(api, ops, dave, (bob, carol), **billing)
        assert api.post(credit, json={"amount": "0"}, headers=ops.bearer(dave)).status_code == 422
        made = api.post(credit, json={"amount": "15.00"}, headers=ops.bearer(dave))
        assert made.status_code == 201, made.text
        assert Decimal(made.json()["amount"]) == Decimal("15.00")
        assert made.json()["number"].startswith(ENTITY)

        # it left the queue, with the credit note named, and cannot be credited twice
        assert "evt_review001" not in {
            i["event_id"]
            for i in api.get("/v1/commercial/payment-events", headers=ops.bearer(alice)).json()[
                "items"
            ]
        }
        done = api.get("/v1/commercial/payment-events?resolved=true", headers=ops.bearer(alice))
        row = next(i for i in done.json()["items"] if i["event_id"] == "evt_review001")
        assert row["resolved"] and row["resolution"].startswith("Credit note " + ENTITY)
        assert (
            api.post(credit, json={"amount": "15.00"}, headers=ops.bearer(dave)).status_code == 404
        )

        # a stray payment can be closed with a note but not credited
        stray = {**charge, "id": "ch_stray", "metadata": {}}
        api.post(
            "/v1/provider-events/stripe",
            **signed("charge.dispute.created", stray, event_id="evt_review002", secret=SECRET),
        )
        assert (
            api.post(
                "/v1/commercial/payment-events/evt_review002:credit-note",
                json={"amount": "1.00"},
                headers=ops.bearer(dave),
            ).status_code
            == 404
        )
        closed = api.post(
            "/v1/commercial/payment-events/evt_review002:resolve",
            json={"resolution": "Dispute answered in the Stripe dashboard"},
            headers=ops.bearer(alice),
        )
        assert closed.status_code == 200 and closed.json()["status"] == "RESOLVED"
        assert (
            api.post(
                "/v1/commercial/payment-events/evt_review002:resolve",
                json={"resolution": "again"},
                headers=ops.bearer(alice),
            ).status_code
            == 404
        )
        assert (
            api.post(
                "/v1/commercial/payment-events/bad-id:resolve",
                json={"resolution": "x" * 5},
                headers=ops.bearer(alice),
            ).status_code
            == 404
        )
