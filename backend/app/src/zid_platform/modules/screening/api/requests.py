"""Screening requests for a tenant (ZID-DTV-WF-06 S01 to S03, section 10; CR-113).

    GET  /v1/screening/eligibility            what this tenant may ask for in a market, and why not, by class      (tenant OWNER or ADMIN)
    POST /v1/screening/cases                  screen one subject for one purpose (Idempotency-Key required)        (tenant OWNER or ADMIN)
    GET  /v1/screening/cases/{id}             the minimal status: per-class coverage, one calibrated finding      (tenant OWNER or ADMIN)
    POST /v1/screening/cases/{id}:request-review   ask for a second look; it decides nothing                        (tenant OWNER or ADMIN)

Screening is off until a market's signed policy names the tenant. The identity a screening rests on is read here from the platform's own verified claim (a person's current identity assertion, or
the company's own grade), never taken from the caller, and the region is this cell's, never the caller's. What the caller reads never names a list, a candidate or a reason: a restricted or
prohibited case reads like a hold. 503 is a required source that is down: it is never turned into a clear answer.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.screening.application import edd, licensed, ownership_screening
from zid_platform.modules.screening.application import screening_requests as service
from zid_platform.modules.screening.domain import coverage as cov
from zid_platform.modules.screening.domain import disposition as disp
from zid_platform.modules.screening.domain import eligibility as elig
from zid_platform.modules.screening.domain import ownership_rollup as rollup
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.modules.screening.domain.lists import PartyKind
from zid_platform.modules.screening.domain.matching import Party
from zid_platform.modules.screening.infrastructure import store
from zid_platform.modules.screening.infrastructure.config import config as screening_config
from zid_platform.modules.screening.infrastructure.market_policies import market_packs
from zid_platform.modules.screening.infrastructure.providers import providers as provider_registry

router = APIRouter(prefix="/v1/screening", tags=["screening"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "CASE_NOT_FOUND": 404,
    "SCREENING_NOT_ELIGIBLE": 422,
}


class CaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str = Field(pattern=r"^[A-Z]{2}$")
    purpose: str = Field(min_length=1, max_length=64)
    subject_type: elig.SubjectType
    subject_ref: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=500)
    birth: str | None = Field(default=None, pattern=r"^\d{4}(-\d{2}-\d{2})?$")
    countries: list[Annotated[str, Field(min_length=2, max_length=64)]] = Field(
        default_factory=list, max_length=10
    )
    identifiers: list[Annotated[str, Field(min_length=3, max_length=64)]] = Field(
        default_factory=list, max_length=10
    )
    expired_identifiers: list[Annotated[str, Field(min_length=3, max_length=64)]] = Field(
        default_factory=list, max_length=10
    )
    classes: list[cov.Klass] = Field(
        default_factory=lambda: [cov.Klass.SANCTIONS], min_length=1, max_length=4
    )


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str = Field(min_length=10, max_length=2000)


def _error(e: ScreeningError) -> ZidError:
    if e.code == "SCREENING_NOT_ELIGIBLE":
        codes = [c for c in e.detail.split(",") if c]
        return ZidError(
            e.code,
            "Screening cannot be requested: " + " ".join(elig.explain(c) for c in codes),
            http_status=422,
            details=[ErrorDetail(field="reasons", issue=c) for c in codes],
        )
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError("FORBIDDEN", "A signed-in person is required.", http_status=403)
    return str(caller.ctx.principal_id)


# The composition root wires these (screening imports neither the tenant, the verification nor the organization module):
#   an admin check:  async (conn, principal_id) -> bool     whether the person holds an OWNER or ADMIN role in the tenant
#   a claim lookup:  async (conn, tenant, subject_type, subject_ref, now) -> (claim, assured_at) | None
_ADMIN_CHECK: Any = None
_CLAIM_LOOKUP: Any = None


def configure_sources(admin_check: Any, claim_lookup: Any) -> None:
    global _ADMIN_CHECK, _CLAIM_LOOKUP
    _ADMIN_CHECK, _CLAIM_LOOKUP = admin_check, claim_lookup


async def _admin(conn: Any, caller: Caller) -> str:
    me = _me(caller)
    if _ADMIN_CHECK is None:
        raise ZidError(
            "SCREENING_NOT_CONFIGURED",
            "Screening is not wired in this deployment.",
            http_status=503,
        )
    if not await _ADMIN_CHECK(conn, me):
        raise ZidError(
            "TENANT_ROLE_REQUIRED", "An OWNER or ADMIN role is required.", http_status=403
        )
    return me


async def _identity(
    conn: Any,
    tenant: str,
    subject_type: elig.SubjectType,
    subject_ref: str,
    now: datetime,
    lookup: Any = None,
) -> elig.IdentityClaim | None:
    """The identity claim a subject rests on, read from the platform by the composition root's lookup (a test may supply its own). It is never fed by the caller. A company is screened only as the
    tenant's own organization, and a person only by a real account identifier."""
    source = lookup or _CLAIM_LOOKUP
    if source is None:
        return None
    found = await source(conn, tenant, subject_type, subject_ref, now)
    return None if found is None else elig.IdentityClaim(found[0], found[1], tenant)


