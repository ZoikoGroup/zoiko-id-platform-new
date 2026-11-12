"""Finance authority: appointments, the signed policy, tiers, separation of duties and one execution (ZID-COM-CL-003, ZID-COM-CL-004; CR-054).

A request is placed in a tier by its USD exposure (credits for one organization add up for 90 days), each role the tier names signs the exact
request, each a different person and never the one who asked, and a separate person runs it once. A deployed environment acts on no policy the Founder
and the CFO have not signed; a signer who loses the appointment voids the approval; lapsed requests and approvals are never run.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import ctx, engine
from finance_people import PEOPLE_TENANT, appoint_person
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import finance_service as finance
from zid_platform.modules.commercial.application import invoicing_service as inv
from zid_platform.modules.commercial.application import orders_service as orders
from zid_platform.modules.commercial.application import rating_service, recorder
from zid_platform.modules.commercial.application.adapters import NoTaxConfigured
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.finance import FinanceError, Kind
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.modules.principal.domain.principal import PrincipalStatus, PrincipalType

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"
NOW = datetime.now(UTC)
LAST = b.window_month(NOW.replace(day=1) - timedelta(days=1))
L_START = datetime(LAST.year, LAST.month, 1, tzinfo=UTC)
ENTITY = f"F{uuid.uuid4().hex[:8].upper()}"
D = Decimal


def uid() -> str:
    return str(uuid.uuid4())


async def hold(eng: Any, person: str, *roles: str, start: datetime | None = None) -> None:
    for role in roles:
        await appoint_person(eng, person, role, start=start or NOW - timedelta(days=200))


async def cast(eng: Any, *roles: str) -> dict[str, str]:
    """A new person for each role."""
    people = {role: uid() for role in roles}
    for role, person in people.items():
        await hold(eng, person, role)
    return people


async def billed(c: Any, tenant: str) -> None:
    """The organization has a commercial account, as every customer being invoiced does."""
    if await inv.account_of_tenant(c, tenant) is None:
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


async def pilot(
    c: Any,
    requester: str,
    tenant: str,
    amount: str,
    *,
    now: datetime | None = None,
    days: int = 30,
    env: str = "test",
    currency: str = "USD",
) -> dict[str, Any]:
    at = now or NOW
    await billed(c, tenant)
    return await finance.open_request(
        c, kind=Kind.PILOT_CREDIT, tenant_id=tenant, invoice_id=None, amount=D(amount), currency=currency, category="PILOT", reason="design partner pilot", program_ref="PILOT-2026-01", sponsor="Head of IT",
        requested_by=requester, ends_at=at + timedelta(days=days), environment=env, now=now,
    )  # fmt: skip


async def sign(
    c: Any,
    req: dict[str, Any],
    role: str,
    who: str,
    *,
    now: datetime | None = None,
    env: str = "test",
    h: str | None = None,
) -> dict[str, Any]:
    return await finance.decide(
        c, req["request_id"], role=role, approver=who, decision="APPROVE", presented_hash=h or req["snapshot_hash"], reason=None, environment=env, now=now
    )  # fmt: skip


async def refused(c: Any, call: Any, code: str) -> None:
    with pytest.raises(FinanceError) as e:
        async with c.begin_nested():
            await call
    assert e.value.code == code, e.value.code


def test_tiers_are_placed_by_exposure_rounded_up_and_credits_add_up(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS")
            async with eng.begin() as c:
                edge = await pilot(c, p["REVOPS"], uid(), "1000.00")
                over = await pilot(c, p["REVOPS"], uid(), "1000.01")
                assert edge["required_roles"] == ["CCO", "FINANCE_OPS_LEAD"] and over[
                    "required_roles"
                ] == ["CCO", "CONTROLLER"]
                assert (await pilot(c, p["REVOPS"], uid(), "10000.01"))["required_roles"] == [
                    "CFO",
                    "FOUNDER",
                ]

                t = uid()  # two credits for one organization within 90 days are one exposure: no splitting to stay under a limit
                first = await pilot(c, p["REVOPS"], t, "600.00")
                second = await pilot(c, p["REVOPS"], t, "600.00")
                assert first["required_roles"] == ["CCO", "FINANCE_OPS_LEAD"]
                assert second["exposure_usd_minor"] == 120_000 and second["required_roles"] == [
                    "CCO",
                    "CONTROLLER",
                ]
                old = await pilot(c, p["REVOPS"], uid(), "900.00", now=NOW - timedelta(days=100))
                assert old["exposure_usd_minor"] == 90_000

                await refused(
                    c, pilot(c, p["REVOPS"], uid(), "10.00", currency="EUR"), "FX_UNPINNED"
                )
                await refused(
                    c, pilot(c, p["REVOPS"], uid(), "10.00", days=91), "PILOT_PERIOD_INVALID"
                )
                await refused(
                    c, pilot(c, p["REVOPS"], uid(), "10.00", days=-1), "PILOT_PERIOD_INVALID"
                )
                await refused(
                    c, pilot(c, uid(), uid(), "10.00"), "NOT_A_REQUESTER"
                )  # nobody appointed
                unbilled = finance.open_request(
                    c, kind=Kind.PILOT_CREDIT, tenant_id=uid(), invoice_id=None, amount=D("10.00"), currency="USD", category="PILOT",
                    reason="x", requested_by=p["REVOPS"], ends_at=NOW + timedelta(days=5), environment="test",
                )  # fmt: skip
                await refused(
                    c, unbilled, "ACCOUNT_REQUIRED"
                )  # no commercial account: nothing to credit

    asyncio.run(run())


def test_separation_of_duties_one_execution_and_nothing_to_replay(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            dual = uid()  # one person holding two roles, and also able to run
            await hold(eng, dual, "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                req = await pilot(c, p["REVOPS"], uid(), "500.00")
                assert req["state"] == "PENDING_APPROVAL" and req["policy_version"].startswith(
                    "ZID-COM-CL-004"
                )
                await refused(c, sign(c, req, "CCO", dual, h="0" * 64), "SNAPSHOT_CHANGED")
                await refused(c, sign(c, req, "CFO", dual), "ROLE_NOT_REQUIRED")
                await refused(c, sign(c, req, "CCO", uid()), "NOT_APPOINTED")
                await refused(
                    c, sign(c, req, "CCO", p["REVOPS"]), "SELF_APPROVAL"
                )  # the person who asked
                await refused(
                    c,
                    finance.decide(
                        c,
                        req["request_id"],
                        role="CCO",
                        approver=dual,
                        decision="REJECT",
                        presented_hash=req["snapshot_hash"],
                        reason=" ",
                        environment="test",
                    ),
                    "REASON_REQUIRED",
                )

                half = await sign(c, req, "CCO", dual)
                assert half["state"] == "PENDING_APPROVAL" and [
                    a["role"] for a in half["approvals"]
                ] == ["CCO"]
                await refused(c, sign(c, req, "CCO", p["FINANCE_OPS_LEAD"]), "ROLE_ALREADY_DECIDED")
                await refused(c, sign(c, req, "FINANCE_OPS_LEAD", dual), "ONE_PERSON_TWO_SLOTS")
                await refused(
                    c,
                    finance.execute(
                        c,
                        req["request_id"],
                        executor=p["FINANCE_EXECUTOR"],
                        idempotency_key="k" * 10,
                        environment="test",
                    ),
                    "NOT_APPROVED",
                )
                done = await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"])
                assert done["state"] == "APPROVED" and done["approval_expires_at"]
                await refused(
                    c, sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"]), "NOT_PENDING"
                )

                await refused(
                    c,
                    finance.execute(
                        c,
                        req["request_id"],
                        executor=uid(),
                        idempotency_key="k" * 10,
                        environment="test",
                    ),
                    "NOT_AN_EXECUTOR",
                )
                await refused(
                    c,
                    finance.execute(
                        c,
                        req["request_id"],
                        executor=dual,
                        idempotency_key="k" * 10,
                        environment="test",
                    ),
                    "EXECUTOR_NOT_INDEPENDENT",
                )
                ran = await finance.execute(
                    c,
                    req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="run-once-1",
                    environment="test",
                )
                assert (
                    ran["state"] == "APPLIED"
                    and ran["result"]["classification"] == "PILOT_NON_BILLABLE"
                )
                again = await finance.execute(
                    c,
                    req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="run-once-2",
                    environment="test",
                )
                assert again["state"] == "APPLIED" and again["result"] == ran["result"]
                n = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM commercial.finance_execution WHERE request_id = :r"
                        ),
                        {"r": uuid.UUID(req["request_id"])},
                    )
                ).scalar_one()
                credits = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM commercial.finance_pilot_credit WHERE request_id = :r"
                        ),
                        {"r": uuid.UUID(req["request_id"])},
                    )
                ).scalar_one()
                assert (n, credits) == (1, 1)  # the one right was used once

                # the request's facts and an approval are never edited, and a final state never moves
                for sql in (
                    "UPDATE commercial.finance_request SET amount_minor = 1 WHERE request_id = :r",
                    "UPDATE commercial.finance_request SET state = 'PENDING_APPROVAL' WHERE request_id = :r",
                    "DELETE FROM commercial.finance_approval WHERE request_id = :r",
                    "UPDATE commercial.finance_execution SET executed_by = gen_random_uuid() WHERE request_id = :r",
                ):
                    with pytest.raises(
                        Exception, match="immutable|final|append-only|permission denied"
                    ):  # the role also has no right to try
                        async with c.begin_nested():
                            await c.execute(text(sql), {"r": uuid.UUID(req["request_id"])})

                # a rejection ends it; only the requester cancels
                r2 = await pilot(c, p["REVOPS"], uid(), "100.00")
                no = await finance.decide(
                    c,
                    r2["request_id"],
                    role="CCO",
                    approver=dual,
                    decision="REJECT",
                    presented_hash=r2["snapshot_hash"],
                    reason="not a pilot",
                    environment="test",
                )
                assert no["state"] == "REJECTED"
                await refused(
                    c, sign(c, r2, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"]), "NOT_PENDING"
                )
                r3 = await pilot(c, p["REVOPS"], uid(), "100.00")
                await refused(c, finance.cancel(c, r3["request_id"], by=dual), "NOT_THE_REQUESTER")
                assert (await finance.cancel(c, r3["request_id"], by=p["REVOPS"]))[
                    "state"
                ] == "CANCELED"

    asyncio.run(run())


def test_a_signer_who_loses_the_appointment_voids_the_approval(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                req = await pilot(c, p["REVOPS"], uid(), "500.00")
                await sign(c, req, "CCO", p["CCO"])
                assert (await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"]))[
                    "state"
                ] == "APPROVED"
                held = [
                    a
                    for a in await finance.appointments(c)
                    if a["principal_id"] == p["CCO"] and a["role"] == "CCO"
                ]
                await finance.end_appointment(c, held[0]["appointment_id"], by=uid())
                assert not await finance.holds(c, p["CCO"], "CCO", datetime.now(UTC))
                with pytest.raises(FinanceError) as e:
                    await finance.execute(
                        c,
                        req["request_id"],
                        executor=p["FINANCE_EXECUTOR"],
                        idempotency_key="k" * 10,
                        environment="test",
                    )
                assert e.value.code == "SIGNER_AUTHORITY_LOST"
                gone = await finance.get_request(c, req["request_id"])
                assert (
                    gone["state"] == "EXPIRED"
                    and gone["result"]["reason"] == "SIGNER_AUTHORITY_LOST"
                )
                none = (
                    await c.execute(
                        text(
                            "SELECT count(*) FROM commercial.finance_pilot_credit WHERE request_id = :r"
                        ),
                        {"r": uuid.UUID(req["request_id"])},
                    )
                ).scalar_one()
                assert none == 0

    asyncio.run(run())


def test_lapsed_requests_and_lapsed_approvals_are_never_run(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                old = await pilot(
                    c, p["REVOPS"], uid(), "500.00", now=NOW - timedelta(days=20)
                )  # asked 20 days ago, valid 14
                with pytest.raises(FinanceError) as e:
                    await sign(c, old, "CCO", p["CCO"])
                assert e.value.code == "NOT_PENDING"
                assert (await finance.get_request(c, old["request_id"]))["state"] == "EXPIRED"

                stale = await pilot(c, p["REVOPS"], uid(), "500.00", now=NOW - timedelta(days=30))
                at = NOW - timedelta(
                    days=17
                )  # still inside the request's 14 days, but its approval is 14 days from here
                await sign(c, stale, "CCO", p["CCO"], now=at)
                assert (await sign(c, stale, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"], now=at))[
                    "state"
                ] == "APPROVED"
                with pytest.raises(FinanceError) as e:
                    await finance.execute(
                        c,
                        stale["request_id"],
                        executor=p["FINANCE_EXECUTOR"],
                        idempotency_key="k" * 10,
                        environment="test",
                    )
                assert e.value.code == "NOT_APPROVED"
                assert (await finance.get_request(c, stale["request_id"]))["state"] == "EXPIRED"

                waiting = await pilot(c, p["REVOPS"], uid(), "500.00", now=NOW - timedelta(days=20))
                assert await finance.lapse_due(c, NOW) >= 1
                assert (await finance.get_request(c, waiting["request_id"]))["state"] == "EXPIRED"

    asyncio.run(run())


def test_a_deployed_environment_acts_only_on_a_policy_the_founder_and_the_cfo_signed(
    rt_url: str,
) -> None:
    async def run() -> None:
        digest = finance.finance_policy().content_hash
        async with engine(rt_url) as eng:
            p = await cast(
                eng, "REVOPS", "FOUNDER", "CFO", "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR"
            )
            async with eng.begin() as c:
                assert (await finance.policy_state(c))["effective"] is False
                await refused(
                    c, pilot(c, p["REVOPS"], uid(), "500.00", env="production"), "POLICY_NOT_SIGNED"
                )
                await refused(
                    c,
                    finance.sign_policy(c, signer=p["FOUNDER"], content_hash="0" * 64),
                    "POLICY_CHANGED",
                )
                await refused(
                    c, finance.sign_policy(c, signer=uid(), content_hash=digest), "NOT_A_SIGNATORY"
                )
                half = await finance.sign_policy(c, signer=p["FOUNDER"], content_hash=digest)
                assert half["effective"] is False and half["awaiting"] == ["CFO"]
                await refused(
                    c,
                    finance.sign_policy(c, signer=p["FOUNDER"], content_hash=digest),
                    "ALREADY_SIGNED",
                )
                assert (await finance.sign_policy(c, signer=p["CFO"], content_hash=digest))[
                    "effective"
                ] is True

                req = await pilot(c, p["REVOPS"], uid(), "500.00", env="production")
                await sign(c, req, "CCO", p["CCO"], env="production")
                assert (
                    await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"], env="production")
                )["state"] == "APPROVED"
                ran = await finance.execute(
                    c,
                    req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="signed-policy",
                    environment="production",
                )
                assert ran["state"] == "APPLIED"

    asyncio.run(run())


async def issued_invoice(rt_url: str, tenant: str) -> str:
    async with engine(rt_url) as eng:
        async with eng.begin() as c:
            draft = await catalogue_store.create_draft(
                c, copy_of=SEED_BOOK, created_by=uid(), note="fin"
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
                quantity=68_000,
            )
            await recorder.freeze_month(c, tenant, LAST)
            await rating_service.run(c, tenant_id=tenant, month=LAST, connections={"saml_sso": 1})
            d = await inv.create_draft(
                c, tenant_id=tenant, month=LAST, issuing_entity=ENTITY, actor=uid()
            )
            out = await inv.issue(
                c,
                d["invoice_id"],
                issuer=uid(),
                tax=NoTaxConfigured(),
                payment_terms_days=30,
                allow_zero_tax=True,
                region="uk-1",
            )
            assert out["status"] == "ISSUED"
            return str(d["invoice_id"])


def test_a_credit_note_is_applied_only_through_its_approvals_and_never_beyond_the_invoice(
    rt_url: str,
) -> None:
    tenant = uid()

    async def run() -> None:
        invoice = await issued_invoice(rt_url, tenant)
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")

            async def ask(c: Any, amount: str, **kw: Any) -> dict[str, Any]:
                args: dict[str, Any] = {
                    "tenant_id": tenant,
                    "invoice_id": invoice,
                    "currency": "USD",
                    "category": "CORRECTION",
                    "evidence_ref": "INC-4411",
                } | kw
                await billed(c, args["tenant_id"])
                return await finance.open_request(
                    c,
                    kind=Kind.CREDIT_NOTE,
                    amount=D(amount),
                    reason="billed a seat twice",
                    requested_by=p["REVOPS"],
                    environment="test",
                    **args,
                )

            async with eng.begin() as c:
                await refused(c, ask(c, "2000.00"), "CREDIT_NOTE_EXCEEDS_INVOICE")
                await refused(
                    c, ask(c, "10.00", tenant_id=uid()), "INVOICE_NOT_CREDITABLE"
                )  # not this organization's invoice
                await refused(c, ask(c, "10.00", currency="EUR"), "INVOICE_NOT_CREDITABLE")
                await refused(c, ask(c, "10.00", category="PILOT"), "CATEGORY_INVALID")
                await refused(c, ask(c, "10.005"), "AMOUNT_INVALID")
                await refused(c, ask(c, "10.00", evidence_ref=None), "EVIDENCE_REQUIRED")
                await refused(c, ask(c, "10.00", disposition="REFUND"), "DISPOSITION_INVALID")
                good = await ask(
                    c, "10.00", category="GOODWILL", evidence_ref=None
                )  # goodwill is a concession, not a correction of a fact
                assert good["state"] == "PENDING_APPROVAL"

                req = await ask(c, "400.00")
                assert (
                    req["required_roles"] == ["FINANCE_OPS_LEAD"] and req["invoice_id"] == invoice
                )
                assert (await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"]))[
                    "state"
                ] == "APPROVED"
                ran = await finance.execute(
                    c,
                    req["request_id"],
                    executor=p["FINANCE_EXECUTOR"],
                    idempotency_key="credit-note-1",
                    environment="test",
                )
                assert ran["state"] == "APPLIED" and ran["result"]["credit_note"].startswith(ENTITY)
                await refused(
                    c, ask(c, "1400.00"), "CREDIT_NOTE_EXCEEDS_INVOICE"
                )  # what is left of the invoice shrinks with each note

    asyncio.run(run())


def test_finance_routes_over_http(rt_url: str) -> None:
    from conftest import CELL
    from fastapi.testclient import TestClient
    from onboarding_fixture import AUD, ISS
    from operator_fixture import activate_grant, operators
    from zid_platform.main import create_app
    from zid_platform.modules.operator_access.domain.model import OperatorRole
    from zid_platform.settings import Settings

    adm, rev, cco, lead, exe, bob, carol = (uid() for _ in range(7))
    ops = operators(
        rt_url,
        {
            **{p: {OperatorRole.REQUESTER} for p in (adm, rev, cco, lead, exe)},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )
    tenant = uid()

    async def link() -> None:
        async with engine(rt_url) as eng, eng.begin() as c:
            await billed(c, tenant)

    asyncio.run(link())
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD, environment="test",
            cell_id="uk-1", cell_uuid=CELL, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,),
        ),
        keyring=ops.ring,
    )  # fmt: skip
    with TestClient(app, base_url=ISS) as api:
        assert (
            api.get("/v1/commercial/finance/policy", headers=ops.bearer(adm)).status_code == 403
        )  # no grant yet
        for who in (adm, rev, cco, lead, exe):
            activate_grant(
                api,
                ops,
                who,
                (bob,),
                capability="commercial.finance.manage",
                resource_scope="commercial:finance",
            )
        h = lambda who: ops.bearer(who)

        policy = api.get("/v1/commercial/finance/policy", headers=h(adm))
        assert policy.status_code == 200, policy.text
        body = policy.json()
        assert (
            body["unit"] == "USD"
            and isinstance(body["effective"], bool)
            and body["locked"] is False
        )  # a sandbox is never locked, signed or not
        assert body["credit_note"]["tiers"][0] == {
            "max_usd": "500.00",
            "roles": ["FINANCE_OPS_LEAD"],
        }

        for who, role in (
            (rev, "REVOPS"),
            (cco, "CCO"),
            (lead, "FINANCE_OPS_LEAD"),
            (exe, "FINANCE_EXECUTOR"),
        ):
            r = api.post(
                "/v1/commercial/finance/appointments",
                json={"principal_id": who, "role": role},
                headers=h(adm),
            )
            assert r.status_code == 201, r.text
        r = api.post(
            "/v1/commercial/finance/appointments",
            json={"principal_id": adm, "role": "CFO"},
            headers=h(adm),
        )
        assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPOINTMENT", r.text
        r = api.post(
            "/v1/commercial/finance/appointments",
            json={"principal_id": cco, "role": "CCO"},
            headers=h(adm),
        )
        assert r.status_code == 409 and r.json()["error"]["code"] == "ALREADY_APPOINTED"
        r = api.post(
            "/v1/commercial/finance/appointments",
            json={"principal_id": cco, "role": "PROCUREMENT_APPROVER"},
            headers=h(adm),
        )
        assert r.status_code == 422  # a Procurement Approver belongs to one organization

        ask = {"kind": "PILOT_CREDIT", "target_tenant_id": tenant, "amount": "500.00", "currency": "USD", "category": "PILOT",
               "reason": "design partner", "program_ref": "PILOT-1", "sponsor": "CIO", "ends_at": (datetime.now(UTC) + timedelta(days=30)).isoformat()}  # fmt: skip
        assert (
            api.post(
                "/v1/commercial/finance/requests", json={**ask, "amount": "0"}, headers=h(rev)
            ).status_code
            == 422
        )
        assert (
            api.post("/v1/commercial/finance/requests", json=ask, headers=h(cco)).status_code == 403
        )  # appointed, but not to ask
        r = api.post("/v1/commercial/finance/requests", json=ask, headers=h(rev))
        assert r.status_code == 201, r.text
        req = r.json()
        rid, digest = req["request_id"], req["snapshot_hash"]
        mine_cco = api.get("/v1/commercial/finance/overview", headers=h(cco)).json()
        assert (
            rid in mine_cco["mine"]
            and mine_cco["counts"]["pending_approval"] >= 1
            and "as_of" in mine_cco
        )
        assert (
            rid not in api.get("/v1/commercial/finance/overview", headers=h(rev)).json()["mine"]
        )  # the person who asked is never asked to sign
        assert (
            req["required_roles"] == ["CCO", "FINANCE_OPS_LEAD"]
            and req["state"] == "PENDING_APPROVAL"
        )

        def decide(who: str, role: str, hash_: str = digest) -> Any:
            return api.post(
                f"/v1/commercial/finance/requests/{rid}:decide",
                json={"role": role, "decision": "APPROVE", "snapshot_hash": hash_},
                headers=h(who),
            )

        r = decide(rev, "CCO")
        assert r.status_code == 403 and r.json()["error"]["code"] == "SELF_APPROVAL"
        assert decide(cco, "CCO", "0" * 64).json()["error"]["code"] == "SNAPSHOT_CHANGED"
        assert decide(cco, "CCO").json()["state"] == "PENDING_APPROVAL"
        done = decide(lead, "FINANCE_OPS_LEAD")
        assert done.status_code == 200 and done.json()["state"] == "APPROVED", done.text

        run = f"/v1/commercial/finance/requests/{rid}:execute"
        assert (
            api.post(run, json={"idempotency_key": "http-run-0001"}, headers=h(cco)).status_code
            == 403
        )  # may not run
        r = api.post(run, json={"idempotency_key": "http-run-0001"}, headers=h(exe))
        assert r.status_code == 200 and r.json()["state"] == "APPLIED", r.text
        again = api.post(run, json={"idempotency_key": "http-run-0002"}, headers=h(exe))
        assert again.status_code == 200 and again.json()["result"] == r.json()["result"]

        listed = api.get("/v1/commercial/finance/requests?state=APPLIED", headers=h(adm)).json()[
            "items"
        ]
        assert rid in [i["request_id"] for i in listed]
        assert (
            api.get(f"/v1/commercial/finance/requests/{rid}", headers=h(adm)).json()["approvals"][
                0
            ]["role"]
            == "CCO"
        )
        held = [
            a
            for a in api.get("/v1/commercial/finance/appointments", headers=h(adm)).json()["items"]
            if a["principal_id"] == cco
        ]
        assert (
            api.post(
                f"/v1/commercial/finance/appointments/{held[0]['appointment_id']}:end",
                headers=h(adm),
            ).status_code
            == 200
        )


def test_a_change_of_payer_after_approval_voids_the_request(rt_url: str) -> None:
    async def run() -> None:
        async with engine(rt_url) as eng:
            p = await cast(eng, "REVOPS", "CCO", "FINANCE_OPS_LEAD", "FINANCE_EXECUTOR")
            async with eng.begin() as c:
                tenant = uid()
                req = await pilot(c, p["REVOPS"], tenant, "500.00")
                await sign(c, req, "CCO", p["CCO"])
                await sign(c, req, "FINANCE_OPS_LEAD", p["FINANCE_OPS_LEAD"])
                other = await orders.create_account(
                    c,
                    legal_name="New Payer Ltd",
                    kind="DIRECT",
                    country="GB",
                    currency="USD",
                    reseller_of=None,
                    actor=uid(),
                )
                await orders.link_tenant(
                    c, other, tenant
                )  # the tenant now belongs to another legal payer
                with pytest.raises(FinanceError) as e:
                    await finance.execute(
                        c,
                        req["request_id"],
                        executor=p["FINANCE_EXECUTOR"],
                        idempotency_key="k" * 10,
                        environment="test",
                    )
                assert e.value.code == "PAYER_CHANGED"
                assert (await finance.get_request(c, req["request_id"]))["state"] == "EXPIRED"

    asyncio.run(run())


def test_a_role_that_approves_is_held_by_an_active_person_never_a_service(rt_url: str) -> None:
    """CL04-C12 and Closure 04 section 08: a service, workload, application or device never signs as the CFO, the Founder or any approver, an unknown
    or inactive principal holds nothing, and only the two roles that run work (REVOPS, FINANCE_EXECUTOR) may be held by an automation."""

    async def run() -> None:
        async with engine(rt_url) as eng:
            for kind, role in (
                (PrincipalType.SERVICE, "CFO"),
                (PrincipalType.WORKLOAD, "FOUNDER"),
                (PrincipalType.APPLICATION, "CONTROLLER"),
                (PrincipalType.DEVICE, "CCO"),
            ):
                with pytest.raises(FinanceError) as e:
                    await appoint_person(eng, uid(), role, kind=kind)
                assert e.value.code == "NOT_A_PERSON", (kind, role)
            with pytest.raises(FinanceError) as e:  # a principal nobody created
                async with tenant_transaction(eng, ctx(PEOPLE_TENANT)) as c:
                    await finance.appoint(
                        c,
                        principal=uid(),
                        role="CFO",
                        appointed_by=uid(),
                        effective_from=NOW - timedelta(days=1),
                    )
            assert e.value.code == "PRINCIPAL_UNKNOWN"
            with pytest.raises(FinanceError) as e:  # a person whose account is disabled
                await appoint_person(eng, uid(), "CFO", status=PrincipalStatus.DISABLED)
            assert e.value.code == "PRINCIPAL_NOT_ACTIVE"
            # the roles that only run work are open to an automation, and a person is appointed as before
            for role in ("REVOPS", "FINANCE_EXECUTOR"):
                made = await appoint_person(eng, uid(), role, kind=PrincipalType.SERVICE)
                assert made["role"] == role
            assert (await appoint_person(eng, uid(), "CFO"))["role"] == "CFO"

    asyncio.run(run())
