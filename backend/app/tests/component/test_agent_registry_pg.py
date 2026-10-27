"""Agent, blueprint and tool registry over HTTP (CR-021; ZID-MACHINE-001 §2-3, §12-15, §18; UX-11, UX-12).

Full app against PostgreSQL with RLS and real access tokens: a tool is not executable until ACTIVE; a
blueprint's publisher cannot approve it; an agent cannot be ACTIVE until all ten gates hold, and the
security review must be independent; sub-agents are bounded by their parent; the kill path and a
blueprint compromise reach exactly what they should; a revoked agent never returns.
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
    Attestation,
    BindingStatus,
    WorkloadBinding,
)
from zid_platform.modules.machine.infrastructure.machine_store import save_binding
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
ACTIONS = [
    "machine.agent.read",
    "machine.agent.manage",
    "machine.agent.activate",
    "machine.blueprint.manage",
    "machine.tool.manage",
]
RESOURCES = ["agent:*", "blueprint:*", "tool:*"]
DIGEST = "sha256:" + "a" * 64
MANIFEST = "sha256:" + "b" * 64
BASE = "/v1/machine"


def uid() -> str:
    return str(uuid.uuid4())


def test_agent_registry_lifecycle(rt_url: str, owner_url: str) -> None:
    tenant, admin, reviewer, sponsor, operator, publisher, server, nobody_id, org = (
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
        uid(),
    )
    ring = KeyRing(LocalSigner("issuer-1"))

    async def seed() -> None:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            for p, kind in (
                (admin, PrincipalType.HUMAN),
                (reviewer, PrincipalType.HUMAN),
                (sponsor, PrincipalType.HUMAN),
                (operator, PrincipalType.HUMAN),
                (publisher, PrincipalType.HUMAN),
                (nobody_id, PrincipalType.HUMAN),
                (server, PrincipalType.SERVICE),
            ):
                await create_principal(
                    c, Principal(p, tenant, kind, PrincipalStatus.ACTIVE, "uk-1")
                )
            await make_admin(c, tenant, [admin, reviewer, sponsor, publisher], ACTIONS, RESOURCES)
            await create_organization(
                c,
                organization_id=org,
                tenant_id=tenant,
                legal_name="Acme Ltd",
                jurisdiction="GB",
                valid_from=datetime.now(UTC) - timedelta(days=30),
            )
        async with engine(owner_url) as eng, eng.begin() as c:
            await c.execute(
                text(
                    "INSERT INTO agent.writer_fence (tenant_id, cell_id, epoch) VALUES (:t, :c, 1)"
                ),
                {"t": uuid.UUID(tenant), "c": uuid.UUID(CELL)},
            )

    async def new_session(p: str) -> str:
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

    async def bind(agent_id: str) -> str:
        """A live runtime binding (registration of bindings is covered by the workload tests)."""
        binding_id = uid()
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            await save_binding(
                c,
                tenant,
                WorkloadBinding(
                    binding_id,
                    agent_id,
                    f"spiffe://x/{binding_id}",
                    "1.0.0",
                    DIGEST,
                    "prod",
                    Attestation.NONE,
                    BindingStatus.ACTIVE,
                    datetime.now(UTC) - timedelta(minutes=1),
                    datetime.now(UTC) + timedelta(days=30),
                ),
            )
        return binding_id

    async def db(sql: str, **params: Any) -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant)) as c:
            return (await c.execute(text(sql), params)).scalar_one()

    asyncio.run(seed())
    ids = (admin, reviewer, nobody_id, sponsor, publisher)
    sessions = {p: asyncio.run(new_session(p)) for p in ids}
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

    def bearer(sub: str) -> dict[str, str]:
        tok = issue_access_token(
            ring,
            issuer=ISS,
            audience=AUD,
            subject=sub,
            tenant_id=tenant,
            client_id="admin-console",
            now=datetime.now(UTC),
            session_id=sessions[sub],
            scope=frozenset(),
        )
        return {"Authorization": f"Bearer {tok}"}

    adm, rev, nobody = bearer(admin), bearer(reviewer), bearer(nobody_id)
    spo, pub = bearer(sponsor), bearer(publisher)

    with TestClient(app, base_url=ISS) as http:
        # --- nobody without authority sees or changes anything
        assert http.get(f"{BASE}/agents", headers=nobody).status_code == 403
        assert http.post(f"{BASE}/tools", headers=nobody, json={}).status_code in (403, 422)

        # --- UX-12: a registered tool is not executable until ACTIVE; side effects force a mandate
        tool_body = {
            "tool_id": "payments.release",
            "version": "1",
            "server_principal_id": server,
            "canonical_action": "payment.release",
            "default_risk_class": 3,
            "side_effect": "EXTERNAL_TRANSACTION",
            "requires_mandate": False,
        }
        bad = http.post(
            f"{BASE}/tools", headers=adm, json={**tool_body, "canonical_action": "Bad Action"}
        )
        assert bad.status_code == 422
        notserver = http.post(
            f"{BASE}/tools", headers=adm, json={**tool_body, "server_principal_id": sponsor}
        )
        assert notserver.status_code == 409 or notserver.status_code == 422
        tool = http.post(f"{BASE}/tools", headers=adm, json=tool_body)
        assert tool.status_code == 201, tool.text
        assert tool.json()["status"] == "DRAFT" and tool.json()["executable"] is False
        assert tool.json()["requires_mandate"] is True  # forced by the side-effect class
        assert http.post(f"{BASE}/tools", headers=adm, json=tool_body).status_code == 409
        act = http.post(f"{BASE}/tools/payments.release:activate", headers=adm)
        assert act.json()["status"] == "ACTIVE" and act.json()["executable"] is True

        # --- blueprint: immutable release, never approved by its own publisher
        bp = {
            "blueprint_id": "finance-bot",
            "version": "1.0.0",
            "artifact_digest": DIGEST,
            "tool_manifest_digest": MANIFEST,
            "publisher_principal_id": publisher,
        }
        assert (
            http.post(
                f"{BASE}/blueprints", headers=adm, json={**bp, "artifact_digest": "latest"}
            ).status_code
            == 422
        )
        assert http.post(f"{BASE}/blueprints", headers=adm, json=bp).status_code == 201
        assert http.post(f"{BASE}/blueprints", headers=adm, json=bp).status_code == 409
        bp_path = f"{BASE}/blueprints/finance-bot/versions/1.0.0"

        # --- registering an agent needs a known blueprint and registered tools
        agent_body = {
            "agent_name": "Finance approval bot",
            "blueprint_id": "finance-bot",
            "blueprint_version": "1.0.0",
            "operator_principal_id": operator,
            "sponsor_principal_id": sponsor,
            "owning_organization_id": org,
            "model_provider": "example-llm",
            "allowed_tools": ["payments.release"],
            "max_subagent_depth": 2,
        }
        assert (
            http.post(
                f"{BASE}/agents", headers=adm, json={**agent_body, "blueprint_id": "nope"}
            ).status_code
            == 404
        )
        r = http.post(
            f"{BASE}/agents", headers=adm, json={**agent_body, "allowed_tools": ["ghost.tool"]}
        )
        assert r.status_code == 422 and r.json()["error"]["code"] == "TOOL_NOT_REGISTERED"
        created = http.post(f"{BASE}/agents", headers=adm, json=agent_body)
        assert created.status_code == 201, created.text
        agent = created.json()
        aid = agent["agent_principal_id"]
        # UX-11: publisher, operator, sponsor and owning organization are separate fields
        assert (agent["publisher_principal_id"], agent["operator_principal_id"]) == (
            publisher,
            operator,
        )
        assert (agent["sponsor_principal_id"], agent["owning_organization_id"]) == (sponsor, org)
        assert agent["status"] == "DRAFT"

        # --- blueprint still DRAFT: the agent cannot activate, and the publisher cannot approve it
        own_release = http.post(
            f"{bp_path}:approve", headers=pub
        )  # the publisher approving their own release
        assert (
            own_release.status_code == 403
            and own_release.json()["error"]["code"] == "AGENT_REVIEW_NOT_INDEPENDENT"
        )
        assert (
            http.post(f"{BASE}/agents/{aid}:activate", headers=adm).status_code == 409
        )  # DRAFT -> ACTIVE illegal
        assert (
            http.post(f"{BASE}/agents/{aid}:submit", headers=adm).json()["status"]
            == "PENDING_APPROVAL"
        )
        approve = http.post(f"{bp_path}:approve", headers=adm)
        assert approve.status_code == 200 and approve.json()["review_status"] == "APPROVED"
        assert http.post(f"{bp_path}:approve", headers=adm).status_code == 409  # already approved

        # --- all ten gates are visible, and what is still blocking is listed
        ready = http.get(f"{BASE}/agents/{aid}", headers=adm).json()["readiness"]
        assert set(ready["blockers"]) >= {
            "AGENT_RUNTIME_BINDING_REQUIRED",
            "AGENT_SECURITY_REVIEW_REQUIRED",
            "AGENT_KILL_PATH_UNTESTED",
            "AGENT_CREDENTIALS_NOT_SHORT_LIVED",
            "AGENT_OBSERVABILITY_REQUIRED",
        }
        assert "AGENT_SPONSOR_REQUIRED" not in ready["blockers"]  # sponsor + organization verified
        assert "AGENT_BLUEPRINT_NOT_APPROVED" not in ready["blockers"]
        blocked = http.post(f"{BASE}/agents/{aid}:activate", headers=adm)
        assert blocked.status_code == 409 and "AGENT_SECURITY_REVIEW_REQUIRED" in blocked.text

        # --- the security review is independent of the agent's own chain of accountability
        self_review = http.post(
            f"{BASE}/agents/{aid}:attest", headers=spo, json={"gate": "SECURITY_REVIEW"}
        )
        assert self_review.status_code == 403  # the sponsor cannot review their own agent
        assert self_review.json()["error"]["code"] == "AGENT_REVIEW_NOT_INDEPENDENT"
        indep = http.post(
            f"{BASE}/agents/{aid}:attest", headers=adm, json={"gate": "SECURITY_REVIEW"}
        )
        assert indep.status_code == 200  # the admin is not sponsor, operator or publisher
        for gate in ("KILL_PATH_TESTED", "SHORT_LIVED_CREDENTIALS", "OBSERVABILITY"):
            assert (
                http.post(
                    f"{BASE}/agents/{aid}:attest", headers=rev, json={"gate": gate, "note": "ok"}
                ).status_code
                == 200
            )
        assert (
            http.post(
                f"{BASE}/agents/{aid}:attest", headers=adm, json={"gate": "NOT_A_GATE"}
            ).status_code
            == 422
        )

        # --- still no runtime binding: not activatable; with one it activates
        still = http.post(f"{BASE}/agents/{aid}:activate", headers=adm)
        assert still.status_code == 409 and "AGENT_RUNTIME_BINDING_REQUIRED" in still.text
        binding_id = asyncio.run(bind(aid))
        active = http.post(f"{BASE}/agents/{aid}:activate", headers=adm)
        assert active.status_code == 200 and active.json()["status"] == "ACTIVE", active.text
        assert (
            asyncio.run(
                db(
                    "SELECT status::text FROM core.principal WHERE principal_id = :p",
                    p=uuid.UUID(aid),
                )
            )
            == "ACTIVE"
        )

        # --- sub-agents: bounded by the parent's depth and tools
        child_body = {
            "agent_name": "Invoice reader",
            "operator_principal_id": operator,
            "sponsor_principal_id": sponsor,
            "allowed_tools": ["payments.release"],
            "max_subagent_depth": 1,
        }
        too_deep = http.post(
            f"{BASE}/agents/{aid}/children",
            headers=adm,
            json={**child_body, "max_subagent_depth": 2},
        )
        assert too_deep.status_code == 409 and "GRANT_DEPTH_EXCEEDED" in too_deep.text
        child = http.post(f"{BASE}/agents/{aid}/children", headers=adm, json=child_body)
        assert child.status_code == 201 and child.json()["parent_agent_id"] == aid
        assert http.get(f"{BASE}/agents/{aid}", headers=adm).json()["sub_agents"] == 1

        # --- blueprint compromise: the live binding and the agent stop; history stays
        revoked_bp = http.post(
            f"{bp_path}:revoke", headers=adm, json={"reason_code": "SUPPLY_CHAIN"}
        )
        assert revoked_bp.status_code == 200, revoked_bp.text
        assert (
            revoked_bp.json()["bindings_suspended"] == 1
            and revoked_bp.json()["agents_suspended"] == 1
        )
        assert (
            asyncio.run(
                db(
                    "SELECT status FROM agent.workload_binding WHERE binding_id = :b",
                    b=uuid.UUID(binding_id),
                )
            )
            == "SUSPENDED"
        )
        assert http.get(f"{BASE}/agents/{aid}", headers=adm).json()["status"] == "SUSPENDED"
        assert (
            asyncio.run(
                db(
                    "SELECT status::text FROM core.principal WHERE principal_id = :p",
                    p=uuid.UUID(aid),
                )
            )
            == "SUSPENDED"
        )

        # --- coming back needs re-validation: the binding is suspended and the blueprint revoked
        back = http.post(f"{BASE}/agents/{aid}:activate", headers=adm)
        assert back.status_code == 409
        assert (
            "AGENT_RUNTIME_BINDING_REQUIRED" in back.text
            and "AGENT_BLUEPRINT_NOT_APPROVED" in back.text
        )

        # --- the kill path: revoke is terminal and cannot be undone
        gone = http.post(
            f"{BASE}/agents/{aid}:revoke", headers=adm, json={"reason_code": "COMPROMISED"}
        )
        assert gone.status_code == 200 and gone.json()["agent_principal_id"] == aid
        assert http.get(f"{BASE}/agents/{aid}", headers=adm).json()["status"] == "REVOKED"
        assert http.post(f"{BASE}/agents/{aid}:activate", headers=adm).status_code == 409
        assert http.post(f"{BASE}/agents/{aid}:retire", headers=adm).status_code == 409
        assert (
            asyncio.run(
                db(
                    "SELECT status::text FROM core.principal WHERE principal_id = :p",
                    p=uuid.UUID(aid),
                )
            )
            == "DISABLED"
        )

        # --- listing and a compromised tool
        listed = http.get(f"{BASE}/agents", headers=adm, params={"status": "REVOKED"}).json()[
            "items"
        ]
        assert [a["agent_principal_id"] for a in listed] == [aid]
        sus = http.post(
            f"{BASE}/tools/payments.release:suspend",
            headers=adm,
            json={"reason_code": "TOOL_COMPROMISED"},
        )
        assert sus.json()["status"] == "SUSPENDED" and sus.json()["executable"] is False
        assert http.post(f"{BASE}/tools/ghost:suspend", headers=adm, json={}).status_code == 404
        assert [t["tool_id"] for t in http.get(f"{BASE}/tools", headers=adm).json()["items"]] == [
            "payments.release"
        ]
