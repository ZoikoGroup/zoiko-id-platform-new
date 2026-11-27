"""A small company with a parent and a child agent, a tool, a sponsor and three administrators, for the WF07 tests (CR-169). Real principals, sessions and tokens against PostgreSQL."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from fastapi.testclient import TestClient
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
from zid_platform.modules.machine.domain.tool_gateway import args_digest
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
)
from zid_platform.modules.session.domain.session import Session, SessionStatus
from zid_platform.modules.session.infrastructure.session_store import create_session
from zid_platform.modules.session.ports import issue_access_token
from zid_platform.settings import Settings

ISS, AUD = "https://id.zoiko.test", "https://api.zoiko.test"
DIGEST = "sha256:" + "a" * 64
ARGS = args_digest({"invoice": "demo_492"})
READ = "machine.agent.read"
ADMIN_ACTIONS = [
    READ,
    "machine.agent.manage",
    "agent.security.pause",
    "agent.security.restore.close",
    "machine.agent.activate",
]


def uid() -> str:
    return str(uuid.uuid4())


class W:
    """tenant, sponsor, root and child agents, a tool docs.read on `server`, people `human`, `other` and `third` (each an administrator), plus `bearer(sub)` and `db(sql)`."""

    def __init__(self, rt_url: str, owner_url: str) -> None:
        self.rt_url, self.owner_url = rt_url, owner_url
        self.tenant, self.sponsor, self.operator, self.publisher, self.server, self.org = (
            uid() for _ in range(6)
        )
        self.human, self.other, self.third = uid(), uid(), uid()
        self.root, self.child = uid(), uid()
        self.ring = KeyRing(LocalSigner("issuer-wf07"))
        asyncio.run(self._seed())
        self.sessions = {
            p: asyncio.run(self._session(p))
            for p in (self.root, self.child, self.human, self.other, self.third, self.server)
        }
        self.app = create_app(
            Settings(
                database_url=rt_url,
                token_issuer=ISS,
                token_audience=AUD,
                public_base_url=AUD,
                environment="test",
                cell_uuid=CELL,
            ),
            keyring=self.ring,
        )

    def _profile(self, agent_id: str, parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, self.tenant, f"bot-{agent_id[:4]}", "bp", "1", self.publisher, self.operator, self.sponsor, self.org, "", frozenset({"docs.read"}), 2 if parent is None else 0, AgentStatus.DRAFT, parent_agent_id=parent
        )  # fmt: skip

    async def _seed(self) -> None:
        async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
            people = (
                (self.sponsor, PrincipalType.HUMAN),
                (self.operator, PrincipalType.HUMAN),
                (self.publisher, PrincipalType.HUMAN),
                (self.human, PrincipalType.HUMAN),
                (self.other, PrincipalType.HUMAN),
                (self.third, PrincipalType.HUMAN),
                (self.server, PrincipalType.SERVICE),
            )
            for p, kind in people:
                await create_principal(
                    c, Principal(p, self.tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_organization(
                c,
                organization_id=self.org,
                tenant_id=self.tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
            await save_blueprint(
                c,
                self.tenant,
                BlueprintRelease("bp", "1", DIGEST, DIGEST, self.publisher, ReviewStatus.APPROVED),
            )
            await save_tool(
                c,
                self.tenant,
                Tool(
                    "docs.read",
                    "1",
                    self.server,
                    READ,
                    1,
                    SideEffect.NONE,
                    False,
                    False,
                    ToolStatus.DRAFT,
                ),
            )
            await save_agent(c, self._profile(self.root))
            await save_agent(c, self._profile(self.child, self.root))
            for agent_id in (self.root, self.child):
                await create_principal(
                    c,
                    Principal(
                        agent_id,
                        self.tenant,
                        PrincipalType.AI_AGENT,
                        PrincipalStatus.ACTIVE,
                        "uk-1",
                        sponsor_principal_id=self.sponsor,
                    ),
                )
            await make_admin(
                c,
                self.tenant,
                [self.root, self.child, self.human, self.other, self.third],
                ADMIN_ACTIONS,
                ["agent:*", "agent:revocation:*"],
            )
        async with engine(self.owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(self.tenant)},
            )
            await c.execute(
                text("UPDATE agent.tool_capability SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(self.tenant)},
            )

    async def _session(self, p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid,
                    self.tenant,
                    p,
                    t - timedelta(seconds=30),
                    "native:webauthn",
                    2,
                    principal.security_revision,
                    1,
                    SessionStatus.ACTIVE,
                    t + timedelta(hours=8),
                ),
            )
        return sid

    def bearer(
        self, sub: str, *, scope: frozenset[str] = frozenset({"workload"})
    ) -> dict[str, str]:
        tok = issue_access_token(
            self.ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=self.tenant,
            client_id="agent-runtime",
            now=datetime.now(UTC),
            session_id=self.sessions[sub],
            scope=scope,
        )
        return {"Authorization": f"Bearer {tok}"}

    def person(self, sub: str) -> dict[str, str]:
        return self.bearer(sub, scope=frozenset({"admin"}))

    def db(self, sql: str, **params: Any) -> list[Any]:
        async def run() -> list[Any]:
            async with engine(self.owner_url) as eng, eng.begin() as c:
                result = await c.execute(text(sql), {"t": uuid.UUID(self.tenant), **params})
                return list(result.all()) if result.returns_rows else []

        return asyncio.run(run())

    def client(self) -> TestClient:
        return TestClient(self.app, base_url=ISS)
