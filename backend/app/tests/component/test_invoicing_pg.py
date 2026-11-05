"""Invoices, credit notes, payments and the billing state machine (ZID-COM-001 §11, §30, §31; CR-027).

From a rated month to an issued invoice: a second operator issues it, tax comes from a port (or an explicit
zero-tax override), the document's digest is stored, numbers are gap-free across tenants and chained; an
issued invoice never changes, a dispute is a credit note, a payment never charges twice; dunning follows
only the configured policy and restriction never touches a security path.
"""

from __future__ import annotations

import asyncio
import hashlib
import pathlib
import re
import uuid
from collections.abc import Sequence
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
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import rating_service, recorder, service
from zid_platform.modules.commercial.application.adapters import (
    NoTaxConfigured,
    RecordingPayments,
    TaxableLine,
    TaxLine,
)
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.invoicing import InvoicingError, verify_chain
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.settings import Settings

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
NOW = datetime.now(UTC)
LAST = b.window_month(NOW.replace(day=1) - timedelta(days=1))
L_START = datetime(LAST.year, LAST.month, 1, tzinfo=UTC)
ENTITY = f"T{uuid.uuid4().hex[:8].upper()}"  # this test's own invoice series


class TwentyPercent:
    """A stand-in tax engine: 20% on every positive line."""

    async def lines_for(
        self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
    ) -> dict[int, TaxLine] | None:
        return {
            ln.seq: TaxLine("VAT20", (ln.net * Decimal("0.20")).quantize(Decimal("0.01")))
            for ln in lines
            if ln.net > 0
        }


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
        commercial_issuing_entity=ENTITY,
        **kw,
    )


async def rated_month(rt_url: str, tenant: str, mah: int = 68_000) -> None:
    """A Business tenant with one month of usage, frozen and rated."""
    async with engine(rt_url) as eng:
        async with eng.begin() as c:
            draft = await catalogue_store.create_draft(
                c, copy_of=SEED_BOOK, created_by=uid(), note="inv"
            )
            await catalogue_store.approve(
                c,
                draft["price_book_id"],
                approver=uid(),
                effective_from=L_START - timedelta(days=1),
                now=datetime.now(UTC),
            )
        async with tenant_transaction(eng, ctx(tenant)) as c:
            await catalogue_store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="BUSINESS",
                price_book_id=draft["price_book_id"],
                actor=None,
                now=L_START - timedelta(days=3),
            )
            await recorder.record(
                c,
                tenant_id=tenant,
                meter="MAH",
                key=f"mah:{tenant}",
                at=L_START + timedelta(days=4),
                quantity=mah,
            )
            await recorder.freeze_month(c, tenant, LAST)
            await rating_service.run(c, tenant_id=tenant, month=LAST, connections={"saml_sso": 1})


