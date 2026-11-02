"""The scheduled company refresh and what an adverse change does (ZID-ED-03 section 08; WF-13B AT-11).

A verification is looked at again before it lapses. A company that is no longer what it was (dissolved, renamed, a different form)
stops being verified at once and the loss is one audited event; the earlier evidence stays on the ledger untouched. A source outage
never takes away a verification that still stands, a country that has been closed is left alone, and a second pass does nothing."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from test_kyb_pg import NOW, ROUTING, Register, company, sign, uid
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_platform.modules.organization.application import kyb as service
from zid_platform.modules.organization.application.passes import refresh_pass
from zid_platform.modules.organization.domain.kyb import Applicant, KybStatus

APPLICANT = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd")
LOST = "com.zoiko.id.organization.kyb.verification_lost.v1"


async def verified(eng: Any, tenant: str, *, days_ago: int, key: str) -> str:
    """A VERIFIED case decided `days_ago` days before NOW, so its 365-day verification lapses in 365 - days_ago days."""
    async with tenant_transaction(eng, ctx(tenant)) as c:
        v = await service.request_verification(
            c, tenant, applicant=APPLICANT, requested_by=uid(), idempotency_key=key,
            adapters={"companies-house": Register(company())}, routing=ROUTING, sign=sign,
            now=NOW - timedelta(days=days_ago),
        )  # fmt: skip
        assert v.status is KybStatus.VERIFIED
        return v.case_id


async def ledger(
    owner: Any, tenant: str, case_id: str
) -> tuple[list[tuple[int, str, str]], int, int]:
    """(evidence rows as (version, status, hash), loss events, loss audit records) for one case, seen by the worker role."""
    async with owner.begin() as c:
        rows = (
            await c.execute(
                text(
                    "SELECT version, decision_status, evidence_hash FROM org.kyb_evidence WHERE case_id = :c ORDER BY version"
                ),
                {"c": case_id},
            )
        ).all()
        events = (
            await c.execute(
                text(
                    "SELECT count(*) FROM events.outbox_event WHERE event_type = :t AND aggregate_id = :c"
                ),
                {"t": LOST, "c": case_id},
            )
        ).scalar_one()
        audits = (
            await c.execute(
                text(
                    "SELECT count(*) FROM evidence.audit_record WHERE event_type = 'organization.kyb.verification_lost' AND target_ref = :c"
                ),
                {"c": case_id},
            )
        ).scalar_one()
    return (
        [(int(r.version), str(r.decision_status), str(r.evidence_hash)) for r in rows],
        int(events),
        int(audits),
    )


def mine(done: dict[str, int], *tenants: str) -> dict[str, int]:
    """The pass is global and the test database is shared: look only at this test's own tenants."""
    return {k: v for k, v in done.items() if k.split(":")[0] in tenants}


async def case_status(eng: Any, tenant: str, case_id: str, when: Any) -> KybStatus:
    async with tenant_transaction(eng, ctx(tenant)) as c:
        return (await service.get_case(c, tenant, case_id, now=when)).status