_MODULE = {
    "SANCTIONS": "SANCTIONS",
    "PEP_RCA": "PEP_RCA",
    "ADVERSE_MEDIA": "ADVERSE_MEDIA",
    "OWNERSHIP_CONTROL": "SANCTIONS",
}


async def _gated_off(
    request: Request, conn: Any, market: str, classes: Any, now: datetime
) -> frozenset[cov.Klass]:
    """The requested classes whose module is not released for this market. Enforced unless a deployment's own test setup says otherwise: with no signed gates nothing is available."""
    if getattr(request.app.state, "screening_enforce_gates", True) is False:
        return frozenset()
    from zid_platform.modules.screening.application import gates

    off = set()
    for klass in classes:
        if not await gates.is_go(conn, market, _MODULE[klass.value], now=now):
            off.add(klass)
    return frozenset(off)


def _region(request: Request) -> str:
    return str(getattr(request.app.state, "home_cell", ""))


def _registry(request: Request) -> Any:
    return getattr(request.app.state, "screening_providers", None) or provider_registry()


def _adapters(request: Request) -> Any:
    return getattr(request.app.state, "screening_adapters", None) or {}


def _packs(request: Request) -> Any:
    return getattr(request.app.state, "screening_market_packs", None) or market_packs()


@router.get("/eligibility")
async def eligibility(
    request: Request,
    caller: CallerDep,
    market: Annotated[str, Query(pattern=r"^[A-Z]{2}$")],
    purpose: Annotated[str, Query(min_length=1, max_length=64)],
    subject_type: Annotated[elig.SubjectType, Query()],
    subject_ref: Annotated[str, Query(min_length=1, max_length=128)],
    classes: Annotated[list[cov.Klass] | None, Query()] = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin(conn, caller)
        identity = await _identity(
            conn,
            tenant,
            subject_type,
            subject_ref,
            now,
            getattr(request.app.state, "screening_identity_lookup", None),
        )
        off = await _gated_off(request, conn, market, classes or [cov.Klass.SANCTIONS], now)
    pack = _packs(request).get(market)
    result = elig.check(
        elig.Request(
            tenant,
            market,
            purpose,
            _region(request),
            subject_type,
            identity,
            frozenset(classes or [cov.Klass.SANCTIONS]),
        ),
        pack.policy if pack else None,
        entitled=pack.entitled(tenant) if pack else frozenset(),
        usable_sources=frozenset({cov.Klass.SANCTIONS})
        | licensed.usable_classes(_registry(request), _adapters(request)),
        gated_off=off,
        now=now,
    )
    return {
        "eligible": result.eligible,
        "blocked": [{"code": c, "text": elig.explain(c)} for c in result.blocked],
        "pending_identity": result.pending_identity,
        "policy_version": result.policy_version,
        "region": result.region,
        "classes": [
            {
                "class": c.klass.value,
                "available": c.available,
                "tier": c.tier.value if c.tier else None,
                "reasons": [{"code": r, "text": elig.explain(r)} for r in c.reasons],
            }
            for c in result.classes
        ],
        "note": "Official sanctions screening only: this is not an AML clearance."
        if result.available() <= {cov.Klass.SANCTIONS}
        else "",
    }


@router.post("/cases", status_code=201)
async def open_case(
    body: CaseIn,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)],
) -> dict[str, Any]:
    now = datetime.now(UTC)
    tenant = caller.ctx.tenant_id or ""
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            me = await _admin(conn, caller)
            identity = await _identity(
                conn,
                tenant,
                body.subject_type,
                body.subject_ref,
                now,
                getattr(request.app.state, "screening_identity_lookup", None),
            )
            kind = (
                PartyKind.ENTITY
                if body.subject_type is elig.SubjectType.BUSINESS
                else PartyKind.PERSON
            )
            party = Party(
                "subject",
                kind,
                body.name,
                body.birth,
                tuple(body.countries),
                tuple(body.identifiers),
                tuple(body.expired_identifiers),
            )
            off = await _gated_off(request, conn, body.market, body.classes, now)
            return await service.open_request(
                conn,
                tenant,
                actor=me,
                idempotency_key=idempotency_key,
                request=elig.Request(
                    tenant,
                    body.market,
                    body.purpose,
                    _region(request),
                    body.subject_type,
                    identity,
                    frozenset(body.classes),
                    body.subject_ref,
                ),
                subject=party,
                pack=_packs(request).get(body.market),
                snapshots=await store.snapshots(conn),
                max_age=cfg.policy.list_max_age,
                match_policy=cfg.policy.match,
                now=now,
                registry=_registry(request),
                adapters=_adapters(request),
                gated_off=off,
            )
    except ScreeningError as e:
        raise _error(e) from e


