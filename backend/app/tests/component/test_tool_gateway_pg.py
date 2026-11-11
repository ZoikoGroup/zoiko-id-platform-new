"""WP-11 (ZID-GAP-001): the agent-to-tool gateway over HTTP against PostgreSQL, real tokens, real policy decision.

An agent asks the gateway for a grant to call a tool. The cases are the plan's own attacks: a person asking in an
agent's name, a token that is not a workload token, a forged body, a tool the chain never listed, a suspended parent,
an orphaned sponsor, a tool that needs a bound human approval, and an agent the policy gives no authority to. The one
allowed call returns a grant that is bound to the tool's server, the exact arguments and the whole chain, and every
outcome, allow or deny, is on the audit trail with an AGENT actor.
"""

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
from zid_platform.modules.machine.domain.tool_gateway import GRANT_TYP, args_digest, call_context
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
URL = "/v1/machine/tool-calls:authorize"
DIGEST = "sha256:" + "a" * 64
ARGS = args_digest({"agent": "x"})
READ = "machine.agent.read"  # a catalogued action the test policy permits for agents that hold authority


def uid() -> str:
    return str(uuid.uuid4())


def tool(tool_id: str, server: str, *, approval: bool = False) -> Tool:
    return Tool(tool_id, "1", server, READ, 1, SideEffect.NONE, False, approval, ToolStatus.DRAFT)


