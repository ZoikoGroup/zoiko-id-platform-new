"""API-based agents (CR-093): register an API client as a build, bind it to an agent, revoke the binding.

The steps are the registry's own, in the same order and with the same people: register the client (a build, DRAFT), a second person approves it,
a blueprint release is made from the approved build and approved by someone else, the agent is registered from that release, and THEN the client
is bound to the agent. The binding satisfies the runtime-binding activation gate that a container binding satisfies on the image path; the other
gates (sponsor, approved blueprint, tool manifest, security review, kill path, short-lived credentials, traceability) are unchanged.

What an API client cannot prove is what code runs behind it. The platform therefore caps what such an agent may hold (`domain.api_agents`),
and the gateway enforces that cap on every call.

Not built, deliberately: how the client obtains a token for the agent. That is the security owner's decision (CR-066 covers only the build
pipeline). Until it is made, a bound API agent can be governed, set up and activated, but has no credential path of its own.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.machine.application import agents as agent_service
from zid_platform.modules.machine.domain import api_agents as d
from zid_platform.modules.machine.domain.agents import AgentStatus, MachineError
from zid_platform.modules.machine.infrastructure import agent_store, build_store, machine_store

BINDING_LIFETIME = timedelta(
    days=365
)  # a key rotation is a new build and a new approval long before this ends


def _now() -> datetime:
    return datetime.now(UTC)


async def register_api_client(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    name: str,
    jwks: dict[str, Any],
    audiences: list[str],
    source_ref: str | None,
    provenance_ref: str | None,
    actor: str,
) -> dict[str, Any]:
    """Register an API client as a build. The platform generates the client id; the caller supplies PUBLIC keys only. The digest of the client's
    descriptor is what everything after names, so changing a key is a different build that needs its own approval."""
    problems = [*d.jwks_problems(jwks), *d.audience_problems(audiences)]
    if problems:
        raise MachineError(
            "API_CLIENT_INVALID", ", ".join(problems)
        )  # codes only: a refused key is never echoed back
    client_id = f"zid-api-{uuid.uuid4().hex[:20]}"
    desc = d.descriptor(client_id, jwks, audiences)
    digest = d.descriptor_digest(client_id, jwks, audiences)
    if await build_store.by_digest(conn, digest) is not None:
        raise MachineError("BUILD_EXISTS", "this client is already registered")
    build_id = str(uuid7())
    await build_store.insert_api_client(
        conn, tenant_id, build_id=build_id, name=name.strip(), artifact_digest=digest, source_ref=source_ref or None,
        provenance_ref=provenance_ref or None, registered_by=actor,
        client_descriptor={**desc, "jwks": {"keys": jwks["keys"]}},
    )  # fmt: skip
    await agent_service._record(
        conn, tenant_id, name="agent_registry.api_client_registered", event=False, aggregate_type="agent_build",
        aggregate_id=f"{tenant_id}:{build_id}", actor=actor, data={"build_id": build_id, "client_id": client_id, "kind": "API_CLIENT"},
    )  # fmt: skip
    build = await build_store.get(conn, build_id)
    assert build is not None
    return {
        **build,
        "client_id": client_id,
        "thumbprints": desc["thumbprints"],
        "audiences": desc["audiences"],
        "next": "A second person approves this build.",
    }


async def bind_api_client(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, actor: str
) -> dict[str, Any]:
    """Bind the agent's approved API client build to it. It needs an APPROVED build of kind API_CLIENT whose digest is the one the agent's
    blueprint release names, and an agent whose listed tools an API-bound agent may hold."""
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise MachineError("AGENT_NOT_FOUND")
    if agent.status in (AgentStatus.REVOKED, AgentStatus.RETIRED):
        raise MachineError(
            "AGENT_INVALID_TRANSITION", f"{agent.status.value} agents take no binding"
        )
    bp = await machine_store.load_blueprint(conn, agent.blueprint_id, agent.blueprint_version)
    if bp is None:
        raise MachineError("BLUEPRINT_NOT_FOUND")
    build = await agent_service.require_build_approved(
        conn, bp.artifact_digest
    )  # approved by someone else, and not revoked
    if build["kind"] != "API_CLIENT":
        raise MachineError(
            "BUILD_NOT_AN_API_CLIENT",
            "this agent's release is an image build: bind it as a workload instead",
        )
    registry = await machine_store.load_tools(conn)
    refused = sorted(
        t
        for t in agent.allowed_tools
        if t in registry
        and d.api_tool_blockers(registry[t].side_effect.value, registry[t].default_risk_class)
    )
    if refused:
        raise MachineError(
            "API_AGENT_TOOL_NOT_ALLOWED", "an API-bound agent cannot hold: " + ", ".join(refused)
        )
    if await agent_store.api_bound_agents(conn, [agent_id]):
        raise MachineError(
            "BINDING_EXISTS", "this agent already has an API client binding: revoke it first"
        )
    binding_id = str(uuid.uuid4())
    await agent_store.insert_api_binding(
        conn, tenant_id, binding_id=binding_id, agent_id=agent_id, build_id=build["build_id"], client_id=build["client_descriptor"]["client_id"],
        descriptor_digest=build["artifact_digest"], valid_to=_now() + BINDING_LIFETIME, created_by=actor,
    )  # fmt: skip
    await agent_service._record(
        conn, tenant_id, name="agent_registry.api_client_bound", event=False, aggregate_type="agent", aggregate_id=agent_id, actor=actor,
        data={"binding_id": binding_id, "build_id": build["build_id"]},
    )  # fmt: skip
    return await view(conn, agent_id)


async def view(conn: AsyncConnection, agent_id: str) -> dict[str, Any]:
    b = await agent_store.api_binding(conn, agent_id)
    if b is None:
        return {"agent_id": agent_id, "bound": False, "binding": None}
    build = await build_store.get(conn, str(b["build_id"]))
    desc = (build or {}).get("client_descriptor") or {}
    return {
        "agent_id": agent_id,
        "bound": b["status"] == "ACTIVE",
        "binding": {
            "binding_id": str(b["binding_id"]), "status": b["status"], "client_id": b["client_id"], "descriptor_digest": b["descriptor_digest"],
            "thumbprints": desc.get("thumbprints", []), "audiences": desc.get("audiences", []), "valid_to": b["valid_to"].isoformat(),
            "created_at": b["created_at"].isoformat(), "revoke_reason": b["revoke_reason"],
        },
        "limits": {
            "max_risk_class": d.API_MAX_RISK_CLASS, "side_effects": sorted(d.API_ALLOWED_SIDE_EFFECTS), "attestation_cap": d.API_ATTESTATION_CAP,
            "note": "An API client proves it holds a key, not what code runs behind it, so this agent may hold only tools that read or can be undone.",
        },
    }  # fmt: skip


async def revoke_api_binding(
    conn: AsyncConnection, tenant_id: str, agent_id: str, *, reason: str, actor: str
) -> dict[str, Any]:
    """Revoke the binding (terminal) and stop the agent now if it is running: an agent without a runtime binding must not act."""
    if not await agent_store.revoke_api_binding(conn, agent_id, reason):
        raise MachineError("BINDING_NOT_FOUND", "this agent has no live API client binding")
    agent = await machine_store.load_agent(conn, agent_id)
    suspended = False
    if agent is not None and agent.status is AgentStatus.ACTIVE:
        await agent_service.suspend(
            conn, tenant_id, agent_id, reason="API_BINDING_REVOKED", actor=actor
        )
        suspended = True
    await agent_service._record(
        conn, tenant_id, name="agent_registry.api_client_binding_revoked", event=False, aggregate_type="agent", aggregate_id=agent_id,
        actor=actor, data={"reason_code": reason[:64], "agent_suspended": suspended},
    )  # fmt: skip
    return {**(await view(conn, agent_id)), "agent_suspended": suspended}
