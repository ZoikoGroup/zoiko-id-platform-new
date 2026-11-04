"""Discount authority on real records (ZID-COM-CL-003; ZID-COM-001 section 12).

A discount needs a person who holds the band's role today, not the author; an approval binds the exact quote and is spent once; a revoked appointment or an
expired quote approves nothing; approvals can never be edited; and a signed quote becomes an order once. Stacked promotions are judged together."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from conftest import engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from test_orders_pg import SIGNED, D, setup, uid
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.domain.orders import OrderError, QuoteLine


def test_who_may_release_which_discount_and_what_ends_an_approval(rt_url: str, owner_url: str) -> None:
    """CL03-T05 (an unappointed AE cannot release 1%, an appointed AE cannot release 6% without the CCO), T03 (stacked promotions need their combined
    band), T09 (a revoked appointment, an expired quote and a spent approval fail closed), T14 (approvals are append-only and name the person and the
    digest), T16 (only a complete approval set lets a quote be signed, and only once)."""
    author, ae, unappointed, cco, cfo, founder, tenant = uid(), uid(), uid(), uid(), uid(), uid(), uid()

    async def run() -> None:
        book = await setup(rt_url, owner_url, cco=cco, cfo=cfo, founder=founder)
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("INSERT INTO commercial.commercial_role_holder (principal_id, role, granted_by) VALUES (:p, 'AE', :g) ON CONFLICT DO NOTHING"),
                {"p": uuid.UUID(ae), "g": uuid.uuid4()},
            )
        async with engine(rt_url) as eng, eng.begin() as c:
            account = await orders.create_account(c, legal_name="Acme Ltd", kind="DIRECT", country="GB", currency="USD", reseller_of=None, actor=author)
            await orders.link_tenant(c, account, tenant)
            base = {
                "account_id": account, "price_book_id": book, "region": "uk-1", "term_months": 12, "prepaid": False, "prepaid_discount": D(0),
                "liability_capped": True, "residency_commitment": None, "pilot": False, "actor": author,
            }  # fmt: skip

            def quote(line: QuoteLine) -> object:
                return orders.create_quote(c, lines=[line], **base)

            # 1%: the account executive's band. Somebody who is not appointed to it cannot release it; an appointed one can
            one = await quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.01")))
            assert one["approvers_required"] == ["AE"]  # type: ignore[index]
            with pytest.raises(OrderError) as e:
                await orders.approve_quote(c, one["quote_id"], role="AE", approver=unappointed)  # type: ignore[index]
            assert e.value.code == "APPROVER_ROLE_MISSING"
            assert (await orders.approve_quote(c, one["quote_id"], role="AE", approver=ae))["status"] == "APPROVED"  # type: ignore[index]

            # 6%: the CCO's band. The appointed AE cannot release it, as AE or as CCO
            six = await quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.06")))
            assert six["approvers_required"] == ["CCO"]  # type: ignore[index]
            for role, code in (("AE", "APPROVAL_ROLE_NOT_REQUIRED"), ("CCO", "APPROVER_ROLE_MISSING")):
                with pytest.raises(OrderError) as e:
                    await orders.approve_quote(c, six["quote_id"], role=role, approver=ae)  # type: ignore[index]
                assert e.value.code == code
            assert six["status"] == "NEEDS_APPROVAL"  # type: ignore[index]

            # two 5% promotions on one line are 9.75%: the CCO's band, though each alone is the account executive's
            stacked = await quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.05"), (D("0.05"),)))
            assert stacked["approvers_required"] == ["CCO"] and stacked["lines"][0]["promotions"] == ["0.05"]  # type: ignore[index]
            with pytest.raises(OrderError) as e:
                await orders.approve_quote(c, stacked["quote_id"], role="CCO", approver=ae)  # type: ignore[index]
            assert e.value.code == "APPROVER_ROLE_MISSING"

            # an approval is spent once: the quote is no longer waiting, so a second approval (a replay) is refused
            ok = await orders.approve_quote(c, six["quote_id"], role="CCO", approver=cco)  # type: ignore[index]
            assert ok["status"] == "APPROVED"
            with pytest.raises(OrderError) as e:
                await orders.approve_quote(c, six["quote_id"], role="CCO", approver=cco)  # type: ignore[index]
            assert e.value.code == "QUOTE_NOT_AWAITING_APPROVAL"

            # a quote that has expired approves nothing
            late = await quote(QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.08")))
            with pytest.raises(OrderError) as e:
                await orders.approve_quote(c, late["quote_id"], role="CCO", approver=cco, now=datetime.now(UTC) + timedelta(days=400))  # type: ignore[index]
            assert e.value.code == "QUOTE_EXPIRED"

        # the appointment is taken away: the same person can no longer release the band, on the next attempt
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(text("UPDATE commercial.commercial_role_holder SET revoked_at = now() WHERE principal_id = :p AND role = 'CCO'"), {"p": uuid.UUID(cco)})
        async with engine(rt_url) as eng, eng.begin() as c:
            again = await orders.create_quote(c, lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.09"))], **base)
            with pytest.raises(OrderError) as e:
                await orders.approve_quote(c, again["quote_id"], role="CCO", approver=cco)
            assert e.value.code == "APPROVER_ROLE_MISSING"

            # nothing that was recorded can be edited, and each row names the person, the role and the exact digest that was approved
            rows = (await c.execute(text("SELECT role, approver, digest FROM commercial.quote_approval WHERE quote_id = :q"), {"q": uuid.UUID(ok["quote_id"])})).all()
            assert [(r[0], str(r[1])) for r in rows] == [("CCO", cco)] and rows[0][2] == ok["digest"]

            # only a complete approval set lets a quote be signed, and the signature is spent once
            eff = datetime.now(UTC) + timedelta(minutes=1)
            waiting = await orders.create_quote(c, lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1), D("0.07"))], **base)
            args = {"tenant_id": tenant, "signer_ref": "CFO of Acme", "signed_document_digest": SIGNED, "effective_from": eff, "amends_order_id": None, "actor": author}
            with pytest.raises(OrderError) as e:
                await orders.accept(c, waiting["quote_id"], **args)
            assert e.value.code == "QUOTE_NOT_ACCEPTABLE"
            signed = await orders.accept(c, ok["quote_id"], **args)
            assert signed["status"] == "SIGNED"
            with pytest.raises(OrderError) as e:
                await orders.accept(c, ok["quote_id"], **args)
            assert e.value.code == "QUOTE_NOT_ACCEPTABLE"

        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.quote_approval SET approver = gen_random_uuid()",
                "DELETE FROM commercial.quote_approval",
            ):
                with pytest.raises(DBAPIError, match="never|immutable"):
                    async with eng.begin() as c:
                        await c.execute(text(sql))
            # the quote's lines carry their promotions and are never edited either
            with pytest.raises(DBAPIError, match="never|immutable"):
                async with eng.begin() as c:
                    await c.execute(text("UPDATE commercial.quote_line SET promotions = '{}'"))

    asyncio.run(run())

