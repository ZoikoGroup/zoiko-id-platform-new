"""WF-05 verification cases, tenant API (ZID-DTV-WF-05 §19, §20, §21, A01, A09; CR-112).

    POST /v1/verification-cases                          open a case (Idempotency-Key required)       verification:execute
    GET  /v1/verification-cases/{id}                     the case and the binding it opened with      verification:read
    GET  /v1/verification-cases/{id}/requirements        what the case may use now, and why not       verification:read
    GET  /v1/verification-cases/{id}/evidence            each assertion on its own, no overall result verification:read
    POST /v1/verification-cases/{id}:close               end the case; it takes no new evidence       verification:execute

A case is bound to the caller's tenant (never one named in the body), an opaque subject reference (never an email, phone or name), a purpose, a market
and this cell's region, under the eligibility answer it opened with. When no requested method is available the case opens CREATED with the reasons and
the message "This step is unavailable": nothing is silently opted in. Errors are problem documents with stable codes; nothing returns a contact, a
challenge secret, a supplier payload or a device identifier.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Path, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import check_cases
from zid_platform.modules.verification.domain import check_cases as cc
from zid_platform.modules.verification.domain import check_methods as cm
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
CasePath = Annotated[str, Path(pattern=r"^[0-9a-fA-F-]{36}$")]
MethodName = Literal[
    "EMAIL_CHALLENGE",
    "PHONE_SMS_CHALLENGE",
    "PHONE_VOICE_CHALLENGE",
    "CARRIER_INTELLIGENCE",
    "ADDRESS_LOOKUP",
    "ADDRESS_DOCUMENT",
    "ADDRESS_DATA_SOURCE",
    "ADDRESS_POSTAL_CHALLENGE",
    "DEVICE_RISK",
]
_STATUS = {"CASE_NOT_FOUND": 404, "CASE_MOVE_NOT_ALLOWED": 409, "CASE_CLOSED": 409}


class VerificationCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{2,127}$",
        description="Your opaque reference for the person. Never an email address, phone number or name.",
    )
    purpose: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    market: str = Field(pattern=r"^[A-Za-z]{2}$")
    methods: list[MethodName] = Field(min_length=1, max_length=9)


def _refuse(e: cc.CaseError) -> ZidError:
    return ZidError(
        "VERIFICATION_CASE_REFUSED" if e.code not in _STATUS else e.code,
        str(e),
        http_status=_STATUS.get(e.code, 422),
    )


def _overlays(request: Request) -> Any:
    return getattr(request.app.state, "privacy_overlays", None) or privacy_overlays()


@router.post("/v1/verification-cases", status_code=201)
async def open_case(
    body: VerificationCaseIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    tenant = caller.ctx.tenant_id or ""
    scope = Scope(tenant, caller.client_id, "POST /v1/verification-cases")
    ctx = current_context()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        try:
            case, _ = await check_cases.open_case(
                conn, tenant_id=tenant, subject_ref=body.subject_ref, purpose=body.purpose, market=body.market,
                region=str(getattr(request.app.state, "home_cell", "") or ""), requested=[cm.CheckMethod(m) for m in body.methods],
                overlays=_overlays(request), correlation_id=ctx.correlation_id if ctx else "cor_unknown",
                created_by=caller.ctx.principal_id or caller.client_id, now=datetime.now(UTC),
            )  # fmt: skip
        except cc.CaseError as e:
            raise _refuse(e) from e
        out = check_cases.case_view(case)
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out


@router.get("/v1/verification-cases/{case_id}")
async def get_case(case_id: CasePath, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return check_cases.case_view(await check_cases.get_case(conn, case_id))
        except cc.CaseError as e:
            raise _refuse(e) from e


@router.get("/v1/verification-cases/{case_id}/requirements")
async def requirements(case_id: CasePath, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await check_cases.requirements(
                conn, case_id, overlays=_overlays(request), now=datetime.now(UTC)
            )
        except cc.CaseError as e:
            raise _refuse(e) from e


@router.get("/v1/verification-cases/{case_id}/evidence")
async def evidence(case_id: CasePath, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await check_cases.evidence(conn, case_id, now=datetime.now(UTC))
        except cc.CaseError as e:
            raise _refuse(e) from e


@router.post("/v1/verification-cases/{case_id}:close")
async def close(case_id: CasePath, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return check_cases.case_view(
                await check_cases.close(conn, case_id, now=datetime.now(UTC))
            )
        except cc.CaseError as e:
            raise _refuse(e) from e


@router.get("/v1/verification-cases")
async def list_cases(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The latest 50 cases of the caller's tenant, newest first."""
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": [check_cases.case_view(c) for c in await check_cases.recent(conn)]}
