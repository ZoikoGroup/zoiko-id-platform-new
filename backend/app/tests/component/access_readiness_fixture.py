"""A tenant with a DAARA agent (active, attested, sponsored), mandates to review, and a running app (CR-091; ZID-AG-WF-001).

Shared by the access readiness component tests. Everything is synthetic; every call that needs another tenant makes its own `World`.
The seeded mandates, all ACTIVE and homed in this cell:

    expired     valid_to in the past                               -> R-01
    expiring    valid_to in 3 days                                 -> R-02
    orphan      executed by an agent whose sponsor was disabled     -> R-03
    wide        allows an action its grant does not               -> R-04
    noevidence  its grant carries no evidence reference            -> R-05
    nogrant     points at a grant that does not exist              -> R-05
    clean       nothing to report
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from admin_fixture import make_admin
from conftest import CELL, ctx, engine
from sqlalchemy import text
from zid_crypto import KeyRing, LocalSigner
from zid_db import tenant_transaction
from zid_platform.main import create_app
from zid_platform.modules.access_readiness.application.common import Runtime
from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    Attestation,
    BindingStatus,
    BlueprintRelease,
    ReviewStatus,
    WorkloadBinding,
)
from zid_platform.modules.machine.infrastructure.machine_store import (
    save_agent,
    save_binding,
    save_blueprint,
)
from zid_platform.modules.mandate.domain.ledger import Budget, MandateStatus
from zid_platform.modules.mandate.infrastructure.ledger_store import MandateRecord, create_mandate
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
REGION = "local-1"  # the default cell name the app reports as its region
DIGEST = "sha256:" + "a" * 64
ALLOWED = frozenset(
    {"read_mandate_metadata", "read_agent_identity_status", "create_access_review_proposal"}
)
ACTIONS = [
    "access_readiness.read",
    "access_readiness.manage",
    "access_readiness.approve",
    "access_readiness.review",
]
RESOURCES = ["agent:*", "access_readiness:*", "review-proposal:*"]
BASE = "/v1/access-readiness"
NOW = datetime.now(UTC)


def uid() -> str:
    return str(uuid.uuid4())


def runtime(**kw: Any) -> Runtime:
    base: dict[str, Any] = {
        "enabled": True,
        "regions": frozenset({REGION}),
        "region": REGION,
        "cell_uuid": CELL,
        "signer": lambda body: "test-signature." + str(len(body)),
    }
    return Runtime(**{**base, **kw})


@dataclass
class World:
    rt_url: str
    owner_url: str
    tenant: str = field(default_factory=uid)
    sponsor: str = field(default_factory=uid)
    sponsor2: str = field(default_factory=uid)
    operator: str = field(default_factory=uid)
    publisher: str = field(default_factory=uid)
    admin: str = field(default_factory=uid)
    approver: str = field(default_factory=uid)
    reviewer: str = field(default_factory=uid)
    nobody: str = field(default_factory=uid)
    holder: str = field(default_factory=uid)
    org: str = field(default_factory=uid)
    daara: str = field(default_factory=uid)
    exec_ok: str = field(default_factory=uid)
    exec_orphan: str = field(default_factory=uid)
    ring: KeyRing = field(default_factory=lambda: KeyRing(LocalSigner("issuer-ar")))
    sessions: dict[str, str] = field(default_factory=dict)
    grants: dict[str, str] = field(default_factory=dict)
    mandates: dict[str, str] = field(default_factory=dict)

    def run(self, coro: Any) -> Any:
        return asyncio.run(coro)

    def profile(
        self, agent_id: str, sponsor: str, *, blueprint: str, tools: set[str]
    ) -> AgentProfile:
        return AgentProfile(
            agent_id, self.tenant, f"bot-{agent_id[:4]}", blueprint, "1.0.0", self.publisher, self.operator,
            sponsor, self.org, "", frozenset(tools), 0, AgentStatus.DRAFT,
        )  # fmt: skip

    async def add_agent(
        self,
        c: Any,
        agent_id: str,
        sponsor: str,
        *,
        blueprint: str = "daara",
        tools: set[str] | None = None,
    ) -> None:
        await save_agent(
            c,
            self.profile(
                agent_id,
                sponsor,
                blueprint=blueprint,
                tools=set(ALLOWED if tools is None else tools),
            ),
        )
        await create_principal(
            c,
            Principal(
                agent_id, self.tenant, PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE, "uk-1",
                sponsor_principal_id=sponsor,
            ),
        )  # fmt: skip

    async def mandate(
        self,
        c: Any,
        key: str,
        executor: str,
        grant: str,
        actions: tuple[str, ...],
        valid_to: datetime,
    ) -> None:
        mid = uuid.uuid4()
        self.mandates[key] = str(mid)
        await create_mandate(
            c,
            MandateRecord(
                mandate_id=mid, tenant_id=uuid.UUID(self.tenant), authority_grant_id=uuid.UUID(grant),
                represented_principal_id=uuid.UUID(self.org), executor_principal_id=uuid.UUID(executor),
                purpose_id=uuid.uuid4(), resource_scope_id=uuid.uuid4(), allowed_actions=actions,
                budget=Budget(currency="GBP", value_limit=Decimal(100)), home_cell_id=uuid.UUID(CELL),
                status=MandateStatus.ACTIVE, valid_from=NOW - timedelta(days=30), valid_to=valid_to,
                created_by_principal_id=uuid.UUID(self.holder),
            ),
        )  # fmt: skip

    async def _seed(self) -> None:
        t = self.tenant
        async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(t)) as c:
            for p in (
                self.sponsor,
                self.sponsor2,
                self.operator,
                self.publisher,
                self.admin,
                self.approver,
                self.reviewer,
                self.nobody,
                self.holder,
            ):
                await create_principal(
                    c, Principal(p, t, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_organization(
                c, organization_id=self.org, tenant_id=t, legal_name="Acme Ltd", jurisdiction="GB",
                valid_from=NOW - timedelta(days=30),
            )  # fmt: skip
            for blueprint in ("daara", "other-bot"):
                await save_blueprint(
                    c,
                    t,
                    BlueprintRelease(
                        blueprint, "1.0.0", DIGEST, DIGEST, self.publisher, ReviewStatus.APPROVED
                    ),
                )
            await self.add_agent(c, self.daara, self.sponsor)
            await self.add_agent(
                c, self.exec_ok, self.sponsor, blueprint="other-bot", tools={"docs.read"}
            )
            await self.add_agent(
                c, self.exec_orphan, self.sponsor2, blueprint="other-bot", tools={"docs.read"}
            )
            await make_admin(c, t, [self.admin, self.approver, self.reviewer], ACTIONS, RESOURCES)
            grant_actions = ["invoice.approve", "invoice.view"]
            self.grants["ok"] = await make_admin(
                c, t, self.holder, grant_actions, ["invoice:*"], release=False
            )
            holder2 = uid()
            await create_principal(
                c, Principal(holder2, t, PrincipalType.HUMAN, PrincipalStatus.ACTIVE, "uk-1")
            )
            self.grants["noevidence"] = await make_admin(
                c, t, holder2, grant_actions, ["invoice:*"], release=False
            )
            ok = ("invoice.approve",)
            await self.mandate(
                c, "expired", self.exec_ok, self.grants["ok"], ok, NOW - timedelta(days=2)
            )
            await self.mandate(
                c, "expiring", self.exec_ok, self.grants["ok"], ok, NOW + timedelta(days=3)
            )
            await self.mandate(
                c, "orphan", self.exec_orphan, self.grants["ok"], ok, NOW + timedelta(days=90)
            )
            await self.mandate(
                c,
                "wide",
                self.exec_ok,
                self.grants["ok"],
                ("invoice.approve", "invoice.delete"),
                NOW + timedelta(days=90),
            )
            await self.mandate(
                c,
                "noevidence",
                self.exec_ok,
                self.grants["noevidence"],
                ok,
                NOW + timedelta(days=90),
            )
            await self.mandate(c, "nogrant", self.exec_ok, uid(), ok, NOW + timedelta(days=90))
            await self.mandate(
                c, "clean", self.exec_ok, self.grants["ok"], ok, NOW + timedelta(days=90)
            )
            await save_binding(
                c, t,
                WorkloadBinding(
                    uid(), self.daara, f"spiffe://zid/{self.daara}", "1.0.0", DIGEST, "prod", Attestation.ATTEST_1,
                    BindingStatus.ACTIVE, NOW - timedelta(minutes=5), NOW + timedelta(days=30),
                ),
            )  # fmt: skip
            await set_status(c, self.sponsor2, PrincipalStatus.DISABLED)
        async with engine(self.owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(t)},
            )
            await c.execute(
                text(
                    "UPDATE authz.authority_grant SET evidence_ref = :e WHERE authority_grant_id = :g"
                ),
                {"e": uuid.uuid4(), "g": uuid.UUID(self.grants["ok"])},
            )
            await c.execute(
                text(
                    "INSERT INTO agent.workload_attestation (attestation_id, tenant_id, workload_identity_id, environment_id, "
                    "artifact_digest, level, verifier, claims_commitment, status, valid_until) "
                    "VALUES (:id, :t, :w, 'prod', :d, 2, 'test-attestor', :cc, 'VALID', now() + interval '1 day')"
                ),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(t),
                    "w": f"spiffe://zid/{self.daara}",
                    "d": DIGEST,
                    "cc": "c" * 64,
                },
            )

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
        self.sessions = {
            p: self.run(self._session(p))
            for p in (self.admin, self.approver, self.reviewer, self.nobody)
        }
        return self

    def app(self, **settings: Any) -> Any:
        return create_app(
            Settings(
                database_url=self.rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
                environment="test", cell_uuid=CELL, **settings,
            ),
            keyring=self.ring,
        )  # fmt: skip

    def on(self) -> Any:
        return self.app(access_readiness_enabled=True, access_readiness_regions=(REGION,))

    def bearer(self, sub: str) -> dict[str, str]:
        token = issue_access_token(
            self.ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=self.tenant, client_id="admin-console",
            now=datetime.now(UTC), session_id=self.sessions[sub], scope=frozenset(),
        )  # fmt: skip
        return {"Authorization": f"Bearer {token}"}

    def owner(self, sql: str, **params: Any) -> list[Any]:
        async def go() -> list[Any]:
            async with engine(self.owner_url) as eng, eng.begin() as c:
                rows = await c.execute(text(sql), {"t": uuid.UUID(self.tenant), **params})
                return list(rows.all()) if rows.returns_rows else []

        return self.run(go())  # type: ignore[no-any-return]

    def db(self, fn: Any) -> Any:
        """Run `fn(conn)` as this tenant (row-level security in force)."""

        async def go() -> Any:
            async with engine(self.rt_url) as eng, tenant_transaction(eng, ctx(self.tenant)) as c:
                return await fn(c)

        return self.run(go())
