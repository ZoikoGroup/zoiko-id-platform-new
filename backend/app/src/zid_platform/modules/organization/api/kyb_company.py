"""A company's verification grade and its representatives' mandates (ZID-DTV-WF-02 sections 4, 12, 19; CR-109).

    GET  /v1/kyb/assertions                  the company's current grade, the claims it proves, why not higher, what is allowed NOW, worked out again from the stored facts
    GET  /v1/kyb/assertions/history          the grade over time (versions, grade, trigger, when): no facts, no reviewers
    POST /v1/kyb/assertions:assess           assess again from what is recorded and store the result       (organization.kyb.assess)
    GET  /v1/kyb/entitlements?action=        may this person do this act now, and if not, why
    GET  /v1/kyb/mandates                    the representatives' mandates
    POST /v1/kyb/mandates                    record a mandate                                              (organization.kyb.mandate.manage)
    POST /v1/kyb/mandates/{id}:cosign        a second signatory signs                                      (organization.kyb.mandate.manage)
    POST /v1/kyb/mandates/{id}:revoke        revoke it, with a reason                                      (organization.kyb.mandate.manage)
    GET  /v1/kyb/appeals                     the appeals against the company's decisions, with the safe message for each
    POST /v1/kyb/appeals                     open an appeal against one assessment version                 (organization.kyb.appeal.open)
    POST /v1/kyb/appeals/{id}:decide         UPHELD or ALLOWED, by someone who is no party and made no part (organization.kyb.appeal.decide)
    POST /v1/kyb/evidence-exports            a signed, watermarked summary of the grade history            (organization.kyb.export)

The grade is a description of what is proven, never a privilege: `permitted_actions` is recomputed on each read. Nothing here shows a document, a reviewer or a person's data.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.organization.application import kyb_assessment as assessment
from zid_platform.modules.organization.application import kyb_company as company
from zid_platform.modules.organization.application import kyb_operations as ops
from zid_platform.modules.organization.domain import appeal as ap
from zid_platform.modules.organization.domain.kyb_grade import BINDING_ACTIONS
from zid_platform.modules.organization.domain.kyb_mandate import MandateError
from zid_platform.protected import protected_action

router = APIRouter(tags=["organization-kyb"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "MANDATE_NOT_FOUND": 404,
    "MANDATE_REVOKED": 409,
    "COSIGNER_IS_REPRESENTATIVE": 403,
    "COSIGNER_IS_RECORDER": 403,
    "MANDATE_SELF_RECORDED": 403,
}


def _fail(e: MandateError) -> ZidError:
    return ZidError(e.code, e.detail or e.code, http_status=_STATUS.get(e.code, 422))


_APPEAL_STATUS = {
    "DECISION_NOT_FOUND": 404,
    "APPEAL_NOT_FOUND": 404,
    "APPEAL_ALREADY_OPEN": 409,
    "APPEAL_ALREADY_DECIDED": 409,
    "DECIDER_MADE_THE_DECISION": 403,
    "DECIDER_IS_A_PARTY": 403,
}


def _appeal_fail(e: ap.AppealError) -> ZidError:
    return ZidError(e.code, e.detail or e.code, http_status=_APPEAL_STATUS.get(e.code, 422))


def _sign(request: Request) -> Any:
    sign = getattr(request.app.state, "company_evidence_sign", None)
    if sign is None:
        raise ZidError(
            "SIGNING_UNAVAILABLE",
            "No key is configured to sign verification evidence.",
            http_status=503,
            retryable=True,
        )
    return sign


class MandateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person: uuid.UUID
    role: str = Field(max_length=20)
    source: str = Field(max_length=30)
    powers: list[str] = Field(min_length=1, max_length=10)
    evidence_ref: str = Field(min_length=1, max_length=512)
    valid_from: datetime
    valid_to: datetime | None = None
    second_signatory_required: bool = False


class RevokeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _me(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


@router.get("/v1/kyb/assertions")
async def current(request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        s = await assessment.latest(conn, _tenant(caller))
        e = await assessment.allowed(conn, _tenant(caller), person=_me(caller), now=now)
    return dict(assessment.view(s, e))


@router.get("/v1/kyb/assertions/history")
async def history(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = await assessment.history(conn, _tenant(caller))
    return {
        "items": [
            {
                "version": r.version,
                "grade": r.grade.value,
                "label": r.label,
                "claims": list(r.claims),
                "reasons": list(r.reasons),
                "trigger": r.trigger,
                "assessed_at": r.assessed_at.isoformat(),
            }
            for r in rows
        ]
    }


@router.post("/v1/kyb/assertions:assess")
async def assess(request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.assess",
            resource="kyb_assessment:tenant",
            now=now,
        )
        stored = await company.reassess(
            conn, _tenant(caller), trigger="REQUESTED", actor=_me(caller), now=now
        )
        e = await assessment.allowed(conn, _tenant(caller), person=_me(caller), now=now)
    return dict(assessment.view(stored, e))


@router.get("/v1/kyb/entitlements")
async def entitlement(
    request: Request, caller: CallerDep, action: Annotated[str, Query(max_length=60)]
) -> dict[str, Any]:
    if (
        action not in BINDING_ACTIONS
        and not action.startswith("ORG_")
        and action != "RISK_APPROVED_COMMERCIAL_WORKFLOWS"
    ):
        raise ZidError(
            "ACTION_UNKNOWN", "That is not an act the company grade governs.", http_status=422
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        e = await assessment.allowed(
            conn, _tenant(caller), person=_me(caller), now=datetime.now(UTC)
        )
    ok = action in e.actions
    return {
        "action": action,
        "allowed": ok,
        "grade": e.grade.value,
        "label": e.label,
        "reasons": [] if ok else list(e.reasons),
    }


@router.get("/v1/kyb/mandates")
async def mandates(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await company.list_mandates(conn, _tenant(caller))}


@router.post("/v1/kyb/mandates", status_code=201)
async def record_mandate(body: MandateIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.mandate.manage",
            resource=f"kyb_mandate:{body.person}",
            now=now,
        )
        try:
            mandate_id = await company.record_mandate(
                conn, _tenant(caller), person=str(body.person), recorder=_me(caller), role=body.role, source=body.source, powers=body.powers,
                valid_from=body.valid_from, valid_to=body.valid_to, evidence_ref=body.evidence_ref, second_signatory_required=body.second_signatory_required, now=now,
            )  # fmt: skip
        except MandateError as e:
            raise _fail(e) from e
        s = await assessment.latest(conn, _tenant(caller))
    return {"mandate_id": mandate_id, "grade": s.grade.value if s else "K0"}


@router.post("/v1/kyb/mandates/{mandate_id}:cosign")
async def cosign(mandate_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.mandate.manage",
            resource=f"kyb_mandate:{mandate_id}",
            now=now,
        )
        try:
            await company.cosign_mandate(
                conn, _tenant(caller), str(mandate_id), cosigner=_me(caller), now=now
            )
        except MandateError as e:
            raise _fail(e) from e
        s = await assessment.latest(conn, _tenant(caller))
    return {"mandate_id": str(mandate_id), "grade": s.grade.value if s else "K0"}


@router.post("/v1/kyb/mandates/{mandate_id}:revoke")
async def revoke(
    mandate_id: uuid.UUID, body: RevokeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.mandate.manage",
            resource=f"kyb_mandate:{mandate_id}",
            now=now,
        )
        try:
            await company.revoke_mandate(
                conn, _tenant(caller), str(mandate_id), by=_me(caller), reason=body.reason, now=now
            )
        except MandateError as e:
            raise _fail(e) from e
        s = await assessment.latest(conn, _tenant(caller))
    return {"mandate_id": str(mandate_id), "grade": s.grade.value if s else "K0"}


class AppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2000)


class AppealDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["UPHELD", "ALLOWED"]
    reason: str = Field(min_length=1, max_length=2000)


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(min_length=1, max_length=200)


@router.get("/v1/kyb/appeals")
async def list_appeals(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await ops.appeals(conn, _tenant(caller))}


@router.post("/v1/kyb/appeals", status_code=201)
async def open_appeal(body: AppealIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.appeal.open",
            resource=f"kyb_assessment:{body.decision_version}",
            now=now,
        )
        try:
            appeal_id = await ops.open_appeal(
                conn,
                _tenant(caller),
                opened_by=_me(caller),
                version=body.decision_version,
                reason=body.reason,
                now=now,
            )
        except ap.AppealError as e:
            raise _appeal_fail(e) from e
    return {"appeal_id": appeal_id, "state": "OPEN", "message": ap.applicant_message(ap.State.OPEN)}


@router.post("/v1/kyb/appeals/{appeal_id}:decide")
async def decide_appeal(
    appeal_id: uuid.UUID, body: AppealDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.appeal.decide",
            resource=f"kyb_appeal:{appeal_id}",
            now=now,
        )
        try:
            return await ops.decide_appeal(
                conn,
                _tenant(caller),
                str(appeal_id),
                decider=_me(caller),
                outcome=ap.State(body.outcome),
                reason=body.reason,
                now=now,
            )
        except ap.AppealError as e:
            raise _appeal_fail(e) from e


@router.post("/v1/kyb/evidence-exports", status_code=201)
async def evidence_export(body: ExportIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    sign = _sign(request)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="organization.kyb.export",
            resource="kyb_assessment:tenant",
            now=now,
        )
        try:
            return await ops.evidence_export(
                conn,
                _tenant(caller),
                requester=_me(caller),
                purpose=body.purpose,
                sign=sign,
                now=now,
            )
        except ap.AppealError as e:
            raise _appeal_fail(e) from e