@router.get("/cases/{request_id}")
async def get_case(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await _admin(conn, caller)
            return await service.customer_view(
                conn, str(request_id), now=datetime.now(UTC), max_age=cfg.policy.list_max_age
            )
    except ScreeningError as e:
        raise _error(e) from e


class InformationReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provided: list[Annotated[str, Field(pattern=r"^[A-Z][A-Z_]{2,40}$")]] = Field(
        min_length=1, max_length=6
    )


class OwnerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    person_ref: str = Field(min_length=1, max_length=128)
    status: str = Field(pattern=r"^(NEEDS_EVIDENCE|NEEDS_CONFIRMATION|CONFIRMED)$")


class OwnershipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    owners: list[OwnerIn] = Field(max_length=200)


@router.get("/cases/{request_id}/information")
async def information(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """What more is wanted for this case, if anything, in words that never say why."""
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await _admin(conn, caller)
            await service.customer_view(
                conn, str(request_id), now=datetime.now(UTC), max_age=cfg.policy.list_max_age
            )  # the case must be this tenant's
            open_one = await edd.current(conn, str(request_id))
    except ScreeningError as e:
        raise _error(e) from e
    return {"request_id": str(request_id), "open": open_one is not None, "request": open_one}


@router.post("/cases/{request_id}/information", status_code=202)
async def reply_with_information(
    request_id: uuid.UUID, body: InformationReplyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await _admin(conn, caller)
            await service.customer_view(
                conn, str(request_id), now=datetime.now(UTC), max_age=cfg.policy.list_max_age
            )
            out = await edd.receive_for_case(
                conn, str(request_id), body.provided, now=datetime.now(UTC)
            )
    except ScreeningError as e:
        raise _error(e) from e
    return {"request_id": str(request_id), "request": out}


@router.post("/ownership/rollup")
async def ownership_rollup(
    body: OwnershipIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Where a company's screening stands once its owners have been screened as people. The caller names the owners (from the ownership analysis); no owner counts as clear without a current screen, and
    a restricted owner reads as in review. `needs_case` lists the owners a case should be opened for through the ordinary case route."""
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    owners = [rollup.Owner(o.person_ref, o.status) for o in body.owners]
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _admin(conn, caller)
        result = await ownership_screening.company_rollup(
            conn, owners, now=now, max_age=cfg.policy.list_max_age
        )
        needs = await ownership_screening.owners_needing_a_case(
            conn, owners, now=now, max_age=cfg.policy.list_max_age
        )
    return {**rollup.customer_view(result), "needs_case": list(needs)}


@router.post("/cases/{request_id}:request-review", status_code=202)
async def request_review(
    request_id: uuid.UUID, body: ReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    cfg = getattr(request.app.state, "screening_config", None) or screening_config()
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            me = await _admin(conn, caller)
            disp.request_review(
                str(request_id), requested_by=me, role=disp.Role.CUSTOMER_ADMIN, note=body.note
            )
            await service.customer_view(
                conn, str(request_id), now=datetime.now(UTC), max_age=cfg.policy.list_max_age
            )  # the case must be this tenant's
    except disp.DispositionError as e:
        raise ZidError(e.code, e.detail or e.code, http_status=422) from e
    except ScreeningError as e:
        raise _error(e) from e
    return {
        "request_id": str(request_id),
        "status": "A reviewer will look again. This does not change the result by itself.",
    }
