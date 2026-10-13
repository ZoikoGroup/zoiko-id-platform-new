"""Account recovery API (ZID-AUTH-001 §14, §22, §24; AUTH-W7; AUTH-13).

    POST /authn/recovery-codes                    new batch (caller: fresh strong session, AAL-Z3)
    POST /authn/recovery                          start with a recovery code (tenant from client_id)
    POST /authn/recovery/{id}:continue            pass the gates; READY returns an enrollment ticket
    POST /authn/recovery/{id}:cancel              the owner, from a live session, stops a recovery
    POST /admin/recovery-cases                    support opens a case   (authentication.recovery.assist)
    POST /admin/recovery-cases/{id}:deny          deny a case            (authentication.recovery.deny)

The recovery handle and enrollment ticket are shown once; only their hashes are stored. The
ticket registers exactly one passkey (POST /authn/webauthn/registration/*), which completes the
recovery and revokes every older session.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.application import recovery
from zid_platform.modules.authentication.domain.assurance import AAL, effective_level
from zid_platform.modules.authentication.domain.recovery import RecoveryError
from zid_platform.modules.authentication.infrastructure import recovery_store
from zid_platform.modules.session.ports import client_tenant
from zid_platform.protected import protected_action

router = APIRouter(tags=["recovery"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {"RECOVERY_NOT_FOUND": 404, "RECOVERY_CODE_INVALID": 401, "RECOVERY_NOT_ALLOWED": 403}


def _err(e: RecoveryError) -> ZidError:
    return ZidError(e.code, "Recovery could not proceed.", http_status=_STATUS.get(e.code, 409))


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _view(p: recovery.Progress) -> dict[str, Any]:
    out: dict[str, Any] = {
        "recovery_id": p.recovery_id,
        "recovery_class": p.recovery_class,
        "status": p.status,
        "missing": list(p.missing),
    }
    if p.handle:
        out["recovery_handle"] = p.handle
    if p.enrollment_ticket:
        out["enrollment_ticket"] = p.enrollment_ticket
    if p.cooling_off_until:
        out["cooling_off_until"] = p.cooling_off_until.isoformat()
    return out


async def _tenant(request: Request, client_id: str) -> str:
    tenant = await client_tenant(request_engine(request), client_id)
    if tenant is None:
        raise ZidError("CLIENT_UNKNOWN", "Unknown client.", http_status=400)
    return tenant


@router.post("/authn/recovery-codes", status_code=201)
async def issue_codes(request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    if (
        effective_level(AAL(min(caller.authn.level, 2)), auth_time=caller.authn.auth_time, now=now)
        < AAL.Z3
    ):
        raise ZidError(
            "STEP_UP_REQUIRED", "A fresh strong authentication is required.", http_status=403
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        codes = await recovery.issue_codes(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            principal_id=caller.ctx.principal_id or "",
            now=now,
        )
    return {
        "codes": codes,
        "note": "shown once; each code works once; this batch replaces the last",
    }


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)
    recovery_code: str = Field(min_length=16, max_length=32)


@router.post("/authn/recovery", status_code=201)
async def start(body: StartIn, request: Request) -> dict[str, Any]:
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(
            request_engine(request), _ctx().with_identity(tenant, None)
        ) as c:
            progress = await recovery.start_with_code(
                c, tenant_id=tenant, code=body.recovery_code, now=now
            )
    except RecoveryError as e:
        raise _err(e) from e
    return _view(progress)


class ContinueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)
    recovery_handle: str = Field(min_length=16, max_length=128)
    approval_request_id: str | None = None


@router.post("/authn/recovery/{recovery_id}:continue")
async def continue_(recovery_id: uuid.UUID, body: ContinueIn, request: Request) -> dict[str, Any]:
    tenant = await _tenant(request, body.client_id)
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(
            request_engine(request), _ctx().with_identity(tenant, None)
        ) as c:
            progress = await recovery.advance(
                c,
                recovery_id=str(recovery_id),
                handle=body.recovery_handle,
                approval_request_id=body.approval_request_id,
                now=now,
            )
    except RecoveryError as e:
        raise _err(e) from e
    return _view(progress)


@router.post("/authn/recovery/{recovery_id}:cancel")
async def cancel(recovery_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The account owner, still signed in somewhere, stops a recovery they did not start."""
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            case = await recovery_store.load(conn, str(recovery_id))
            if case.principal_id != caller.ctx.principal_id:
                raise RecoveryError("RECOVERY_NOT_FOUND")
            await recovery.deny(
                conn, recovery_id=str(recovery_id), reason="OWNER_CANCELLED", now=now
            )
        except RecoveryError as e:
            raise _err(e) from e
    return {"recovery_id": str(recovery_id), "status": "DENIED"}


class AssistIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID


@router.post("/admin/recovery-cases", status_code=201)
async def assist(body: AssistIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="authentication.recovery.assist",
            resource=f"principal:{body.principal_id}",
        )
        if str(body.principal_id) == caller.ctx.principal_id:
            raise ZidError(
                "RECOVERY_NOT_ALLOWED", "Operators cannot open their own recovery.", http_status=403
            )
        try:
            progress = await recovery.open_assisted(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                principal_id=str(body.principal_id),
                operator_id=caller.ctx.principal_id or "",
                now=now,
            )
        except RecoveryError as e:
            raise _err(e) from e
    return _view(progress)  # the handle goes to the user out of band; it is not proof of identity


class DenyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=200)


@router.post("/admin/recovery-cases/{recovery_id}:deny")
async def deny(
    recovery_id: uuid.UUID, body: DenyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="authentication.recovery.deny",
            resource=f"recovery_case:{recovery_id}",
        )
        try:
            await recovery.deny(conn, recovery_id=str(recovery_id), reason=body.reason, now=now)
        except RecoveryError as e:
            raise _err(e) from e
    return {"recovery_id": str(recovery_id), "status": "DENIED"}
