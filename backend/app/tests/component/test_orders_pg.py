"""Accounts, quotes, discount approvals and orders (ZID-COM-001 §10, §12, §14, §31; CR-028).

A quote for an unlaunched region is refused; a discount needs its band's role, held by someone other than the
quote's author; an approval binds the exact quote; a signed order becomes the tenant's entitlement only through
activation by a second person, once, never repriced; a tenant sees its own orders only.
"""

from __future__ import annotations

import asyncio
import pathlib
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import service
from zid_platform.modules.commercial.domain.orders import OrderError, QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.settings import Settings

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
D = Decimal
SIGNED = "a" * 64


def uid() -> str:
    return str(uuid.uuid4())


def settings(rt_url: str, **kw: Any) -> Settings:
    return Settings(
        database_url=rt_url,
        token_issuer=ISS,
        token_audience=AUD,
        public_base_url=AUD,
        environment="test",
        cell_id="uk-1",
        cell_uuid=CELL,
        **kw,
    )


async def setup(rt_url: str, owner_url: str, *, cco: str, cfo: str, founder: str) -> str:
    """An approved copy of the proposal book, a released region and the commercial role holders."""
    async with engine(rt_url) as eng, eng.begin() as c:
        draft = await catalogue_store.create_draft(
            c, copy_of=SEED_BOOK, created_by=uid(), note="orders"
        )
        await catalogue_store.approve(
            c,
            draft["price_book_id"],
            approver=uid(),
            effective_from=datetime.now(UTC) - timedelta(days=1),
            now=datetime.now(UTC),
        )
    async with engine(owner_url) as eng, eng.begin() as c:
        await c.execute(
            text(
                "INSERT INTO commercial.region_release (region, sre_gate_passed, evidence_ref, recorded_by) "
                "VALUES ('uk-1', true, 'PRR-uk-1', :b), ('eu-9', false, 'not yet', :b) ON CONFLICT DO NOTHING"
            ),
            {"b": uuid.uuid4()},
        )
        for who, role in ((cco, "CCO"), (cfo, "CFO"), (founder, "FOUNDER")):
            await c.execute(
                text(
                    "INSERT INTO commercial.commercial_role_holder (principal_id, role, granted_by) "
                    "VALUES (:p, :r, :g) ON CONFLICT DO NOTHING"
                ),
                {"p": uuid.UUID(who), "r": role, "g": uuid.uuid4()},
            )
    return str(draft["price_book_id"])


