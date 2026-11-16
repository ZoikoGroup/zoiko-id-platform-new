"""The /v2 facade of the global trust contract (ZID-INT-002, Document 02 v2.0).

    GET  /v2/capabilities                     what this platform can check, how ready each is, and where it has live coverage
    GET  /v2/coverage?country=&profession=    packs and source coverage for a place (scope and state only)
    POST /v2/credentials/verify               one credential check (Idempotency-Key required); issuer and award claims kept apart
    GET  /v2/credentials/{credential_ref}     the stored, signed result
    POST /v2/eligibility/evaluate             licence eligibility for a profession and place (Idempotency-Key required)
    GET  /v2/eligibility/{eligibility_ref}    a stored decision
    POST /v2/zoikorum/engagement-checks       Zoikorum engagement eligibility, signed (Idempotency-Key required)

The facade adapts the /v1 credential routes: it adds the section 02.1 envelope (request and correlation ids, pack id and version,
provenance manifest, checked_at, valid_until, dimension outcomes), the section 06.1 status vocabulary and RFC 9457 errors. It decides
nothing itself. Document 02 says its route names are proposals; these are the ones that exist. Not built: /v2/verifications,
evidence uploads, documents/validate, issuer-checks, organizations and mandates verify, presentations, rooms property checks,
webhook-endpoints (they need providers, a document validator or modules that do not exist yet).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_idempotency import Scope, begin, complete

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.api import claim_checks as v1
from zid_platform.modules.credential.api import trust as t1
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.application import v2_view as vw
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain.claims import ClaimType
from zid_platform.modules.credential.domain.sources import coverage
from zid_platform.modules.credential.infrastructure import claim_store, trust_store
from zid_platform.modules.credential.infrastructure.registries import (
    current_issuer_registry,
    source_contracts,
)
from zid_platform.modules.evidence.ports import unusable_evidence
from zid_platform.modules.verification.ports import pack_registry, public_view
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]

# the honest readiness label of section 01: the logic exists and is tested, but no live source or legal approval does
_READINESS = "PROVIDER_DEPENDENT"
_RANK = {"NOT_ELIGIBLE": 0, "REVIEW_REQUIRED": 1, "UNKNOWN": 2, "ELIGIBLE": 3}


def _ids() -> tuple[str, str]:
    ctx = current_context()
    return (ctx.request_id, ctx.correlation_id) if ctx else ("req_unknown", "cor_unknown")


def _now() -> datetime:
    return datetime.now(UTC)


def _ref(value: str, prefix: str) -> uuid.UUID:
    """A pseudonymous reference (`sub_<uuid>`) or a bare uuid. Anything else is an invalid request, never a lookup."""
    raw = value.removeprefix(prefix)
    try:
        return uuid.UUID(raw)
    except ValueError as e:
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e


class V2Jurisdiction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    country: str = Field(pattern=r"^[A-Z]{2}$")
    subdivision: str | None = Field(default=None, pattern=r"^([A-Z]{2}-)?[A-Z0-9]{1,3}$")

    def as_v1(self) -> v1.CredJurisdictionIn:
        sub = self.subdivision.split("-")[-1] if self.subdivision else None
        return v1.CredJurisdictionIn(country=self.country, subdivision=sub)


class V2IssuerClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class V2CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str | None = Field(default=None, max_length=80)
    credential_type: str = Field(pattern=r"^[a-z_]{3,32}$")
    issuer_claim: V2IssuerClaim
    credential_claim: dict[str, Any] = Field(default_factory=dict, max_length=30)
    evidence_refs: list[str] = Field(default_factory=list, max_length=20)
    jurisdiction: V2Jurisdiction
    source_preference: str = Field(default="authoritative_first", pattern=r"^authoritative_first$")
    purpose: str = Field(default="professional_onboarding", max_length=64)
    consent_ref: str | None = Field(default=None, max_length=200)
    consumer: str = Field(default="direct", pattern=r"^[a-z][a-z0-9_-]{1,31}$")


# ---------------------------------------------------------------------------------------------------- discovery


@router.get("/v2/capabilities")
async def capabilities(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What can be checked, honestly: a capability with no live source says so (`live_coverage_countries` empty)."""
    today = date.today()
    contracts = source_contracts()
    countries = sorted({c for k in contracts for c in k.countries})
    items = []
    for credential_type, claim in vw.CREDENTIAL_TYPES.items():
        live = [
            c
            for c in countries
            if coverage(contracts, country=c, claim_type=claim.value, on=today).value
            != "NO_COVERAGE"
        ]
        items.append(
            {
                "capability": "credentials.verify",
                "credential_type": credential_type,
                "claim_type": claim.value,
                "readiness": _READINESS,
                "live_coverage_countries": live,
                "limitations": []
                if live
                else ["NO_LIVE_SOURCE: no contracted primary source is in force for any country"],
            }
        )
    return {
        "request_id": _ids()[0],
        "api_version": "2.0",
        "capabilities": items,
        "outcomes": [o.value for o in vw.Outcome],
        "engagement_eligibility": [e.value for e in vw.EngagementEligibility],
    }


