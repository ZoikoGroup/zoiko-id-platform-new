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
from decimal import Decimal, InvalidOperation
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce
from zid_platform.modules.organization.ports import (
    CompaniesHouseKyb,
    GleifKyb,
    KybAdapter,
    KybApplicant,
    KybError,
    KybReview,
    KybStatus,
    SireneKyb,
    kyb_get_case,
    kyb_refresh,
    kyb_request_verification,
    kyb_review,
    kyb_routing,
    kyb_search,
    usable_root_for,
)
from zid_platform.modules.screening.ports import (
    AuthorityEvidence,
    AuthorityKind,
    Control,
    Edge,
    Graph,
    Holder,
    HolderKind,
    Party,
    PartyKind,
    Role,
    Ruling,
    ScreeningError,
    ScreeningStatus,
    case_for_kyb,
    open_case,
    restricted_detail,
    screening_config,
)
from zid_platform.modules.screening.ports import review as screening_review
from zid_platform.modules.tenant.ports import Mode, live_roles, load_tenant, matrix_gate
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
    broker = getattr(request.app.state, "secret_broker", None)
    transport = getattr(request.app.state, "kyb_transport", None)

    def key() -> str | None:
        try:
            return str(broker.get("companies-house-api-key")) if broker else None
        except Exception:  # noqa: BLE001 - the source then reports itself unavailable
            return None

    return {
        "companies-house": CompaniesHouseKyb(key, transport),
        "gleif": GleifKyb(transport),
        "sirene": SireneKyb(transport),
    }  # Creditsafe joins only when it is enabled in the routing and has credentials


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


class PartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ref: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")  # the applicant's own id for this person
    kind: Literal["PERSON", "ENTITY"] = "PERSON"
    name: str = Field(min_length=1, max_length=300)
    birth: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}-\d{2})?$")
    countries: list[str] = Field(default_factory=list, max_length=10)
    identifiers: list[str] = Field(default_factory=list, max_length=10)


class HoldingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owner: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    owned: str = Field(default="company", pattern=r"^[A-Za-z0-9_.:-]{1,64}$")
    shares_pct: str | None = None
    votes_pct: str | None = None
    controls: list[
        Literal[
            "APPOINT_BOARD_MAJORITY", "SIGNIFICANT_INFLUENCE", "OTHER_CONTROL", "NOMINEE", "TRUST"
        ]
    ] = Field(default_factory=list)


class ScreeningIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    representative: PartyIn
    directors: list[PartyIn] = Field(default_factory=list, max_length=50)
    owners: list[PartyIn] = Field(default_factory=list, max_length=100)
    holdings: list[HoldingIn] = Field(default_factory=list, max_length=300)
    elevated_risk: bool = False


def _pct(value: str | None) -> Decimal | None:
    if value is None:
        return None
    try:
        d = Decimal(value)
    except InvalidOperation as e:
        raise ZidError(
            "VALIDATION_FAILED", "A holding percentage is not a number.", http_status=422
        ) from e
    if not (Decimal(0) <= d <= Decimal(100)):
        raise ZidError(
            "VALIDATION_FAILED", "A holding percentage is between 0 and 100.", http_status=422
        )
    return d


def _party(p: PartyIn) -> Party:
    return Party(
        p.ref, PartyKind(p.kind), p.name.strip(), p.birth, tuple(p.countries), tuple(p.identifiers)
    )


@router.post(
    "/org-claims/{claim_id}/screening",
    status_code=201,
    dependencies=[Depends(matrix_gate("b.submit-refresh-organization-verification", Mode.PREPARE))],
)
async def start_screening(
    claim_id: uuid.UUID, body: ScreeningIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        me = await _require_admin(conn, caller)
        try:
            kyb = await kyb_get_case(conn, tenant, str(claim_id), now=now)
        except KybError as e:
            raise _kyb_error(e) from e
        # Authority is what the server finds, not what the applicant says: an authority root this person holds, which exists
        # only when the person was matched to a current officer of the register. No root, no verified authority.
        t = await load_tenant(conn, tenant)
        root_id = (
            await usable_root_for(conn, tenant, t.organization_id or "", me, now)
            if t is not None and t.organization_id
            else None
        )
        authority = (
            AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, root_id)
            if root_id
            else AuthorityEvidence(AuthorityKind.NONE)
        )
        subjects = [
            (Role.ENTITY, Party("company", PartyKind.ENTITY, kyb.legal_name or "")),
            (Role.REPRESENTATIVE, _party(body.representative)),
            *[(Role.DIRECTOR, _party(p)) for p in body.directors],
            *[(Role.OWNER, _party(p)) for p in body.owners],
        ]
        holders = {"company": Holder("company", HolderKind.ENTITY)}
        for p in [body.representative, *body.directors, *body.owners]:
            holders[p.ref] = Holder(
                p.ref, HolderKind.PERSON if p.kind == "PERSON" else HolderKind.ENTITY
            )
        for h in body.holdings:
            holders.setdefault(
                h.owner, Holder(h.owner, HolderKind.UNKNOWN)
            )  # a holder nobody described is unknown
        graph = Graph(
            "company",
            holders,
            tuple(
                Edge(
                    h.owner,
                    "company" if h.owned == "company" else h.owned,
                    _pct(h.shares_pct),
                    _pct(h.votes_pct),
                    frozenset(Control(c) for c in h.controls),
                    False,  # declared by the applicant and not confirmed by a source: the rules treat it as unverified
                )
                for h in body.holdings
            ),
        )
        try:
            view = await open_case(
                conn,
                tenant,
                country=kyb.jurisdiction,
                subjects=subjects,
                graph=graph,
                authority=authority,
                requested_by=me,
                idempotency_key=f"claim-{claim_id}",
                cfg=screening_config(),
                sign=_sign(request),
                now=now,
                kyb_case_id=str(claim_id),
                elevated_risk=body.elevated_risk,
                country_gate=_gate(request),
            )
        except ScreeningError as e:
            raise _screening_error(e) from e
        del view
        return await _status(conn, tenant, str(claim_id), now)


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