def test_an_invoice_is_issued_by_a_second_person_taxed_by_the_port_and_never_changes(
    rt_url: str, owner_url: str
) -> None:
    tenant, other, drafter, issuer = uid(), uid(), uid(), uid()

    async def run() -> None:
        await rated_month(rt_url, tenant)
        await rated_month(rt_url, other, mah=10_000)
        # other test modules issue into this same series (they import ENTITY), so count only what this test adds
        async with engine(rt_url) as eng, eng.begin() as c:
            baseline = len(await inv.chain_entries(c, ENTITY))
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                d = await inv.create_draft(
                    c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
                )
                assert d["status"] == "DRAFT" and d["number"] is None and d["subtotal"] == "1769"
                again = await inv.create_draft(
                    c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
                )
                assert again["invoice_id"] == d["invoice_id"]  # one live invoice per rating run
                iid = d["invoice_id"]
                for kw, code in (
                    (
                        {"issuer": drafter, "tax": TwentyPercent(), "allow_zero_tax": False},
                        "INVOICE_SELF_ISSUE",
                    ),
                    (
                        {"issuer": issuer, "tax": NoTaxConfigured(), "allow_zero_tax": False},
                        "TAX_NOT_CONFIGURED",
                    ),
                ):
                    with pytest.raises(InvoicingError) as e:
                        async with c.begin_nested():
                            await inv.issue(c, iid, payment_terms_days=30, region="uk-1", **kw)  # type: ignore[arg-type]
                    assert e.value.code == code
                out = await inv.issue(
                    c,
                    iid,
                    issuer=issuer,
                    tax=TwentyPercent(),
                    payment_terms_days=30,
                    allow_zero_tax=False,
                    region="uk-1",
                )
                assert out["status"] == "ISSUED" and out["tax_status"] == "CONFIGURED"
                assert (
                    out["subtotal"] == "1769"
                    and out["tax_total"] == "353.8"
                    and out["gross"] == "2122.8"
                )
                assert all(ln["tax_code"] == "VAT20" for ln in out["lines"])
                document = await inv.document(c, iid)
                assert hashlib.sha256(document.encode()).hexdigest() == out["document_digest"]
                assert document == await inv.document(
                    c, iid
                )  # re-rendering proves the stored document
                assert tenant not in document  # the customer appears by alias only
                first_number = out["number"]

            async with tenant_transaction(eng, ctx(other)) as c:
                d2 = await inv.create_draft(
                    c, tenant_id=other, month=LAST, issuing_entity=ENTITY, actor=drafter
                )
                out2 = await inv.issue(
                    c,
                    d2["invoice_id"],
                    issuer=issuer,
                    tax=NoTaxConfigured(),
                    payment_terms_days=14,
                    allow_zero_tax=True,
                    region="uk-1",
                )
                assert out2["tax_status"] == "ZERO_TAX_OVERRIDE" and out2["tax_total"] == "0"
                n1, n2 = (int(x.rsplit("-", 1)[1]) for x in (first_number, out2["number"]))
                assert n2 == n1 + 1  # gap-free across tenants of one issuing entity

            # a dispute is a credit note; the invoice itself never moves
            async with tenant_transaction(eng, ctx(tenant)) as c:
                cn = await inv.credit_note(
                    c,
                    iid,
                    amount=Decimal("122.80"),
                    reason_code="DISPUTE",
                    reverses_line=2,
                    issuer=issuer,
                )
                assert cn["amount"] == "122.8"
                with pytest.raises(InvoicingError) as e:
                    await inv.credit_note(
                        c,
                        iid,
                        amount=Decimal("2000.01"),
                        reason_code="DISPUTE",
                        reverses_line=None,
                        issuer=issuer,
                    )
                assert e.value.code == "CREDIT_NOTE_EXCEEDS_INVOICE"
                v = await inv.view(c, iid)
                assert (
                    v["gross"] == "2122.8"
                    and v["credited"] == "122.8"
                    and v["outstanding"] == "2000"
                )

                # a payment never charges twice for the same key; a declined one is recorded
                pay = RecordingPayments(decline_next=1)
                failed = await inv.collect(c, iid, payments=pay, idempotency_key="key-decline-1")
                assert failed["status"] == "FAILED" and failed["failure_code"] == "CARD_DECLINED"
                ok = await inv.collect(c, iid, payments=pay, idempotency_key="key-pay-0001")
                assert ok["status"] == "CAPTURED" and ok["amount"] == "2000"
                replay = await inv.collect(c, iid, payments=pay, idempotency_key="key-pay-0001")
                assert replay["replayed"] and len(pay.captured) == 1
                with pytest.raises(InvoicingError) as e:
                    await inv.collect(c, iid, payments=pay, idempotency_key="key-pay-0002")
                assert e.value.code == "INVOICE_SETTLED"
                assert (await inv.view(c, iid))["outstanding"] == "0"

        # the chain over this issuing entity is whole: two invoices and one credit note
        async with engine(rt_url) as eng, eng.begin() as c:
            entries = await inv.chain_entries(c, ENTITY)
        assert len(entries) - baseline == 3 and verify_chain(entries) is None

        # the database refuses to edit an issued invoice, its lines, the chain or a credit note
        async with engine(owner_url) as eng:
            for sql, match in (
                ("UPDATE commercial.invoice SET gross = 1 WHERE invoice_id = :i", "immutable"),
                ("DELETE FROM commercial.invoice WHERE invoice_id = :i", "never deleted"),
                ("UPDATE commercial.invoice_line SET net = 0 WHERE invoice_id = :i", "immutable"),
                ("DELETE FROM commercial.credit_note WHERE invoice_id = :i", "immutable"),
                (
                    "UPDATE commercial.payment_attempt SET amount = 1 WHERE invoice_id = :i",
                    "immutable",
                ),
            ):
                with pytest.raises(DBAPIError, match=match):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"i": uuid.UUID(iid)})
            with pytest.raises(DBAPIError, match="immutable"):
                async with eng.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE commercial.invoice_chain SET chain_hash = repeat('0', 64) WHERE issuing_entity = :e"
                        ),
                        {"e": ENTITY},
                    )

    asyncio.run(run())


