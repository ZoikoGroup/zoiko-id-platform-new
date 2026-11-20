"""Organization claims and their reviews (WF-13B; ZID-ED-03, ZID-ED-04).

    POST /v1/org-claims/search                      find the company in an official source (candidates, never auto-picked)
    POST /v1/org-claims                             open a provisional claim for ONE selected company and check it
    GET  /v1/org-claims/{id}/status                 the four independent checks and one outcome, safe for the applicant
    POST /v1/org-claims/{id}/refresh                check the sources again; the earlier decision is kept
    POST /v1/org-claims/{id}/screening              declare the people and ownership to screen, and how the person acts for the company
    POST /v1/org-claims/{id}/submit                 say the claim is complete and ready for review (202; once, a repeat changes nothing)
    POST /v1/org-claims/{id}/withdraw               withdraw a claim that is not verified; nothing is deleted

    GET  /v1/reviews/screening-cases/{id}           the alerts with the listed names   (verification.screening.read_restricted)
    POST /v1/reviews/screening-cases/{id}/rulings   a reviewer's ruling                (verification.screening.review)
    POST /v1/reviews/kyb-cases/{id}/rulings         a reviewer's ruling                (organization.kyb.review)

The applicant routes need an OWNER or ADMIN of the tenant. Nothing the client says decides anything: the server re-reads the
official source, derives the representative's authority from the authority root it holds, and issues the outcome. A match, an
upload or a payment never makes a company verified. Names listed on a sanctions list are never in an applicant response; the
reviewer routes are protected actions, so a role flag never grants them.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce, enforce_regulated
from zid_platform.modules.onboarding.application import claims
from zid_platform.modules.organization.ports import (
    KybAdapter,
    KybApplicant,
    KybError,
    KybReview,
    KybStatus,
    default_adapters,
    dual_position,
    kyb_awaiting_compliance,
    kyb_get_case,
    kyb_refresh,
    kyb_request_verification,
    kyb_review,
    kyb_routing,
    kyb_search,
    kyb_submit,
)
from zid_platform.modules.screening.ports import (
    Ruling,
    ScreeningError,
    ScreeningStatus,
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
    "CLAIM_CLOSED": 409,
    "CLAIM_INCOMPLETE": 409,
    "CLAIM_VERIFIED": 409,
    "CLOSE_REASON_INVALID": 422,
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
    "MANDATE_NOT_CONFIRMABLE": 409,
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


class _FixedKey:
    """The development register simulator ignores its credential, but the adapter insists on one."""

    def __init__(self, value: str) -> None:
        self._value = value

    def get(self, name: str) -> str:
        return self._value


def _adapters(request: Request) -> dict[str, KybAdapter]:
    injected = getattr(request.app.state, "kyb_adapters", None)
    if injected is not None:
        return dict(injected)  # tests supply their own sources
    broker = getattr(request.app.state, "secret_broker", None)
    override = getattr(
        request.app.state, "kyb_key_override", None
    )  # set only with the development simulators (local and test)
    if override:
        broker = _FixedKey(override)
    return default_adapters(broker, getattr(request.app.state, "kyb_transport", None))


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

final_outcome = claims.final_outcome
_peek = claims.status  # reads (the status page, readiness) change nothing


async def _status(conn: Any, tenant_id: str, case_id: str, now: datetime) -> dict[str, Any]:
    """What a write route answers with: the status, after remembering it, so that whatever it changed is announced once."""
    seen = await claims.status(conn, tenant_id, case_id, now)
    await claims.record_outcome(conn, tenant_id, case_id, seen, now)
    return seen


async def _open_claim(conn: Any, tenant_id: str, claim_id: str, now: datetime) -> None:
    """A withdrawn or abandoned claim takes nothing more: not a declaration, not an appeal, not a submission."""
    if (await kyb_get_case(conn, tenant_id, claim_id, now=now)).closed_at is not None:
        raise KybError("CLAIM_CLOSED")


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
    await enforce_regulated(request, body.country, Capability.BUSINESS_VERIFICATION, caller.ctx)
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
                "lei": c.lei,
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
    # ZID-RCG-003: the regulatory checks for company verification before any register is asked (the country check also runs inside)
    await enforce_regulated(request, body.country, Capability.BUSINESS_VERIFICATION, caller.ctx)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        # CR-068: count this claim attempt (best effort, never blocks it). The contact key is the person's stable id here.
        from zid_platform.attempt_context import attempt_inputs
        from zid_platform.modules.screening.ports import AttemptAction, note_attempt

        await note_attempt(
            conn,
            tenant,
            subject_ref=f"claim-attempt:{idempotency_key[:32]}",
            action=AttemptAction.CLAIM,
            now=now,
            contact=me,
            **attempt_inputs(request),
        )
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
async def claim_status(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    """The status carries an ETag of exactly what it says, and is never cached by anyone: a person polling it sends the tag back and
    gets a bare 304 while nothing has changed."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
        try:
            shown = await _peek(conn, caller.ctx.tenant_id or "", str(claim_id), datetime.now(UTC))
        except KybError as e:
            raise _kyb_error(e) from e
    tag = (
        '"'
        + hashlib.sha256(json.dumps(shown, sort_keys=True).encode("utf-8")).hexdigest()[:32]
        + '"'
    )
    headers = {"ETag": tag, "Cache-Control": "no-store"}
    if request.headers.get("if-none-match", "").replace("W/", "").strip() == tag:
        return Response(status_code=304, headers=headers)
    return JSONResponse(shown, headers=headers)


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


