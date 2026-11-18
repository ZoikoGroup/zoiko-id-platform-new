"""A company's WF-05 verification policy, tenant API (ZID-DTV-WF-05 A11; CR-118).

    GET  /v1/check-policies                    the company's policies, newest first          verification:read
    POST /v1/check-policies                    write a draft                                 verification:execute
    POST /v1/check-policies/{id}:submit        the author submits it                         verification:execute
    POST /v1/check-policies/{id}:approve       a different person approves it                verification:review
    POST /v1/check-policies/{id}:reject        a different person rejects it                 verification:review

Only an approved policy has effect: new cases then ask for its required checks, and proof of address documents are limited to its types and ages
(never wider than the platform's). Requiring a check never switches it on.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import check_policies as svc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.domain import check_policy as cp

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Id = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]
_STATUS = {
    "POLICY_NOT_FOUND": 404,
    "APPROVER_IS_AUTHOR": 403,
    "ONLY_AUTHOR_SUBMITS": 403,
    "POLICY_NOT_DRAFT": 409,
    "POLICY_NOT_SUBMITTED": 409,
}
Method = Literal[
    "EMAIL_CHALLENGE", "PHONE_SMS_CHALLENGE", "PHONE_VOICE_CHALLENGE", "CARRIER_INTELLIGENCE", "ADDRESS_LOOKUP", "ADDRESS_DOCUMENT",
    "ADDRESS_DATA_SOURCE", "ADDRESS_POSTAL_CHALLENGE", "DEVICE_RISK",
]  # fmt: skip


class CheckPolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str = Field(pattern=r"^[A-Za-z]{2}$")
    purpose: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    required_methods: list[Method] = Field(default_factory=list, max_length=9)
    document_types: dict[str, int] = Field(
        default_factory=dict,
        max_length=20,
        description="Accepted document type -> maximum age in days.",
    )


class PolicyDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


def _refuse(e: cp.PolicyError) -> ZidError:
    return ZidError(
        e.code if e.code in _STATUS else "CHECK_POLICY_REFUSED",
        str(e).split(":")[0],
        http_status=_STATUS.get(e.code, 422),
    )


def _who(caller: Caller) -> str:
    return caller.ctx.principal_id or caller.client_id


@router.get("/v1/check-policies")
async def listing(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": [svc.view(p) for p in await svc.listing(conn)]}


@router.post("/v1/check-policies", status_code=201)
async def create(body: CheckPolicyIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            p = await svc.create(
                conn, caller.ctx.tenant_id or "", market=body.market, purpose=body.purpose,
                required=[cm.CheckMethod(m) for m in body.required_methods], document_types=body.document_types, by=_who(caller),
                now=datetime.now(UTC),
            )  # fmt: skip
        except cp.PolicyError as e:
            raise _refuse(e) from e
    return svc.view(p)


@router.post("/v1/check-policies/{policy_id}:submit")
async def submit(policy_id: Id, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return svc.view(await svc.submit(conn, policy_id, by=_who(caller)))
        except cp.PolicyError as e:
            raise _refuse(e) from e


async def _decide(
    policy_id: str, body: PolicyDecisionIn, request: Request, caller: Caller, approve: bool
) -> dict[str, Any]:
    _scope(caller, "verification:review")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            p = await svc.decide(
                conn,
                policy_id,
                approve=approve,
                by=_who(caller),
                reason=body.reason,
                now=datetime.now(UTC),
            )
        except cp.PolicyError as e:
            raise _refuse(e) from e
    return svc.view(p)


@router.post("/v1/check-policies/{policy_id}:approve")
async def approve(
    policy_id: Id, body: PolicyDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(policy_id, body, request, caller, True)


@router.post("/v1/check-policies/{policy_id}:reject")
async def reject(
    policy_id: Id, body: PolicyDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _decide(policy_id, body, request, caller, False)
