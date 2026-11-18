"""Ongoing screening watches on PostgreSQL (WF06 S12; ENG-14; QA-032, QA-048, QA-051; CR-113).

A watch is created inactive, needs two other people and signed gates, and re-screens when its cadence runs out. An attempt and a success are kept apart, a stale list is an attempt and never a success, a
refused run is an attempt, a market with no monitoring approval is held, and "monitored continuously" is read from successes only."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from conftest import ctx, engine
from sqlalchemy import text
from test_screening_requests_pg import MAX_AGE, NOW, load_lists, open_case, pack_for, person, uid
from zid_db import tenant_transaction
from zid_platform.modules.screening.application import watch_pass as wp
from zid_platform.modules.screening.application import watches
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.domain import monitoring_watch as mw
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import parse as parse_config

CADENCE = timedelta(days=1)
CFG = parse_config(
    {"version": "t", "policy": {"required_lists": ["UN_SC"], "list_max_age_hours": 48}}
)
TRIGGERS = frozenset({mw.Trigger.SCHEDULE, mw.Trigger.LIST_UPDATE})


def lookup(tenant: str):
    async def find(conn: Any, t: str, subject_type: Any, subject_ref: str, now: Any) -> Any:
        return ("IDENTITY_VERIFIED_FOR_SCOPE", NOW - timedelta(days=2))

    return find


async def make_case(c: Any, tenant: str) -> str:
    snaps = await store.snapshots(c)
    case = await open_case(c, tenant, "key-watch-0001", person("Alice Wonderland"), snapshots=snaps)
    return str(case["request_id"])


async def run(c: Any, tenant: str, watch_id: str, at: Any, **kw: Any) -> dict[str, Any]:
    args = {
        "pack": pack_for(tenant),
        "snapshots": await store.snapshots(c),
        "max_age": MAX_AGE,
        "match_policy": CFG.policy.match,
        "identity": elig.IdentityClaim(
            "IDENTITY_VERIFIED_FOR_SCOPE", NOW - timedelta(days=2), tenant
        ),
        "now": at,
    }
    args.update(kw)
    return await watches.run_watch(c, watch_id, **args)


def test_a_watch_needs_two_other_people_and_signed_gates_before_it_runs(rt_url: str) -> None:
    tenant, creator = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = await make_case(c, tenant)
                w = await watches.create_watch(
                    c, case, created_by=creator, cadence=CADENCE, triggers=TRIGGERS, now=NOW
                )
                assert w["active"] is False and len(w["sources"]) == 5
                with pytest.raises(ScreeningError) as e:
                    await watches.approve_watch(c, w["watch_id"], approver=creator)
                assert e.value.code == "APPROVER_CREATED_THE_WATCH"
                with pytest.raises(ScreeningError) as e2:
                    await watches.activate_watch(c, w["watch_id"], gates_signed=True)
                assert "TWO_APPROVERS_REQUIRED" in e2.value.detail
                await watches.approve_watch(c, w["watch_id"], approver="p1")
                with pytest.raises(ScreeningError) as dup:
                    await watches.approve_watch(c, w["watch_id"], approver="p1")
                assert dup.value.code == "APPROVER_ALREADY_APPROVED"
                await watches.approve_watch(c, w["watch_id"], approver="p2")
                with pytest.raises(ScreeningError) as e3:
                    await watches.activate_watch(c, w["watch_id"], gates_signed=False)
                assert e3.value.detail == "RELEASE_GATES_NOT_SIGNED"
                assert (await watches.activate_watch(c, w["watch_id"], gates_signed=True))[
                    "active"
                ] is True
                assert await watches.due_watches(c, tenant, now=NOW) == [
                    w["watch_id"]
                ]  # never succeeded: due at once
                with pytest.raises(ScreeningError) as nope:
                    await watches.create_watch(
                        c,
                        "00000000-0000-4000-8000-000000000000",
                        created_by=creator,
                        cadence=CADENCE,
                        triggers=TRIGGERS,
                        now=NOW,
                    )
                assert nope.value.code == "CASE_NOT_FOUND"

    asyncio.run(go())


def test_a_run_records_successes_and_a_stale_list_or_a_refusal_records_only_an_attempt(
    rt_url: str,
) -> None:
    tenant, creator = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = await make_case(c, tenant)
                w = (
                    await watches.create_watch(
                        c, case, created_by=creator, cadence=CADENCE, triggers=TRIGGERS, now=NOW
                    )
                )["watch_id"]
                for who in ("p1", "p2"):
                    await watches.approve_watch(c, w, approver=who)
                await watches.activate_watch(c, w, gates_signed=True)

                first = await run(c, tenant, w, NOW)
                assert (
                    first["run"] == 1
                    and first["request_id"]
                    and len(first["sources_succeeded"]) == 5
                    and first["sources_not_succeeded"] == []
                )
                st = await watches.status(c, w, now=NOW + timedelta(hours=1))
                assert st["continuous"] is True and st["runs"] == 1
                assert (
                    await watches.due_watches(c, tenant, now=NOW + timedelta(hours=1)) == []
                )  # inside the cadence: not due

                # a day later it is due; the lists were released more than 48 hours before: an attempt, never a success
                later = NOW + timedelta(days=3)
                assert await watches.due_watches(c, tenant, now=later) == [w]
                stale = await run(c, tenant, w, later)
                assert stale["sources_succeeded"] == [] and len(stale["sources_not_succeeded"]) == 5
                st = await watches.status(c, w, now=later)
                assert st["continuous"] is False  # attempts do not make it continuous
                attempts = {m["last_attempt_at"] for m in st["sources"].values()}
                successes = {m["last_success_at"] for m in st["sources"].values()}
                assert attempts == {later.isoformat()} and successes == {
                    NOW.isoformat()
                }  # the last success is still the first run

                # a run refused by the market (no signed policy) is an attempt and nothing else
                refused = await run(c, tenant, w, later + timedelta(days=1), pack=None)
                assert refused["request_id"] is None and "NO_POLICY_FOR_MARKET" in str(
                    refused["refused"]
                )
                assert (await watches.status(c, w, now=later + timedelta(days=1)))[
                    "continuous"
                ] is False

    asyncio.run(go())


def test_the_scheduled_pass_screens_due_watches_and_holds_those_without_a_monitoring_approval(
    rt_url: str, owner_url: str
) -> None:
    tenant, creator = uid(), uid()

    async def go() -> None:
        async with engine(rt_url) as eng:
            await load_lists(eng, at=NOW - timedelta(hours=1))
            async with tenant_transaction(eng, ctx(tenant)) as c:
                case = await make_case(c, tenant)
                w = (
                    await watches.create_watch(
                        c, case, created_by=creator, cadence=CADENCE, triggers=TRIGGERS, now=NOW
                    )
                )["watch_id"]
                for who in ("p1", "p2"):
                    await watches.approve_watch(c, w, approver=who)
                await watches.activate_watch(c, w, gates_signed=True)

            def refuse(market: str) -> None:
                raise ValueError("NO_MONITORING_APPROVAL")

            async with (
                engine(owner_url) as owner,
                owner.begin() as oc,
            ):  # the worker role reads across tenants
                held = await wp.watch_pass(
                    oc,
                    CFG,
                    NOW,
                    packs={"GB": pack_for(tenant)},
                    identity_lookup=lookup(tenant),
                    monitoring_gate=refuse,
                )
                assert held[f"{tenant}:held_by_monitoring_scope"] == 1 and held[tenant] == 0

                async def unreleased(c: Any, market: str) -> bool:
                    return False

                blocked = await wp.watch_pass(
                    oc,
                    CFG,
                    NOW,
                    packs={"GB": pack_for(tenant)},
                    identity_lookup=lookup(tenant),
                    monitoring_gate=lambda m: None,
                    gate_check=unreleased,
                )
                assert (
                    blocked[f"{tenant}:held_by_release_gates"] == 1 and blocked[tenant] == 0
                )  # ENG-20: no release gates, no ongoing monitoring
                done = await wp.watch_pass(
                    oc,
                    CFG,
                    NOW,
                    packs={"GB": pack_for(tenant)},
                    identity_lookup=lookup(tenant),
                    monitoring_gate=lambda m: None,
                )
                assert done[tenant] == 1
                again = await wp.watch_pass(
                    oc,
                    CFG,
                    NOW + timedelta(hours=1),
                    packs={"GB": pack_for(tenant)},
                    identity_lookup=lookup(tenant),
                    monitoring_gate=lambda m: None,
                )
                assert again[tenant] == 0  # inside the cadence: nothing to do
                runs = (
                    await oc.execute(
                        text(
                            "SELECT runs FROM verify.screening_watch WHERE watch_id = CAST(:w AS uuid)"
                        ),
                        {"w": w},
                    )
                ).scalar_one()
                assert runs == 1

    asyncio.run(go())