@router.get("/v2/coverage")
async def coverage_view(
    request: Request,
    caller: CallerDep,
    country: str = Query(pattern=r"^[A-Za-z]{2}$"),
    profession: str | None = Query(default=None, max_length=32),
) -> dict[str, Any]:
    """Packs and source coverage for one place. A place with no pack is reported UNAVAILABLE, never as covered."""
    code = country.upper()
    contracts = source_contracts()
    today = date.today()
    environment = str(getattr(request.app.state, "environment", "production"))
    packs = [
        p
        for p in public_view(pack_registry(), now=_now(), environment=environment)
        if p["country"] == code and (profession is None or p["profession"] in (None, profession))
    ]
    return {
        "request_id": _ids()[0],
        "country": code,
        "profession": profession,
        "packs": packs,
        "source_coverage": {
            ct.value: coverage(contracts, country=code, claim_type=ct.value, on=today).value
            for ct in vw.CREDENTIAL_TYPES.values()
        },
        "limitations": [
            "Coverage counts only contracted, in-force sources; a simulator never counts."
        ],
    }


# ------------------------------------------------------------------------------------------------ credentials


@router.post("/v2/credentials/verify", status_code=201)
async def verify_credential(
    body: V2CredentialIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    claim_type = vw.CREDENTIAL_TYPES.get(body.credential_type)
    if (
        claim_type is None
    ):  # an unknown credential type fails closed: nothing is asked of any source
        raise ZidError("VALIDATION_FAILED", "credential_type is not supported.", http_status=422)
    subject = _ref(body.subject_ref, "sub_") if body.subject_ref else None
    v1_body = v1.CredCheckIn(
        claim=vw.claim_from_v2(claim_type, body.issuer_claim.model_dump(), body.credential_claim),
        jurisdiction=body.jurisdiction.as_v1(),
        subject_principal_id=subject,
        purpose=body.purpose.upper(),
        consumer=body.consumer,
        consent_ref=body.consent_ref,
    )
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/credentials/verify")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        if (
            body.evidence_refs
        ):  # a check may rest only on evidence that was scanned clean and is still held
            bad = await unusable_evidence(conn, body.evidence_refs, _now())  # type: ignore[no-untyped-call]
            if bad:
                raise ZidError(
                    "EVIDENCE_NOT_USABLE",
                    "Evidence is unknown, not scanned clean, deleted or expired.",
                    http_status=422,
                )
        result = await v1.perform_check(claim_type, v1_body, request, caller, conn)
        rid, cid = _ids()
        out = vw.credential_response(
            result.to_contract(),
            claim_type,
            check_id=result.check_id,
            request_id=rid,
            correlation_id=cid,
        )
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out


@router.get("/v2/credentials/{credential_ref}")
async def read_credential(
    credential_ref: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    check_id = _ref(credential_ref, "cred_")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource="credential-claim:*"
        )
        row = await claim_store.load_check(conn, str(check_id))
    if row is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    rid, cid = _ids()
    out = vw.credential_response(
        {**row["assertion"]},
        ClaimType(row["claim_type"]),
        check_id=str(check_id),
        request_id=rid,
        correlation_id=cid,
    )
    out["superseded"] = row["superseded_at"] is not None
    # T20: judged now, a stale proof is EXPIRED or RECHECK_REQUIRED and its dimension is lowered, never a cached VERIFIED
    status = lc.proof_status(row["assertion"], now=v1._now())
    out["proof_status"] = status.value
    if status is not lc.ProofStatus.CURRENT:
        dim = out["dimensions"][0]
        if dim["outcome"] in (vw.Outcome.VERIFIED.value, vw.Outcome.VERIFIED_LIMITED.value):
            dim["outcome"] = (
                vw.Outcome.EXPIRED.value
                if status is lc.ProofStatus.EXPIRED
                else vw.Outcome.INDETERMINATE.value
            )
        dim["limitations"] = [*dim["limitations"], status.value]
    return out


# ------------------------------------------------------------------------------------------------ eligibility


class V2EligibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str | None = Field(default=None, max_length=80)
    jurisdiction: V2Jurisdiction
    profession_code: str = Field(min_length=1, max_length=32)
    service_code: str = Field(min_length=1, max_length=64)