@router.post(
    "/org-claims/{claim_id}/establish",
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def establish_claim(
    claim_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The claim is VERIFIED (all four checks complete): create the organization from it, name the workspace after the register and activate the claimant as
    owner. Refused with CLAIM_NOT_VERIFIED until then. A repeat answers as it stands (13B hands off to 13C; nothing here grants authority a check has not)."""
    from zid_platform.modules.onboarding.application.organization import (
        establish_from_verified_claim,
    )
    from zid_platform.modules.onboarding.domain.gates import OnboardingError
    from zid_platform.protected import catalog

    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _require_admin(conn, caller)
        try:
            result = await establish_from_verified_claim(
                conn, tenant, str(claim_id), adapters=_adapters(request), catalog=catalog(), now=now
            )
        except OnboardingError as e:
            status = (
                409
                if e.code in ("CLAIM_NOT_VERIFIED", "TENANT_STATE_INVALID")
                else 503
                if e.code == "SOURCE_UNAVAILABLE"
                else 403
                if e.code == "TENANT_OWNER_UNAUTHORIZED"
                else 404
            )
            raise ZidError(
                e.code,
                getattr(e, "detail", e.code),
                http_status=status,
                retryable=e.code == "SOURCE_UNAVAILABLE",
            ) from e
        except KybError as e:
            raise _kyb_error(e) from e
    root = result.root
    return {
        "tenant_status": result.status,
        "organization_id": root.organization_id if root else None,
        "legal_name": root.legal_name if root else None,
        "established": root is not None,
    }


@router.post(
    "/org-claims/{claim_id}/submit",
    status_code=202,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def submit_claim(claim_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """The applicant says the claim is complete. It is accepted for review (202), not decided: the checks are the same checks, and nothing
    here makes the company verified. Once: a repeat is
    the same answer and changes nothing."""
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            seen = await claims.status(conn, tenant, str(claim_id), now)
            if seen["closed_at"] is not None:
                raise KybError("CLAIM_CLOSED", seen["state"])
            _, first = await kyb_submit(conn, tenant, str(claim_id), now=now)
        except KybError as e:
            raise _kyb_error(e) from e
        if first:
            await claims.announce_submitted(conn, tenant, str(claim_id), actor=me, now=now)
        return await _status(conn, tenant, str(claim_id), now)


@router.post(
    "/org-claims/{claim_id}/withdraw",
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def withdraw_claim(
    claim_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The applicant withdraws a claim. Nothing is deleted; nobody is watched for it any more; the applicant may start a new claim. A
    claim that is verified is not withdrawn here (that is a governed change), and a repeat is the same answer."""
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            seen = await claims.status(conn, tenant, str(claim_id), now)
            if seen["closed_at"] is None:
                if seen["outcome"] == "VERIFIED":
                    raise KybError("CLAIM_VERIFIED", "A verified claim is not withdrawn here.")
                await claims.close(conn, tenant, str(claim_id), "WITHDRAWN", actor=me, now=now)
        except KybError as e:
            raise _kyb_error(e) from e
        return await _status(conn, tenant, str(claim_id), now)


# ---- reviewer routes (protected actions: a role never grants these) ---------------------------------------------------------


class RulingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["VERIFIED", "REJECTED", "REVIEW_REQUIRED"]
    rationale: str = Field(min_length=10, max_length=2000)
    # the reviewer read the delegated mandate (an audited read of the document) and confirms the person may act for the company;
    # only a ruling that verifies can say so, and only for a case that rests on a mandate
    confirm_mandate: bool = False


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
                ruling=Ruling(
                    _me(caller), ScreeningStatus(body.outcome), body.rationale, body.confirm_mandate
                ),
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
        escalated = await kyb_awaiting_compliance(conn, tenant, str(case_id))
        if escalated:  # two reviewers disagreed: only someone authorised for escalations may decide it (WF02 K07, T11)
            await protected_action(
                conn,
                caller,
                action="organization.kyb.escalation.decide",
                resource=f"kyb_case:{case_id}",
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
                escalation_authorized=escalated,
            )
        except KybError as e:
            raise _kyb_error(e) from e
    return {
        "case_id": view.case_id,
        "status": view.status.value,
        "version": view.version,
        "position": dual_position(view.status, view.reasons),
    }