def test_quote_validation_approval_order_and_activation(rt_url: str, owner_url: str) -> None:
    ae, cco, cfo, founder, activator = uid(), uid(), uid(), uid(), uid()
    tenant, stranger = uid(), uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(rt_url) as eng, eng.begin() as c:
            account = await orders.create_account(
                c,
                legal_name="Acme Ltd",
                kind="DIRECT",
                country="GB",
                currency="USD",
                reseller_of=None,
                actor=ae,
            )
            await orders.link_tenant(c, account, tenant)
            base = {
                "account_id": account,
                "price_book_id": book,
                "term_months": 12,
                "prepaid": False,
                "prepaid_discount": D(0),
                "liability_capped": True,
                "residency_commitment": None,
                "pilot": False,
                "actor": ae,
            }
            plan = QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))

            # an unlaunched region is refused outright (an SLA for an unlaunched cell cannot be sold)
            q = await orders.create_quote(c, region="eu-9", lines=[plan], **base)
            assert q["status"] == "REJECTED" and q["reasons"] == ["REGION_NOT_RELEASED"]

            # list price: validated, nothing to approve
            clean = await orders.create_quote(
                c, region="uk-1", lines=[plan, QuoteLine(2, "ZID-FED", "BUSINESS", D(2))], **base
            )
            assert clean["status"] == "VALIDATED"
            assert [ln["net_price"] for ln in clean["lines"]] == ["1499", "149"]

            # an 8% discount is the CCO's band; the author cannot approve; the CFO is not the role asked for
            disc = await orders.create_quote(
                c,
                region="uk-1",
                lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.08"))],
                **base,
            )
            assert disc["status"] == "NEEDS_APPROVAL" and disc["approvers_required"] == ["CCO"]
            for who, role, code in (
                (ae, "CCO", "QUOTE_SELF_APPROVAL"),
                (cfo, "CFO", "APPROVAL_ROLE_NOT_REQUIRED"),
                (cfo, "CCO", "APPROVER_ROLE_MISSING"),
            ):
                with pytest.raises(OrderError) as e:
                    await orders.approve_quote(c, disc["quote_id"], role=role, approver=who)
                assert e.value.code == code
            approved = await orders.approve_quote(c, disc["quote_id"], role="CCO", approver=cco)
            assert approved["status"] == "APPROVED" and approved["missing_roles"] == []

            # a 20% discount needs the CFO and the Founder, two different people
            big = await orders.create_quote(
                c,
                region="uk-1",
                lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.20"))],
                **base,
            )
            assert set(big["approvers_required"]) == {"CFO", "FOUNDER"}
            half = await orders.approve_quote(c, big["quote_id"], role="CFO", approver=cfo)
            assert half["status"] == "NEEDS_APPROVAL" and half["missing_roles"] == ["FOUNDER"]
            assert (
                await orders.approve_quote(c, big["quote_id"], role="FOUNDER", approver=founder)
            )["status"] == "APPROVED"

            # the signed quote becomes an order only for a tenant of the quoted account
            eff = datetime.now(UTC) + timedelta(minutes=1)
            with pytest.raises(OrderError) as e:
                await orders.accept(
                    c,
                    approved["quote_id"],
                    tenant_id=stranger,
                    signer_ref="CFO of Acme",
                    signed_document_digest=SIGNED,
                    effective_from=eff,
                    amends_order_id=None,
                    actor=ae,
                )
            assert e.value.code == "TENANT_NOT_IN_ACCOUNT"
            with pytest.raises(OrderError) as e:
                await orders.accept(
                    c,
                    q["quote_id"],
                    tenant_id=tenant,
                    signer_ref="CFO of Acme",
                    signed_document_digest=SIGNED,
                    effective_from=eff,
                    amends_order_id=None,
                    actor=ae,
                )
            assert e.value.code == "QUOTE_NOT_ACCEPTABLE"  # a rejected quote is never signed
            order = await orders.accept(
                c,
                clean["quote_id"],
                tenant_id=tenant,
                signer_ref="CFO of Acme",
                signed_document_digest=SIGNED,
                effective_from=eff,
                amends_order_id=None,
                actor=ae,
            )
            assert order["status"] == "SIGNED" and order["plan"] == "BUSINESS"

            # activation: a second person, once; the entitlement and the extra connections follow the order
            with pytest.raises(OrderError) as e:
                await orders.activate(c, order["order_id"], actor=ae)
            assert e.value.code == "ORDER_SELF_ACTIVATION"
            done = await orders.activate(c, order["order_id"], actor=activator)
            assert done["status"] == "ACTIVATED" and not done["already_active"]
            again = await orders.activate(c, order["order_id"], actor=activator)
            assert again["already_active"]

        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            view = await service.entitlement_view(c)
            assert view["plan"] == "BUSINESS" and view["source"] == "ORDER"
            sso = next(x for x in view["capabilities"] if x["capability"] == "saml_sso")
            assert sso["limit"] == 3  # 1 included on Business + 2 bought on the order
            hist = await catalogue_store.entitlement_history(c)
            assert len(hist) == 1 and str(hist[0]["source_order_id"]) == order["order_id"]
            mine = await orders.orders_of_tenant(c, tenant)
            assert [o["order_id"] for o in mine] == [order["order_id"]]
            assert await orders.orders_of_tenant(c, stranger) == []

        # a signed order is never repriced or rewritten
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.sales_order SET plan_code = 'SCALE' WHERE order_id = :o",
                "UPDATE commercial.sales_order_line SET net_price = 0 WHERE order_id = :o",
                "DELETE FROM commercial.sales_order WHERE order_id = :o",
            ):
                with pytest.raises(DBAPIError, match="never|immutable"):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"o": uuid.UUID(order["order_id"])})

    asyncio.run(run())