def test_the_tool_gateway(rt_url: str, owner_url: str) -> None:
    tenant, sponsor, operator, publisher, server, org, human = (uid() for _ in range(7))
    root, child, outsider = uid(), uid(), uid()
    ring = KeyRing(LocalSigner("issuer-gw"))

    def profile(agent_id: str, tools: set[str], parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, tenant, f"bot-{agent_id[:4]}", "bp", "1", publisher, operator, sponsor, org,
            "", frozenset(tools), 2 if parent is None else 0, AgentStatus.DRAFT, parent_agent_id=parent,
        )  # fmt: skip

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (publisher, PrincipalType.HUMAN),
                (human, PrincipalType.HUMAN),
                (server, PrincipalType.SERVICE),
            ):
                await create_principal(
                    c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
            await save_blueprint(
                c,
                tenant,
                BlueprintRelease("bp", "1", DIGEST, DIGEST, publisher, ReviewStatus.APPROVED),
            )
            for t in (
                tool("docs.read", server),
                tool("docs.other", server),
                tool("docs.sign", server, approval=True),
            ):
                await save_tool(c, tenant, t)
            await save_agent(c, profile(root, {"docs.read", "docs.sign"}))
            await save_agent(c, profile(child, {"docs.read", "docs.sign"}, root))
            await save_agent(c, profile(outsider, {"docs.read"}))
            for agent_id in (root, child, outsider):
                await create_principal(
                    c,
                    Principal(
                        agent_id, tenant, PrincipalType.AI_AGENT, PrincipalStatus.ACTIVE, "uk-1",
                        sponsor_principal_id=sponsor,
                    ),
                )  # fmt: skip
            # the policy gives authority to the root and the child, but not to the outsider
            await make_admin(c, tenant, [root, child], [READ], ["agent:*"])
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text("UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )
            await c.execute(
                text("UPDATE agent.tool_capability SET status = 'ACTIVE' WHERE tenant_id = :t"),
                {"t": uuid.UUID(tenant)},
            )

    async def session_for(p: str) -> str:
        sid, t = uid(), datetime.now(UTC)
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            principal, _ = await load_principal(c, p)
            await create_session(
                c,
                Session(
                    sid, tenant, p, t - timedelta(seconds=30), "native:webauthn", 2,
                    principal.security_revision, 1, SessionStatus.ACTIVE, t + timedelta(hours=8),
                ),
            )  # fmt: skip
        return sid

    async def db(sql: str, **params: Any) -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return list((await c.execute(text(sql), {"t": uuid.UUID(tenant), **params})).all())

    async def disable_sponsor() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await set_status(c, sponsor, PrincipalStatus.DISABLED)

    asyncio.run(seed())
    sessions = {p: asyncio.run(session_for(p)) for p in (root, child, outsider, human)}
    app = create_app(
        Settings(
            database_url=rt_url, token_issuer=ISS, token_audience=AUD, public_base_url=AUD,
            environment="test", cell_uuid=CELL,
        ),
        keyring=ring,
    )  # fmt: skip

    def bearer(sub: str, *, scope: frozenset[str] = frozenset({"workload"})) -> dict[str, str]:
        tok = issue_access_token(
            ring, issuer=ISS, audience=AUD, subject=sub, tenant_id=tenant, client_id="agent-runtime",
            now=datetime.now(UTC), session_id=sessions[sub], scope=scope,
        )  # fmt: skip
        return {"Authorization": f"Bearer {tok}"}

    def body(tool_id: str = "docs.read", **over: Any) -> dict[str, Any]:
        return {
            "tool_id": tool_id,
            "resource_type": "agent",
            "resource_id": "any",
            "arguments_digest": ARGS,
            **over,
        }

    def codes(r: Any) -> list[str]:
        return [d["issue"] for d in r.json()["error"]["details"] if d.get("field") is None]

    with TestClient(app, base_url=ISS) as http:
        # --- the allowed call: a sub-agent, through its parent, to a tool both list
        ok = http.post(URL, headers=bearer(child), json=body())
        assert ok.status_code == 200, ok.text
        data = ok.json()
        assert data["agent_chain"] == [child, root]
        _header, claims = ring.verify(data["grant"], typ=GRANT_TYP)
        assert claims["aud"] == server  # only the tool's own server may accept it
        assert claims["sub"] == child and claims["agent_chain"] == [child, root]
        assert claims["args_sha256"] == ARGS and claims["tool_id"] == "docs.read"
        assert claims["action"] == READ and claims["decision_id"] == data["decision_id"]
        assert 0 < claims["exp"] - claims["iat"] <= 120 and claims["jti"]
        again = http.post(URL, headers=bearer(child), json=body()).json()
        assert again != data  # a second call is a second grant with its own nonce

        # --- who may ask: not a person, not a token that is not a workload token, not another agent's name
        assert http.post(URL, headers=bearer(human), json=body()).status_code == 404  # not an agent
        no_scope = http.post(URL, headers=bearer(child, scope=frozenset()), json=body())
        assert (
            no_scope.status_code == 403
            and no_scope.json()["error"]["code"] == "TOOL_CALL_FORBIDDEN"
        )
        forged = http.post(
            URL, headers=bearer(child), json=body(agent_id=root)
        )  # the agent is never a body field
        assert forged.status_code == 422
        assert (
            http.post(URL, headers=bearer(child), json=body(arguments_digest="x")).status_code
            == 422
        )
        assert http.post(URL, headers=bearer(child), json=body(tenant_id=uid())).status_code == 422

        # --- a tool the chain never listed, one that does not exist, and an agent the policy gives nothing to
        other = http.post(URL, headers=bearer(child), json=body("docs.other"))
        assert other.status_code == 403 and "MANDATE_TOOL_NOT_ALLOWED" in codes(other)
        ghost = http.post(URL, headers=bearer(child), json=body("docs.ghost"))
        assert ghost.status_code == 403 and "TOOL_NOT_REGISTERED" in codes(ghost)
        nobody = http.post(URL, headers=bearer(outsider), json=body())
        assert nobody.status_code == 403 and nobody.json()["error"]["code"] == "TOOL_CALL_DENIED"
        assert "SUBJECT_UNKNOWN" not in codes(nobody) and codes(nobody)  # the policy itself said no

        # --- a tool that needs a human approval is not run on the agent's word, and says what to approve
        needs = http.post(URL, headers=bearer(child), json=body("docs.sign"))
        assert needs.status_code == 403 and "HUMAN_APPROVAL_REQUIRED" in codes(needs)
        context = next(
            d["issue"]
            for d in needs.json()["error"]["details"]
            if d.get("field") == "approval_context"
        )
        assert context == call_context(
            READ, "agent:any", ARGS
        )  # bound to this action, resource and these arguments
        bad_ref = http.post(
            URL, headers=bearer(child), json=body("docs.sign", approval_request_id=uid())
        )
        assert bad_ref.status_code == 403 and "HUMAN_APPROVAL_REQUIRED" in codes(
            bad_ref
        )  # an unknown approval is no approval

        # --- a suspended parent takes its children's calls with it
        async def suspend_root() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE agent.agent_profile SET status = 'SUSPENDED' WHERE agent_principal_id = :a"
                    ),
                    {"a": uuid.UUID(root)},
                )

        asyncio.run(suspend_root())
        down = http.post(URL, headers=bearer(child), json=body())
        assert down.status_code == 403 and "AGENT_NOT_ACTIVE" in codes(down)

        async def revive_root() -> None:
            async with engine(owner_url) as eng, eng.begin() as c:
                await c.execute(
                    text(
                        "UPDATE agent.agent_profile SET status = 'ACTIVE' WHERE agent_principal_id = :a"
                    ),
                    {"a": uuid.UUID(root)},
                )

        asyncio.run(revive_root())
        assert http.post(URL, headers=bearer(child), json=body()).status_code == 200

        # --- the sponsor leaves: the very next call is refused, before any sweep has run
        asyncio.run(disable_sponsor())
        orphan = http.post(URL, headers=bearer(child), json=body())
        assert orphan.status_code == 403 and "SPONSOR_NOT_ACTIVE" in codes(orphan)

    # --- every outcome is on the trail, with the agent as the actor and the whole chain named
    rows = asyncio.run(
        db(
            "SELECT event_type, actor_type, outcome, body::text AS body FROM evidence.audit_record "
            "WHERE tenant_id = :t AND event_type LIKE 'tool_call.%'"
        )
    )
    kinds = {r.event_type for r in rows}
    assert kinds == {"tool_call.authorized", "tool_call.denied"}
    assert {r.actor_type for r in rows} == {"AGENT"}
    allowed = [r for r in rows if r.event_type == "tool_call.authorized"]
    assert len(allowed) == 3 and all(f"{child},{root}" in r.body for r in allowed)
    assert {r.outcome for r in allowed} == {"SUCCESS"}
    refused = [r for r in rows if r.event_type == "tool_call.denied"]
    assert refused and {r.outcome for r in refused} == {
        "DENIED"
    }  # a refusal never reads as a success
