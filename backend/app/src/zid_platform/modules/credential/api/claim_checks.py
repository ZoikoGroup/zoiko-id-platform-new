"""Credential claim checks (ZID-GAP-001 v2.0 GID-037; section 08; CR-069).

    POST /v1/credentials/academic/checks                     academic award
    POST /v1/credentials/professional-licenses/checks        professional license
    POST /v1/credentials/certifications/checks               industry certificate
    POST /v1/credentials/experience/checks                   employment
    POST /v1/credentials/continuing-education/checks         CPD
    POST /v1/credentials/insurance/checks                    professional indemnity / insurance
    GET  /v1/credentials/assertions/{check_id}               the stored assertion (credential.read)
    GET  /v1/credential-packs                                which service packs a caller may rely on (public scope and state only)

Every check needs a service pack for the country and service: no pack, no check (`PACK_UNAVAILABLE`, 422). The five
facts in the response (credential status, issuer authenticity, primary-source verification, professional eligibility,
consumer entitlement) are separate keys; eligibility is NOT_EVALUATED here and is decided by its own policy (GID-041).
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.application import assertion_contract as ac
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import resilience as rs
from zid_platform.modules.credential.application.identity_match import Holder
from zid_platform.modules.credential.domain.claims import ClaimError, ClaimType
from zid_platform.modules.credential.domain.sources import SourceError, coverage
from zid_platform.modules.credential.infrastructure import claim_store as store
from zid_platform.modules.credential.infrastructure.registries import (
    issuer_registry,
    source_contracts,
)
from zid_platform.modules.verification.ports import (
    PackError,
    certification_registry,
    pack_registry,
    public_view,
)
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-claims"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "CLAIM_CLASS_UNRECOGNIZED": 422,
    "CLAIM_CLASS_NOT_CHECKABLE": 422,
    "CLAIM_FIELD_MISSING": 422,
    "CLAIM_FIELD_UNKNOWN": 422,
    "CLAIM_VALUE_UNRECOGNIZED": 422,
    "PACK_UNAVAILABLE": 422,
    "SOURCE_NO_CONTRACT": 422,
    "SOURCE_CONTRACT_NOT_IN_FORCE": 422,
    "SOURCE_NOT_COVERED": 422,
    "SOURCE_PURPOSE_NOT_PERMITTED": 403,
    "SOURCE_REGION_BLOCKED": 403,
    "SOURCE_TRANSFER_NOT_LAWFUL": 403,
    "SENSITIVE_NO_BASIS": 403,
    "SENSITIVE_USER_NOT_AUTHORIZED": 403,
    "SENSITIVE_NO_REVIEWER": 403,
    "REGULATOR_NOT_TRUSTED": 422,
}
_ROUTES: dict[str, ClaimType] = {
    "academic": ClaimType.ACADEMIC_AWARD,
    "professional-licenses": ClaimType.PROFESSIONAL_LICENSE,
    "certifications": ClaimType.INDUSTRY_CERTIFICATE,
    "experience": ClaimType.EMPLOYMENT_ASSERTION,
    "continuing-education": ClaimType.CPD_RECORD,
    "insurance": ClaimType.INSURANCE_ASSERTION,
}


class CredJurisdictionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    subdivision: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,3}$")


class CredCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: dict[str, Any] = Field(min_length=1, max_length=30)
    jurisdiction: CredJurisdictionIn
    profession_code: str | None = Field(default=None, max_length=32)
    subject_principal_id: uuid.UUID | None = None
    # who the credential is meant to belong to, for matching against the source's record (never stored)
    holder_name: str | None = Field(default=None, max_length=200)
    former_names: list[str] = Field(default_factory=list, max_length=10)
    name_change_evidence: bool = False
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    purpose: str = Field(default="PROFESSIONAL_ENGAGEMENT", max_length=64)
    # section 08.1: which consumer the assertion is for, and the consent it was requested under
    consumer: str = Field(default="direct", pattern=r"^[a-z][a-z0-9_-]{1,31}$")
    consent_ref: str | None = Field(default=None, max_length=200)


def _err(e: ClaimError | PackError | SourceError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 409)
    )


def _environment(request: Request) -> str:
    return str(getattr(request.app.state, "environment", "production"))


def _source(request: Request) -> checks.ClaimSource:
    """The claim source for this deployment. Only the simulator exists, and only local and test may build it; a
    deployed environment has no source, so a check there is refused before it could ask anything."""
    source = getattr(request.app.state, "claim_source", None)
    if source is not None:
        return source  # type: ignore[no-any-return]
    try:
        return checks.SimulatorSource(_environment(request))
    except RuntimeError as e:
        raise ZidError(
            "DEPENDENCY_UNAVAILABLE", "No credential source is configured.", http_status=503
        ) from e


def signer_for(request: Request) -> ac.Signer:
    """The platform's credential signing key, used to sign every assertion. No key, no assertion (503): an unsigned
    assertion is never returned by the API."""
    keyrings = getattr(request.app.state, "keyrings", None)
    if keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No signing key is configured.", http_status=503)
    ring = keyrings.credential
    return lambda claims: ring.sign(claims, typ=ac.SIGNATURE_TYP)


def _resilience(request: Request) -> rs.Resilience:
    """One retry, rate-limit and circuit-breaker state per process, shared by every request: a breaker that each request
    recreated would never open."""
    state = request.app.state
    if getattr(state, "source_resilience", None) is None:
        state.source_resilience = rs.Resilience()
    return state.source_resilience  # type: ignore[no-any-return]


def _gate(
    request: Request,
    purpose: str,
    caller: Caller | None = None,
    consent_ref: str | None = None,
    consumer: str = "direct",
) -> checks.Gate:
    """The deployment's controls: the issuer registry and the source contracts. The region is the cell's own region."""
    return checks.Gate(
        registry=issuer_registry(),
        contracts=source_contracts(),
        purpose=purpose,
        processing_region=str(getattr(request.app.state, "processing_region", "local")),
        certifications=certification_registry(),
        actor=(caller.ctx.principal_id if caller else None),
        consumer=consumer,
        consent_ref=consent_ref,
        signer=signer_for(request),
        resilience=_resilience(request),
    )


async def perform_check(
    claim_type: ClaimType,
    body: CredCheckIn,
    request: Request,
    caller: Caller,
    conn: AsyncConnection | None = None,
) -> checks.CheckResult:
    """Run one claim check for the caller: authorize, ask the source, sign, write the receipt. Shared by the /v1 routes and the
    /v2 facade so both behave identically. Pass `conn` to run inside a transaction the caller already holds (the /v2 route
    keeps its idempotency record and the check in ONE transaction)."""
    subject = str(body.subject_principal_id or caller.ctx.principal_id or "")
    if not subject:
        raise ZidError("SUBJECT_REQUIRED", "A subject is required.", http_status=422)

    async def run_in(c: AsyncConnection) -> checks.CheckResult:
        await protected_action(
            c,
            caller,
            action="credential.claim.verify",
            resource=f"credential-claim:{claim_type.value}",
        )
        try:
            return await checks.run_check(
                c,
                tenant_id=caller.ctx.tenant_id or "",
                subject_principal_id=subject,
                kind=claim_type.value,
                claimed=body.claim,
                jurisdiction=body.jurisdiction.model_dump(exclude_none=True),
                profession=body.profession_code,
                packs=pack_registry(),
                source=_source(request),
                now=datetime.now(UTC),
                environment=_environment(request),
                gate=_gate(request, body.purpose, caller, body.consent_ref, body.consumer),
                holder=(
                    Holder(
                        body.holder_name,
                        tuple(body.former_names),
                        body.name_change_evidence,
                        body.birth_year,
                    )
                    if body.holder_name
                    else None
                ),
            )
        except (ClaimError, PackError, SourceError) as e:
            raise _err(e) from e

    if conn is not None:
        return await run_in(conn)
    async with tenant_transaction(request_engine(request), caller.ctx) as c:
        return await run_in(c)


def _register(path: str, claim_type: ClaimType) -> None:
    async def run(body: CredCheckIn, request: Request, caller: CallerDep) -> dict[str, Any]:
        return (await perform_check(claim_type, body, request, caller)).to_contract()

    run.__name__ = f"check_{claim_type.value.lower()}"
    router.add_api_route(f"/v1/credentials/{path}/checks", run, methods=["POST"], status_code=201)


for _path, _type in _ROUTES.items():
    _register(_path, _type)


class CredDisciplinaryCheckIn(BaseModel):
    """A sensitive-category check: it always names its basis and a reviewer other than the caller."""

    model_config = ConfigDict(extra="forbid")
    regulator: str = Field(min_length=1, max_length=300)
    jurisdiction: CredJurisdictionIn
    subject_principal_id: uuid.UUID | None = None
    consent_ref: str | None = Field(default=None, max_length=200)
    legal_basis_ref: str | None = Field(default=None, max_length=200)
    reviewer_principal_id: uuid.UUID
    reference: str | None = Field(default=None, max_length=100)
    holder_name: str | None = Field(default=None, max_length=200)
    former_names: list[str] = Field(default_factory=list, max_length=10)
    name_change_evidence: bool = False
    birth_year: int | None = Field(default=None, ge=1900, le=2100)
    purpose: str = Field(default="PROFESSIONAL_ENGAGEMENT", max_length=64)


@router.post("/v1/credentials/disciplinary/checks", status_code=201)
async def check_disciplinary(
    body: CredDisciplinaryCheckIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    subject = str(body.subject_principal_id or caller.ctx.principal_id or "")
    if not subject:
        raise ZidError("SUBJECT_REQUIRED", "A subject is required.", http_status=422)
    source = getattr(request.app.state, "disciplinary_source", None)
    if source is None:
        try:
            source = checks.SimulatorSource(_environment(request), "simulator-disciplinary")
        except RuntimeError as e:
            raise ZidError(
                "DEPENDENCY_UNAVAILABLE", "No regulator source is configured.", http_status=503
            ) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        # passing this action IS the entitlement: only a person the policy lets run sensitive checks gets this far
        await protected_action(
            conn,
            caller,
            action="credential.disciplinary.check",
            resource="credential-claim:DISCIPLINARY",
        )
        base = _gate(request, body.purpose, caller, body.consent_ref)
        gate = replace(
            base,
            consent_ref=body.consent_ref,
            legal_basis_ref=body.legal_basis_ref,
            authorized_user=True,
            reviewer_assigned=str(body.reviewer_principal_id) != str(caller.ctx.principal_id),
        )
        try:
            result = await checks.run_disciplinary_check(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                subject_principal_id=subject,
                regulator=body.regulator,
                reference=body.reference,
                jurisdiction=body.jurisdiction.model_dump(exclude_none=True),
                packs=pack_registry(),
                source=source,
                now=datetime.now(UTC),
                environment=_environment(request),
                gate=gate,
                holder=(
                    Holder(
                        body.holder_name,
                        tuple(body.former_names),
                        body.name_change_evidence,
                        body.birth_year,
                    )
                    if body.holder_name
                    else None
                ),
            )
        except (ClaimError, PackError, SourceError) as e:
            raise _err(e) from e
    return result.to_contract()


@router.get("/v1/credentials/assertions/{check_id}")
async def read_assertion(
    check_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource="credential-claim:*"
        )
        row = await store.load_check(conn, str(check_id))
    if row is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    # the stored body is what was signed: return it as is, plus whether a newer check has replaced it
    return {**row["assertion"], "superseded": row["superseded_at"] is not None}


@router.get("/v1/credential-packs")
async def list_packs(request: Request, caller: CallerDep) -> dict[str, Any]:
    registry = pack_registry()
    return {
        "version": registry.version,
        "packs": public_view(registry, now=datetime.now(UTC), environment=_environment(request)),
    }


@router.get("/v1/credential-packs/{country}/{profession}")
async def packs_for(
    country: str, profession: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Which checks are available for one country and profession, and how far each can be relied on. Scope and state
    only; no legal reference, owner or source id."""
    code = country.upper()
    if len(code) != 2 or not code.isalpha():
        raise ZidError(
            "COUNTRY_INVALID", "Country must be an ISO 3166-1 alpha-2 code.", http_status=422
        )
    registry = pack_registry()
    now = datetime.now(UTC)
    contracts = source_contracts()
    out = []
    for v in public_view(registry, now=now, environment=_environment(request)):
        if v["country"] != code or v["profession"] not in (None, profession):
            continue
        claim = next(
            (t.value for t, svc in checks.SERVICE_CODES.items() if svc == v["service"]),  # type: ignore[attr-defined]
            None,
        )
        out.append(
            {
                **v,
                "coverage": coverage(
                    contracts, country=code, claim_type=claim or "", on=now.date()
                ).value,
            }
        )
    return {"version": registry.version, "country": code, "profession": profession, "checks": out}
