"""Organization claims and their reviews (WF-13B; ZID-ED-03, ZID-ED-04).

    POST /v1/org-claims/search                      find the company in an official source (candidates, never auto-picked)
    POST /v1/org-claims                             open a provisional claim for ONE selected company and check it
    GET  /v1/org-claims/{id}/status                 the four independent checks and one outcome, safe for the applicant
    POST /v1/org-claims/{id}/refresh                check the sources again; the earlier decision is kept
    POST /v1/org-claims/{id}/screening              declare the people and ownership to screen (directors, owners, sanctions)

    GET  /v1/reviews/screening-cases/{id}           the alerts with the listed names   (verification.screening.read_restricted)
    POST /v1/reviews/screening-cases/{id}/rulings   a reviewer's ruling                (verification.screening.review)
    POST /v1/reviews/kyb-cases/{id}/rulings         a reviewer's ruling                (organization.kyb.review)

The applicant routes need an OWNER or ADMIN of the tenant. Nothing the client says decides anything: the server re-reads the
official source, derives the representative's authority from the authority root it holds, and issues the outcome. A match, an
upload or a payment never makes a company verified. Names listed on a sanctions list are never in an applicant response; the
reviewer routes are protected actions, so a role flag never grants them.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce
from zid_platform.modules.organization.ports import (
    KybAdapter,
    KybApplicant,
    KybError,
    KybReview,
    KybStatus,
    default_adapters,
    kyb_get_case,
    kyb_refresh,
    kyb_request_verification,
    kyb_review,
    kyb_routing,
    kyb_search,
)
from zid_platform.modules.screening.ports import (
    Ruling,
    ScreeningError,
    ScreeningStatus,
    case_for_kyb,
    restricted_detail,
    screening_config,
)
from zid_platform.modules.screening.ports import review as screening_review
from zid_platform.modules.tenant.ports import Mode, live_roles, matrix_gate
from zid_platform.modules.tenant.ports import Role as TenantRole
from zid_platform.modules.verification.ports import Capability
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1", tags=["org-claims"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_KYB_STATUS = {
    "IDENTIFIER_INVALID": 422,
    "JURISDICTION_INVALID": 422,
    "REGISTER_REQUIRED": 422,
    "NAME_REQUIRED": 422,
    "CASE_NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "REVIEWER_IS_REQUESTER": 403,
    "CASE_NOT_IN_REVIEW": 409,
    "RATIONALE_REQUIRED": 422,
    "REVIEW_OUTCOME_INVALID": 422,
}
_SCREENING_STATUS = {
    "SCREENING_RESIDENCY_DENIED": 403,
    "AMEND_NOT_AVAILABLE": 409,
    "APPEAL_NOT_AVAILABLE": 409,
    "APPEAL_ALREADY_OPEN": 409,
    "STATEMENT_INVALID": 422,
    "APPEAL_KIND_INVALID": 422,
    "CASE_NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "REVIEWER_CONFLICT": 403,
    "CASE_NOT_IN_REVIEW": 409,
    "RATIONALE_REQUIRED": 422,
    "RULING_OUTCOME_INVALID": 422,
    "COUNTRY_INVALID": 422,
}


def _kyb_error(e: KybError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_KYB_STATUS.get(e.code, 422),
    )


def _screening_error(e: ScreeningError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_SCREENING_STATUS.get(e.code, 422),
    )


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


async def _require_admin(conn: Any, caller: Caller) -> str:
    me = _me(caller)
    if not any(a.role in (TenantRole.OWNER, TenantRole.ADMIN) for a in await live_roles(conn, me)):
        raise ZidError(
            "TENANT_ROLE_REQUIRED", "An OWNER or ADMIN role is required.", http_status=403
        )
    return me


def _adapters(request: Request) -> dict[str, KybAdapter]:
    injected = getattr(request.app.state, "kyb_adapters", None)
    if injected is not None:
        return dict(injected)  # tests supply their own sources
    return default_adapters(
        getattr(request.app.state, "secret_broker", None),
        getattr(request.app.state, "kyb_transport", None),
    )


def _gate(request: Request) -> Callable[[str], None]:
    """The country must be open for company verification (EDR-001) before anything is looked up or stored."""

    def gate(country: str) -> None:
        enforce(request, country, Capability.BUSINESS_VERIFICATION)

    return gate


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


# ---- the four checks and one outcome (WF-13B-03) -----------------------------------------------------------------------------

_COMPANY = {
    KybStatus.VERIFIED: "COMPLETED",
    KybStatus.NEEDS_REVIEW: "HELD",
    KybStatus.UNAVAILABLE: "PENDING",
    KybStatus.IN_PROGRESS: "PENDING",
    KybStatus.NOT_STARTED: "PENDING",
    KybStatus.REJECTED: "DECLINED",
    KybStatus.EXPIRED: "NEEDS_ACTION",
}
_SCREENING = {
    ScreeningStatus.VERIFIED: "COMPLETED",
    ScreeningStatus.REVIEW_REQUIRED: "HELD",
    ScreeningStatus.UNAVAILABLE: "PENDING",
    ScreeningStatus.GATHERING: "NEEDS_ACTION",
    ScreeningStatus.SCREENING: "PENDING",
    ScreeningStatus.NOT_STARTED: "PENDING",
    ScreeningStatus.REJECTED: "DECLINED",
    ScreeningStatus.EXPIRED: "NEEDS_ACTION",
    ScreeningStatus.SUSPENDED: "HELD",
}


def final_outcome(checks: dict[str, str]) -> str:
    """The one label (WF-13B-03D). VERIFIED only when every check is complete; the person-assurance check is a separate
    verification step, so while it is not complete a company is never VERIFIED here."""
    states = set(checks.values())
    if "DECLINED" in states:
        return "DECLINED"
    if "HELD" in states:
        return "IN_REVIEW"
    if "NEEDS_ACTION" in states:
        return "NEEDS_EVIDENCE"
    if states == {"COMPLETED"}:
        return "VERIFIED"
    return "PROVISIONAL"


async def _status(conn: Any, tenant_id: str, case_id: str, now: datetime) -> dict[str, Any]:
    kyb = await kyb_get_case(conn, tenant_id, case_id, now=now)
    screening = await case_for_kyb(conn, tenant_id, case_id, now=now)
    checks = {
        "company": _COMPANY[kyb.status],
        "person_assurance": "PENDING",  # identity verification is its own step and is not read here
        "authority": "COMPLETED" if screening and screening[1] else "PENDING",
        "risk": _SCREENING[screening[0].status] if screening else "PENDING",
    }
    return {
        "claim_id": case_id,
        "jurisdiction": kyb.jurisdiction,
        "registration_number": kyb.registration_number,
        "outcome": final_outcome(checks),
        "checks": checks,
        "company": {
            "status": kyb.status.value,
            "review_reason": kyb.review_reason,
            "reasons": list(kyb.reasons),
            "expires_at": kyb.expiry_at.isoformat() if kyb.expiry_at else None,
        },
        "screening": (
            {
                "status": screening[0].status.value,
                "next_action": screening[0].next_action,
                "tasks": list(screening[0].tasks),
                "expires_at": screening[0].expiry_at.isoformat()
                if screening[0].expiry_at
                else None,
            }
            if screening
            else None
        ),
    }


# ---- applicant routes --------------------------------------------------------------------------------------------------------


class SearchIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(
        pattern=r"^[A-Z]{2}$"
    )  # the company's incorporation country, not where the user lives
    query: str = Field(min_length=2, max_length=200)


@router.post(
    "/org-claims/search",
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def search(body: SearchIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
    enforce(request, body.country, Capability.BUSINESS_VERIFICATION)
    found = await kyb_search(
        body.query,
        body.country,
        adapters=_adapters(request),
        routing=kyb_routing(),
        now=datetime.now(UTC),
    )
    return {
        "candidates": [
            {
                "jurisdiction": c.jurisdiction,
                "registration_number": c.registration_number,
                "legal_name": c.legal_name,
                "status": c.registration_status,
                "confidence": c.confidence,  # ranking only: nothing is chosen for the applicant
                "register": c.registry_authority,
            }
            for c in found[:10]
        ],
        "manual_review_available": True,  # "Can't find your organization?" leads to governed review, never a badge
    }


class ClaimIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    registration_number: str = Field(min_length=1, max_length=40)
    register_id: str | None = Field(default=None, max_length=80)
    legal_name: str = Field(min_length=1, max_length=300)
    entity_form: str | None = Field(default=None, max_length=80)
    lei: str | None = Field(default=None, min_length=20, max_length=20)


@router.post(
    "/org-claims",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def create_claim(
    body: ClaimIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(min_length=8, max_length=128)],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            view = await kyb_request_verification(
                conn,
                tenant,
                applicant=KybApplicant(
                    body.country,
                    body.registration_number,
                    body.legal_name,
                    body.entity_form,
                    body.lei,
                    body.register_id,
                ),
                requested_by=me,
                idempotency_key=idempotency_key,
                adapters=_adapters(request),
                routing=kyb_routing(),
                sign=_sign(request),
                now=now,
                country_gate=_gate(request),
            )
        except KybError as e:
            raise _kyb_error(e) from e
        return await _status(conn, tenant, view.case_id, now)


@router.get(
    "/org-claims/{claim_id}/status",
    dependencies=[Depends(matrix_gate("b.view-verification-state-expiration", Mode.READ))],
)
async def claim_status(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
        try:
            return await _status(conn, caller.ctx.tenant_id or "", str(claim_id), datetime.now(UTC))
        except KybError as e:
            raise _kyb_error(e) from e


@router.post(
    "/org-claims/{claim_id}/refresh",
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def refresh_claim(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            await kyb_refresh(
                conn,
                tenant,
                str(claim_id),
                adapters=_adapters(request),
                routing=kyb_routing(),
                sign=_sign(request),
                now=now,
                actor=me,
                country_gate=_gate(request),
            )
            return await _status(conn, tenant, str(claim_id), now)
        except KybError as e:
            raise _kyb_error(e) from e


# ---- reviewer routes (protected actions: a role never grants these) ---------------------------------------------------------


class RulingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]
    rationale: str = Field(min_length=10, max_length=2000)


class KybRulingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["VERIFIED", "REJECTED", "NEEDS_REVIEW"]
    rationale: str = Field(min_length=10, max_length=2000)


@router.get("/reviews/screening-cases/{case_id}")
async def screening_alerts(
    case_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.read_restricted",
            resource=f"screening_case:{case_id}",
        )
        try:
            return await restricted_detail(conn, caller.ctx.tenant_id or "", str(case_id))
        except ScreeningError as e:
            raise _screening_error(e) from e


@router.post("/reviews/screening-cases/{case_id}/rulings")
async def screening_ruling(
    case_id: uuid.UUID, body: RulingIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.review",
            resource=f"screening_case:{case_id}",
        )
        try:
            view = await screening_review(
                conn,
                tenant,
                str(case_id),
                ruling=Ruling(_me(caller), ScreeningStatus(body.outcome), body.rationale),
                cfg=screening_config(),
                sign=_sign(request),
                now=now,
            )
        except ScreeningError as e:
            raise _screening_error(e) from e
    return {"case_id": view.case_id, "status": view.status.value, "version": view.version}


@router.post("/reviews/kyb-cases/{case_id}/rulings")
async def kyb_ruling(
    case_id: uuid.UUID, body: KybRulingIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="organization.kyb.review", resource=f"kyb_case:{case_id}"
        )
        try:
            view = await kyb_review(
                conn,
                tenant,
                str(case_id),
                ruling=KybReview(_me(caller), KybStatus(body.outcome), body.rationale),
                routing=kyb_routing(),
                sign=_sign(request),
                now=now,
            )
        except KybError as e:
            raise _kyb_error(e) from e
    return {"case_id": view.case_id, "status": view.status.value, "version": view.version}
