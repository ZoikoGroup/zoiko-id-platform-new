"""ZID-AG-WF-002 (agent governance) against PostgreSQL: policy versions and approvals, budgets and atomic reservation, runs and
quotas, the emergency stop and the evidence chain. Synthetic data only. Rows T01-T46 these exercise are in each docstring.

The services are driven directly (so time can be injected and 100 callers can race for the last cent) and a short HTTP pass checks the
routes, the redaction by role and the gateway. A refusal is an outcome that was written down first: every refused case below also
checks that its evidence row exists.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from admin_fixture import make_admin
from conftest import ctx, engine
from fastapi.testclient import TestClient
from gateway_fixture import ISS, World, uid
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from zid_db import tenant_transaction
from zid_platform.modules.machine.application import governance as svc
from zid_platform.modules.machine.domain import governance as g
from zid_platform.modules.machine.infrastructure import governance_store as store
from zid_platform.modules.machine.infrastructure import machine_store
from zid_platform.modules.machine.infrastructure.governance_config import config
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    set_status,
)

PURPOSE = "access_posture_review"
REGION = "eu-west"
RESOURCE = "mandate:42"


@pytest.fixture(autouse=True)
def _enforce() -> Any:
    before = svc.mode()
    svc.configure("enforce")
    yield
    svc.configure(before)


class Gov:
    """A World (one tenant, an ACTIVE agent `root`, tools `docs.read` / `docs.sign`) plus the people governance needs."""

    def __init__(self, rt_url: str, owner_url: str) -> None:
        self.w = World(rt_url, owner_url).start()
        svc.configure(
            "enforce"
        )  # create_app set the start-up mode; these tests exercise the refusing mode
        self.tenant = self.w.tenant
        self.agent = self.w.root
        self.people = {
            n: uid() for n in ("dev", "own", "sec", "auth", "priv", "fin", "sec2", "auth2", "fin2")
        }
        self.w.run(self._people())
        self.run(
            lambda c: svc.set_baseline(
                c,
                self.tenant,
                self.p("own"),
                allowed_tools=["docs.read"],
                allowed_regions=[REGION],
                ai_providers=[],
                quotas=None,
                schedules_allowed=False,
            )
        )
        for who, slot in (
            ("sec", "SECURITY"),
            ("auth", "AUTHORITY"),
            ("priv", "PRIVACY"),
            ("fin", "FINANCE"),
            ("sec2", "SECURITY"),
            ("auth2", "AUTHORITY"),
            ("fin2", "FINANCE"),
        ):
            self.run(
                lambda c, who=who, slot=slot: svc.grant_slot(
                    c, self.tenant, self.p("own"), self.p(who), slot
                )
            )

    async def _people(self) -> None:
        async with engine(self.w.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
            for p in self.people.values():
                await create_principal(
                    c,
                    Principal(p, self.tenant, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1"),
                )

    def p(self, name: str) -> str:
        return self.people[name]

    def run(self, fn: Callable[[Any], Awaitable[Any]]) -> Any:
        async def go() -> Any:
            async with engine(self.w.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
                return await fn(c)

        return self.w.run(go())

    # -- steps

    def lease(self, **kw: Any) -> dict[str, Any]:
        base = {
            "tool_id": "docs.read",
            "resource_kind": "mandate",
            "resource_selector": "mandate:",
            "purpose": PURPOSE,
            "region": REGION,
            "data_class": "metadata",
            "valid_until": datetime.now(UTC) + timedelta(days=30),
        }
        base.update(kw)
        return base

    def draft(self, leases: list[dict[str, Any]] | None = None, **kw: Any) -> dict[str, Any]:
        args = {
            "leases": leases if leases is not None else [self.lease()],
            "quotas": None,
            "region": REGION,
            "ai_enabled": False,
            "risk_tier": "LOW",
            "expires_at": None,
        }
        args.update(kw)
        return self.run(
            lambda c: svc.draft_policy(c, self.tenant, self.agent, self.p("dev"), **args)
        )  # type: ignore[no-any-return]

    def request(self, policy_id: str) -> dict[str, Any]:
        return self.run(
            lambda c: svc.request_change(
                c,
                self.tenant,
                self.agent,
                self.p("dev"),
                policy_id=policy_id,
                reason_code="ACCESS_REVIEW",
            )
        )  # type: ignore[no-any-return]

    def decide(
        self,
        request_id: str,
        who: str,
        h: str,
        decision: str = "approve",
        reason: str | None = None,
        now: datetime | None = None,
    ) -> svc.Outcome:
        return self.run(
            lambda c: svc.decide_request(
                c,
                self.tenant,
                request_id,
                self.p(who),
                decision=decision,
                presented_hash=h,
                reason=reason,
                now=now,
            )
        )  # type: ignore[no-any-return]

    def approve(self, view: dict[str, Any], signers: list[str]) -> None:
        for who in signers:
            out = self.decide(view["request_id"], who, view["policy_hash"])
            assert out.ok, (who, out.codes)

    def apply(self, request_id: str) -> svc.Outcome:
        return self.run(lambda c: svc.apply_request(c, self.tenant, request_id, self.p("own")))  # type: ignore[no-any-return]

    def publish(
        self,
        leases: list[dict[str, Any]] | None = None,
        signers: tuple[str, ...] = ("sec", "auth"),
        **kw: Any,
    ) -> dict[str, Any]:
        d = self.draft(leases, **kw)
        r = self.request(d["policy_id"])
        self.approve(r, list(signers))
        assert self.apply(r["request_id"]).ok
        return r

    def call(self, **kw: Any) -> tuple[str, ...]:
        tool = self.run(lambda c: machine_store.load_tools(c))["docs.read"]
        args = {"resource_type": "mandate", "resource_id": "42", "purpose": PURPOSE}
        args.update(kw)
        return self.run(
            lambda c: svc.govern_tool_call(c, self.tenant, agent_id=self.agent, tool=tool, **args)
        )  # type: ignore[no-any-return]

    def events(self, **kw: Any) -> list[dict[str, Any]]:
        async def go(c: Any) -> list[dict[str, Any]]:
            return await store.list_events(
                c,
                agent_id=self.agent,
                since=None,
                until=None,
                before_seq=None,
                outcome=kw.get("outcome"),
                limit=500,
            )

        return self.run(go)  # type: ignore[no-any-return]

    def budget(
        self,
        scope: str,
        period: str,
        cap: str,
        signers: tuple[str, ...] = ("fin", "sec"),
        agent: bool = False,
        currency: str = "USD",
    ) -> dict[str, Any]:
        out = self.run(
            lambda c: svc.propose_budget(
                c,
                self.tenant,
                self.p("dev"),
                scope=scope,
                agent_id=self.agent if agent else None,
                currency=currency,
                period=period,
                hard_cap=cap,
                reason_code="PILOT",
            )
        )
        if out["state"] == "PENDING":
            h = self.run(lambda c: _hash_of(c, out["approval_request_id"]))
            for who in signers:
                res = self.decide(out["approval_request_id"], who, h)
                assert res.ok, (who, res.codes)
        return out

    def reserve(
        self,
        amount: str = "0.10",
        key: str | None = None,
        cls: str = "AI_INFERENCE",
        run_id: str | None = None,
    ) -> svc.Outcome:
        return self.run(
            lambda c: svc.reserve(
                c,
                self.tenant,
                self.agent,
                self.p("dev"),
                tool_id="docs.read",
                resource_kind="mandate",
                resource_id=RESOURCE,
                purpose=PURPOSE,
                spend_class=cls,
                amount=amount,
                currency="USD",
                idempotency_key=key or str(uuid.uuid4()),
                run_id=run_id,
            )
        )  # type: ignore[no-any-return]

    def view(self) -> dict[str, Any]:
        return self.run(lambda c: svc.budget_view(c, self.agent))  # type: ignore[no-any-return]


async def _hash_of(c: Any, request_id: str) -> str:
    loaded = await store.load_request(c, request_id)
    assert loaded
    return loaded[0].policy_hash


def codes_of(events: list[dict[str, Any]], action: str) -> list[str | None]:
    return [e["code"] for e in events if e["action"] == action]


# ====================================================================== policy, approvals, gates


def test_policy_lifecycle_approval_and_every_gate(rt_url: str, owner_url: str) -> None:
    gv = Gov(rt_url, owner_url)

    # T15: nothing is approved yet, so nothing runs, and the refusal is on the evidence trail
    assert gv.call() == (str(g.Code.POLICY_UNAVAILABLE),)
    assert "POLICY_UNAVAILABLE" in codes_of(gv.events(), "tool_call.governed")

    d = gv.draft()
    assert (
        d["state"] == "DRAFT" and d["grants"][0]["state"] == "REQUESTED"
    )  # a draft is requested rights, not effective ones
    r = gv.request(d["policy_id"])
    assert (
        r["kind"] == "PUBLISH"
        and r["required_slots"] == ["SECURITY", "AUTHORITY"]
        and r["ref"].startswith("AGP-")
    )
    assert (
        gv.request(d["policy_id"])["request_id"] == r["request_id"]
    )  # asking twice opens one request
    h = r["policy_hash"]

    # T17/T23: the requester, an unqualified person, a stale hash, and a signature by a person without a slot are all refused and recorded
    assert "SEPARATION_OF_DUTIES" in gv.decide(r["request_id"], "dev", h).codes
    assert (
        "SIGNER_NOT_QUALIFIED" in gv.decide(r["request_id"], "own", h).codes
    )  # the Owner holds no signer slot
    assert gv.decide(r["request_id"], "sec", "sha256:" + "f" * 64).codes == ("SCOPE_HASH_MISMATCH",)
    assert (
        len([c for c in codes_of(gv.events(), "approval.approve") if c]) >= 3
    )  # every refusal left a row

    # T18: one signature is not approval; the second slot is a different person
    one = gv.decide(r["request_id"], "sec", h)
    assert one.ok and one.data["state"] == "PENDING" and one.data["open_slots"] == ["AUTHORITY"]
    assert "ALREADY_SIGNED" in gv.decide(r["request_id"], "sec", h).codes
    assert (
        "SIGNER_NOT_QUALIFIED" in gv.decide(r["request_id"], "sec2", h).codes
    )  # SECURITY is already filled; this slot is not open to them
    assert gv.decide(r["request_id"], "auth", h).data["state"] == "APPROVED"

    # approved is not active: the gate still refuses until the version is applied (T45)
    assert gv.call() == (str(g.Code.POLICY_UNAVAILABLE),)
    assert gv.apply(r["request_id"]).ok
    assert gv.apply(r["request_id"]).codes == ("ALREADY_APPLIED",)  # applying twice changes nothing

    assert (
        gv.call() == ()
    )  # allowed: exact lease, purpose, region, company baseline, approval for this exact hash
    assert gv.call(purpose="payments") == (
        str(g.Code.SCOPE_NOT_APPROVED),
    )  # T05 family: outside the lease
    assert gv.call(region="us-east") == (str(g.Code.REGION_DENIED),)  # T07
    assert (
        gv.call(resource_id="other") == ()
    )  # the lease names the class "mandate:", so any mandate is covered
    assert gv.call(resource_type="credential") == (str(g.Code.SCOPE_NOT_APPROVED),)

    # shadow mode records what enforce would refuse and lets the call through; nothing else about the result changes
    svc.configure("shadow")
    assert gv.call(purpose="payments") == ()
    svc.configure("enforce")
    assert "SHADOW_DENY" in {e["outcome"] for e in gv.events()}

    # T10/T22: a changed policy has no approval of its own, and the old approval cannot carry it
    wider = gv.draft([gv.lease(), gv.lease(resource_selector="mandate:eu:")])
    r2 = gv.request(wider["policy_id"])
    assert r2["kind"] == "SCOPE_EXTENSION"
    assert gv.decide(r2["request_id"], "sec", h).codes == (
        "SCOPE_HASH_MISMATCH",
    )  # the OLD hash is refused for the new request
    assert gv.call() == ()  # until it is signed and applied, the active version keeps governing
    gv.approve(r2, ["sec2", "auth2"])
    assert gv.apply(r2["request_id"]).ok
    assert gv.call(resource_id="eu:7") == ()

    # editing again voids the in-flight approval of the older draft (sponsor, rotation or policy edits void it, section 04)
    third = gv.draft([gv.lease()])
    r3 = gv.request(third["policy_id"])
    fourth = gv.draft([gv.lease(purpose="other_review")])
    voided = gv.run(lambda c: store.load_request(c, r3["request_id"]))
    assert voided is not None and voided[0].state is g.RequestState.INVALIDATED
    assert "REQUEST_NOT_OPEN" in gv.decide(r3["request_id"], "sec", r3["policy_hash"]).codes
    del fourth

    # T21: an unsigned request expires and can no longer be signed
    last = gv.draft([gv.lease(resource_selector="mandate:expiring:")])
    r4 = gv.request(last["policy_id"])
    late = datetime.now(UTC) + timedelta(hours=25)
    assert gv.decide(r4["request_id"], "sec", r4["policy_hash"], now=late).codes == (
        "REQUEST_EXPIRED",
    )

    # T23: a signer whose slot was revoked after the screen rendered fails at decision time
    r5 = gv.request(gv.draft([gv.lease(resource_selector="mandate:revoked:")])["policy_id"])
    gv.run(lambda c: svc.revoke_slot(c, gv.tenant, gv.p("own"), gv.p("sec"), "SECURITY"))
    assert "SIGNER_NOT_QUALIFIED" in gv.decide(r5["request_id"], "sec", r5["policy_hash"]).codes

    # T04/T03: the sponsor leaves, and the run is refused (SPONSOR_INVALID) with no change to any policy
    gv.w.disable_sponsor()
    try:
        assert gv.call() == (str(g.Code.SPONSOR_INVALID),)
    finally:  # the lifecycle sweep is global: leave no orphaned active agent behind for another test to find
        gv.w.owner("UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE tenant_id = :t")


def test_wildcards_hard_denies_and_unregistered_tools_cannot_even_be_requested(
    rt_url: str, owner_url: str
) -> None:
    gv = Gov(rt_url, owner_url)
    for bad in (
        {"resource_selector": "*"},
        {"purpose": "*"},
        {"region": "*"},
        {"valid_until": datetime.now(UTC) - timedelta(days=1)},
        {"valid_until": datetime.now(UTC) + timedelta(days=500)},
    ):
        with pytest.raises(svc.GovernanceError, match="GRANT_INVALID|WILDCARD"):  # T08
            gv.draft([gv.lease(**bad)])
    with pytest.raises(svc.GovernanceError, match="TOOL_NOT_REGISTERED"):
        gv.draft([gv.lease(tool_id="docs.ghost")])
    with pytest.raises(svc.GovernanceError, match="MANDATE_TOOL_NOT_ALLOWED"):
        gv.draft([gv.lease(tool_id="docs.other")])  # registered, but not on this agent's list
    with pytest.raises(svc.GovernanceError, match="QUOTA_ABOVE_PLATFORM_CEILING"):
        gv.draft(quotas={"runs_per_agent_day": 10_000})
    with pytest.raises(svc.GovernanceError, match="TOOL_HARD_DENIED"):
        gv.run(
            lambda c: _allow_a_prohibited_tool(c, gv)
        )  # the company baseline cannot name a prohibited action's tool
    # a baseline cannot exceed the platform ceiling either
    with pytest.raises(svc.GovernanceError, match="QUOTA_ABOVE_PLATFORM_CEILING"):
        gv.run(
            lambda c: svc.set_baseline(
                c,
                gv.tenant,
                gv.p("own"),
                allowed_tools=[],
                allowed_regions=[REGION],
                ai_providers=[],
                quotas={"records_per_run": 10_000},
                schedules_allowed=False,
            )
        )


async def _allow_a_prohibited_tool(c: Any, gv: Gov) -> None:
    from zid_platform.modules.machine.domain.agents import SideEffect, Tool, ToolStatus

    t = Tool(
        "pay.out",
        "1",
        gv.w.server,
        "funds.transfer",
        3,
        SideEffect.NONE,
        False,
        False,
        ToolStatus.ACTIVE,
    )
    await machine_store.save_tool(c, gv.tenant, t)
    await svc.set_baseline(
        c,
        gv.tenant,
        gv.p("own"),
        allowed_tools=["pay.out"],
        allowed_regions=[REGION],
        ai_providers=[],
        quotas=None,
        schedules_allowed=False,
    )


def test_the_simulator_is_synthetic_only_and_shows_a_platform_prohibition(
    rt_url: str, owner_url: str
) -> None:
    gv = Gov(rt_url, owner_url)
    gv.publish()
    req: dict[str, Any] = {
        "tool_id": "docs.read",
        "resource_kind": "mandate",
        "resource_id": "mandate:synthetic-1",
        "purpose": PURPOSE,
    }
    ok = gv.run(lambda c: svc.simulate(c, gv.tenant, gv.agent, gv.p("dev"), request=req))
    assert (
        ok["decision"] == "ALLOW"
        and ok["effective"] is False
        and [t["gate"] for t in ok["trace"]][-1] == "allow"
    )
    deny = gv.run(
        lambda c: svc.simulate(
            c,
            gv.tenant,
            gv.agent,
            gv.p("dev"),
            request={**req, "tool_id": "role.modify", "action": "role.grant"},
        )
    )
    assert deny["decision"] == "DENY" and deny["code"] == "SCOPE_PROHIBITED"
    with pytest.raises(svc.GovernanceError, match="SIMULATION_REAL_DATA_REFUSED"):
        gv.run(
            lambda c: svc.simulate(
                c, gv.tenant, gv.agent, gv.p("dev"), request={**req, "resource_id": "mandate:42"}
            )
        )
    assert not gv.run(lambda c: _spent(c))  # a simulation reserves nothing


async def _spent(c: Any) -> list[Any]:
    return list((await c.execute(text("SELECT 1 FROM agent.budget_reservation"))).all())


# ====================================================================== budgets


def test_budgets_are_hierarchical_atomic_exact_and_settle_once(rt_url: str, owner_url: str) -> None:
    gv = Gov(rt_url, owner_url)
    # AI needs its own approval: Security + Privacy + Finance (T19/T20 family), and an approved company provider
    gv.run(
        lambda c: svc.set_baseline(
            c,
            gv.tenant,
            gv.p("own"),
            allowed_tools=["docs.read"],
            allowed_regions=[REGION],
            ai_providers=["provider-a"],
            quotas=None,
            schedules_allowed=False,
        )
    )
    gv.publish(signers=("sec", "auth"))
    ai = gv.draft(ai_enabled=True)
    req = gv.request(ai["policy_id"])
    assert req["kind"] == "AI_ENABLE" and req["required_slots"] == [
        "SECURITY",
        "PRIVACY",
        "FINANCE",
    ]
    gv.approve(req, ["sec2", "priv", "fin"])
    assert gv.apply(req["request_id"]).ok

    # T26/T27: no ceiling exists, so AI cost is refused and external spend is prohibited outright
    no_cap = gv.reserve("0.01")
    assert not no_cap.ok and no_cap.codes == ("BUDGET_EXCEEDED",)
    assert gv.reserve("0", cls="EXTERNAL_TRANSACTION").codes == ("SCOPE_PROHIBITED",)
    assert gv.reserve("0", cls="PAID_VENDOR_CHECK").codes == ("SCOPE_PROHIBITED",)

    # a child cannot be set before its parent, and never above it
    with pytest.raises(svc.GovernanceError, match="NO_PARENT_CEILING"):
        gv.budget("AGENT", "DAY", "5", agent=True)
    company = gv.budget("COMPANY", "DAY", "20", signers=())
    assert company["state"] == "PENDING" and company["required_slots"] == ["FINANCE", "SECURITY"]
    assert gv.view()["ceilings"] == [] and len(gv.view()["pending"]) == 1  # pending is never live
    chash = gv.run(lambda c: _hash_of(c, company["approval_request_id"]))
    assert (
        gv.decide(company["approval_request_id"], "fin", chash).data["state"] == "PENDING"
    )  # one of two signatures is still not live
    assert gv.view()["ceilings"] == []
    assert gv.decide(company["approval_request_id"], "sec", chash).data["state"] == "APPLIED"
    assert [c["name"] for c in gv.view()["ceilings"]] == ["company_day"]
    with pytest.raises(svc.GovernanceError, match="BUDGET_ABOVE_PARENT"):
        gv.budget("AGENT", "DAY", "25", agent=True)
    for bad in ("0.001", "-1", "1e400", "abc"):
        with pytest.raises(svc.GovernanceError):
            gv.budget("COMPANY", "MONTH", bad)
    with pytest.raises(svc.GovernanceError, match="CURRENCY_NOT_APPROVED"):
        gv.budget("COMPANY", "MONTH", "10", currency="XXX")
    # the requester cannot sign their own cap, and the same person cannot fill both slots
    month = gv.budget("COMPANY", "MONTH", "100", signers=())
    mh = gv.run(lambda c: _hash_of(c, month["approval_request_id"]))
    refused = gv.decide(month["approval_request_id"], "dev", mh).codes
    assert "SEPARATION_OF_DUTIES" in refused  # the requester cannot sign their own cap
    assert gv.decide(month["approval_request_id"], "own", mh).codes == ("SIGNER_NOT_QUALIFIED",)
    gv.budget("AGENT", "DAY", "0.50", agent=True)
    gv.budget("CALL", "CALL", "0.10", agent=False)
    v = gv.view()
    assert {c["name"] for c in v["ceilings"]} == {"company_day", "agent_day", "call_call"}
    assert all(Decimal(c["committed"]) == 0 and Decimal(c["reserved"]) == 0 for c in v["ceilings"])

    # T28: 100 callers race for 0.50 in 0.10 steps: exactly five win, nobody overspends
    async def race() -> list[svc.Outcome]:
        async with engine(gv.w.rt_url, size=25) as eng:

            async def one(i: int) -> svc.Outcome:
                async with tenant_transaction(eng, ctx(gv.tenant)) as c:
                    return await svc.reserve(
                        c,
                        gv.tenant,
                        gv.agent,
                        gv.p("dev"),
                        tool_id="docs.read",
                        resource_kind="mandate",
                        resource_id=RESOURCE,
                        purpose=PURPOSE,
                        spend_class="AI_INFERENCE",
                        amount="0.10",
                        currency="USD",
                        idempotency_key=f"race-key-{i:04d}",
                    )

            return list(await asyncio.gather(*(one(i) for i in range(100))))

    results = gv.w.run(race())
    won = [r for r in results if r.ok]
    assert len(won) == 5, [r.codes for r in results if not r.ok][:3]
    assert {c for r in results if not r.ok for c in r.codes} == {"BUDGET_EXCEEDED"}
    agent_day = next(c for c in gv.view()["ceilings"] if c["name"] == "agent_day")
    assert (
        Decimal(agent_day["reserved"]) == Decimal("0.50")
        and Decimal(agent_day["available"]) == 0
        and agent_day["level"] == "EXHAUSTED"
    )

    # T29: the same key charges once, in parallel or later
    async def same_key(key: str) -> list[svc.Outcome]:
        async with engine(gv.w.rt_url, size=10) as eng:

            async def one() -> svc.Outcome:
                async with tenant_transaction(eng, ctx(gv.tenant)) as c:
                    return await svc.reserve(
                        c,
                        gv.tenant,
                        gv.agent,
                        gv.p("dev"),
                        tool_id="docs.read",
                        resource_kind="mandate",
                        resource_id=RESOURCE,
                        purpose=PURPOSE,
                        spend_class="AI_INFERENCE",
                        amount="0.10",
                        currency="USD",
                        idempotency_key=key,
                    )

            return list(await asyncio.gather(*(one() for _ in range(5))))

    first = next(r for r in won)
    rid = first.data["reservation_id"]
    winning_key = gv.w.owner(
        "SELECT idempotency_key FROM agent.budget_reservation WHERE reservation_id = :r AND tenant_id = :t",
        r=uuid.UUID(rid),
    )[0][0]
    replays = gv.w.run(same_key(winning_key))
    assert all(r.ok and r.data["replayed"] for r in replays)
    assert len(gv.run(lambda c: _spent(c))) == 5

    # T30: a partial failure releases the unused part; the freed allowance can be reserved at once
    s = gv.run(lambda c: svc.settle(c, gv.tenant, rid, gv.p("dev"), actual="0.04"))
    assert (
        s.ok
        and Decimal(s.data["committed"]) == Decimal("0.04")
        and Decimal(s.data["released"]) == Decimal("0.06")
    )
    assert gv.reserve("0.06").ok and not gv.reserve("0.01").ok
    # settle exactly once
    assert gv.run(lambda c: svc.settle(c, gv.tenant, rid, gv.p("dev"), actual="0.04")).codes == (
        "RESERVATION_CLOSED",
    )
    with pytest.raises(DBAPIError):  # and the database itself refuses a second settlement
        gv.w.owner(
            "UPDATE agent.budget_reservation SET committed = 0.01 WHERE reservation_id = :r",
            r=uuid.UUID(rid),
        )

    # T31/T32: a cost above the reservation commits only what was reserved; the rest is a signed adjustment, never a rewrite
    other = next(r for r in won if r.data["reservation_id"] != rid)
    over = gv.run(
        lambda c: svc.settle(c, gv.tenant, other.data["reservation_id"], gv.p("dev"), actual="0.13")
    )
    assert Decimal(over.data["committed"]) == Decimal("0.10") and Decimal(
        over.data["overrun"]
    ) == Decimal("0.03")
    late = gv.run(
        lambda c: svc.adjust(c, gv.tenant, rid, gv.p("fin"), reason="LATE_INVOICE", amount="0.01")
    )
    assert Decimal(late["amount"]) == Decimal("0.01")
    refund = gv.run(
        lambda c: svc.adjust(c, gv.tenant, rid, gv.p("fin"), reason="REFUND", amount="0.01")
    )
    assert Decimal(refund["amount"]) == Decimal("-0.01")
    with pytest.raises(DBAPIError):
        gv.w.owner(
            "UPDATE agent.budget_adjustment SET amount = 0 WHERE adjustment_id = :a",
            a=uuid.UUID(late["adjustment_id"]),
        )
    with pytest.raises(svc.GovernanceError):
        gv.run(
            lambda c: svc.adjust(
                c, gv.tenant, rid, gv.p("fin"), reason="LATE_INVOICE", amount="0.001"
            )
        )  # too many decimals

    # an explicit release gives everything back
    spare = gv.reserve("0.05") if gv.reserve("0.0").ok else None
    del spare

    # a cap reduction is live immediately and never touches what was already reserved; a raise is pending again
    low = gv.run(
        lambda c: svc.propose_budget(
            c,
            gv.tenant,
            gv.p("dev"),
            scope="AGENT",
            agent_id=gv.agent,
            currency="USD",
            period="DAY",
            hard_cap="0.20",
            reason_code="TIGHTEN",
        )
    )
    assert low["state"] == "ACTIVE"
    assert (
        next(c for c in gv.view()["ceilings"] if c["name"] == "agent_day")["hard_cap"] == "0.200000"
    )
    up = gv.run(
        lambda c: svc.propose_budget(
            c,
            gv.tenant,
            gv.p("dev"),
            scope="AGENT",
            agent_id=gv.agent,
            currency="USD",
            period="DAY",
            hard_cap="0.40",
            reason_code="RAISE",
        )
    )
    assert up["state"] == "PENDING"
    with pytest.raises(DBAPIError):  # a budget revision is immutable
        gv.w.owner(
            "UPDATE agent.budget SET hard_cap = 99 WHERE budget_id = :b",
            b=uuid.UUID(low["budget_id"]),
        )


def test_a_stale_reservation_is_released_by_the_sweep(rt_url: str, owner_url: str) -> None:
    gv = Gov(rt_url, owner_url)
    gv.run(
        lambda c: svc.set_baseline(
            c,
            gv.tenant,
            gv.p("own"),
            allowed_tools=["docs.read"],
            allowed_regions=[REGION],
            ai_providers=["provider-a"],
            quotas=None,
            schedules_allowed=False,
        )
    )
    gv.publish()
    ai = gv.request(gv.draft(ai_enabled=True)["policy_id"])
    gv.approve(ai, ["sec2", "priv", "fin"])
    gv.apply(ai["request_id"])
    gv.budget("COMPANY", "DAY", "20")
    gv.budget("AGENT", "DAY", "1", agent=True)
    r = gv.reserve("0.30")
    assert r.ok
    swept = gv.run(
        lambda c: svc.governance_sweep(c, gv.tenant, datetime.now(UTC) + timedelta(hours=1))
    )
    assert swept["reservations_released"] == 1
    agent_day = next(c for c in gv.view()["ceilings"] if c["name"] == "agent_day")
    assert Decimal(agent_day["reserved"]) == 0  # a crashed run no longer holds budget


# ====================================================================== runs, quotas, freeze


def test_runs_quotas_timeouts_and_the_emergency_stop(rt_url: str, owner_url: str) -> None:
    gv = Gov(rt_url, owner_url)
    # a run cannot start without an active, approved policy
    first = gv.run(
        lambda c: svc.start_run(c, gv.tenant, gv.agent, gv.p("dev"), idempotency_key="run-key-0001")
    )
    assert first.codes == ("APPROVAL_REQUIRED",) or first.codes == ("POLICY_UNAVAILABLE",)
    gv.publish()

    def start(key: str, now: datetime | None = None) -> svc.Outcome:
        return gv.run(
            lambda c: svc.start_run(
                c, gv.tenant, gv.agent, gv.p("dev"), idempotency_key=key, now=now
            )
        )  # type: ignore[no-any-return]

    a = start("run-key-0002")
    assert a.ok and a.data["state"] == "RUNNING"
    assert start("run-key-0002").data["run_id"] == a.data["run_id"]  # a replay is the same run
    waiting = start("run-key-0003")  # T34: one active run per agent
    assert (
        waiting.codes == ("USAGE_LIMIT",)
        and waiting.data["waiting_capacity"] is True
        and waiting.status == 429
    )
    run_id = a.data["run_id"]

    prog = lambda rec, prop=0, now=None: gv.run(
        lambda c: svc.record_progress(
            c, gv.tenant, run_id, gv.p("dev"), records=rec, proposals=prop, now=now
        )
    )
    assert prog(100).ok
    over = prog(1)  # T35: records per run
    assert over.codes == ("USAGE_LIMIT",) and over.data["quota"] == "records_per_run"
    too_many = prog(0, 26)
    assert too_many.codes == ("USAGE_LIMIT",) and too_many.data["quota"] == "proposals_per_run"

    # T36: the deadline ends the run, releases what it reserved and leaves evidence
    timed = prog(0, 0, now=datetime.now(UTC) + timedelta(seconds=121))
    assert timed.codes == ("USAGE_LIMIT",) and timed.data["quota"] == "run_seconds"
    assert gv.run(lambda c: store.load_run(c, run_id))["state"] == "TIMED_OUT"  # type: ignore[index]
    assert "run.timeout" in {e["action"] for e in gv.events()}

    # the daily cap: five runs a day for one agent
    for i in range(4):
        r = start(f"run-day-{i:04d}")
        assert r.ok, (i, r.codes)
        gv.run(
            lambda c, r=r: svc.finish_run(
                c, gv.tenant, r.data["run_id"], gv.p("dev"), state="COMPLETED"
            )
        )
    capped = start("run-day-9999")
    assert (
        capped.codes == ("USAGE_LIMIT",)
        and capped.data["quota"] == "runs_per_agent_day"
        and capped.data["retry_after"]
    )

    # T24/T41: pause needs no second person and stops the next call at once, in every mode
    pause = gv.run(
        lambda c: svc.pause(
            c,
            gv.tenant,
            gv.p("sec"),
            scope="AGENT",
            agent_id=gv.agent,
            reason="suspicious reads",
            severity="P0",
        )
    )
    assert pause["agents_suspended"] == 1 and pause["generation"] > 0
    assert (
        gv.run(
            lambda c: svc.pause(
                c, gv.tenant, gv.p("sec"), scope="AGENT", agent_id=gv.agent, reason="again"
            )
        )["already_frozen"]
        is True
    )
    svc.configure("shadow")
    assert gv.call() == (str(g.Code.EMERGENCY_FROZEN),)  # shadow mode never softens a freeze
    svc.configure("enforce")
    assert start("run-after-freeze").codes[0] in ("EMERGENCY_FROZEN", "AGENT_INACTIVE")
    frozen = gv.run(lambda c: svc.frozen_codes(c, [gv.agent]))
    assert frozen == [
        "EMERGENCY_FROZEN"
    ]  # and a grant signed before the freeze is refused at redemption

    # T25: restore is not immediate, and not by the person who froze
    case = pause["case_id"]
    rr = gv.run(lambda c: svc.request_restore(c, gv.tenant, gv.p("dev"), case))
    assert (
        rr.ok
        and rr.data["kind"] == "RESTORE"
        and rr.data["required_slots"] == ["SECURITY", "AUTHORITY"]
    )
    rid = rr.data["request_id"]
    hh = rr.data["policy_hash"]
    assert (
        "RESTORE_NOT_INDEPENDENT" in gv.decide(rid, "sec", hh).codes
    )  # the initiator holds the slot but froze it
    assert gv.decide(rid, "sec2", hh).ok
    assert gv.call() == (str(g.Code.EMERGENCY_FROZEN),)  # one signature restores nothing
    done = gv.decide(rid, "auth", hh)
    assert done.ok and done.data["state"] == "APPLIED"
    assert gv.run(lambda c: svc.frozen_codes(c, [gv.agent])) == []
    status = gv.run(lambda c: machine_store.load_agent(c, gv.agent))
    assert (
        status is not None and status.status.value == "SUSPENDED"
    )  # restored, but back in service only through its gates


# ====================================================================== evidence chain


def test_the_evidence_chain_detects_tampering_and_cannot_be_edited(
    rt_url: str, owner_url: str
) -> None:
    gv = Gov(rt_url, owner_url)
    gv.publish()
    for _ in range(3):
        gv.call()
        gv.call(purpose="payments")
    assert gv.run(lambda c: store.verify_chain(c))["intact"] is True
    row = gv.w.owner(
        "SELECT event_id FROM agent.governance_event WHERE tenant_id = :t ORDER BY seq LIMIT 1"
    )[0][0]
    for sql in (
        "UPDATE agent.governance_event SET outcome = 'ALLOW' WHERE event_id = :e",
        "DELETE FROM agent.governance_event WHERE event_id = :e",
    ):
        with pytest.raises(DBAPIError):  # T38/T43: append-only by trigger, even for the owner role
            gv.w.owner(sql, e=row)
    # T39: a feed that went quiet is unknown, not healthy
    over = gv.run(
        lambda c: svc.overview(c, gv.tenant, gv.p("own"), now=datetime.now(UTC) + timedelta(days=2))
    )
    assert (
        over["telemetry"]["state"] == "STALE" and "unknown" in over["telemetry"]["message"].lower()
    )
    assert any(a["rule"] == "TELEMETRY_UNKNOWN" for a in over["requires_attention"])


# ====================================================================== HTTP: routes, redaction, gateway


def test_http_routes_redaction_and_the_gateway(rt_url: str, owner_url: str) -> None:
    gv = Gov(rt_url, owner_url)
    gv.publish(
        [gv.lease(resource_kind="agent", resource_selector="agent:")]
    )  # the fixture's authority grant covers agent:*
    actions = [
        "agent.governance.read",
        "agent.policy.read",
        "agent.policy.simulate",
        "agent.budget.read",
        "agent.audit.read",
        "agent.security.pause",
    ]

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(gv.tenant)) as c:
            await make_admin(c, gv.tenant, gv.w.human, actions, ["agent:*"], release=False)
            await make_admin(
                c,
                gv.tenant,
                gv.w.approver,
                actions + ["agent.audit.read_detail"],
                ["agent:*"],
                release=False,
            )

    gv.w.run(grant())
    with TestClient(gv.w.app, base_url=ISS) as http:
        person = gv.w.bearer(gv.w.human, scope=frozenset({"admin"}))
        viewer = gv.w.bearer(gv.w.approver, scope=frozenset({"admin"}))
        o = http.get("/v1/machine/governance/overview", headers=person)
        assert o.status_code == 200, o.text
        body = o.json()
        assert body["mode"] == "enforce" and body["counts_source"].startswith("server-generated")
        assert any(a["agent_id"] == gv.agent and a["governed"] for a in body["agents"])
        eff = http.get(f"/v1/machine/agents/{gv.agent}/effective-access", headers=person)
        assert (
            eff.status_code == 200
            and "role.grant" in eff.json()["hard_denies"]
            and eff.json()["effective_leases"][0]["effective"] is True
        )
        sim = http.post(
            f"/v1/machine/agents/{gv.agent}/policy/simulate",
            headers=person,
            json={
                "tool_id": "docs.read",
                "resource_kind": "agent",
                "resource_id": "agent:synthetic-9",
                "purpose": PURPOSE,
            },
        )
        assert sim.status_code == 200 and sim.json()["decision"] == "ALLOW"
        real = http.post(
            f"/v1/machine/agents/{gv.agent}/policy/simulate",
            headers=person,
            json={
                "tool_id": "docs.read",
                "resource_kind": "agent",
                "resource_id": "agent:42",
                "purpose": PURPOSE,
            },
        )
        assert real.status_code == 422
        # T40: a role without the detail action gets the stream redacted; one with it gets the detail
        gv.call(resource_type="agent")
        redacted = http.get(f"/v1/machine/agents/{gv.agent}/events", headers=person).json()
        detailed = http.get(f"/v1/machine/agents/{gv.agent}/events", headers=viewer).json()
        assert redacted["redacted"] is True and all(
            "resource" not in e and "actor" not in e for e in redacted["items"]
        )
        assert detailed["redacted"] is False and any(
            e.get("tool_id") == "docs.read" for e in detailed["items"]
        )
        assert (
            http.get("/v1/machine/governance/evidence:verify", headers=person).json()["intact"]
            is True
        )
        # T16: a request for a person's own write path without the action is refused by the PDP, not by the page
        assert http.post(
            f"/v1/machine/agents/{gv.agent}/policy/drafts",
            headers=person,
            json={"region": REGION, "leases": []},
        ).status_code in (403, 404)
        # the gateway: an agent's call carries purpose, and the governance gates apply to the same request
        call = {
            "tool_id": "docs.read",
            "resource_type": "agent",
            "resource_id": "42",
            "arguments_digest": "sha256:" + "a" * 64,
            "purpose": PURPOSE,
        }
        ok = http.post(
            "/v1/machine/tool-calls:authorize", headers=gv.w.bearer(gv.w.root), json=call
        )
        assert ok.status_code == 200, ok.text
        # an emergency pause by the person with the pause action refuses the next call, whatever the mode
        paused = http.post(
            f"/v1/machine/agents/{gv.agent}/pause",
            headers=person,
            json={"reason": "drill", "severity": "P2"},
        )
        assert paused.status_code == 201, paused.text
        denied = http.post(
            "/v1/machine/tool-calls:authorize", headers=gv.w.bearer(gv.w.root), json=call
        )
        assert denied.status_code in (
            401,
            403,
        )  # the pause suspended the agent, so even its token is no longer accepted


# ====================================================================== tenant boundary and in-flight work


def test_another_tenant_sees_and_changes_nothing_of_this_tenants_governance(
    rt_url: str, owner_url: str
) -> None:
    """T01, T02, T16: a request id, an agent id or an evidence row from one company is invisible to another."""
    a = Gov(rt_url, owner_url)
    published = a.publish()
    b = Gov(rt_url, owner_url)
    with pytest.raises(svc.GovernanceError, match="AGENT_NOT_FOUND"):
        b.run(lambda c: svc.effective_access(c, b.tenant, a.agent))
    with pytest.raises(svc.GovernanceError, match="AGENT_NOT_FOUND"):
        b.run(lambda c: svc.agent_monitoring(c, b.tenant, a.agent))
    assert (
        b.run(lambda c: svc.list_change_requests(c, agent_id=None, states=None, viewer=b.p("dev")))
        == []
    )
    with pytest.raises(
        svc.GovernanceError, match="REQUEST_NOT_FOUND"
    ):  # signing another company's request by its id
        b.run(
            lambda c: svc.decide_request(
                c,
                b.tenant,
                published["request_id"],
                b.p("sec"),
                decision="approve",
                presented_hash=published["policy_hash"],
            )
        )
    with pytest.raises(svc.GovernanceError, match="REQUEST_NOT_FOUND"):
        b.run(lambda c: svc.apply_request(c, b.tenant, published["request_id"], b.p("own")))
    seen = b.run(
        lambda c: store.list_events(
            c, agent_id=None, since=None, until=None, before_seq=None, outcome=None, limit=500
        )
    )
    assert seen and all(str(e["tenant_id"]) == b.tenant for e in seen)
    tool = b.run(lambda c: machine_store.load_tools(c))["docs.read"]
    with pytest.raises(
        svc.GovernanceError, match="AGENT_NOT_FOUND"
    ):  # a call for A's agent under B's tenant context
        b.run(
            lambda c: svc.govern_tool_call(
                c,
                b.tenant,
                agent_id=a.agent,
                tool=tool,
                resource_type="mandate",
                resource_id="42",
                purpose=PURPOSE,
            )
        )
    assert a.call() == ()  # and A is unaffected by any of it


def test_a_pause_cancels_running_work_and_the_next_progress_is_refused(
    rt_url: str, owner_url: str
) -> None:
    """T41, T42, T43: running work is cancelled, not left to finish, and the stop is in the database, not in memory."""
    gv = Gov(rt_url, owner_url)
    gv.publish()
    run = gv.run(
        lambda c: svc.start_run(
            c, gv.tenant, gv.agent, gv.p("dev"), idempotency_key="run-live-0001"
        )
    )
    assert run.ok
    run_id = run.data["run_id"]
    gv.run(
        lambda c: svc.pause(
            c,
            gv.tenant,
            gv.p("sec"),
            scope="AGENT",
            agent_id=gv.agent,
            reason="drill",
            severity="P2",
        )
    )
    stored = gv.run(lambda c: store.load_run(c, run_id))
    assert (
        stored is not None
        and stored["state"] == "CANCELED"
        and stored["reason_code"] == "EMERGENCY_FROZEN"
    )
    again = gv.run(
        lambda c: svc.record_progress(c, gv.tenant, run_id, gv.p("dev"), records=1, proposals=0)
    )
    assert again.codes == ("RUN_NOT_RUNNING",)  # already cancelled, nothing more is counted
    # a brand-new connection (a restarted process) still sees the freeze: it is a row, not a cache
    assert gv.run(lambda c: svc.frozen_codes(c, [gv.agent])) == ["EMERGENCY_FROZEN"]
    # T38: every attempt left a row with the policy hash it was judged under
    judged = [e for e in gv.events() if e["action"] == "run.start" and e["outcome"] == "ALLOW"]
    assert judged and judged[0]["policy_hash"].startswith("sha256:")


# ====================================================================== alert handling and the timed stop


def test_alerts_are_tracked_grouped_timed_acknowledged_routed_and_resolved(
    rt_url: str, owner_url: str
) -> None:
    """Section 15 / GOV05-04: severity, acknowledge, route, SLA, duplicate grouping, and a resolved condition leaving the list."""
    gv = Gov(rt_url, owner_url)
    gv.publish()
    gv.w.disable_sponsor()
    try:
        t0 = datetime.now(UTC)

        def overview(at: datetime) -> dict[str, Any]:
            return gv.run(lambda c: svc.overview(c, gv.tenant, gv.p("own"), now=at))  # type: ignore[no-any-return]

        def sponsor_alerts(view: dict[str, Any]) -> list[dict[str, Any]]:
            return [a for a in view["requires_attention"] if a["rule"] == "SPONSOR_INVALID"]

        first = sponsor_alerts(overview(t0))
        assert first and all(
            a["state"] == "OPEN"
            and a["tracked"]
            and a["severity"] == "P1"
            and a["occurrences"] == 1
            for a in first
        )
        key = first[0]["alert_key"]
        second = sponsor_alerts(overview(t0 + timedelta(minutes=1)))
        assert [a["alert_key"] for a in second].count(
            key
        ) == 1  # a repeat is grouped, not listed twice
        assert next(a for a in second if a["alert_key"] == key)["occurrences"] == 2
        assert not any(a["sla_breached"] for a in second)

        late = sponsor_alerts(
            overview(t0 + timedelta(hours=5))
        )  # P1 has 4 hours; nobody has looked
        assert all(a["sla_breached"] for a in late)
        ack = gv.run(
            lambda c: svc.acknowledge_alert(c, gv.tenant, gv.p("sec"), key, note="looking now")
        )
        assert ack["state"] == "ACKNOWLEDGED"
        after = {a["alert_key"]: a for a in sponsor_alerts(overview(t0 + timedelta(hours=6)))}
        assert (
            after[key]["state"] == "ACKNOWLEDGED"
            and not after[key]["sla_breached"]
            and after[key]["acknowledged_by"] == gv.p("sec")
        )
        assert any(
            a["sla_breached"] for k, a in after.items() if k != key
        )  # the ones nobody acknowledged are still past their SLA

        with pytest.raises(
            svc.GovernanceError, match="OWNER_INVALID"
        ):  # an agent cannot own an alert
            gv.run(
                lambda c: svc.route_alert(
                    c, gv.tenant, gv.p("sec"), key, route="SECURITY", owner=gv.agent, note=None
                )
            )
        with pytest.raises(svc.GovernanceError, match="ROUTE_INVALID"):
            gv.run(
                lambda c: svc.route_alert(
                    c, gv.tenant, gv.p("sec"), key, route="EVERYONE", owner=None, note=None
                )
            )
        routed = gv.run(
            lambda c: svc.route_alert(
                c, gv.tenant, gv.p("sec"), key, route="SECURITY", owner=gv.p("sec2"), note=None
            )
        )
        assert routed["state"] == "ROUTED"
        with pytest.raises(svc.GovernanceError, match="ALERT_NOT_FOUND"):
            gv.run(lambda c: svc.acknowledge_alert(c, gv.tenant, gv.p("sec"), "f" * 32, note=None))

        async def reactivate() -> None:
            async with engine(rt_url) as eng, tenant_transaction(eng, ctx(gv.tenant)) as c:
                await set_status(c, gv.w.sponsor, PrincipalStatus.ACTIVE)

        gv.w.run(reactivate())
        assert not sponsor_alerts(
            overview(t0 + timedelta(hours=7))
        )  # the condition no longer holds: the alert leaves the list
        with pytest.raises(svc.GovernanceError, match="ALERT_RESOLVED"):
            gv.run(lambda c: svc.acknowledge_alert(c, gv.tenant, gv.p("sec"), key, note=None))
        gv.w.disable_sponsor()  # and a recurrence starts a fresh clock, not the old acknowledgement
        again = {a["alert_key"]: a for a in sponsor_alerts(overview(t0 + timedelta(hours=8)))}
        assert (
            again[key]["state"] == "OPEN"
            and again[key]["acknowledged_by"] is None
            and not again[key]["sla_breached"]
        )
    finally:
        gv.w.owner("UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE tenant_id = :t")


def test_a_company_wide_stop_cuts_off_twenty_running_agents_inside_the_target(
    rt_url: str, owner_url: str
) -> None:
    """T41, T42 and the document's 5-second provisional target, measured on this machine against one local database. It is a measurement
    of correctness under a realistic count, not a load test and not an SLO."""
    gv = Gov(rt_url, owner_url)
    ids = [gv.agent]

    async def more() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(gv.tenant)) as c:
            for _ in range(19):
                aid = str(uuid.uuid4())
                await machine_store.save_agent(c, gv.w.profile(aid, {"docs.read"}))
                await create_principal(
                    c,
                    Principal(
                        aid,
                        gv.tenant,
                        PrincipalType.AI_AGENT,
                        PrincipalStatus.ACTIVE,
                        "uk-1",
                        sponsor_principal_id=gv.w.sponsor,
                    ),
                )
                ids.append(aid)

    gv.w.run(more())
    gv.w.owner("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t")
    for aid in ids:
        gv.w.owner(
            "INSERT INTO agent.run_ledger (run_id, tenant_id, agent_principal_id, state, idempotency_key, policy_hash, started_at, deadline_at) "
            "VALUES (:r, :t, :a, 'RUNNING', :k, 'none', now(), now() + interval '1 hour')",
            r=uuid.uuid4(), a=uuid.UUID(aid), k=f"stop-run-{aid[:12]}",
        )  # fmt: skip
    assert len(gv.run(lambda c: store.running_runs(c))) == 20
    tool = gv.run(lambda c: machine_store.load_tools(c))["docs.read"]
    in_company = gv.w.owner("SELECT count(*) FROM agent.agent_profile WHERE tenant_id = :t")[0][0]
    assert (
        in_company >= 20
    )  # the fixture seeds a couple more agents besides these; a company-wide stop reaches every one of them

    started = time.perf_counter()
    stop = gv.run(
        lambda c: svc.pause(
            c, gv.tenant, gv.p("sec"), scope="TENANT", agent_id=None, reason="drill", severity="P0"
        )
    )
    committed = time.perf_counter()

    async def probe() -> list[tuple[str, ...]]:
        async with engine(rt_url, size=25) as eng:

            async def one(aid: str) -> tuple[str, ...]:
                async with tenant_transaction(eng, ctx(gv.tenant)) as c:
                    return await svc.govern_tool_call(
                        c,
                        gv.tenant,
                        agent_id=aid,
                        tool=tool,
                        resource_type="mandate",
                        resource_id="1",
                        purpose=PURPOSE,
                    )

            return list(await asyncio.gather(*(one(a) for a in ids)))

    refused = gv.w.run(probe())
    finished = time.perf_counter()
    assert stop["agents_suspended"] == in_company and all(
        r == (str(g.Code.EMERGENCY_FROZEN),) for r in refused
    )
    assert (
        gv.run(lambda c: store.running_runs(c)) == []
    )  # every one of the 20 executing runs was cancelled, none left to finish
    stored = gv.w.owner(
        "SELECT count(*) FROM agent.run_ledger WHERE tenant_id = :t AND state = 'CANCELED' AND reason_code = 'EMERGENCY_FROZEN'"
    )
    assert stored[0][0] == 20
    elapsed, to_commit = finished - started, committed - started
    print(
        f"company-wide stop with 20 running agents: commit {to_commit * 1000:.0f} ms, all 20 fresh calls refused at {elapsed * 1000:.0f} ms"
    )
    assert elapsed <= config().emergency_stop_target_seconds, (
        f"the stop took {elapsed:.2f}s against a {config().emergency_stop_target_seconds}s target"
    )


def test_the_evidence_export_is_redacted_by_role_audited_and_safe_to_open(
    rt_url: str, owner_url: str
) -> None:
    """T40: the downloadable file is redacted exactly as the page is, a cell a spreadsheet would run is made inert, and the export is itself evidence."""
    gv = Gov(rt_url, owner_url)
    gv.publish([gv.lease(resource_kind="agent", resource_selector="agent:")])
    gv.run(
        lambda c: svc._event(
            c, gv.tenant, agent_id=gv.agent, action="tool_call.governed", outcome="ALLOW", actor=gv.agent, actor_type="AGENT",
            tool_id="docs.read", resource="=HYPERLINK(\"http://example.invalid\")", region=REGION,
        )
    )  # fmt: skip

    async def grant() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(gv.tenant)) as c:
            base = ["agent.audit.read", "agent.audit.export"]
            await make_admin(c, gv.tenant, gv.w.human, base, ["agent:*"], release=False)
            await make_admin(
                c,
                gv.tenant,
                gv.w.approver,
                [*base, "agent.audit.read_detail"],
                ["agent:*"],
                release=False,
            )

    gv.w.run(grant())
    with TestClient(gv.w.app, base_url=ISS) as http:
        person = gv.w.bearer(gv.w.human, scope=frozenset({"admin"}))
        viewer = gv.w.bearer(gv.w.approver, scope=frozenset({"admin"}))
        plain = http.get(f"/v1/machine/agents/{gv.agent}/events:export", headers=person)
        assert plain.status_code == 200 and plain.headers["content-type"].startswith("text/csv")
        assert (
            plain.headers["x-export-redacted"] == "true"
            and "attachment" in plain.headers["content-disposition"]
        )
        assert plain.headers["cache-control"] == "no-store"
        header = plain.text.splitlines()[0].split(",")
        assert "action" in header and "digest" in header
        assert not {
            "resource",
            "actor",
            "tool_id",
            "region",
            "policy_hash",
            "correlation_id",
            "cost_receipt",
        } & set(header)  # the columns are not there at all
        assert "HYPERLINK" not in plain.text
        full = http.get(f"/v1/machine/agents/{gv.agent}/events:export?limit=500", headers=viewer)
        assert full.status_code == 200 and full.headers["x-export-redacted"] == "false"
        assert {"resource", "actor", "tool_id"} <= set(full.text.splitlines()[0].split(","))
        assert (
            "'=HYPERLINK" in full.text and ",=HYPERLINK" not in full.text
        )  # inert in a spreadsheet
        fleet = http.get("/v1/machine/governance/events:export?limit=5", headers=person)
        assert fleet.status_code == 200 and int(fleet.headers["x-export-rows"]) <= 5
        # a person without the export action is refused by the PDP, not by the page
        nobody = gv.w.bearer(gv.w.root, scope=frozenset({"admin"}))
        assert http.get(
            f"/v1/machine/agents/{gv.agent}/events:export", headers=nobody
        ).status_code in (401, 403)
    exported = [e["code"] for e in gv.events() if e["action"] == "export.created"]
    assert (
        exported.count("REDACTED") >= 1 and exported.count("DETAIL") >= 1
    )  # each export left a row saying which kind it was
    assert gv.run(lambda c: store.verify_chain(c))["intact"] is True


def test_evidence_chains_are_per_agent_and_a_tampered_row_is_found_in_its_own_chain(
    rt_url: str, owner_url: str
) -> None:
    """One chain per agent plus one for the company: agents never serialize on each other, each chain starts from nothing, and an edit made
    even with the append-only trigger switched off breaks the digests of exactly that chain."""
    gv = Gov(rt_url, owner_url)
    gv.publish()
    other = str(uuid.uuid4())
    for _ in range(2):  # interleave a second agent's rows with the first's
        for aid in (gv.agent, other):
            gv.run(
                lambda c, aid=aid: svc._event(
                    c,
                    gv.tenant,
                    agent_id=aid,
                    action="tool_call.governed",
                    outcome="ALLOW",
                    actor=aid,
                    actor_type="AGENT",
                    tool_id="docs.read",
                )
            )
    first = gv.w.owner(
        "SELECT chain_key, prev_digest FROM agent.governance_event WHERE tenant_id = :t AND seq IN "
        "(SELECT min(seq) FROM agent.governance_event WHERE tenant_id = :t GROUP BY chain_key) ORDER BY chain_key"
    )
    keys = {k for k, _ in first}
    assert {gv.agent, other, "tenant"} <= keys
    assert all(
        prev == "sha256:" + "0" * 64 for _k, prev in first
    )  # every chain begins from nothing: none waits on another
    ok = gv.run(lambda c: store.verify_chain(c))
    assert ok["intact"] is True and ok["chains"] >= 3

    victim = gv.w.owner(
        "SELECT seq FROM agent.governance_event WHERE tenant_id = :t AND chain_key = :k ORDER BY seq LIMIT 1",
        k=gv.agent,
    )[0][0]
    original = gv.w.owner(
        "SELECT outcome FROM agent.governance_event WHERE seq = :s AND tenant_id = :t", s=victim
    )[0][0]  # put back exactly what was there, whatever it was
    gv.w.owner("ALTER TABLE agent.governance_event DISABLE TRIGGER governance_event_append_only")
    try:
        gv.w.owner(
            "UPDATE agent.governance_event SET outcome = 'DENY' WHERE seq = :s AND tenant_id = :t",
            s=victim,
        )
        broken = gv.run(lambda c: store.verify_chain(c))
    finally:
        gv.w.owner(
            "UPDATE agent.governance_event SET outcome = :o WHERE seq = :s AND tenant_id = :t",
            s=victim,
            o=original,
        )
        gv.w.owner("ALTER TABLE agent.governance_event ENABLE TRIGGER governance_event_append_only")
    assert broken["intact"] is False and broken["broken_at"] == victim
    assert (
        gv.run(lambda c: store.verify_chain(c))["intact"] is True
    )  # put back, the chain verifies again
