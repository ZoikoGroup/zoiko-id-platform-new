"""Development provider simulator endpoints (ZID-VERIFY-001 §30 VERIFY-W2; local/test only).

Mounted only when ZID_DEV_SIMULATORS is on (refused outside local/test).

    POST /dev/verification-simulator/provider/sessions          the "provider" side of submit
    GET  /dev/verification-simulator/status                     is the simulator here
    POST /dev/verification-simulator/setup                      baseline profile + provider for
                                                                the caller's company (owner/admin
                                                                or its pending owner)
    GET  /dev/verification-simulator/sessions/{id}              what the provider would ask for
    POST /dev/verification-simulator/sessions/{id}:complete     the applicant finishes the check;
                                                                a signed result goes to the real
                                                                provider-callback endpoint
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.tenant.ports import Role, RoleStatus, live_roles
from zid_platform.modules.verification.application.baseline import (
    ensure_simulator_adapter,
    seed_representative_proofing,
)
from zid_platform.modules.verification.domain.verification import SessionStatus, VerificationError
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.simulator import (
    SIM_ADAPTER_ID,
    ProviderSimulator,
)

router = APIRouter(prefix="/dev/verification-simulator", tags=["development"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
JURISDICTIONS = ["GB"]


def _sim(request: Request) -> ProviderSimulator:
    sim: ProviderSimulator | None = getattr(request.app.state, "dev_simulator", None)
    if sim is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    return sim


class SubmitIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # a provider accepts its own request shape
    session_ref: str = Field(min_length=1, max_length=128)


@router.post("/provider/sessions", status_code=202)
async def provider_submit(body: SubmitIn, request: Request) -> dict[str, Any]:
    _sim(request)
    return {"transaction_ref": ProviderSimulator.transaction_ref(body.session_ref)}


@router.get("/status")
async def status(request: Request) -> dict[str, Any]:
    _sim(request)
    return {"enabled": True, "adapter_id": SIM_ADAPTER_ID, "jurisdictions": JURISDICTIONS}


@router.post("/setup")
async def setup(request: Request, caller: CallerDep) -> dict[str, Any]:
    sim = _sim(request)
    me = caller.ctx.principal_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        roles = await live_roles(conn, me)
        if not any(
            a.role in (Role.OWNER, Role.ADMIN)
            and a.status in (RoleStatus.ACTIVE, RoleStatus.PENDING_VERIFICATION)
            for a in roles
        ):
            raise ZidError(
                "TENANT_ROLE_REQUIRED", "An owner or administrator sets this up.", http_status=403
            )
        created = await seed_representative_proofing(
            conn,
            caller.ctx.tenant_id or "",
            now=datetime.now(UTC),
            jurisdictions=JURISDICTIONS,
            simulator_jwks=sim.jwks(),
        )
    return {"profile_created": created is not None, "adapter_id": SIM_ADAPTER_ID}


async def _own_session(conn: Any, caller: Caller, verification_id: uuid.UUID) -> store.VSession:
    try:
        s = await store.load_session(conn, str(verification_id))
    except VerificationError as e:
        raise ZidError("VERIFICATION_NOT_FOUND", "No such check.", http_status=404) from e
    if s.subject_principal_id != caller.ctx.principal_id:
        raise ZidError("VERIFICATION_NOT_FOUND", "No such check.", http_status=404)
    if s.adapter_id != SIM_ADAPTER_ID:
        raise ZidError(
            "NOT_SIMULATED", "This check is handled by a real provider.", http_status=409
        )
    return s


@router.get("/sessions/{verification_id}")
async def session(
    verification_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _sim(request)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await _own_session(conn, caller, verification_id)
        profile = await store.load_profile(conn, s.profile_id)
    return {
        "verification_id": s.session_id,
        "status": s.status.value,
        "required_claims": list(profile.required_claims),
        "jurisdiction": s.jurisdiction,
    }


class CompleteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    legal_name: str = Field(min_length=1, max_length=200)
    birthdate: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    outcome: Literal["VERIFIED", "FAILED", "FRAUD"] = "VERIFIED"


@router.post("/sessions/{verification_id}:complete")
async def complete(
    verification_id: uuid.UUID, body: CompleteIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    sim = _sim(request)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await _own_session(conn, caller, verification_id)
        if s.status is not SessionStatus.PENDING_EXTERNAL or not s.provider_transaction_ref:
            raise ZidError(
                "VERIFICATION_NOT_PENDING", f"The check is {s.status.value}.", http_status=409
            )
        profile = await store.load_profile(conn, s.profile_id)
        await ensure_simulator_adapter(
            conn, tenant, sim.jwks(), JURISDICTIONS
        )  # re-pin this process's key
        ref = s.provider_transaction_ref
    signed = sim.result(
        transaction_ref=ref,
        outcome=body.outcome,
        claims={"legal_name": body.legal_name.strip(), "birthdate": body.birthdate or ""},
        required=list(profile.required_claims),
    )
    # Through the public callback endpoint, as any provider: signature, mapping, dedup, decision.
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=request.app), base_url="http://internal"
    ) as c:
        r = await c.post(
            f"/verification-provider-callbacks/{tenant}.{SIM_ADAPTER_ID}", content=signed
        )
    out = r.json() if r.content else {}
    if r.status_code != 202:
        raise ZidError(
            str(out.get("error", "CALLBACK_REJECTED")),
            "The simulated provider result was rejected.",
            http_status=409,
        )
    return {"verification_id": str(verification_id), "outcome": out.get("outcome")}
