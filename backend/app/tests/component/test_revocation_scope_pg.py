"""Stopping agents by scope (ZID-CP-WF07 v2.0 .10; A07-08; CR-169), over the real routes with real tokens against PostgreSQL.

What these hold: a dry run reaches the agents a stop would and changes nothing; a stop by sponsor suspends every agent that sponsor vouches for with an epoch, and the next gateway call is refused; the stop says which places it
cannot reach; the agents stay stopped until someone other than the person who made the stop closes it; the person who made it can never close it or reinstate; and the record cannot be edited or deleted.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
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
CELL_ID = uuid.UUID(CELL)


def uid() -> str:
    return str(uuid.uuid4())


def test_a_stop_by_scope_denies_new_calls_pauses_mandates_is_closed_by_someone_else_and_cannot_be_reinstated_by_its_maker(
    rt_url: str, owner_url: str
) -> None:
    tenant, sponsor, operator, publisher, server, other_server, org, human = (
        uid() for _ in range(8)
    )
    root, child = uid(), uid()
    ring = KeyRing(LocalSigner("issuer-act"))

    def profile(agent_id: str, parent: str | None = None) -> AgentProfile:
        return AgentProfile(
            agent_id, tenant, f"bot-{agent_id[:4]}", "bp", "1", publisher, operator, sponsor, org, "", frozenset({"docs.read"}), 2 if parent is None else 0, AgentStatus.DRAFT, parent_agent_id=parent
        )  # fmt: skip

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (publisher, PrincipalType.HUMAN),
                (human, PrincipalType.HUMAN),
                (server, PrincipalType.SERVICE),
                (other_server, PrincipalType.SERVICE),
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
            await save_tool(
                c,
                tenant,
                Tool(
                    "docs.read",
                    "1",
                    server,
                    READ,
                    1,
                    SideEffect.NONE,
                    False,
                    False,
                    ToolStatus.DRAFT,
                ),
            )
            await save_agent(c, profile(root))
            await save_agent(c, profile(child, root))
            for agent_id in (root, child):
                await create_principal(
                    c,
                    Principal(
                        agent_id,
                        tenant,
                        PrincipalType.AI_AGENT,
                        PrincipalStatus.ACTIVE,
                        "uk-1",
                        sponsor_principal_id=sponsor,
                    ),
                )
            await make_admin(
                c,
                tenant,
                [root, child, human, other_server],
                [
                    READ,
                    "agent.security.pause",
                    "agent.security.restore.close",
                    "machine.agent.activate",
                ],
                ["agent:*", "agent:revocation:*"],
            )
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
                    sid,
                    tenant,
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

    async def db(sql: str, **params: Any) -> list[Any]:
        async with engine(owner_url) as eng, eng.begin() as c:
            return list((await c.execute(text(sql), {"t": uuid.UUID(tenant), **params})).all())

    asyncio.run(seed())
    sessions = {p: asyncio.run(session_for(p)) for p in (root, child, human, server, other_server)}
    app = create_app(
        Settings(
            database_url=rt_url,
            token_issuer=ISS,
            token_audience=AUD,
            public_base_url=AUD,
            environment="test",
            cell_uuid=CELL,
        ),
        keyring=ring,
    )

    def bearer(sub: str, *, scope: frozenset[str] = frozenset({"workload"})) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="agent-runtime",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=scope,
        )
        return {"Authorization": f"Bearer {tok}"}

    def counts() -> Any:
        return asyncio.run(
            db(
                "SELECT (SELECT count(*) FROM agent.tool_grant WHERE tenant_id = :t) AS grants, (SELECT count(*) FROM agent.tool_action WHERE tenant_id = :t) AS actions, "
                "(SELECT count(*) FROM agent.tool_action_receipt WHERE tenant_id = :t) AS receipts, (SELECT count(*) FROM evidence.audit_record WHERE tenant_id = :t) AS audit, "
                "(SELECT count(*) FROM evidence.decision_receipt WHERE tenant_id = :t) AS decisions"
            )
        )[0]

    with TestClient(app, base_url=ISS) as http:
        agent_call = {
            "tool_id": "docs.read",
            "resource_type": "agent",
            "resource_id": "any",
            "arguments_digest": ARGS,
        }
        assert (
            http.post(
                "/v1/machine/tool-calls:authorize", headers=bearer(child), json=agent_call
            ).status_code
            == 200
        )
        person = lambda p: {**bearer(p, scope=frozenset({"admin"}))}

        # a malformed stop is refused; a dry run reaches the same agents the stop would and changes nothing
        assert (
            http.post(
                "/v1/machine/revocations:plan",
                headers=person(human),
                json={"scope": "TENANT", "subject": "x"},
            ).status_code
            == 422
        )
        before = asyncio.run(
            db(
                "SELECT agent_principal_id::text AS a, status::text AS s FROM agent.agent_profile WHERE tenant_id = :t ORDER BY 1"
            )
        )
        planned = http.post(
            "/v1/machine/revocations:plan",
            headers=person(human),
            json={"scope": "SPONSOR", "subject": sponsor},
        )
        assert planned.status_code == 200, planned.text
        p = planned.json()
        assert (
            p["dry_run"] is True
            and set(p["would_stop"]["agents"]) == {root, child}
            and p["would_stop"]["grants_not_yet_spent"] >= 1
        )
        assert (
            p["enforcement"]["enforced_everywhere"] is False
            and p["enforcement"]["unsupported"]
            and "cannot undo" in p["would_not_change"]["note"]
        )
        assert (
            asyncio.run(
                db(
                    "SELECT agent_principal_id::text AS a, status::text AS s FROM agent.agent_profile WHERE tenant_id = :t ORDER BY 1"
                )
            )
            == before
        )
        assert (
            asyncio.run(
                db("SELECT count(*) AS n FROM agent.revocation_event WHERE tenant_id = :t")
            )[0].n
            == 0
        )

        # a stop with no reason, or that reaches nothing, is refused
        assert (
            http.post(
                "/v1/machine/revocations",
                headers=person(human),
                json={"scope": "SPONSOR", "subject": sponsor, "reason": "x"},
            ).status_code
            == 422
        )
        assert (
            http.post(
                "/v1/machine/revocations",
                headers=person(human),
                json={"scope": "SPONSOR", "subject": uid(), "reason": "Nobody vouches"},
            ).status_code
            == 409
        )

        # the stop: every agent that sponsor vouches for, recorded with an epoch, and the next call is refused
        stop = http.post(
            "/v1/machine/revocations",
            headers=person(human),
            json={
                "scope": "SPONSOR",
                "subject": sponsor,
                "reason": "Sponsor reported a compromised key",
            },
        )
        assert stop.status_code == 201, stop.text
        s = stop.json()
        assert (
            s["epoch"] == 1
            and s["state"] == "OPEN"
            and set(s["targets"]["agent_ids"]) == {root, child}
            and s["actor"] == human
        )
        assert (
            s["enforcement"]["acknowledged"][0]["point"] == "tool_gateway"
            and s["enforcement"]["enforced_everywhere"] is False
        )
        statuses = {
            r.a: r.s
            for r in asyncio.run(
                db(
                    "SELECT agent_principal_id::text AS a, status::text AS s FROM agent.agent_profile WHERE tenant_id = :t"
                )
            )
        }
        assert statuses[root] == statuses[child] == "SUSPENDED"
        denied = http.post(
            "/v1/machine/tool-calls:authorize", headers=bearer(child), json=agent_call
        )
        # a stopped agent's suspended principal is refused at sign-in (401) or, if it got that far, by the gateway (403): either way no new grant is signed
        assert denied.status_code in (401, 403)
        assert (
            asyncio.run(db("SELECT count(*) AS n FROM agent.tool_grant WHERE tenant_id = :t"))[0].n
            == 1
        )  # only the call made before the stop
        assert (
            http.post(
                "/v1/machine/revocations",
                headers=person(human),
                json={"scope": "SPONSOR", "subject": sponsor, "reason": "Again"},
            ).status_code
            == 409
        )  # nothing active is left to stop
        assert (
            http.get(
                "/v1/machine/revocations", headers=person(human), params={"open": "true"}
            ).json()["items"][0]["event_id"]
            == s["event_id"]
        )

        # the agents wait for the stop to be closed by someone else, and the person who made it never reinstates them
        reinstate = lambda p: http.post(f"/v1/machine/agents/{root}:activate", headers=person(p))
        again = reinstate(other_server)
        assert again.status_code == 409 and again.json()["error"]["code"] == "REVOCATION_OPEN"
        mine = http.post(
            f"/v1/machine/revocations/{s['event_id']}:close",
            headers=person(human),
            json={"note": "Closing my own stop"},
        )
        assert (
            mine.status_code == 403
            and mine.json()["error"]["code"] == "REVOCATION_CLOSE_NOT_INDEPENDENT"
        )
        closed = http.post(
            f"/v1/machine/revocations/{s['event_id']}:close",
            headers=person(other_server),
            json={"note": "Key rotated and checked"},
        )
        assert (
            closed.status_code == 200
            and closed.json()["state"] == "CLOSED"
            and closed.json()["closed_by"] == other_server
        )
        assert (
            http.post(
                f"/v1/machine/revocations/{s['event_id']}:close",
                headers=person(other_server),
                json={"note": "Twice"},
            ).status_code
            == 409
        )
        by_maker = reinstate(human)
        assert by_maker.json()["error"]["code"] in (
            "REVOCATION_REINSTATE_NOT_INDEPENDENT",
            "AGENT_REVIEW_NOT_INDEPENDENT",
        )  # the maker cannot reinstate
        assert (
            asyncio.run(
                db(
                    "SELECT status::text AS s FROM agent.agent_profile WHERE agent_principal_id = CAST(:a AS uuid)",
                    a=root,
                )
            )[0].s
            == "SUSPENDED"
        )

        # the record cannot be edited or deleted, and nobody can un-close a stop
        with pytest.raises(Exception, match="stop"):
            asyncio.run(db("DELETE FROM agent.revocation_event WHERE tenant_id = :t"))
        with pytest.raises(Exception, match="closed stop|only the closing"):
            asyncio.run(
                db("UPDATE agent.revocation_event SET reason = 'changed' WHERE tenant_id = :t")
            )