def test_dunning_follows_only_the_policy_restricts_paid_extras_and_paying_restores(
    rt_url: str, owner_url: str
) -> None:
    tenant, drafter, issuer = uid(), uid(), uid()

    async def run() -> None:
        await rated_month(rt_url, tenant)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            d = await inv.create_draft(
                c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
            )
            await inv.issue(
                c,
                d["invoice_id"],
                issuer=issuer,
                tax=NoTaxConfigured(),
                payment_terms_days=0,
                allow_zero_tax=True,
                region="uk-1",
            )
            assert (await inv.billing_state(c))["state"] == "ACTIVE"
            later = datetime.now(UTC) + timedelta(days=20)
            assert (
                await inv.run_dunning(c, tenant, later) == []
            )  # no policy configured: nobody moves

        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("DELETE FROM commercial.dunning_policy"))
            await c.execute(
                text(
                    "INSERT INTO commercial.dunning_policy (days_after_due, target_state) VALUES "
                    "(1, 'PAST_DUE'), (15, 'RESTRICTED'), (30, 'SUSPENDED')"
                )
            )
        try:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
                moves = await inv.run_dunning(c, tenant, later)
                assert [(m["from"], m["to"], m["reason"]) for m in moves] == [
                    ("ACTIVE", "PAST_DUE", "PAYMENT_OVERDUE"),
                    ("PAST_DUE", "RESTRICTED", "DUNNING"),
                ]
                assert await inv.run_dunning(c, tenant, later) == []  # idempotent at the same age
                st = await inv.billing_state(c)
                assert st["state"] == "RESTRICTED" and st["restricted"] == ["new_paid_extras"]
                assert "sign_in" in st["never_restricted"]

                # new paid extras stop when enforcing; the shadow mode only reports it
                r = await service.entitled(c, "saml_sso", used=0, enforcement="enforce")
                assert not r.allowed and r.reason == "BILLING_RESTRICTED"
                assert (await service.entitled(c, "saml_sso", used=0, enforcement="shadow")).allowed

                with pytest.raises(InvoicingError) as e:
                    await inv.transition(c, tenant, "CLOSED", "OFFBOARDED", actor=None)
                assert e.value.code == "BILLING_TRANSITION_INVALID"

                # a safety hold is separate: paying returns the billing state, never the hold
                await inv.set_safety_hold(c, tenant, on=True, reason="acceptable-use review")
                paid = await inv.collect(
                    c,
                    d["invoice_id"],
                    payments=RecordingPayments(),
                    idempotency_key="key-dun-001",
                    now=later,
                )
                assert paid["state_change"]["to"] == "ACTIVE"
                st = await inv.billing_state(c)
                assert (
                    st["state"] == "ACTIVE"
                    and st["safety_hold"]
                    and "new_entities" in st["restricted"]
                )
                await inv.set_safety_hold(c, tenant, on=False, reason=None)
                assert (await inv.billing_state(c))["restricted"] == []
                hist = (
                    await c.execute(
                        text(
                            "SELECT from_state, to_state FROM commercial.billing_state_history ORDER BY at"
                        )
                    )
                ).all()
                assert [tuple(h) for h in hist] == [
                    ("ACTIVE", "PAST_DUE"),
                    ("PAST_DUE", "RESTRICTED"),
                    ("RESTRICTED", "ACTIVE"),
                ]
        finally:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(text("DELETE FROM commercial.dunning_policy"))

    asyncio.run(run())


