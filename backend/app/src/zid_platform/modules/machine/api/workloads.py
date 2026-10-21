"""Workload identity administration (DEC-F7; MACHINE-001 §16; F-W6; CR-014).

    GET  /v1/machine/workload-bindings?status=              machine.workload.manage (R3; read)
    POST /v1/machine/workload-bindings                      machine.workload.manage
    POST /v1/machine/workload-bindings:revoke               machine.workload.manage
    POST /v1/machine/environments/{environment_id}:quarantine   machine.workload.manage
    POST /v1/machine/environments/{environment_id}:release      machine.workload.manage
    GET  /v1/machine/trust-domain                           the cell's trust domain (public)

Registration is a governed administrative act: a workload never registers or rebinds itself.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.machine.application import workload as service
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.infrastructure import workload_store as store
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["workload-identity"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
ACTION = "machine.workload.manage"


def http(e: MachineError) -> ZidError:
    status = 404 if e.code.endswith(("NOT_FOUND", "UNREGISTERED", "NOT_QUARANTINED")) else 409
    if e.code.startswith(("SPIFFE_", "WORKLOAD_ARTIFACT")):
        status = 422
    return ZidError(e.code, str(e), http_status=status)


async def _run(request: Request, caller: Caller, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn)
    except MachineError as e:
        raise http(e) from e


@router.get("/trust-domain")
async def trust_domain(request: Request) -> dict[str, Any]:
    policy = getattr(request.app.state, "trust_policy", None)
    return {
        "trust_domain": policy.own_domain if policy else None,
        "federated": sorted(policy.federated) if policy else [],
    }


@router.get("/workload-bindings")
async def list_bindings(request: Request, caller: CallerDep, status: str | None = None) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action=ACTION, resource="workload_binding:*", change="read")
        return {
            "items": [
                {
                    "binding_id": str(r.binding_id),
                    "spiffe_id": r.workload_identity_id,
                    "logical_principal_id": str(r.logical_principal_id),
                    "environment_id": r.environment_id,
                    "artifact_digest": r.artifact_digest,
                    "attestation_required": r.attestation_required,
                    "status": r.status,
                    "security_revision": r.security_revision,
                    "valid_from": r.valid_from.isoformat(),
                    "valid_to": r.valid_to.isoformat(),
                }
                for r in await store.bindings(conn, status=status)
            ]
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class BindingBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_principal_id: str = Field(min_length=36, max_length=36)
    spiffe_id: str = Field(min_length=10, max_length=2048)
    blueprint_version: str = Field(min_length=1, max_length=64)
    artifact_digest: str = Field(pattern=r"^sha-?256:[0-9a-f]{64}$")
    environment_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    attestation_required: int = Field(default=0, ge=0, le=3)
    valid_to: datetime


@router.post("/workload-bindings", status_code=201)
async def register(body: BindingBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    policy = getattr(request.app.state, "trust_policy", None)
    if policy is None:
        raise ZidError("WORKLOAD_IDENTITY_UNAVAILABLE", "No trust domain is configured.", http_status=503)

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action=ACTION, resource=f"workload_binding:{body.spiffe_id}")
        b = await service.register_binding(
            conn, caller.ctx.tenant_id or "", policy=policy, actor=caller.ctx.principal_id or "",
            **body.model_dump(),
        )
        return {"binding_id": b.binding_id, "spiffe_id": b.workload_identity_id, "status": b.status.value}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spiffe_id: str = Field(min_length=10, max_length=2048)
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/workload-bindings:revoke")
async def revoke(body: RevokeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action=ACTION, resource=f"workload_binding:{body.spiffe_id}")
        n = await service.revoke_binding(
            conn, caller.ctx.tenant_id or "", body.spiffe_id, reason=body.reason, actor=caller.ctx.principal_id
        )
        return {"spiffe_id": body.spiffe_id, "revoked": n}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class QuarantineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/environments/{environment_id}:quarantine")
async def quarantine(environment_id: str, body: QuarantineBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action=ACTION, resource=f"workload_environment:{environment_id}")
        rev = await service.quarantine_environment(
            conn, caller.ctx.tenant_id or "", environment_id, reason=body.reason, actor=caller.ctx.principal_id
        )
        return {"environment_id": environment_id, "quarantined": True, "security_revision": rev}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/environments/{environment_id}:release")
async def release(environment_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action=ACTION, resource=f"workload_environment:{environment_id}")
        await service.release_environment(conn, caller.ctx.tenant_id or "", environment_id, actor=caller.ctx.principal_id or "")
        return {"environment_id": environment_id, "quarantined": False}

    return await _run(request, caller, go)  # type: ignore[no-any-return]
