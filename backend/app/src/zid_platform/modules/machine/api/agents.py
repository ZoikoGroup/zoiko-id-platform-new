"""Agent, blueprint and tool registry API (ZID-MACHINE-001 §21; ZID-UX-001 §13; CR-021).

    GET  /v1/machine/agents?status=                         list                   machine.agent.read
    POST /v1/machine/agents                                 register (DRAFT)       machine.agent.manage
    GET  /v1/machine/agents/{id}                            profile + readiness    machine.agent.read
    POST /v1/machine/agents/{id}:submit | :attest           gate attestation       machine.agent.manage
    POST /v1/machine/agents/{id}:suspend                    kill path              machine.agent.manage
    POST /v1/machine/agents/{id}:activate | :revoke | :retire                      machine.agent.activate (R3)
    POST /v1/machine/agents/{id}/children                   sub-agent identity     machine.agent.manage
    GET  /v1/machine/blueprints                             list                   machine.agent.read
    GET  /v1/machine/builds                                 approved builds        machine.agent.read
    POST /v1/machine/builds                                 register (DRAFT)       machine.build.register
    POST /v1/machine/builds:resolve                         digest of an image name machine.build.register (named registries only)
    POST /v1/machine/builds/{id}:approve | :revoke          second person / kill   machine.build.manage (R3)
    POST /v1/machine/blueprints                             draft a release        machine.blueprint.manage
    POST /v1/machine/blueprints/{id}/versions/{v}:approve | :revoke                machine.blueprint.manage (R3)
    GET  /v1/machine/tools                                  list                   machine.agent.read
    POST /v1/machine/tools                                  register (DRAFT)       machine.tool.manage
    POST /v1/machine/tools/{id}:activate | :suspend | :retire                      machine.tool.manage

Identity, not authority: an ACTIVE agent still acts only through grants and mandates, and a tool that
shows up in discovery is not executable until it is ACTIVE here (MACH-13, MACH-15).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.machine.application import agents as service
from zid_platform.modules.machine.domain.agents import ATTESTABLE_GATES, MachineError
from zid_platform.modules.machine.infrastructure import (
    agent_store,
    build_store,
    machine_store,
    registry,
)
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-registry"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
UUID36 = Field(min_length=36, max_length=36)
NAME = Field(min_length=2, max_length=120)


def http(e: MachineError) -> ZidError:
    code = e.code
    if code.endswith("NOT_FOUND"):
        status = 404
    elif code.endswith(("_INVALID", "ARTIFACT_NOT_APPROVED", "TOOL_NOT_REGISTERED")):
        status = 422
    elif code in ("AGENT_REVIEW_NOT_INDEPENDENT",):
        status = 403
    else:
        status = 409
    return ZidError(code, str(e), http_status=status)


async def _run(request: Request, caller: Caller, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _actor(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


# ---------------------------------------------------------------- reads


@router.get("/agents")
async def list_agents(
    request: Request,
    caller: CallerDep,
    status: Literal["DRAFT", "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "REVOKED", "RETIRED"]
    | None = None,
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.agent.read", resource="agent:*")
        return {"items": await agent_store.list_agents(conn, status=status)}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}")
async def read_agent(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.read", resource=f"agent:{agent_id}"
        )
        agent = await service._load(conn, agent_id)
        children = await agent_store.descendants(conn, agent_id)
        return {
            **service.profile_view(agent),
            "readiness": await service.readiness(conn, agent_id),
            "sub_agents": len(children),
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/blueprints")
async def list_blueprints(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.agent.read", resource="blueprint:*")
        return {"items": await agent_store.list_blueprints(conn)}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/tools")
async def list_tools(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.agent.read", resource="tool:*")
        tools = await machine_store.load_tools(conn)
        return {
            "items": [service.tool_view(t) for t in sorted(tools.values(), key=lambda t: t.tool_id)]
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- blueprints


class BlueprintBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    blueprint_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,62}$")
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,31}$")
    artifact_digest: str = Field(pattern=r"^sha-?256:[0-9a-f]{64}$")
    tool_manifest_digest: str = Field(pattern=r"^sha-?256:[0-9a-f]{64}$")
    publisher_principal_id: str = UUID36


@router.post("/blueprints", status_code=201)
async def create_blueprint(
    body: BlueprintBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="machine.blueprint.manage",
            resource=f"blueprint:{body.blueprint_id}@{body.version}",
        )
        bp = await service.create_blueprint(
            conn,
            _tenant(caller),
            actor=_actor(caller),
            require_approved_build=builds_enforced(request),
            **body.model_dump(),
        )
        return {
            "blueprint_id": bp.blueprint_id,
            "version": bp.version,
            "review_status": bp.review.value,
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: str = Field(default="OPERATOR_DECISION", pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


@router.post("/blueprints/{blueprint_id}/versions/{version}:approve")
async def approve_blueprint(
    blueprint_id: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="machine.blueprint.manage",
            resource=f"blueprint:{blueprint_id}@{version}",
        )
        bp = await service.approve_blueprint(
            conn, _tenant(caller), blueprint_id, version, actor=_actor(caller)
        )
        return {
            "blueprint_id": bp.blueprint_id,
            "version": bp.version,
            "review_status": bp.review.value,
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/blueprints/{blueprint_id}/versions/{version}:revoke")
async def revoke_blueprint(
    blueprint_id: str, version: str, body: ReasonBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="machine.blueprint.manage",
            resource=f"blueprint:{blueprint_id}@{version}",
        )
        out = await service.revoke_blueprint(
            conn,
            _tenant(caller),
            blueprint_id,
            version,
            reason=body.reason_code,
            actor=_actor(caller),
        )
        return {"blueprint_id": blueprint_id, "version": version, "review_status": "REVOKED", **out}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- builds (CR-064)


def builds_enforced(request: Request) -> bool:
    """Must a blueprint release run an approved build? On unless the deployment is local, test or dev (or says so)."""
    mode = getattr(request.app.state, "agent_builds", "auto")
    env = getattr(request.app.state, "environment", "local")
    return mode == "enforce" or (mode == "auto" and env not in ("local", "test", "dev"))


class BuildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=120)
    artifact_digest: str = Field(pattern=r"^sha-?256:[0-9a-f]{64}$")
    source_ref: str | None = Field(default=None, max_length=300)
    provenance_ref: str | None = Field(default=None, max_length=300)


@router.get("/builds")
async def list_builds(
    request: Request,
    caller: CallerDep,
    status: Literal["DRAFT", "APPROVED", "REVOKED"] | None = None,
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.agent.read", resource="build:*")
        return {
            "items": await build_store.list_builds(conn, status=status),
            "enforced": builds_enforced(request),
            "lookup_enabled": bool(getattr(request.app.state, "build_registries", ())),
            "images": list(getattr(request.app.state, "build_images", ())),
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/builds", status_code=201)
async def register_build(body: BuildBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        # registering is its own, lower authority so a build pipeline can hold it without being able to approve its own builds
        await protected_action(conn, caller, action="machine.build.register", resource="build:new")
        return await service.register_build(conn, _tenant(caller), actor=_actor(caller), **body.model_dump())

    return await _run(request, caller, go)  # type: ignore[no-any-return]


_LOOKUP_STATUS = {
    "IMAGE_NAME_INVALID": 422,
    "REGISTRY_NOT_ALLOWED": 422,
    "REGISTRY_AUTH_REQUIRED": 422,
    "REGISTRY_LOOKUP_DISABLED": 409,
    "IMAGE_NOT_FOUND": 404,
}


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image: str = Field(min_length=1, max_length=400)


@router.post("/builds:resolve")
async def resolve_build(body: ResolveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The digest an image name points at today, asked of its registry (CR-065). Changes nothing: a person registers what it returns, and a second
    person approves it. The registry is contacted outside the database transaction, only if it is one the operator named."""

    async def authorize(conn: Any) -> None:
        await protected_action(conn, caller, action="machine.build.register", resource="build:new")

    await _run(request, caller, authorize)
    state = request.app.state
    try:
        found = await registry.resolve(
            body.image,
            allowed=getattr(state, "build_registries", ()),
            private_ok=getattr(state, "build_registries_private", ()),
            transport=getattr(state, "registry_transport", None),
            resolver=getattr(state, "registry_resolver", None),
        )
    except registry.RegistryError as e:
        raise ZidError(e.code, str(e), http_status=_LOOKUP_STATUS.get(e.code, 502)) from e
    return {
        "image": body.image.strip(),
        "registry": found.host,
        "repository": found.repository,
        "reference": found.reference,
        "digest": found.digest,
        "media_type": found.media_type,
    }


