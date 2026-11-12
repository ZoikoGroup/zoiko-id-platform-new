"""Workload identity administration (DEC-F7; MACHINE-001 §16; F-W6; CR-014).

    GET  /v1/machine/workload-bindings?status=              machine.workload.manage (R3; read)
    POST /v1/machine/workload-bindings                      machine.workload.manage
    POST /v1/machine/workload-bindings:revoke               machine.workload.manage
    POST /v1/machine/environments/{environment_id}:quarantine   machine.workload.manage
    POST /v1/machine/environments/{environment_id}:release      machine.workload.manage
    GET  /v1/machine/trust-domain                           the cell's trust domain (public)
    POST /v1/machine/workload-token                         a mesh-verified workload's credential

The workload token is the SVID -> logical principal step of DEC-F7 §3: the peer identity comes
from the mesh (never a header the caller sets), the tenant from the SPIFFE path, the principal
from the registered binding; the token is short-lived and bound to the presenting SVID (RFC 8705
cnf.x5t), so it is useless away from that workload. Authority, mandate and policy are evaluated
on every later call, as for any principal.

Registration is a governed administrative act: a workload never registers or rebinds itself.
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_context.spiffe import SpiffeError, SpiffeId, tenant_of
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import key_m2m, record_billable
from zid_platform.modules.machine.api.agents import builds_enforced
from zid_platform.modules.machine.application import attestation
from zid_platform.modules.machine.application import workload as service
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.infrastructure import workload_store as store
from zid_platform.protected import protected_action
from zid_platform.workload_identity import workload_peer_svid

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
async def list_bindings(
    request: Request, caller: CallerDep, status: str | None = None
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource="workload_binding:*", change="read"
        )
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
        raise ZidError(
            "WORKLOAD_IDENTITY_UNAVAILABLE", "No trust domain is configured.", http_status=503
        )

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource=f"workload_binding:{body.spiffe_id}"
        )
        b = await service.register_binding(
            conn,
            caller.ctx.tenant_id or "",
            policy=policy,
            actor=caller.ctx.principal_id or "",
            require_approved_build=builds_enforced(request),
            **body.model_dump(),
        )
        return {
            "binding_id": b.binding_id,
            "spiffe_id": b.workload_identity_id,
            "status": b.status.value,
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class RevokeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    spiffe_id: str = Field(min_length=10, max_length=2048)
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/workload-bindings:revoke")
async def revoke(body: RevokeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource=f"workload_binding:{body.spiffe_id}"
        )
        n = await service.revoke_binding(
            conn,
            caller.ctx.tenant_id or "",
            body.spiffe_id,
            reason=body.reason,
            actor=caller.ctx.principal_id,
        )
        return {"spiffe_id": body.spiffe_id, "revoked": n}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class QuarantineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")


@router.post("/environments/{environment_id}:quarantine")
async def quarantine(
    environment_id: str, body: QuarantineBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource=f"workload_environment:{environment_id}"
        )
        rev = await service.quarantine_environment(
            conn,
            caller.ctx.tenant_id or "",
            environment_id,
            reason=body.reason,
            actor=caller.ctx.principal_id,
        )
        return {"environment_id": environment_id, "quarantined": True, "security_revision": rev}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/environments/{environment_id}:release")
async def release(environment_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action=ACTION, resource=f"workload_environment:{environment_id}"
        )
        await service.release_environment(
            conn, caller.ctx.tenant_id or "", environment_id, actor=caller.ctx.principal_id or ""
        )
        return {"environment_id": environment_id, "quarantined": False}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class WorkloadTokenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    environment_id: str = Field(pattern=r"^[a-z0-9-]{1,64}$")
    artifact_digest: str = Field(pattern=r"^sha-?256:[0-9a-f]{64}$")
    # Accepted for older callers and ignored: the attestation level comes from verified attestation records, never from
    # what the requesting workload says about itself (CR-042).
    attestation: int = Field(default=0, ge=0, le=3, deprecated=True)


class AttestationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=20, max_length=8192)


@router.post("/attestations:verify")
async def verify_attestation(body: AttestationBody, request: Request) -> dict[str, Any]:
    """Record a runtime attestation signed by an attestor this cell trusts (MACHINE-001 §9, §21). The signature is the
    only credential: the tenant is read from the signed SPIFFE ID, the claims are checked only after the signature
    passes, and an out-of-date but genuinely signed statement is kept as a FAILED record so the refusal is evidence."""
    keys = list(getattr(request.app.state, "attestor_keys", None) or [])
    if not keys:
        raise ZidError("NOT_FOUND", "No such endpoint.", http_status=404)
    try:
        header, claims = attestation.signature_checked(body.statement, keys)
        stmt = attestation.read_claims(header, claims)
    except MachineError as e:
        status = 400 if e.code == "ATTESTATION_SIGNATURE_INVALID" else 422
        raise ZidError(e.code, "The attestation could not be accepted.", http_status=status) from e
    problem = attestation.level_problem(stmt, keys) or attestation.timing_problem(stmt)
    base = current_context() or RequestContext("req_attest", "cor_attest", "zoiko-id", "unknown")
    ctx = base.with_identity(stmt.tenant_id, None)
    from zid_db import tenant_transaction as tx

    async with tx(request_engine(request), ctx) as conn:
        attestation_id = await service.record_attestation(conn, stmt, failure_code=problem)
    if problem:
        raise ZidError(problem, "The attestation could not be accepted.", http_status=422)
    return {
        "attestation_id": attestation_id,
        "status": "VALID",
        "level": stmt.level,
        "valid_until": stmt.valid_until.isoformat(),
    }


WORKLOAD_TOKEN_LIFETIME = timedelta(minutes=5)


@router.post("/workload-token")
async def workload_token(
    body: WorkloadTokenBody,
    request: Request,
    peer: Annotated[tuple[SpiffeId, bytes], Depends(workload_peer_svid)],
) -> dict[str, Any]:
    from zid_platform.modules.session.ports import issue_access_token

    settings = getattr(request.app.state, "token_settings", None)
    if settings is None:
        raise ZidError("UNAUTHENTICATED", "No token issuer is configured.", http_status=503)
    sid, leaf = peer
    try:
        tenant = tenant_of(sid)
    except SpiffeError as e:
        raise ZidError(e.code, str(e), http_status=401) from e
    base = current_context() or RequestContext(
        "req_workload", "cor_workload", "zoiko-id", "unknown"
    )
    ctx = base.with_identity(tenant, None)
    now = datetime.now(UTC)
    try:
        from zid_db import tenant_transaction as tx

        async with tx(request_engine(request), ctx) as conn:
            principal = await service.resolve_workload(
                conn,
                spiffe_id=sid,
                environment_id=body.environment_id,
                artifact_digest=body.artifact_digest,
            )
            await service.record_credential_issued(
                conn, tenant, principal, expires_at=now + WORKLOAD_TOKEN_LIFETIME
            )
            await record_billable(
                conn,
                tenant_id=tenant,
                meter="M2M_GRANT",
                key=key_m2m(f"workload:{uuid.uuid4()}"),
                at=now,
                principal_class="NON_HUMAN",
            )
    except MachineError as e:
        raise ZidError(e.code, "This workload may not act right now.", http_status=403) from e
    x5t = base64.urlsafe_b64encode(hashlib.sha256(leaf).digest()).rstrip(b"=").decode("ascii")
    token = issue_access_token(
        settings.keyring,
        issuer=settings.issuer,
        audience=settings.audience,
        subject=principal.logical_principal_id,
        tenant_id=tenant,
        client_id=f"workload:{sid}",
        now=now,
        scope=frozenset({"workload"}),
        cert_thumbprint=x5t,
        risk_class=2,
        lifetime=WORKLOAD_TOKEN_LIFETIME,
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": int(WORKLOAD_TOKEN_LIFETIME.total_seconds()),
        "principal_id": principal.logical_principal_id,
        "security_revision": principal.security_revision,
        "cnf": {"x5t#S256": x5t},
    }
