"""Customer commitment approval: tiers, delegated limits, review of nonstandard terms and the activation check (ZID-COM-CL-004 section 04; CR-054).

The server works out a signed order's exposure; the customer Owner, a Billing Admin with a limit or a Procurement Approver signs by tier, never beyond a
limit, never twice; nonstandard terms wait for a Zoiko review mark; a revoked limit has no effect left when the order is activated; a customer sees only
its own requests.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import commitment_service as commitments
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.domain.commitment import CommitmentError
from zid_platform.modules.commercial.domain.orders import OrderError, QuoteLine
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
D = Decimal
SIGNED = "a" * 64


def uid() -> str:
    return str(uuid.uuid4())


async def book(rt_url: str, owner_url: str) -> str:
    async with engine(rt_url) as eng, eng.begin() as c:
        draft = await catalogue_store.create_draft(
            c, copy_of=SEED_BOOK, created_by=uid(), note="commit"
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
                "VALUES ('uk-1', true, 'PRR-uk-1', :b) ON CONFLICT DO NOTHING"
            ),
            {"b": uuid.uuid4()},
        )
    return str(draft["price_book_id"])


async def signed_order(
    rt_url: str, book_id: str, tenant: str, *, term: int = 12, capped: bool = True
) -> str:
    async with engine(rt_url) as eng, eng.begin() as c:
        account = await orders.create_account(
            c,
            legal_name="Acme Ltd",
            kind="DIRECT",
            country="GB",
            currency="USD",
            reseller_of=None,
            actor=uid(),
        )
        await orders.link_tenant(c, account, tenant)
        q = await orders.create_quote(
            c, account_id=account, price_book_id=book_id, region="uk-1", term_months=term, prepaid=False, prepaid_discount=D(0),
            liability_capped=capped, residency_commitment=None, pilot=False, actor=uid(), lines=[QuoteLine(1, "ZID-BASE", "BUSINESS", D(1))],
        )  # fmt: skip
        assert q["status"] == "VALIDATED", q
        o = await orders.accept(
            c, q["quote_id"], tenant_id=tenant, signer_ref="CFO of Acme", signed_document_digest=SIGNED,
            effective_from=datetime.now(UTC) + timedelta(minutes=1), amends_order_id=None, actor=uid(),
        )  # fmt: skip
        return str(o["order_id"])


def owners(monkeypatch: pytest.MonkeyPatch, *people: str) -> None:
    async def check(_c: Any, _tenant: str, principal: str) -> bool:
        return principal in people

    monkeypatch.setattr(commitments, "_owner_check", check)


def mode(monkeypatch: pytest.MonkeyPatch, enforcement: str) -> None:
    real = finance_policy()
    monkeypatch.setattr(
        orders,
        "finance_policy",
        lambda: replace(real, commitment=replace(real.commitment, enforcement=enforcement)),
    )


async def refused(call: Any, code: str) -> None:
    with pytest.raises(CommitmentError) as e:
        await call
    assert e.value.code == code, e.value.code


def test_exposure_tier_delegated_limits_and_the_activation_check(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tenant, other, owner, member, proc = uid(), uid(), uid(), uid(), uid()
    owners(monkeypatch, owner)

    async def run() -> None:
        b = await book(rt_url, owner_url)
        order = await signed_order(rt_url, b, tenant)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                req = await commitments.request(
                    c, tenant_id=tenant, order_id=order, requested_by=member
                )
                assert (
                    req["tier"] == "T2"
                    and req["exposure_usd_minor"] == 1499 * 12 * 100
                    and req["needs"] == ["OWNER_OR_PROCUREMENT_APPROVER"]
                )
                assert (
                    await commitments.request(
                        c, tenant_id=tenant, order_id=order, requested_by=member
                    )
                )["request_id"] == req["request_id"]
                rid, digest = req["request_id"], req["snapshot_hash"]

                def sign(who: str, h: str = digest) -> Any:
                    return commitments.decide(
                        c,
                        tenant_id=tenant,
                        request_id=rid,
                        approver=who,
                        decision="APPROVE",
                        presented_hash=h,
                        reason=None,
                        step_up_at=None,
                        environment="test",
                    )

                await refused(
                    sign(member), "NOT_AN_APPROVER"
                )  # an ordinary member, an unassigned Billing Admin, platform support
                await refused(sign(owner, "0" * 64), "SNAPSHOT_CHANGED")
                await refused(
                    commitments.delegate(
                        c,
                        tenant_id=tenant,
                        grantor=member,
                        principal=proc,
                        kind="PROCUREMENT_APPROVER",
                        cap_amount=D("20000"),
                    ),
                    "OWNER_ONLY",
                )
                await refused(
                    commitments.delegate(
                        c,
                        tenant_id=tenant,
                        grantor=owner,
                        principal=owner,
                        kind="PROCUREMENT_APPROVER",
                        cap_amount=D("20000"),
                    ),
                    "SELF_DELEGATION",
                )
                small = await commitments.delegate(
                    c,
                    tenant_id=tenant,
                    grantor=owner,
                    principal=proc,
                    kind="PROCUREMENT_APPROVER",
                    cap_amount=D("10000"),
                )
                await refused(
                    sign(proc), "OVER_SPEND_LIMIT"
                )  # $17,988.00 is above the $10,000.00 the Owner gave
                await commitments.revoke_delegate(
                    c, tenant_id=tenant, delegate_id=small["delegate_id"], by=owner
                )
                await refused(
                    sign(proc), "NOT_AN_APPROVER"
                )  # a revoked limit has no effect on the next call
                big = await commitments.delegate(
                    c,
                    tenant_id=tenant,
                    grantor=owner,
                    principal=proc,
                    kind="PROCUREMENT_APPROVER",
                    cap_amount=D("20000"),
                )
                done = await sign(proc)
                assert (
                    done["state"] == "APPROVED"
                    and done["approvals"][0]["authority"] == "PROCUREMENT_APPROVER"
                )
                await refused(sign(proc), "NOT_PENDING")

            async with tenant_transaction(eng, ctx(other)) as c:  # a customer sees only its own
                await refused(commitments.get(c, rid), "REQUEST_NOT_FOUND")
                assert await commitments.list_requests(c) == []
                await refused(
                    commitments.request(c, tenant_id=other, order_id=order, requested_by=member),
                    "ORDER_NOT_FOUND",
                )

            # activation: refused while the approval is not valid, allowed when it is
            mode(monkeypatch, "ENFORCED")
            activator = uid()
            async with eng.begin() as c:
                assert (
                    await commitments.activation_problem(
                        c, order_id=order, tenant_id=tenant, now=datetime.now(UTC)
                    )
                    is None
                )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await commitments.revoke_delegate(
                    c, tenant_id=tenant, delegate_id=big["delegate_id"], by=owner
                )
            async with eng.begin() as c:
                with pytest.raises(OrderError) as e:
                    await orders.activate(c, order, actor=activator)
                assert e.value.code == "COMMITMENT_REQUIRED" and e.value.detail == "AUTHORITY_LOST"

            other_order = await signed_order(rt_url, b, uid())
            async with eng.begin() as c:
                with pytest.raises(OrderError) as e:
                    await orders.activate(c, other_order, actor=activator)
                assert e.value.detail == "NO_COMMITMENT_REQUEST"
            mode(monkeypatch, "REPORT_ONLY")  # the owners' default: recorded, let through
            async with eng.begin() as c:
                assert (await orders.activate(c, other_order, actor=activator))[
                    "status"
                ] == "ACTIVATED"
                seen = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM commercial.change_log WHERE kind = 'COMMITMENT_UNAPPROVED_ACTIVATION' AND ref = :r"
                        ),
                        {"r": other_order},
                    )
                ).scalar_one()
                assert seen == 1

            mode(monkeypatch, "ENFORCED")  # an Owner's approval, then the order may go live
            fresh_tenant = uid()
            owners(monkeypatch, owner)
            ok_order = await signed_order(rt_url, b, fresh_tenant)
            async with tenant_transaction(eng, ctx(fresh_tenant)) as c:
                r2 = await commitments.request(
                    c, tenant_id=fresh_tenant, order_id=ok_order, requested_by=member
                )
                await commitments.decide(
                    c,
                    tenant_id=fresh_tenant,
                    request_id=r2["request_id"],
                    approver=owner,
                    decision="APPROVE",
                    presented_hash=r2["snapshot_hash"],
                    reason=None,
                    step_up_at=datetime.now(UTC),
                    environment="test",
                )
            async with eng.begin() as c:
                assert (await orders.activate(c, ok_order, actor=activator))[
                    "status"
                ] == "ACTIVATED"

    asyncio.run(run())


def test_the_top_tier_takes_two_people_and_nonstandard_terms_wait_for_review(
    rt_url: str, owner_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner, member, proc, controller = uid(), uid(), uid(), uid()
    owners(monkeypatch, owner)

    async def run() -> None:
        b = await book(rt_url, owner_url)
        async with engine(rt_url) as eng:
            big, odd, small = uid(), uid(), uid()
            big_order = await signed_order(rt_url, b, big, term=24)  # $35,976.00 over the term
            odd_order = await signed_order(
                rt_url, b, odd, term=48
            )  # longer than the policy's review term
            small_order = await signed_order(rt_url, b, small, term=1)
            async with tenant_transaction(eng, ctx(small)) as c:
                r = await commitments.request(
                    c, tenant_id=small, order_id=small_order, requested_by=member
                )
                assert r["tier"] == "T1" and r["needs"] == ["OWNER_OR_BILLING_SPEND"]
                await commitments.delegate(
                    c,
                    tenant_id=small,
                    grantor=owner,
                    principal=member,
                    kind="BILLING_SPEND",
                    cap_amount=D("2500"),
                )
                # a Billing Admin with a limit signs a small commitment, including one they raised themselves
                done = await commitments.decide(
                    c,
                    tenant_id=small,
                    request_id=r["request_id"],
                    approver=member,
                    decision="APPROVE",
                    presented_hash=r["snapshot_hash"],
                    reason=None,
                    step_up_at=None,
                    environment="test",
                )
                assert (
                    done["state"] == "APPROVED"
                    and done["approvals"][0]["authority"] == "BILLING_SPEND"
                )

            async with tenant_transaction(eng, ctx(big)) as c:
                r = await commitments.request(
                    c, tenant_id=big, order_id=big_order, requested_by=member
                )
                assert r["tier"] == "T3" and r["needs"] == ["OWNER", "PROCUREMENT_APPROVER"]
                rid, h = r["request_id"], r["snapshot_hash"]
                await commitments.delegate(
                    c,
                    tenant_id=big,
                    grantor=owner,
                    principal=proc,
                    kind="PROCUREMENT_APPROVER",
                    cap_amount=D("100000"),
                )

                def sign(who: str, decision: str = "APPROVE", reason: str | None = None) -> Any:
                    return commitments.decide(
                        c,
                        tenant_id=big,
                        request_id=rid,
                        approver=who,
                        decision=decision,
                        presented_hash=h,
                        reason=reason,
                        step_up_at=None,
                        environment="test",
                    )

                assert (await sign(owner))[
                    "state"
                ] == "PENDING_APPROVAL"  # one person is not enough above the top tier
                await refused(sign(owner), "ALREADY_SIGNED")
                assert (await sign(proc))["state"] == "APPROVED"

            async with tenant_transaction(eng, ctx(odd)) as c:
                r = await commitments.request(
                    c, tenant_id=odd, order_id=odd_order, requested_by=member
                )
                assert r["review_required"] is True and r["reviewed_at"] is None
                with pytest.raises(CommitmentError) as e:
                    await commitments.decide(
                        c,
                        tenant_id=odd,
                        request_id=r["request_id"],
                        approver=owner,
                        decision="APPROVE",
                        presented_hash=r["snapshot_hash"],
                        reason=None,
                        step_up_at=None,
                        environment="test",
                    )
                assert e.value.code == "REVIEW_REQUIRED"  # whatever the amount
                await refused(
                    commitments.mark_reviewed(
                        c, tenant_id=odd, request_id=r["request_id"], reviewer=controller
                    ),
                    "NOT_A_REVIEWER",
                )
            async with eng.begin() as c:
                await finance.appoint(
                    c,
                    principal=controller,
                    role="CONTROLLER",
                    appointed_by=uid(),
                    effective_from=datetime.now(UTC) - timedelta(days=1),
                )
            async with tenant_transaction(eng, ctx(odd)) as c:
                marked = await commitments.mark_reviewed(
                    c, tenant_id=odd, request_id=r["request_id"], reviewer=controller
                )
                assert marked["reviewed_at"]
                await refused(
                    commitments.mark_reviewed(
                        c, tenant_id=odd, request_id=r["request_id"], reviewer=controller
                    ),
                    "ALREADY_REVIEWED",
                )
                no = await commitments.decide(
                    c,
                    tenant_id=odd,
                    request_id=r["request_id"],
                    approver=owner,
                    decision="REJECT",
                    presented_hash=r["snapshot_hash"],
                    reason="terms not agreed",
                    step_up_at=None,
                    environment="test",
                )
                assert no["state"] == "REJECTED"

            # a request's facts and an approval are never edited
            async with tenant_transaction(eng, ctx(big)) as c:
                for sql in (
                    "UPDATE commercial.commitment_request SET exposure_usd_minor = 1 WHERE request_id = :r",
                    "UPDATE commercial.commitment_approval SET authority = 'OWNER' WHERE request_id = :r",
                ):
                    with pytest.raises(Exception, match="immutable|append-only|permission denied"):
                        async with c.begin_nested():
                            await c.execute(text(sql), {"r": uuid.UUID(rid)})

    asyncio.run(run())


def test_commitment_routes_over_http(rt_url: str, owner_url: str) -> None:
    from fastapi.testclient import TestClient
    from onboarding_fixture import AUD, ISS, onboard
    from test_role_matrix_pg import _seed_role
    from zid_platform.main import create_app
    from zid_platform.settings import Settings

    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    _seed_role(rt_url, o.tenant, o.second, "ADMIN", create=False)
    order = asyncio.run(signed_order(rt_url, asyncio.run(book(rt_url, owner_url)), o.tenant))
    app = create_app(
        Settings(database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test", cell_id="uk-1"),
        keyring=o.ring,
    )  # fmt: skip
    with TestClient(app) as api:
        own, adm = o.bearer(o.owner), o.bearer(o.second)
        path = "/v1/billing/commitment-requests"
        r = api.post(path, json={"order_id": order}, headers=adm)
        assert (
            r.status_code == 403 and r.json()["error"]["code"] == "BILLING_PERMISSION_REQUIRED"
        )  # an Administrator with no billing grant
        assert api.post(path, json={"order_id": uid()}, headers=own).status_code == 404
        assert (
            api.post(path, json={"order_id": order, "amount": "1"}, headers=own).status_code == 422
        )  # a caller never sends an amount
        r = api.post(path, json={"order_id": order}, headers=own)
        assert r.status_code == 201, r.text
        req = r.json()
        assert req["tier"] == "T2" and req["state"] == "PENDING_APPROVAL"
        assert (
            api.get(f"{path}/{req['request_id']}", headers=own).json()["snapshot_hash"]
            == req["snapshot_hash"]
        )
        assert [i["request_id"] for i in api.get(path, headers=own).json()["items"]] == [
            req["request_id"]
        ]

        deleg = "/v1/billing/commitment-delegates"
        body = {"principal_id": o.second, "kind": "PROCUREMENT_APPROVER", "cap_usd": "20000.00"}
        r = api.post(deleg, json=body, headers=adm)
        assert r.status_code == 403  # only the Owner delegates
        r = api.post(deleg, json={**body, "principal_id": o.owner}, headers=own)
        assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_DELEGATION"
        r = api.post(deleg, json=body, headers=own)
        assert r.status_code == 201, r.text
        did = r.json()["delegate_id"]
        assert [d["kind"] for d in api.get(deleg, headers=own).json()["items"]] == [
            "PROCUREMENT_APPROVER"
        ]

        decide = f"{path}/{req['request_id']}"
        bad = api.post(f"{decide}:approve", json={"snapshot_hash": "0" * 64}, headers=own)
        assert bad.status_code == 409 and bad.json()["error"]["code"] == "SNAPSHOT_CHANGED"
        r = api.post(f"{decide}:approve", json={"snapshot_hash": req["snapshot_hash"]}, headers=adm)
        assert r.status_code == 200 and r.json()["state"] == "APPROVED", (
            r.text
        )  # the delegate, whose $20,000 limit covers $17,988
        assert r.json()["approvals"][0]["authority"] == "PROCUREMENT_APPROVER"
        assert api.post(f"{deleg}/{did}:revoke", headers=own).status_code == 200
        assert (
            api.post(f"{decide}:cancel", headers=adm).status_code == 403
        )  # only the requester cancels
        assert api.post(f"{decide}:cancel", headers=own).json()["state"] == "CANCELED"
