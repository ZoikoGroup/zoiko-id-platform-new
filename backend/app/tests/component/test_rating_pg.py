"""Rating runs over the real ledger (ZID-COM-001 §13, §31; CR-026).

The proposal's worked bill is reproduced from billable events in the database, through a frozen window and
an approved price book, to $2,053.00. A re-run on the same inputs is the same run (the replay proof); a credit
supersedes it and keeps the old lines; a mid-month upgrade is prorated; nothing rates an open month.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
from onboarding_fixture import AUD, ISS, onboard
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.commercial.application import rating_service, recorder
from zid_platform.modules.commercial.domain import billable as b
from zid_platform.modules.commercial.domain.rating import Credit, RatingError
from zid_platform.modules.commercial.infrastructure import catalogue_store
from zid_platform.settings import Settings

SEED_BOOK = "00000000-0000-4000-8000-0000000000b1"  # the proposal rates, DRAFT in the migration
NOW = datetime.now(UTC)
LAST = b.window_month(NOW.replace(day=1) - timedelta(days=1))
L_START = datetime(LAST.year, LAST.month, 1, tzinfo=UTC)
L_END = datetime(NOW.year, NOW.month, 1, tzinfo=UTC)
L_MID = L_START + (L_END - L_START) / 2


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


async def approved_proposal_book(rt_url: str) -> str:
    """A copy of the proposal's rates, approved by a second person and effective before last month."""
    async with engine(rt_url) as eng, eng.begin() as c:
        draft = await catalogue_store.create_draft(
            c, copy_of=SEED_BOOK, created_by=uid(), note="rating test"
        )
        await catalogue_store.approve(
            c,
            draft["price_book_id"],
            approver=uid(),
            effective_from=L_START - timedelta(days=1),
            now=datetime.now(UTC),
        )
    return str(draft["price_book_id"])


async def usage(rt_url: str, tenant: str, items: list[tuple[str, int, datetime]]) -> None:
    async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
        for i, (meter, qty, at) in enumerate(items):
            assert await recorder.record(
                c, tenant_id=tenant, meter=meter, key=f"t:{meter}:{i}", at=at, quantity=qty
            )


