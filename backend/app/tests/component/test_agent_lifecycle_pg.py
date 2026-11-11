"""WP-10 (ZID-GAP-001): the lifecycle sweep against PostgreSQL.

An ACTIVE agent whose sponsor was disabled, or whose expiry passed, is suspended by policy with its
sub-agents; an agent with a healthy sponsor is left alone; a second pass changes nothing; the trail
records a SYSTEM actor, not a person.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from conftest import ctx, engine
from sqlalchemy import text
from zid_db import tenant_transaction
from zid_platform.modules.machine.application.agents import lifecycle_sweep
from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    ReviewStatus,
)
from zid_platform.modules.machine.infrastructure.machine_store import save_agent, save_blueprint
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    set_status,
)

DIGEST = "sha256:" + "a" * 64


def uid() -> str:
    return str(uuid.uuid4())


def test_sweep_suspends_orphaned_and_expired_agents(rt_url: str, owner_url: str) -> None:
    tenant, ok_sponsor, gone_sponsor, operator, publisher, org = (uid() for _ in range(6))
    ok_agent, orphan, orphan_child, expired = uid(), uid(), uid(), uid()
    now = datetime.now(UTC)

    def profile(agent_id: str, sponsor: str, parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, tenant, f"bot-{agent_id[:4]}", "bp", "1", publisher, operator, sponsor, org,
            "", frozenset(), 1, AgentStatus.DRAFT, parent_agent_id=parent,
        )  # fmt: skip

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (ok_sponsor, PrincipalType.HUMAN),
                (gone_sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (publisher, PrincipalType.HUMAN),
            ):
                await create_principal(
                    c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await save_blueprint(
                c,
                tenant,
                BlueprintRelease("bp", "1", DIGEST, DIGEST, publisher, ReviewStatus.APPROVED),
            )
            for agent_id, sponsor, parent in (
                (ok_agent, ok_sponsor, None),
                (orphan, gone_sponsor, None),
                (orphan_child, gone_sponsor, orphan),
                (expired, ok_sponsor, None),
            ):
                await save_agent(c, profile(agent_id, sponsor, parent))
                await create_principal(
                    c,
                    Principal(
                        agent_id, tenant, PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE,
                        "uk-1", sponsor_principal_id=sponsor,
                    ),
                )  # fmt: skip
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
            await c.execute(
                text(
                    "UPDATE agent.agent_profile SET expires_at = :e WHERE agent_principal_id = :a"
                ),
                {"e": now - timedelta(minutes=1), "a": uuid.UUID(expired)},
            )

    async def sponsor_leaves() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await set_status(c, gone_sponsor, PrincipalStatus.DISABLED)

    async def sweep() -> list[dict[str, Any]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return await lifecycle_sweep(c, now)

    async def statuses() -> dict[str, str]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT agent_principal_id, status FROM agent.agent_profile WHERE tenant_id = :t"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return {str(r.agent_principal_id): r.status for r in rows}

    asyncio.run(seed())

    # nobody has left yet and one agent has expired: only that one stops
    first = asyncio.run(sweep())
    assert [(r["agent_principal_id"], r["reason"]) for r in first if r["tenant_id"] == tenant] == [
        (expired, "AGENT_EXPIRED")
    ]

    asyncio.run(sponsor_leaves())
    second = [r for r in asyncio.run(sweep()) if r["tenant_id"] == tenant]
    assert [(r["agent_principal_id"], r["reason"]) for r in second] == [
        (orphan, "SPONSOR_NOT_ACTIVE")
    ]
    assert second[0]["sub_agents"] == 1  # the sub-agent goes with its parent

    state = asyncio.run(statuses())
    assert state[ok_agent] == "ACTIVE"
    assert state[orphan] == state[orphan_child] == state[expired] == "SUSPENDED"

    # idempotent: a third pass has nothing left to do for this tenant
    assert [r for r in asyncio.run(sweep()) if r["tenant_id"] == tenant] == []

    async def trail() -> list[tuple[str, str]]:
        async with engine(owner_url) as eng, eng.begin() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT target_ref, actor_type FROM evidence.audit_record "
                        "WHERE tenant_id = :t AND event_type = 'agent.suspended'"
                    ),
                    {"t": uuid.UUID(tenant)},
                )
            ).all()
        return [(str(r.target_ref), r.actor_type) for r in rows]

    suspended = asyncio.run(trail())
    assert {ref for ref, _ in suspended} == {orphan, orphan_child, expired}
    assert {kind for _, kind in suspended} == {"SYSTEM"}  # the sweep is the actor, never a person
