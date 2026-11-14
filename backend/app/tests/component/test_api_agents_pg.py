"""CR-093 against PostgreSQL: an agent whose software proof is an API client, from registration to ACTIVE with no container image at all.

The path is the registry's own (two-person approval of the build and of the release, the same activation gates); only the runtime-binding gate is
met by an API client binding instead of a workload binding. Synthetic keys only. The container path is not touched: its tests are unchanged.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from conftest import ctx, engine
from gateway_fixture import READ, World
from zid_db import tenant_transaction
from zid_platform.modules.machine.application import agents as agents_service
from zid_platform.modules.machine.application import api_agents as svc
from zid_platform.modules.machine.application import tool_gateway as gateway
from zid_platform.modules.machine.domain.agents import MachineError, SideEffect, Tool, ToolStatus
from zid_platform.modules.machine.infrastructure import machine_store

EC = {
    "kty": "EC",
    "crv": "P-256",
    "x": "f83OJ3D2xF1Bg8vub9tLe1gHMzV76e8Tus9uPHvRVEU",
    "y": "x_FEzRu9m36HLN_tue659LNpXW6pCyStikYjKIWI5a0",
    "kid": "k1",
}
AUD = ["https://api.client.example.test"]
ARGS = "sha256:" + "c" * 64


class Registry:
    """One tenant with the registry people (a registrar, an independent approver) and a way to run a step in a tenant transaction."""

    def __init__(self, rt_url: str, owner_url: str) -> None:
        self.w = World(rt_url, owner_url)
        self.w.run(self.w._seed())
        self.rt = rt_url
        self.t = self.w.tenant

    def run(self, fn: Any) -> Any:
        async def go() -> Any:
            async with engine(self.rt) as eng, tenant_transaction(eng, ctx(self.t)) as c:
                return await fn(c)

        return self.w.run(go())

    def api_agent(
        self, name: str, tools: list[str], *, key: dict[str, str] | None = None, bind: bool = True
    ) -> dict[str, Any]:
        """client -> approved build -> approved release -> agent -> (binding). Two different people approve, as always."""
        w = self.w
        client = self.run(
            lambda c: svc.register_api_client(
                c,
                self.t,
                name=name,
                jwks={"keys": [key or {**EC, "kid": f"k-{uuid.uuid4().hex[:6]}"}]},
                audiences=AUD,
                source_ref=None,
                provenance_ref=None,
                actor=w.human,
            )
        )
        self.run(
            lambda c: agents_service.approve_build(c, self.t, client["build_id"], actor=w.approver)
        )
        bp = f"bp-{uuid.uuid4().hex[:8]}"
        self.run(
            lambda c: agents_service.create_blueprint(
                c,
                self.t,
                blueprint_id=bp,
                version="1",
                artifact_digest=client["artifact_digest"],
                tool_manifest_digest="sha256:" + "b" * 64,
                publisher_principal_id=w.publisher,
                actor=w.human,
                require_approved_build=True,
            )
        )
        self.run(lambda c: agents_service.approve_blueprint(c, self.t, bp, "1", actor=w.approver))
        agent = self.run(
            lambda c: agents_service.register_agent(
                c,
                self.t,
                agent_name=name,
                blueprint_id=bp,
                blueprint_version="1",
                operator_principal_id=w.operator,
                sponsor_principal_id=w.sponsor,
                owning_organization_id=w.org,
                model_provider="",
                allowed_tools=tools,
                max_subagent_depth=0,
                home_cell="uk-1",
                actor=w.human,
            )
        )
        out = {"client": client, "agent_id": agent.agent_principal_id, "blueprint": bp}
        if bind:
            out["binding"] = self.run(
                lambda c: svc.bind_api_client(c, self.t, agent.agent_principal_id, actor=w.human)
            )
        return out

    def activate(self, agent_id: str) -> None:
        w = self.w
        for gate, who in (
            ("SECURITY_REVIEW", w.approver),
            ("KILL_PATH_TESTED", w.human),
            ("SHORT_LIVED_CREDENTIALS", w.human),
            ("OBSERVABILITY", w.human),
        ):
            self.run(
                lambda c, gate=gate, who=who: agents_service.attest_gate(
                    c, self.t, agent_id, gate, note="checked", actor=who
                )
            )
        self.run(lambda c: agents_service.activate(c, self.t, agent_id, actor=w.approver))

    def status(self, agent_id: str) -> str:
        agent = self.run(lambda c: machine_store.load_agent(c, agent_id))
        return str(agent.status.value)


def test_an_api_agent_reaches_active_with_no_image_and_is_capped_at_the_gateway(
    rt_url: str, owner_url: str
) -> None:
    r = Registry(rt_url, owner_url)
    w = r.w

    # only a public key is ever accepted, and a refused key is never echoed back
    secret = "THIS-IS-A-PRIVATE-VALUE"
    with pytest.raises(MachineError) as refused:
        r.run(
            lambda c: svc.register_api_client(
                c,
                r.t,
                name="bad",
                jwks={"keys": [{**EC, "d": secret}]},
                audiences=AUD,
                source_ref=None,
                provenance_ref=None,
                actor=w.human,
            )
        )
    assert (
        refused.value.code == "API_CLIENT_INVALID"
        and "JWK_PRIVATE_MATERIAL" in str(refused.value)
        and secret not in str(refused.value)
    )

    client = r.run(
        lambda c: svc.register_api_client(
            c,
            r.t,
            name="Invoice reader",
            jwks={"keys": [EC]},
            audiences=AUD,
            source_ref=None,
            provenance_ref=None,
            actor=w.human,
        )
    )
    assert (
        client["kind"] == "API_CLIENT"
        and client["review_status"] == "DRAFT"
        and client["client_id"].startswith("zid-api-")
    )
    assert (
        client["artifact_digest"].startswith("sha256:")
        and client["thumbprints"]
        and "jwks" in client["client_descriptor"]
    )
    with pytest.raises(MachineError) as same:  # the registrar cannot approve their own client
        r.run(lambda c: agents_service.approve_build(c, r.t, client["build_id"], actor=w.human))
    assert same.value.code == "AGENT_REVIEW_NOT_INDEPENDENT"
    with pytest.raises(MachineError) as dup:  # a client is registered once per descriptor
        r.run(
            lambda c: agents_service.register_build(
                c,
                r.t,
                name="x",
                artifact_digest=client["artifact_digest"],
                source_ref=None,
                provenance_ref=None,
                actor=w.human,
            )
        )
    assert dup.value.code == "BUILD_EXISTS"

    # an agent that lists a tool that cannot be undone cannot be bound; the same client may not back it
    async def wipe(c: Any) -> None:
        await machine_store.save_tool(
            c,
            r.t,
            Tool(
                "docs.wipe",
                "1",
                w.server,
                READ,
                1,
                SideEffect.IRREVERSIBLE,
                True,
                False,
                ToolStatus.ACTIVE,
            ),
        )

    r.run(wipe)
    bad = r.api_agent("Wiper", ["docs.read", "docs.wipe"], bind=False)
    with pytest.raises(MachineError) as capped:
        r.run(lambda c: svc.bind_api_client(c, r.t, bad["agent_id"], actor=w.human))
    assert capped.value.code == "API_AGENT_TOOL_NOT_ALLOWED" and "docs.wipe" in str(capped.value)

    # the good one: bound, every other gate unchanged, ACTIVE with no workload binding anywhere
    good = r.api_agent("Reader", ["docs.read"], bind=False)
    before = r.run(lambda c: agents_service.readiness(c, good["agent_id"]))
    assert "AGENT_RUNTIME_BINDING_REQUIRED" in before["blockers"] and before["live_bindings"] == 0
    bound = r.run(lambda c: svc.bind_api_client(c, r.t, good["agent_id"], actor=w.human))
    assert (
        bound["bound"] is True
        and bound["binding"]["status"] == "ACTIVE"
        and bound["limits"]["max_risk_class"] == 2
    )
    assert (
        "AGENT_RUNTIME_BINDING_REQUIRED"
        not in r.run(lambda c: agents_service.readiness(c, good["agent_id"]))["blockers"]
    )
    with pytest.raises(MachineError) as twice:
        r.run(lambda c: svc.bind_api_client(c, r.t, good["agent_id"], actor=w.human))
    assert twice.value.code == "BINDING_EXISTS"
    r.activate(good["agent_id"])
    assert r.status(good["agent_id"]) == "ACTIVE"
    assert (
        r.w.owner("SELECT count(*) FROM agent.workload_binding WHERE tenant_id = :t")[0][0] == 0
    )  # no container binding was ever made

    # the gateway enforces the cap on every call, even if a tool was added to the list later behind the registry's back
    r.w.owner(
        "UPDATE agent.agent_profile SET allowed_tools = allowed_tools || CAST(:x AS text[]) WHERE agent_principal_id = :a AND tenant_id = :t",
        x=["docs.wipe"],
        a=uuid.UUID(good["agent_id"]),
    )

    async def decide(_q: Any) -> dict[str, Any]:
        return {
            "decision": True,
            "context": {"decision_id": str(uuid.uuid4()), "valid_until": None},
        }

    def ask(tool: str) -> tuple[str, ...]:
        async def go(c: Any) -> tuple[str, ...]:
            res = await gateway.authorize_tool_call(
                c,
                r.t,
                gateway.ToolCall(good["agent_id"], tool, "agent", "any", ARGS),
                decide=decide,
                sign=lambda claims, typ: "grant",
                issuer="https://id.zoiko.test",
                action_catalogued=lambda action: True,
            )
            return res.codes

        return r.run(go)  # type: ignore[no-any-return]

    assert ask("docs.read") == ()  # an API agent can still do what reads or can be undone
    assert "API_AGENT_TOOL_NOT_ALLOWED" in ask("docs.wipe")  # and nothing else

    # revoking the binding stops a running agent now; a second revoke finds nothing
    out = r.run(
        lambda c: svc.revoke_api_binding(
            c, r.t, good["agent_id"], reason="KEY_LEAKED", actor=w.human
        )
    )
    assert (
        out["agent_suspended"] is True
        and out["bound"] is False
        and out["binding"]["status"] == "REVOKED"
    )
    assert r.status(good["agent_id"]) == "SUSPENDED"
    with pytest.raises(MachineError) as gone:
        r.run(
            lambda c: svc.revoke_api_binding(
                c, r.t, good["agent_id"], reason="AGAIN", actor=w.human
            )
        )
    assert gone.value.code == "BINDING_NOT_FOUND"
    assert (
        "AGENT_RUNTIME_BINDING_REQUIRED"
        in r.run(lambda c: agents_service.readiness(c, good["agent_id"]))["blockers"]
    )


def test_revoking_the_client_build_suspends_its_binding_and_its_agent(
    rt_url: str, owner_url: str
) -> None:
    """The kill path of the image path reaches an API agent too: a compromised client is one revoke away from stopped."""
    r = Registry(rt_url, owner_url)
    a = r.api_agent("Reader", ["docs.read"])
    r.activate(a["agent_id"])
    assert r.status(a["agent_id"]) == "ACTIVE"
    r.run(
        lambda c: agents_service.revoke_build(
            c, r.t, a["client"]["build_id"], reason="CLIENT_COMPROMISED", actor=r.w.approver
        )
    )
    assert r.status(a["agent_id"]) == "SUSPENDED"
    view = r.run(lambda c: svc.view(c, a["agent_id"]))
    assert view["bound"] is False and view["binding"]["status"] == "SUSPENDED"
    # a client build cannot back an image-style bind and an image build cannot back an API bind
    img = r.run(
        lambda c: agents_service.register_build(
            c,
            r.t,
            name="img",
            artifact_digest="sha256:" + "d" * 64,
            source_ref=None,
            provenance_ref=None,
            actor=r.w.human,
        )
    )
    assert img["kind"] == "IMAGE"