def test_the_worked_bill_from_the_ledger_replays_and_a_credit_supersedes(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        book = await approved_proposal_book(rt_url)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await catalogue_store.set_entitlement(
                c,
                tenant_id=tenant,
                plan_code="BUSINESS",
                price_book_id=book,
                actor=None,
                now=L_START - timedelta(days=3),
            )
        day = L_START + timedelta(days=10)
        await usage(
            rt_url,
            tenant,
            [
                ("MAH", 68_000, day),
                ("AUTH_EVENT", 1_200_000, day),
                ("POLICY_DECISION", 6_500_000, day),
                ("M2M_GRANT", 300_000, day),
            ],
        )
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                with pytest.raises(RatingError) as e:  # nothing rates a month that can still change
                    await rating_service.run(
                        c, tenant_id=tenant, month=LAST, connections={"saml_sso": 2}
                    )
                assert e.value.code == "WINDOW_NOT_FROZEN"
                await recorder.freeze_month(c, tenant, LAST)
            async with tenant_transaction(eng, ctx(tenant)) as c:
                first = await rating_service.run(
                    c, tenant_id=tenant, month=LAST, connections={"saml_sso": 2}
                )
            assert first["subtotal"] == "2053" and first["attempt"] == 1 and not first["reused"]
            by = {(ln["kind"], ln["meter"]): ln for ln in first["lines"]}
            assert by[("FEE", None)]["net"] == "1499"
            assert (
                by[("OVERAGE", "MAH")]["formula"] == "18,000 x $0.015"
                and by[("OVERAGE", "MAH")]["net"] == "270"
            )
            assert by[("OVERAGE", "AUTH_EVENT")]["net"] == "50"
            assert by[("OVERAGE", "POLICY_DECISION")]["net"] == "67.5"
            assert by[("OVERAGE", "M2M_GRANT")]["net"] == "17.5"
            assert by[("CONNECTION", "saml_sso")]["net"] == "149"

            # replay: the same inputs are the same run, with the same hash
            async with tenant_transaction(eng, ctx(tenant)) as c:
                again = await rating_service.run(
                    c, tenant_id=tenant, month=LAST, connections={"saml_sso": 2}
                )
            assert again["reused"] and again["run_id"] == first["run_id"]
            assert again["outputs_hash"] == first["outputs_hash"]

            # a credit is a new input: a new run supersedes the old one, whose lines stay
            async with tenant_transaction(eng, ctx(tenant)) as c:
                credited = await rating_service.run(
                    c,
                    tenant_id=tenant,
                    month=LAST,
                    connections={"saml_sso": 2},
                    credits=[Credit(Decimal(53), "GOODWILL")],
                )
                assert credited["attempt"] == 2 and credited["superseded"] == first["run_id"]
                assert credited["subtotal"] == "2000"
                runs = (
                    await c.execute(
                        text("SELECT attempt, state FROM commercial.rating_run ORDER BY attempt")
                    )
                ).all()
                assert [tuple(r) for r in runs] == [(1, "SUPERSEDED"), (2, "COMPUTED")]
                old = (
                    await c.execute(
                        text("SELECT count(*) FROM commercial.rated_usage WHERE run_id = :r"),
                        {"r": uuid.UUID(first["run_id"])},
                    )
                ).scalar_one()
                assert old == 6
                stored = await rating_service.latest(c, LAST)
                assert stored is not None and stored["outputs_hash"] == credited["outputs_hash"]
                assert (
                    stored["lines"][-1]["kind"] == "CREDIT" and stored["lines"][-1]["net"] == "-53"
                )

        # the database keeps history: lines are immutable, a run can only be superseded, one COMPUTED per month
        async with engine(owner_url) as eng:
            for sql, match in (
                ("UPDATE commercial.rated_usage SET net = 0 WHERE tenant_id = :t", "immutable"),
                ("DELETE FROM commercial.rated_usage WHERE tenant_id = :t", "immutable"),
                (
                    "UPDATE commercial.rating_run SET subtotal = 1 WHERE tenant_id = :t",
                    "only be superseded",
                ),
                ("DELETE FROM commercial.rating_run WHERE tenant_id = :t", "never deleted"),
            ):
                with pytest.raises(DBAPIError, match=match):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"t": uuid.UUID(tenant)})
            with pytest.raises(IntegrityError):
                async with eng.begin() as c:
                    await c.execute(
                        text(
                            "INSERT INTO commercial.rating_run (run_id, tenant_id, period, attempt, state, currency, "
                            "subtotal, credit_balance, inputs_hash, outputs_hash) VALUES (gen_random_uuid(), :t, :p, "
                            "9, 'COMPUTED', 'USD', 0, 0, :h, :h)"
                        ),
                        {"t": uuid.UUID(tenant), "p": LAST, "h": "0" * 64},
                    )

    asyncio.run(run())