def test_a_company_that_is_no_longer_active_stops_being_verified_and_the_loss_is_one_audited_event(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            case = await verified(
                eng, tenant, days_ago=340, key="key-refresh-001"
            )  # lapses in 25 days: due
            before, events, audits = await ledger(worker, tenant, case)
            assert [r[1] for r in before] == ["VERIFIED"] and (events, audits) == (0, 0)

            async with worker.begin() as c:  # the register now says the company is dissolved
                done = await refresh_pass(
                    c, {"companies-house": Register(company(active=False))}, ROUTING, sign, NOW
                )
            assert mine(done, tenant) == {tenant: 1}

            assert (
                await case_status(eng, tenant, case, NOW) is not KybStatus.VERIFIED
            )  # what depends on it drops at once
            after, events, audits = await ledger(worker, tenant, case)
            assert [r[0] for r in after] == [1, 2] and after[0] == before[0], (
                "the earlier evidence is untouched"
            )
            assert after[1][1] in {"NEEDS_REVIEW", "REJECTED"}
            assert (events, audits) == (1, 1), (
                "the loss is recorded once, as an event and as an audit record"
            )

            async with (
                worker.begin() as c
            ):  # a second pass has nothing left to do and records nothing more
                assert (
                    mine(
                        await refresh_pass(
                            c,
                            {"companies-house": Register(company(active=False))},
                            ROUTING,
                            sign,
                            NOW,
                        ),
                        tenant,
                    )
                    == {}
                )
            assert await ledger(worker, tenant, case) == (after, 1, 1)

    asyncio.run(run())


def test_a_company_still_in_good_standing_is_renewed_and_not_asked_again(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            case = await verified(eng, tenant, days_ago=340, key="key-refresh-006")
            async with worker.begin() as c:
                assert mine(
                    await refresh_pass(
                        c, {"companies-house": Register(company())}, ROUTING, sign, NOW
                    ),
                    tenant,
                ) == {tenant: 1}
            rows, events, audits = await ledger(worker, tenant, case)
            assert [(r[0], r[1]) for r in rows] == [(1, "VERIFIED"), (2, "VERIFIED")] and (
                events,
                audits,
            ) == (0, 0)
            assert (
                await case_status(eng, tenant, case, NOW + timedelta(days=300))
                is KybStatus.VERIFIED
            )  # a new year on
            async with (
                worker.begin() as c
            ):  # no longer about to lapse: the next pass leaves it alone
                assert (
                    mine(
                        await refresh_pass(
                            c, {"companies-house": Register(company())}, ROUTING, sign, NOW
                        ),
                        tenant,
                    )
                    == {}
                )

    asyncio.run(run())


def test_a_source_outage_never_takes_away_a_verification_that_still_stands(
    rt_url: str, owner_url: str
) -> None:
    tenant = uid()

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            case = await verified(eng, tenant, days_ago=340, key="key-refresh-002")
            before, _, _ = await ledger(worker, tenant, case)
            async with worker.begin() as c:
                done = await refresh_pass(
                    c, {"companies-house": Register("down")}, ROUTING, sign, NOW
                )
            assert mine(done, tenant) == {tenant: 1}, "the case was looked at"
            assert await case_status(eng, tenant, case, NOW) is KybStatus.VERIFIED
            assert await ledger(worker, tenant, case) == (before, 0, 0), (
                "nothing was appended and nothing was lost"
            )

            # the verification lapses on its own date if the sources stay down: it is expired, never verified
            later = NOW + timedelta(days=30)
            assert await case_status(eng, tenant, case, later) is KybStatus.EXPIRED

    asyncio.run(run())


def test_only_cases_about_to_lapse_are_refreshed_and_a_closed_country_is_left_alone(
    rt_url: str, owner_url: str
) -> None:
    due_tenant, fresh_tenant = uid(), uid()

    def closed(_country: str) -> None:
        raise ZidError("COUNTRY_CAPABILITY_UNAVAILABLE", "closed", http_status=422)

    async def run() -> None:
        async with engine(rt_url) as eng, engine(owner_url) as worker:
            due = await verified(eng, due_tenant, days_ago=340, key="key-refresh-003")
            fresh = await verified(
                eng, fresh_tenant, days_ago=10, key="key-refresh-004"
            )  # lapses in 355 days
            adapters = {"companies-house": Register(company(active=False))}
            async with worker.begin() as c:
                done = await refresh_pass(c, adapters, ROUTING, sign, NOW, country_gate=closed)
            assert mine(done, due_tenant, fresh_tenant) == {f"{due_tenant}:held_by_country": 1}, (
                "a closed country is held, never refreshed"
            )
            assert len((await ledger(worker, due_tenant, due))[0]) == 1
            assert len((await ledger(worker, fresh_tenant, fresh))[0]) == 1
            async with (
                worker.begin() as c
            ):  # open again: the due case is refreshed, the fresh one still is not
                done = await refresh_pass(c, adapters, ROUTING, sign, NOW)
            assert mine(done, due_tenant, fresh_tenant) == {due_tenant: 1}
            assert len((await ledger(worker, fresh_tenant, fresh))[0]) == 1

    asyncio.run(run())
