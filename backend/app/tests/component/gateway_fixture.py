"""A tenant with a delegation chain of agents, tools, a policy that gives the agents authority, and a running app.

Shared by the tool gateway tests (ZID-GAP-001 WP-11, WP-24): a root agent and a child below it, an outsider agent the policy
gives nothing to, a tool server, an approver, and three tools (a plain read, one that is not on any agent's list, one that
needs a human approval). Everything the agents may do is the one catalogued action `machine.agent.read`.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import give_tenant_role, make_admin
from conftest import CELL, ctx, engine
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    BlueprintRelease,
    ReviewStatus,
    SideEffect,
    Tool,
    ToolStatus,
)
from zid_platform.modules.machine.infrastructure.machine_store import (
    save_agent,
    save_blueprint,
    save_tool,
)
from zid_platform.modules.organization.infrastructure.root_store import create_organization
from zid_platform.modules.principal.domain.principal import (
    Principal,
    PrincipalStatus,
    PrincipalType,
)
from zid_platform.modules.principal.infrastructure.principal_store import (
    create_principal,
    load_principal,
    set_status,
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
DIGEST = "sha256:" + "a" * 64
READ = "machine.agent.read"


def uid() -> str:
    return str(uuid.uuid4())


def tool(tool_id: str, server: str, *, approval: bool = False) -> Tool:
    return Tool(tool_id, "1", server, READ, 1, SideEffect.NONE, False, approval, ToolStatus.DRAFT)


@dataclass
class World:
    rt_url: str
    owner_url: str
    tenant: str = field(default_factory=uid)
    sponsor: str = field(default_factory=uid)
    operator: str = field(default_factory=uid)
    publisher: str = field(default_factory=uid)
    human: str = field(default_factory=uid)
    approver: str = field(default_factory=uid)
    server: str = field(default_factory=uid)
    other_server: str = field(default_factory=uid)
    org: str = field(default_factory=uid)
    root: str = field(default_factory=uid)
    child: str = field(default_factory=uid)
    outsider: str = field(default_factory=uid)
    # Give the seeded agents the runtime proof a real activation would have left (a workload binding with a valid attestation). A test that asserts an agent it
    # builds has no workload binding anywhere turns this off.
    runtime_proof: bool = True
    ring: KeyRing = field(default_factory=lambda: KeyRing(LocalSigner("issuer-gw")))
    approver_grant: str = ""
    sessions: dict[str, str] = field(default_factory=dict)
    app: Any = None

    def run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def profile(self, agent_id: str, tools: set[str], parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, self.tenant, f"bot-{agent_id[:4]}", "bp", "1", self.publisher, self.operator,
            self.sponsor, self.org, "", frozenset(tools), 2 if parent is None else 0,
            AgentStatus.DRAFT, parent_agent_id=parent,
        )  # fmt: skip

    async def _seed(self) -> None:
        t = self.tenant
        async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(t)) as c:
            for p, kind in (
                (self.sponsor, PrincipalType.HUMAN),
                (self.operator, PrincipalType.HUMAN),
                (self.publisher, PrincipalType.HUMAN),
                (self.human, PrincipalType.HUMAN),
                (self.approver, PrincipalType.HUMAN),
                (self.server, PrincipalType.SERVICE),
                (self.other_server, PrincipalType.SERVICE),
            ):
                await create_principal(c, Principal(p, t, kind, PrincipalStatus.ACTIVE, "uk-1"))
            await create_organization(
                c,
                organization_id=self.org,
                tenant_id=t,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
            await save_blueprint(
                c,
                t,
                BlueprintRelease("bp", "1", DIGEST, DIGEST, self.publisher, ReviewStatus.APPROVED),
            )
            for x in (
                tool("docs.read", self.server),
                tool("docs.other", self.server),
                tool("docs.sign", self.server, approval=True),
            ):
                await save_tool(c, t, x)
            await save_agent(c, self.profile(self.root, {"docs.read", "docs.sign"}))
            await save_agent(c, self.profile(self.child, {"docs.read", "docs.sign"}, self.root))
            await save_agent(c, self.profile(self.outsider, {"docs.read"}))
            for agent_id in (self.root, self.child, self.outsider):
                await create_principal(
                    c,
                    Principal(
                        agent_id, t, PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE, "uk-1",
                        sponsor_principal_id=self.sponsor,
                    ),
                )  # fmt: skip
            await make_admin(c, t, [self.root, self.child], [READ], ["agent:*"])
            self.approver_grant = await make_admin(
                c, t, self.approver, [READ], ["agent:*"], release=False
            )
            await give_tenant_role(c, t, self.approver)  # only an Admin or an Owner records an agent's security review
        async with engine(self.owner_url) as eng, eng.begin() as c:
            for table in ("agent_profile", "tool_capability"):
                await c.execute(
                    text(f"UPDATE agent.{table} SET status = 'ACTIVE' WHERE tenant_id = :t"),
                    {"t": uuid.UUID(t)},
                )
            # An agent that is ACTIVE got there through the activation gates, one of which is a runtime binding. These agents are set ACTIVE directly, so
            # give each the proof a real activation would have left, and a run start checks it is still live (ZID-AG-WF-001 AT-07): a workload binding to
            # the artifact of the agent's release, with a VALID attestation at the level it needs. (Not an API client binding: that caps an agent's tools,
            # and these agents hold a tool that cannot be undone.)
            for agent_id in (self.root, self.child, self.outsider) if self.runtime_proof else ():
                sid = f"spiffe://uk-1.test.id.zoiko/t/{t}/ns/agents/sa/{agent_id[:8]}"
                await c.execute(
                    text(
                        "INSERT INTO agent.workload_binding (binding_id, tenant_id, logical_principal_id, workload_identity_id, blueprint_version, "
                        "artifact_digest, environment_id, attestation_required, status, valid_from, valid_to, security_revision) VALUES "
                        "(:i, :t, :a, :w, '1', :d, 'prod', 2, 'ACTIVE', now() - interval '1 day', now() + interval '10 years', 0)"
                    ),
                    {"i": uuid.uuid4(), "t": uuid.UUID(t), "a": uuid.UUID(agent_id), "w": sid, "d": DIGEST},
                )  # fmt: skip
                await c.execute(
                    text(
                        "INSERT INTO agent.workload_attestation (attestation_id, tenant_id, workload_identity_id, environment_id, artifact_digest, level, "
                        "verifier, claims_commitment, status, valid_until) VALUES (:i, :t, :w, 'prod', :d, 2, 'fixture-attestor', :c, 'VALID', "
                        "now() + interval '10 years')"
                    ),
                    {"i": uuid.uuid4(), "t": uuid.UUID(t), "w": sid, "d": DIGEST, "c": "a" * 64},
                )  # fmt: skip

    async def _session(self, p: str) -> str:
        sid, now = uid(), datetime.now(UTC)
        async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid, self.tenant, p, now - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, now + timedelta(hours=8),
                ),
            )  # fmt: skip
        return sid

    def start(self) -> World:
        self.run(self._seed())
        everyone = (
            self.root, self.child, self.outsider, self.human, self.approver, self.server,
            self.other_server,
        )  # fmt: skip
        self.sessions = {p: self.run(self._session(p)) for p in everyone}
        self.app = create_app(
            Settings(
                database_url=self.rt_url, token_issuer=ISS, token_audience=AUD,
                public_base_url=AUD, environment="test", cell_uuid=CELL, daara_enabled=True,
            ),
            keyring=self.ring,
        )  # fmt: skip
        return self

    def bearer(
        self, sub: str, *, scope: frozenset[str] = frozenset({"workload"})
    ) -> dict[str, str]:
        token = issue_access_token(
            self.ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=self.tenant,
            client_id="agent-runtime", now=datetime.now(UTC), session_id=self.sessions[sub],
            scope=scope,
        )  # fmt: skip
        return {"Authorization": f"Bearer {token}"}

    def owner(self, sql: str, **params: Any) -> list[Any]:
        async def go() -> list[Any]:
            async with engine(self.owner_url) as eng, eng.begin() as c:
                rows = await c.execute(text(sql), {"t": uuid.UUID(self.tenant), **params})
                return list(rows.all()) if rows.returns_rows else []

        return self.run(go())  # type: ignore[no-any-return]

    def disable_sponsor(self) -> None:
        async def go() -> None:
            async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
                await set_status(c, self.sponsor, PrincipalStatus.DISABLED)

        self.run(go())