def test_a_mid_month_upgrade_is_prorated_from_the_entitlement_history(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        book = await approved_proposal_book(rt_url)
        async with engine(rt_url) as eng:
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await catalogue_store.set_entitlement(
                    c,
                    tenant_id=tenant,
                    plan_code="LAUNCH",
                    price_book_id=book,
                    actor=None,
                    now=L_START - timedelta(days=5),
                )
            async with tenant_transaction(eng, ctx(tenant)) as c:
                await catalogue_store.set_entitlement(
                    c,
                    tenant_id=tenant,
                    plan_code="BUSINESS",
                    price_book_id=book,
                    actor=None,
                    now=L_MID,
                )
        await usage(
            rt_url,
            tenant,
            [
                ("MAH", 9_000, L_START + timedelta(days=2)),
                ("MAH", 26_000, L_MID + timedelta(days=2)),
            ],
        )
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            segs = await rating_service.segments_for(c, LAST)
            assert [(p, s, e) for p, s, e, _ in segs] == [
                ("LAUNCH", L_START, L_MID),
                ("BUSINESS", L_MID, L_END),
            ]
            await recorder.freeze_month(c, tenant, LAST)
            out = await rating_service.run(c, tenant_id=tenant, month=LAST, connections={})
        fees = [ln for ln in out["lines"] if ln["kind"] == "FEE"]
        assert [f["plan"] for f in fees] == ["LAUNCH", "BUSINESS"]
        assert [Decimal(f["net"]) for f in fees] == [Decimal("149.50"), Decimal("749.50")]
        mah = [ln for ln in out["lines"] if ln["meter"] == "MAH"]
        assert [Decimal(m["net"]) for m in mah] == [Decimal(100), Decimal(15)]
        assert Decimal(out["subtotal"]) == Decimal(1014)

    asyncio.run(run())


def test_a_month_with_no_entitlement_is_rated_on_the_default_plan(rt_url: str) -> None:
    tenant = uid()

    async def run() -> None:
        await approved_proposal_book(rt_url)
        await usage(rt_url, tenant, [("MAH", 4000, L_START + timedelta(days=1))])
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await recorder.freeze_month(c, tenant, LAST)
            out = await rating_service.run(c, tenant_id=tenant, month=LAST, connections={})
        assert out["subtotal"] == "0" and [ln["plan"] for ln in out["lines"]] == ["DEVELOPER"]
        assert any(
            "no price" in w for w in out["warnings"]
        )  # Developer overage is fair use, never guessed

    asyncio.run(run())


def test_rating_over_http_for_operators_and_the_tenant(rt_url: str) -> None:
    from operator_fixture import activate_grant, operators
    from zid_platform.modules.operator_access.domain.model import OperatorRole

    o = onboard(rt_url, extra_actions=["commercial.read"], extra_resources=["billable:*"])
    alice, bob, carol = uid(), uid(), uid()
    ops = operators(
        rt_url,
        {
            alice: {OperatorRole.REQUESTER},
            bob: {OperatorRole.APPROVER},
            carol: {OperatorRole.APPROVER},
        },
    )

    async def seed() -> None:
        await approved_proposal_book(rt_url)
        await usage(rt_url, o.tenant, [("AUTH_EVENT", 30_000, L_START + timedelta(days=3))])

    asyncio.run(seed())
    month = f"{LAST:%Y-%m}"
    op_app = create_app(
        settings(rt_url, operator_tenant_id=ops.tenant, operator_client_ids=(ops.client,)),
        keyring=ops.ring,
    )
    tenant_app = create_app(settings(rt_url), keyring=o.ring)
    with TestClient(tenant_app, base_url=ISS) as http:
        r = http.get("/v1/usage/rated", params={"month": month}, headers=o.bearer(o.owner))
        assert r.status_code == 404 and r.json()["error"]["code"] == "RATING_NOT_FOUND"
    with TestClient(op_app, base_url=ISS) as api:
        url = f"/v1/commercial/tenants/{o.tenant}/rating-runs"
        assert api.post(url, json={"month": month}, headers=ops.bearer(alice)).status_code == 403
        activate_grant(
            api,
            ops,
            alice,
            (bob, carol),
            capability="commercial.billing.manage",
            resource_scope=f"tenant:{o.tenant}",
            target_tenant_id=o.tenant,
        )
        r = api.post(url, json={"month": month}, headers=ops.bearer(alice))
        assert r.status_code == 409 and r.json()["error"]["code"] == "WINDOW_NOT_FROZEN"
        assert (
            api.post(
                f"/v1/commercial/tenants/{o.tenant}/meter-windows/{month}:freeze",
                headers=ops.bearer(alice),
            ).status_code
            == 200
        )
        r = api.post(url, json={"month": month}, headers=ops.bearer(alice))
        assert r.status_code == 200, r.text
        run_id = r.json()["run_id"]
        assert (
            r.json()["subtotal"] == "0"
        )  # Developer: 30,000 auth events over a 25,000 allowance is unpriced
        assert any("AUTH_EVENT" in w for w in r.json()["warnings"])
        again = api.post(url, json={"month": month}, headers=ops.bearer(alice)).json()
        assert again["reused"] and again["run_id"] == run_id
        bad = api.post(
            url,
            json={"month": month, "credits": [{"amount": "-1", "reason_code": "BAD"}]},
            headers=ops.bearer(alice),
        )
        assert bad.status_code == 422
    with TestClient(tenant_app, base_url=ISS) as http:
        r = http.get("/v1/usage/rated", params={"month": month}, headers=o.bearer(o.owner))
        assert r.status_code == 200 and r.json()["run_id"] == run_id
        assert r.json()["lines"][0]["kind"] == "FEE"