@router.post("/v2/eligibility/evaluate", status_code=201)
async def evaluate(
    body: V2EligibilityIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    v1_body = t1.CredEligibilityIn(
        subject_principal_id=_ref(body.subject_ref, "sub_") if body.subject_ref else None,
        country=body.jurisdiction.country,
        subdivision=body.jurisdiction.as_v1().subdivision,
        profession_code=body.profession_code,
        service_code=body.service_code,
    )
    policy = t1._policy(v1_body)
    scope = Scope(caller.ctx.tenant_id or "", caller.client_id, "POST /v2/eligibility/evaluate")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        await protected_action(
            conn,
            caller,
            action="credential.eligibility.evaluate",
            resource=f"eligibility:{policy.policy_id}",
        )
        r = await svc.evaluate_eligibility(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            subject=t1._subject(caller, v1_body.subject_principal_id),
            policy=policy,
            registry=await current_issuer_registry(conn),
            now=_now(),
        )
        rid, cid = _ids()
        out = vw.eligibility_response(
            {
                "decision_id": r.decision_id,
                "eligibility": r.outcome.eligibility.value,
                "reason_codes": list(r.outcome.reasons),
                "restrictions": list(r.outcome.restrictions),
                "policy_ref": dict(r.outcome.policy_ref),
            },
            request_id=rid,
            correlation_id=cid,
            checked_at=_now(),
        )
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out


@router.get("/v2/eligibility/{eligibility_ref}")
async def read_eligibility(
    eligibility_ref: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    decision_id = _ref(eligibility_ref, "elig_")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource=f"eligibility:{decision_id}"
        )
        row = await trust_store.load_decision(conn, str(decision_id))
    if row is None:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404)
    rid, cid = _ids()
    return vw.eligibility_response(
        {
            "decision_id": str(row["decision_id"]),
            "eligibility": row["outcome"],
            "reason_codes": list(row["reasons"]),
            "restrictions": list(row["restrictions"]),
            "policy_ref": {"id": row["policy_id"], "version": row["policy_version"]},
        },
        request_id=rid,
        correlation_id=cid,
        checked_at=row["created_at"],
    )


# ------------------------------------------------------------------------------------------------ zoikorum


class V2EngagementIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    professional_subject_ref: str = Field(max_length=80)
    engagement_ref: str = Field(min_length=1, max_length=200)
    profession_code: str = Field(min_length=1, max_length=32)
    requested_services: list[str] = Field(min_length=1, max_length=10)
    jurisdiction: V2Jurisdiction
    client_ref: str | None = Field(default=None, max_length=200)
    minimum_insurance_policy_ref: str | None = Field(default=None, max_length=200)
    consent_ref: str | None = Field(default=None, max_length=200)
    decision_profile: str = Field(default="zoikorum-v1", pattern=r"^zoikorum-v1$")


@router.post("/v2/zoikorum/engagement-checks", status_code=201)
async def engagement_check(
    body: V2EngagementIn, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    """Eligibility for each requested service, separately. The overall answer is only as good as the worst service, and it is
    signed for Zoikorum alone with no raw credential in it. Zoikorum owns its marketplace rules; this answers only the evidence."""
    keyrings = getattr(request.app.state, "keyrings", None)
    if keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No signing key is configured.", http_status=503)
    subject = _ref(body.professional_subject_ref, "sub_")
    scope = Scope(
        caller.ctx.tenant_id or "", caller.client_id, "POST /v2/zoikorum/engagement-checks"
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        stored = await begin(
            conn, scope=scope, key=idempotency_key, payload=body.model_dump(mode="json")
        )
        if stored is not None:
            return dict(stored.body)
        services = []
        for service in body.requested_services:
            r = await t1._engage(
                t1.CredEngagementIn(
                    subject_principal_id=subject,
                    country=body.jurisdiction.country,
                    subdivision=body.jurisdiction.as_v1().subdivision,
                    profession_code=body.profession_code,
                    service_code=service,
                    insurance_required=body.minimum_insurance_policy_ref is not None,
                ),
                request,
                caller,
                "zoikorum",
                conn,
            )
            services.append(
                {
                    "service": service,
                    "decision_id": r.decision_id,
                    "engagement_eligibility": vw.engagement_eligibility(
                        r.outcome.eligibility.value, r.outcome.engagement.value
                    ).value,
                    "reason_codes": list(r.outcome.reasons),
                    "tier": r.tier.value,
                    "dimensions": dict(r.dimensions),  # separate statuses, never one badge
                }
            )
        overall = min((s["engagement_eligibility"] for s in services), key=lambda v: _RANK[str(v)])
        rid, cid = _ids()
        out: dict[str, Any] = {
            "request_id": rid,
            "correlation_id": cid,
            "engagement_ref": body.engagement_ref,
            "decision_profile": body.decision_profile,
            "engagement_eligibility": overall,
            "services": services,
            "valid_until": None,
            "consumer": "zoikorum",
        }
        claims = {
            "consumer": "zoikorum",
            "engagement_ref": body.engagement_ref,
            "engagement_eligibility": overall,
            "decisions": [s["decision_id"] for s in services],
            "iat": int(_now().timestamp()),
        }
        out["signature"] = keyrings.credential.sign(claims, typ="zid-decision+jwt")
        await complete(conn, scope=scope, key=idempotency_key, status=201, body=out)
    return out