def test_no_security_path_can_reach_the_billing_gate() -> None:
    """Sign-in, tokens, revocation, recovery, signals and decisions never consult entitlements or billing state."""
    root = pathlib.Path(__file__).resolve().parents[2] / "src" / "zid_platform" / "modules"
    security = [
        "session",
        "authentication",
        "signals",
        "decision",
        "policy_runtime",
        "relationship",
    ]
    gate = re.compile(r"\b(require_entitlement|entitled|billing_state|restricted)\s*\(")
    offenders = [
        str(p.relative_to(root))
        for m in security
        for p in (root / m).rglob("*.py")
        if gate.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


def test_invoices_over_http_for_operators_and_the_tenant(rt_url: str) -> None:
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
    asyncio.run(rated_month(rt_url, o.tenant))
    month = f"{LAST:%Y-%m}"
    op_app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    tenant_app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(op_app, base_url=ISS) as api:
        base = f"/v1/commercial/tenants/{o.tenant}"
        assert (
            api.post(
                f"{base}/invoices", json={"month": month}, headers=ops.bearer(alice)
            ).status_code
            == 403
        )
        scope = {
            "capability": "commercial.billing.manage",
            "resource_scope": f"tenant:{o.tenant}",
            "target_tenant_id": o.tenant,
        }
        activate_grant(api, ops, alice, (bob, carol), **scope)
        activate_grant(api, ops, dave, (bob, carol), **scope)
        r = api.post(f"{base}/invoices", json={"month": month}, headers=ops.bearer(alice))
        assert r.status_code == 201, r.text
        iid = r.json()["invoice_id"]
        issue = f"{base}/invoices/{iid}:issue"
        assert (
            api.post(issue, json={"payment_terms_days": 30}, headers=ops.bearer(alice)).status_code
            == 403
        )  # self
        r = api.post(issue, json={"payment_terms_days": 30}, headers=ops.bearer(dave))
        assert r.status_code == 409 and r.json()["error"]["code"] == "TAX_NOT_CONFIGURED"
        r = api.post(
            issue, json={"payment_terms_days": 30, "allow_zero_tax": True}, headers=ops.bearer(dave)
        )
        assert r.status_code == 200 and r.json()["status"] == "ISSUED", r.text
        digest = r.json()["document_digest"]
        assert api.post(f"{base}/invoices/{iid}:void", headers=ops.bearer(dave)).status_code == 409
        r = api.post(
            f"{base}/invoices/{iid}/credit-notes",
            json={"amount": "10.00", "reason_code": "GOODWILL"},
            headers=ops.bearer(dave),
        )
        assert r.status_code == 201, r.text
        r = api.post(
            f"{base}/invoices/{iid}:collect",
            json={"idempotency_key": "http-pay-0001"},
            headers=ops.bearer(dave),
        )
        assert r.status_code == 200 and r.json()["status"] == "CAPTURED", r.text
        r = api.post(
            f"{base}/billing-state",
            json={"to": "CLOSED", "reason_code": "OFFBOARDED"},
            headers=ops.bearer(dave),
        )
        assert r.status_code == 409
        r = api.get("/v1/commercial/invoice-chain:verify", headers=ops.bearer(alice))
        assert r.status_code == 200 and r.json()["intact"] and r.json()["entries"] >= 2

    with TestClient(tenant_app, base_url=ISS) as http:
        adm = o.bearer(o.owner)
        items = http.get("/v1/invoices", headers=adm).json()["items"]
        assert [i["invoice_id"] for i in items] == [iid]
        one = http.get(f"/v1/invoices/{iid}", headers=adm).json()
        assert one["outstanding"] == "0" and one["credited"] == "10"
        doc = http.get(f"/v1/invoices/{iid}/document", headers=adm)
        assert doc.status_code == 200 and hashlib.sha256(doc.content).hexdigest() == digest
        st = http.get("/v1/billing/state", headers=adm).json()
        assert st["state"] == "ACTIVE" and "data_export" in st["never_restricted"]
        assert http.get(f"/v1/invoices/{uid()}", headers=adm).status_code == 404
        assert http.get("/v1/invoices").status_code == 401


def test_the_buyers_country_reaches_the_tax_engine_and_its_customer_reaches_the_processor(
    rt_url: str, owner_url: str
) -> None:
    """Tax is asked about the account's country and payment is collected against the account's processor
    customer, through the real adapters over a recorded Stripe (ZID-COM-001 §11; CR-027)."""
    import httpx
    from zid_platform.modules.commercial.application import orders_service
    from zid_platform.modules.commercial.application.adapters import StripePayments

    tenant, drafter, issuer = uid(), uid(), uid()
    asked: list[str | None] = []
    charged: list[dict[str, str]] = []

    class CountryRecorder:
        async def lines_for(
            self, *, currency: str, region: str, country: str | None, lines: Sequence[TaxableLine]
        ) -> dict[int, TaxLine] | None:
            asked.append(country)
            return {ln.seq: TaxLine("VAT0", Decimal(0)) for ln in lines if ln.net > 0}

    def stripe_handler(req: httpx.Request) -> httpx.Response:
        from urllib.parse import parse_qs

        if req.method == "GET":  # the customer lookup that names the saved payment method
            return httpx.Response(
                200,
                json={
                    "id": "cus_test",
                    "invoice_settings": {"default_payment_method": "pm_card_test"},
                },
            )
        charged.append({k: v[0] for k, v in parse_qs(req.content.decode()).items()})
        return httpx.Response(200, json={"id": "pi_test_1", "status": "succeeded"})

    payments = StripePayments(
        "sk_test_pg", base_url="https://stripe.test", transport=httpx.MockTransport(stripe_handler)
    )

    async def run() -> None:
        await rated_month(rt_url, tenant)
        async with engine(rt_url) as eng:
            async with eng.begin() as c:
                account = await orders_service.create_account(
                    c,
                    legal_name="Acme Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=drafter,
                )
                await orders_service.link_tenant(c, account, tenant)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                d = await inv.create_draft(
                    c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=drafter
                )
                done = await inv.issue(
                    c,
                    d["invoice_id"],
                    issuer=issuer,
                    tax=CountryRecorder(),
                    payment_terms_days=14,
                    allow_zero_tax=False,
                    region="uk-1",
                )
                assert asked == ["GB"]  # the account's country, not the cell and not a guess

                # no processor customer yet: the attempt fails cleanly and nothing is sent to the processor
                first = await inv.collect(
                    c, d["invoice_id"], payments=payments, idempotency_key="collect-first-0001"
                )
                assert first["status"] == "FAILED" and first["failure_code"] == "NO_PAYMENT_METHOD"
                assert charged == []

            async with eng.begin() as c:
                await inv.set_payment_customer(
                    c, account, provider="stripe", customer_ref="cus_PgTest12345", actor=drafter
                )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                ok = await inv.collect(
                    c, d["invoice_id"], payments=payments, idempotency_key="collect-second-002"
                )
                assert ok["status"] == "CAPTURED" and ok["failure_code"] is None
                again = await inv.collect(
                    c, d["invoice_id"], payments=payments, idempotency_key="collect-second-002"
                )
                assert again["replayed"] is True  # the same key never reaches the processor twice
        assert len(charged) == 1
        assert charged[0]["customer"] == "cus_PgTest12345"
        assert Decimal(charged[0]["amount"]) == Decimal(done["gross"]) * 100
        assert done["tax_status"] == "CONFIGURED"

    asyncio.run(run())