def test_only_activation_and_the_operator_plan_route_write_entitlements() -> None:
    """Every writer of the entitlement table is enumerated: an order's activation, and the operator plan-change
    route (pilots and support, recorded and flagged by revenue assurance when no order backs it)."""
    src = pathlib.Path(__file__).resolve().parents[2] / "src" / "zid_platform"
    writers = sorted(
        p.relative_to(src).as_posix()
        for p in src.rglob("*.py")
        if re.search(r"\bset_entitlement\(", p.read_text(encoding="utf-8"))
        and "infrastructure" not in p.parts
    )
    assert writers == [
        "modules/commercial/application/orders_service.py",
        "modules/operator_access/api/commercial_admin.py",
    ], writers


def test_orders_over_http(rt_url: str, owner_url: str) -> None:
    from operator_fixture import activate_grant, operators
    from zid_platform.modules.operator_access.domain.model import OperatorRole

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
    book = asyncio.run(setup(rt_url, owner_url, cco=bob, cfo=carol, founder=uid()))
    op_app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    with TestClient(op_app, base_url=ISS) as api:
        assert (
            api.post(
                "/v1/commercial/accounts", json={"legal_name": "X"}, headers=ops.bearer(alice)
            ).status_code
            == 403
        )
        sales = {"capability": "commercial.sales.manage", "resource_scope": "commercial:sales"}
        activate_grant(api, ops, alice, (bob,), **sales)
        activate_grant(api, ops, dave, (bob,), **sales)
        r = api.post(
            "/v1/commercial/accounts",
            json={"legal_name": "Acme Ltd", "country": "GB"},
            headers=ops.bearer(alice),
        )
        assert r.status_code == 201, r.text
        account = r.json()["account_id"]
        assert (
            api.post(
                f"/v1/commercial/accounts/{account}/tenants/{o.tenant}",
                headers=ops.bearer(alice),
            ).status_code
            == 200
        )
        body = {
            "account_id": account,
            "price_book_id": book,
            "region": "uk-1",
            "term_months": 12,
            "lines": [{"sku": "ZID-BASE", "plan": "SCALE", "quantity": "1"}],
        }
        r = api.post("/v1/commercial/quotes", json=body, headers=ops.bearer(alice))
        assert r.status_code == 201 and r.json()["status"] == "VALIDATED", r.text
        qid = r.json()["quote_id"]
        assert (
            api.get(f"/v1/commercial/quotes/{qid}", headers=ops.bearer(dave)).json()["digest"]
            == r.json()["digest"]
        )
        bad = {
            **body,
            "lines": [{"sku": "ZID-BASE", "plan": "SCALE", "quantity": "1", "discount_pct": "1.5"}],
        }
        assert (
            api.post("/v1/commercial/quotes", json=bad, headers=ops.bearer(alice)).status_code
            == 422
        )
        accept = {
            "quote_id": qid,
            "signer_ref": "Head of IT, Acme",
            "signed_document_digest": SIGNED,
            "effective_from": (datetime.now(UTC) + timedelta(minutes=1)).isoformat(),
        }
        r = api.post(
            f"/v1/commercial/tenants/{o.tenant}/orders", json=accept, headers=ops.bearer(alice)
        )
        assert r.status_code == 201, r.text
        oid = r.json()["order_id"]
        assert (
            api.post(f"/v1/commercial/orders/{oid}:activate", headers=ops.bearer(alice)).status_code
            == 403
        )
        r = api.post(f"/v1/commercial/orders/{oid}:activate", headers=ops.bearer(dave))
        assert r.status_code == 200 and r.json()["status"] == "ACTIVATED", r.text

    tenant_app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(tenant_app, base_url=ISS) as http:
        adm = o.bearer(o.owner)
        items = http.get("/v1/billing/orders", headers=adm).json()["items"]
        assert [i["order_id"] for i in items] == [oid] and items[0]["plan"] == "SCALE"
        ent = http.get(f"/v1/tenants/{o.tenant}/entitlements", headers=adm).json()
        assert ent["plan"] == "SCALE" and ent["source"] == "ORDER"
