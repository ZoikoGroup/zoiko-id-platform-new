"""The partner channel on Postgres (ZID-COM-WF-2026-11 WF-I06; INV-09; CAT-34, CAT-35): a partner child transaction is billed once, to the contractually right payer, and a
partner path never gets round territory, consent or term; settlement needs a signed arrangement and a second person."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.commercial.application import partner_service
from zid_platform.modules.commercial.domain import partner as partner_rules
from zid_platform.modules.commercial.domain.billable import window_month
from zid_platform.modules.commercial.ports import partner_store as store


def uid() -> str:
    return str(uuid.uuid4())


async def refused(conn, call, code: str) -> None:
    with pytest.raises(Exception) as e:
        async with conn.begin_nested():
            await call()
    assert getattr(e.value, "code", None) == code, repr(e.value)


async def new_partner(
    conn, author: str, *, bill_to: str = "PARTNER", parent: str | None = None, **over
) -> str:
    row = await store.create(
        conn,
        **{
            "name": "Acme Resale", "kind": "RESELLER", "parent_tenant_id": parent or uid(), "bill_to": bill_to,
            "territory": ["DE", "FR"], "agreement_ref": "agreement:2026-11/acme", "brand_terms_ref": None,
            "settlement_ref": None, "effective_from": None, "effective_until": None, "created_by": author, **over,
        },
    )  # fmt: skip
    return str(row["partner_id"])


def test_a_partner_is_activated_by_someone_else_and_its_terms_are_then_fixed(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        author, other = uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            pid = await new_partner(c, author)
            now = datetime.now(UTC) - timedelta(seconds=5)
            await refused(
                c,
                lambda: store.activate(c, pid, activator=author, effective_from=now, now=now),
                "PARTNER_SELF_ACTIVATION",
            )
            await store.activate(c, pid, activator=other, effective_from=now, now=now)
            await refused(
                c,
                lambda: store.activate(c, pid, activator=other, effective_from=now, now=now),
                "PARTNER_NOT_PROPOSED",
            )
        # the database, not only the code, fixes the terms once active, and never deletes
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.partner SET territory = ARRAY['US'] WHERE partner_id = :i",
                "UPDATE commercial.partner SET bill_to = 'CHILD' WHERE partner_id = :i",
                "DELETE FROM commercial.partner WHERE partner_id = :i",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql), {"i": uuid.UUID(pid)})
        async with engine(rt_url) as eng, eng.begin() as c:
            await store.set_status(c, pid, "SUSPENDED")
            await store.set_status(c, pid, "ACTIVE")
            await store.set_status(c, pid, "TERMINATED")
            await refused(c, lambda: store.set_status(c, pid, "ACTIVE"), "PARTNER_STATUS_REFUSED")
            await refused(
                c,
                lambda: store.add_child(
                    c,
                    pid,
                    child_tenant_id=uid(),
                    permitted_by=author,
                    downstream_consent_ref="c",
                    usage_visible=False,
                ),
                "PARTNER_TERMINATED",
            )

    asyncio.run(run())


def test_a_child_belongs_to_one_live_partner_and_is_revoked_not_deleted(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        author, child = uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            a, b = await new_partner(c, author), await new_partner(c, author)
            await store.add_child(
                c,
                a,
                child_tenant_id=child,
                permitted_by=author,
                downstream_consent_ref="consent:1",
                usage_visible=False,
            )
            await refused(
                c,
                lambda: store.add_child(
                    c,
                    b,
                    child_tenant_id=child,
                    permitted_by=author,
                    downstream_consent_ref="consent:2",
                    usage_visible=False,
                ),
                "CHILD_ALREADY_IN_CHANNEL",
            )
            parent = (await store.get(c, a))["parent_tenant_id"]  # type: ignore[index]
            await refused(
                c,
                lambda: store.add_child(
                    c,
                    a,
                    child_tenant_id=str(parent),
                    permitted_by=author,
                    downstream_consent_ref=None,
                    usage_visible=False,
                ),
                "CHILD_IS_PARTNER",
            )
            await store.revoke_child(c, a, child)
            await store.add_child(
                c,
                b,
                child_tenant_id=child,
                permitted_by=author,
                downstream_consent_ref="consent:2",
                usage_visible=False,
            )
            await refused(c, lambda: store.revoke_child(c, a, child), "CHILD_NOT_FOUND")
        async with engine(owner_url) as eng:
            for sql in (
                "DELETE FROM commercial.partner_child",
                "UPDATE commercial.partner_child SET usage_visible = true",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())


def test_cat34_a_child_transaction_is_billed_once_to_the_contractually_correct_payer(
    rt_url: str,
) -> None:
    async def run() -> None:
        author, activator = uid(), uid()
        child_a, child_b, partner_tenant = uid(), uid(), uid()
        month = window_month(datetime.now(UTC))
        async with engine(rt_url) as eng:
            async with tenant_transaction(
                eng, ctx(partner_tenant)
            ) as c:  # the platform's own ctx; the partner is set up in config tables
                pid = await new_partner(c, author, bill_to="PARTNER", parent=partner_tenant)
                pid2 = await new_partner(c, author, bill_to="CHILD")
                now = datetime.now(UTC) - timedelta(seconds=5)
                await store.activate(c, pid, activator=activator, effective_from=now, now=now)
                await store.activate(c, pid2, activator=activator, effective_from=now, now=now)
                await store.add_child(
                    c,
                    pid,
                    child_tenant_id=child_a,
                    permitted_by=author,
                    downstream_consent_ref="consent:a",
                    usage_visible=True,
                )
                await store.add_child(
                    c,
                    pid2,
                    child_tenant_id=child_b,
                    permitted_by=author,
                    downstream_consent_ref="consent:b",
                    usage_visible=False,
                )
            # the same logical operation, retried, is one billed event; the payer is the partner (bill_to PARTNER)
            async with tenant_transaction(eng, ctx(child_a)) as c:
                assert await partner_service.record_event(
                    c,
                    partner_id=pid,
                    child_tenant_id=child_a,
                    country="DE",
                    meter="AUTH_EVENT",
                    key="auth:op-1",
                )
                assert not await partner_service.record_event(
                    c,
                    partner_id=pid,
                    child_tenant_id=child_a,
                    country="DE",
                    meter="AUTH_EVENT",
                    key="auth:op-1",
                )
                n = (
                    await c.execute(
                        text(
                            "SELECT count(*), min(legal_payer_id::text), min(origin), min(partner_id::text) FROM commercial.billable_event"
                        )
                    )
                ).one()
                assert n[0] == 1 and n[1] == partner_tenant and n[2] == "PARTNER" and n[3] == pid
            # a child billed by its own agreement is the payer itself
            async with tenant_transaction(eng, ctx(child_b)) as c:
                assert await partner_service.record_event(
                    c,
                    partner_id=pid2,
                    child_tenant_id=child_b,
                    country="FR",
                    meter="AUTH_EVENT",
                    key="auth:op-9",
                )
                payer = (
                    await c.execute(
                        text("SELECT legal_payer_id::text FROM commercial.billable_event")
                    )
                ).scalar_one()
                assert payer == child_b
            # parent plus child usage reconciles once
            async with tenant_transaction(eng, ctx(uid())) as c:
                events = await store.attributed_events(c, pid, month)
                row = await store.get(c, pid)
                assert row is not None
                result = partner_rules.reconcile(events, store.model(row))
                assert result == {
                    "events": 1,
                    "duplicates": [],
                    "wrong_or_missing_payer": [],
                    "reconciles": True,
                }

    asyncio.run(run())


def test_cat35_a_partner_path_never_gets_round_territory_consent_term_or_status(
    rt_url: str,
) -> None:
    async def run() -> None:
        author, activator, child = uid(), uid(), uid()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(uid())) as c:
            pid = await new_partner(c, author)
            other_child = uid()
            now = datetime.now(UTC) - timedelta(seconds=5)
            # not yet active
            await store.add_child(
                c,
                pid,
                child_tenant_id=child,
                permitted_by=author,
                downstream_consent_ref="consent",
                usage_visible=False,
            )
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=child, country="DE"
                ),
                "PARTNER_NOT_ACTIVE",
            )
            await store.activate(c, pid, activator=activator, effective_from=now, now=now)
            await partner_service.authorise(c, partner_id=pid, child_tenant_id=child, country="DE")
            # outside the territory, for a child never permitted, an unknown partner, and an unstated country
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=child, country="US"
                ),
                "CROSS_TERRITORY",
            )
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=other_child, country="DE"
                ),
                "CHILD_NOT_PERMITTED",
            )
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=uid(), child_tenant_id=child, country="DE"
                ),
                "PARTNER_UNKNOWN",
            )
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=child, country=None
                ),
                "COUNTRY_NOT_STATED",
            )
            # the child withdrew: nothing more is billed through the partner, and nothing is recorded
            await store.revoke_child(c, pid, child)
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=child, country="DE"
                ),
                "CHILD_NOT_PERMITTED",
            )
            # a child without downstream consent on file is refused even when permitted
            nc = uid()
            await store.add_child(
                c,
                pid,
                child_tenant_id=nc,
                permitted_by=author,
                downstream_consent_ref=None,
                usage_visible=False,
            )
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=nc, country="DE"
                ),
                "DOWNSTREAM_CONSENT_MISSING",
            )
            # termination stops new operations at once
            await store.set_status(c, pid, "TERMINATED")
            await refused(
                c,
                lambda: partner_service.authorise(
                    c, partner_id=pid, child_tenant_id=nc, country="DE"
                ),
                "PARTNER_NOT_ACTIVE",
            )

    asyncio.run(run())


def test_settlement_needs_a_signed_arrangement_and_a_second_person_and_pays_nothing(
    rt_url: str, owner_url: str
) -> None:
    async def run() -> None:
        author, activator, proposer, decider = uid(), uid(), uid(), uid()
        async with engine(rt_url) as eng, eng.begin() as c:
            now = datetime.now(UTC) - timedelta(seconds=5)
            bare = await new_partner(c, author)
            await store.activate(c, bare, activator=activator, effective_from=now, now=now)
            period = date.today().replace(day=1)
            ask = {
                "period": period,
                "kind": "COMMISSION",
                "amount": "12.500000",
                "currency": "EUR",
                "proposed_by": proposer,
            }
            await refused(
                c, lambda: store.propose_settlement(c, bare, **ask), "SETTLEMENT_REFUSED"
            )  # no settlement arrangement on file

            signed = await new_partner(c, author, settlement_ref="settlement-arrangement:2026-11")
            await store.activate(c, signed, activator=activator, effective_from=now, now=now)
            sid = await store.propose_settlement(c, signed, **ask)
            await refused(
                c,
                lambda: store.decide_settlement(
                    c, sid, decider=proposer, approve=True, reason="my own"
                ),
                "SETTLEMENT_SELF_APPROVAL",
            )
            await store.decide_settlement(
                c, sid, decider=decider, approve=True, reason="matches the report"
            )
            await refused(
                c,
                lambda: store.decide_settlement(
                    c, sid, decider=activator, approve=False, reason="again"
                ),
                "SETTLEMENT_ALREADY_DECIDED",
            )
            await refused(
                c,
                lambda: store.propose_settlement(
                    c, signed, **{**ask, "period": period + timedelta(days=3)}
                ),
                "SETTLEMENT_REFUSED",
            )  # not a month start
        async with engine(owner_url) as eng:
            for sql in (
                "UPDATE commercial.partner_settlement SET amount = 1",
                "DELETE FROM commercial.partner_settlement",
            ):
                with pytest.raises(DBAPIError):
                    async with eng.begin() as c:
                        await c.execute(text(sql))

    asyncio.run(run())
