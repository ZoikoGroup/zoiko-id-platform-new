"""Stripe's signed events reach the ledger once (CR-040): a late success, a failure, a redelivery, a forged call."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import time
import uuid
from decimal import Decimal

from conftest import ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from test_invoicing_pg import ENTITY, LAST, rated_month, uid
from test_owner_payment_pg import NoTax
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service
from zid_platform.settings import Settings

SECRET = "whsec_pg_test"
CUSTOMER = "cus_HookPay123"
PATH = "/v1/provider-events/stripe"
MINE = {"evt_paid0001", "evt_paid0002", "evt_refund01", "evt_stray001", "evt_noise001"}


def signed(
    kind: str, obj: dict[str, object], *, event_id: str, secret: str = SECRET
) -> dict[str, object]:
    raw = json.dumps({"id": event_id, "type": kind, "data": {"object": obj}}).encode()
    at = int(time.time())
    mac = hmac.new(secret.encode(), f"{at}.".encode() + raw, hashlib.sha256).hexdigest()
    return {
        "content": raw,
        "headers": {"Stripe-Signature": f"t={at},v1={mac}", "Content-Type": "application/json"},
    }


def test_signed_events_settle_fail_and_flag_once(rt_url: str) -> None:
    o = onboard(rt_url, extra_resources=["billable:*", "tenant:*"])
    drafter, issuer = uid(), uid()

    async def prepare() -> str:
        await rated_month(rt_url, o.tenant)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                account = await orders_service.create_account(
                    c,
                    legal_name="Hook Pay Ltd",
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
        h = o.bearer(o.owner)
        pi = {
            "id": "pi_late_1",
            "customer": CUSTOMER,
            "currency": "usd",
            "metadata": {"invoice_id": invoice_id},
        }

        # Nothing is configured: the endpoint says so and reads nothing.
        assert (
            api.post(
                PATH, **signed("payment_intent.succeeded", pi, event_id="evt_unconf1")
            ).status_code
            == 404
        )

        api.app.state.stripe_webhook_secrets = [SECRET]
        due = api.get("/v1/billing/payment", headers=h).json()["amount_due"][0]
        minor = int((Decimal(due["amount"]) * 100).to_integral_value())
        pi["amount"] = minor

        forged = api.post(
            PATH, **signed("payment_intent.succeeded", pi, event_id="evt_forged1", secret="whsec_x")
        )
        assert forged.status_code == 400 and forged.json()["error"]["code"] == "SIGNATURE_INVALID"
        assert api.post(PATH, content=b"{}").status_code == 400  # no signature at all
        assert api.get("/v1/billing/payment", headers=h).json()["payments"] == []

        first = api.post(PATH, **signed("payment_intent.succeeded", pi, event_id="evt_paid0001"))
        assert first.status_code == 200 and first.json()["outcome"] == "APPLIED"
        again = api.post(PATH, **signed("payment_intent.succeeded", pi, event_id="evt_paid0001"))
        assert again.json()["outcome"] == "DUPLICATE"
        other = api.post(PATH, **signed("payment_intent.succeeded", pi, event_id="evt_paid0002"))
        assert (
            other.json()["outcome"] == "IGNORED"
        )  # the same payment under a new event id changes nothing

        after = api.get("/v1/billing/payment", headers=h).json()
        assert after["amount_due"] == [] and [p["status"] for p in after["payments"]] == [
            "CAPTURED"
        ]

        refund = api.post(
            PATH,
            **signed(
                "charge.refunded",
                {
                    "id": "ch_1",
                    "customer": CUSTOMER,
                    "currency": "usd",
                    "amount": minor,
                    "amount_refunded": 100,
                    "metadata": {"invoice_id": invoice_id},
                },
                event_id="evt_refund01",
            ),
        )
        assert refund.json()["outcome"] == "NEEDS_REVIEW"
        stray = api.post(
            PATH,
            **signed(
                "payment_intent.payment_failed",
                {**pi, "id": "pi_other", "metadata": {"invoice_id": str(uid())}},
                event_id="evt_stray001",
            ),
        )
        assert stray.json()["outcome"] == "UNMATCHED"
        assert (
            api.post(
                PATH, **signed("customer.updated", {"id": CUSTOMER}, event_id="evt_noise001")
            ).json()["outcome"]
            == "IGNORED"
        )

    async def ledger() -> dict[str, str]:
        async with engine(rt_url) as eng, eng.begin() as c:
            rows = await c.execute(
                text(
                    "SELECT event_id, outcome FROM commercial.stripe_event WHERE tenant_id = :t OR tenant_id IS NULL"
                ),
                {"t": uuid.UUID(o.tenant)},
            )
            return {r[0]: r[1] for r in rows if r[0] in MINE}

    assert asyncio.run(ledger()) == {
        "evt_paid0001": "APPLIED",
        "evt_paid0002": "IGNORED",
        "evt_refund01": "NEEDS_REVIEW",
        "evt_stray001": "UNMATCHED",
        "evt_noise001": "IGNORED",
    }

    async def queue() -> None:
        from zid_platform.modules.commercial.application import stripe_webhook_service as svc

        async with engine(rt_url) as eng, eng.begin() as c:
            items = {i["event_id"]: i for i in await svc.review_queue(c)}
            assert set(items) & MINE == {"evt_refund01", "evt_stray001"}
            refund = items["evt_refund01"]
            # the refund is tied to the company's own invoice, with what the operator needs to credit it
            assert (refund["tenant_id"], refund["invoice_id"]) == (o.tenant, invoice_id)
            assert (refund["amount"], refund["currency"]) == ("1.00", "USD")
            assert (
                items["evt_stray001"]["invoice_id"] is None
            )  # an invoice that is not the customer's is not linked
            assert await svc.resolve_event(
                c, "evt_refund01", operator=drafter, resolution="Credit note raised"
            )
            assert not await svc.resolve_event(
                c, "evt_refund01", operator=drafter, resolution="again"
            )
            assert not await svc.resolve_event(
                c, "evt_paid0001", operator=drafter, resolution="not a review item"
            )
            assert {i["event_id"] for i in await svc.review_queue(c)} & MINE == {"evt_stray001"}
            done = [
                i
                for i in await svc.review_queue(c, include_resolved=True)
                if i["resolved"] and i["event_id"] in MINE
            ]
            assert [d["resolution"] for d in done] == ["Credit note raised"]

    asyncio.run(queue())