@router.post("/builds:versions")
async def build_versions(body: ResolveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The versions (tags) an image repository has, so a person picks one instead of typing it. Changes nothing, and the same rules as `:resolve`:
    named registries only, public images only. A picked tag is still turned into a digest by `:resolve` before anything is registered."""

    async def authorize(conn: Any) -> None:
        await protected_action(conn, caller, action="machine.build.register", resource="build:new")

    await _run(request, caller, authorize)
    state = request.app.state
    try:
        found = await registry.list_tags(
            body.image,
            allowed=getattr(state, "build_registries", ()),
            private_ok=getattr(state, "build_registries_private", ()),
            transport=getattr(state, "registry_transport", None),
            resolver=getattr(state, "registry_resolver", None),
        )
    except registry.RegistryError as e:
        raise ZidError(e.code, str(e), http_status=_LOOKUP_STATUS.get(e.code, 502)) from e
    return {"image": body.image.strip(), "registry": found.host, "repository": found.repository, "tags": list(found.tags)}


@router.post("/builds/{build_id}:approve")
async def approve_build(build_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.build.manage", resource=f"build:{build_id}")
        return await service.approve_build(conn, _tenant(caller), build_id, actor=_actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/builds/{build_id}:revoke")
async def revoke_build(build_id: str, body: ReasonBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.build.manage", resource=f"build:{build_id}")
        return await service.revoke_build(
            conn, _tenant(caller), build_id, reason=body.reason_code, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- agents


class AgentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = NAME
    blueprint_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,62}$")
    blueprint_version: str = Field(min_length=1, max_length=32)
    operator_principal_id: str = UUID36
    sponsor_principal_id: str = UUID36
    owning_organization_id: str | None = Field(default=None, min_length=36, max_length=36)
    model_provider: str = Field(default="", max_length=120)
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)
    max_subagent_depth: int = Field(default=0, ge=0, le=5)
    expires_at: datetime | None = None  # after this the agent stops acting (WP-10)


@router.post("/agents", status_code=201)
async def register_agent(body: AgentBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    home = getattr(request.app.state, "home_cell", "unknown")

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="machine.agent.manage", resource="agent:new")
        await require_feature(
            conn,
            "advanced.agent-workload-identities",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        agent = await service.register_agent(
            conn, _tenant(caller), home_cell=home, actor=_actor(caller), **body.model_dump()
        )
        return service.profile_view(agent)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ChildBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_name: str = NAME
    operator_principal_id: str = UUID36
    sponsor_principal_id: str = UUID36
    allowed_tools: list[str] = Field(default_factory=list, max_length=64)
    max_subagent_depth: int = Field(default=0, ge=0, le=5)


@router.post("/agents/{agent_id}/children", status_code=201)
async def register_child(
    agent_id: str, body: ChildBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    home = getattr(request.app.state, "home_cell", "unknown")

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        child = await service.register_child(
            conn,
            _tenant(caller),
            agent_id,
            home_cell=home,
            actor=_actor(caller),
            **body.model_dump(),
        )
        return service.profile_view(child)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:submit")
async def submit_agent(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        return service.profile_view(
            await service.submit(conn, _tenant(caller), agent_id, actor=_actor(caller))
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class AttestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: Literal["SECURITY_REVIEW", "KILL_PATH_TESTED", "SHORT_LIVED_CREDENTIALS", "OBSERVABILITY"]
    note: str = Field(default="", max_length=1000)


@router.post("/agents/{agent_id}:attest")
async def attest(
    agent_id: str, body: AttestBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    assert body.gate in ATTESTABLE_GATES

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        await require_feature(
            conn,
            "advanced.advanced-agent-governance-attestations",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        return await service.attest_gate(
            conn, _tenant(caller), agent_id, body.gate, note=body.note, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:suspend")
async def suspend_agent(
    agent_id: str, body: ReasonBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.manage", resource=f"agent:{agent_id}"
        )
        return await service.suspend(
            conn, _tenant(caller), agent_id, reason=body.reason_code, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:activate")
async def activate_agent(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.activate", resource=f"agent:{agent_id}"
        )
        return service.profile_view(
            await service.activate(conn, _tenant(caller), agent_id, actor=_actor(caller))
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:revoke")
async def revoke_agent(
    agent_id: str, body: ReasonBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.activate", resource=f"agent:{agent_id}"
        )
        return await service.revoke(
            conn, _tenant(caller), agent_id, reason=body.reason_code, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:retire")
async def retire_agent(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.agent.activate", resource=f"agent:{agent_id}"
        )
        return service.profile_view(
            await service.retire(conn, _tenant(caller), agent_id, actor=_actor(caller))
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- tools


class ToolBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,62}$")
    version: str = Field(min_length=1, max_length=32)
    server_principal_id: str = UUID36
    canonical_action: str = Field(min_length=3, max_length=120)
    default_risk_class: int = Field(ge=0, le=3)
    side_effect: Literal["NONE", "REVERSIBLE", "IRREVERSIBLE", "EXTERNAL_TRANSACTION"]
    requires_mandate: bool = False
    requires_human_approval: bool = False


@router.post("/tools", status_code=201)
async def register_tool(body: ToolBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.tool.manage", resource=f"tool:{body.tool_id}"
        )
        tool = await service.register_tool(
            conn, _tenant(caller), actor=_actor(caller), **body.model_dump()
        )
        return service.tool_view(tool)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


async def _tool_change(
    request: Request, caller: Caller, tool_id: str, change: str, reason: str
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="machine.tool.manage", resource=f"tool:{tool_id}"
        )
        return await service.change_tool(
            conn, _tenant(caller), tool_id, change=change, reason=reason, actor=_actor(caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/tools/{tool_id}:activate")
async def activate_tool(tool_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _tool_change(request, caller, tool_id, "activate", "REVIEWED")


@router.post("/tools/{tool_id}:suspend")
async def suspend_tool(
    tool_id: str, body: ReasonBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _tool_change(request, caller, tool_id, "suspend", body.reason_code)


@router.post("/tools/{tool_id}:retire")
async def retire_tool(tool_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _tool_change(request, caller, tool_id, "retire", "RETIRED")
