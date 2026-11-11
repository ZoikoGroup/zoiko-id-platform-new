"""Credential trust API: issuers, eligibility, consented disclosure, issuer outreach, appeals, monitoring
(ZID-GAP-001 v2.0 section 08; GID-038, 041, 047, 048, 049, 053, 054, 058, 060, 061; CR-069).

    GET  /v1/issuers/{issuer_id}/trust                      registry facts for one issuer          (credential.read)
    POST /v1/issuers/resolution                             name -> issuer, lookalikes never auto-resolved (credential.read)
    POST /v1/eligibility/professional/evaluate              policy decision for one place + service (credential.eligibility.evaluate)
    POST /v1/eligibility/engagement/evaluate                one piece of work: credential, identity, insurance, tier
    POST /v1/consumers/zoikorum/decisions                   the same, signed, for the Zoikorum consumer only
    POST /credentials/disclosures                           a person grants a recipient named fields (credential.disclosure.manage)
    POST /credentials/disclosures/{id}:withdraw             stops this grant and every one derived from it
    POST /credentials/disclosures/{id}:read                 what a recipient may see of one check
    GET  /credentials/assertions/{tenant}.{check}/verify    bounded public view under a PUBLIC_VERIFIER grant (no account)
    POST /credentials/source-outreach                       ask an issuer's registered contact (credential.outreach.manage)
    POST /credentials/source-outreach/{id}:response         record the issuer's signed answer
    POST /credentials/source-outreach/{id}:review           dual control: two other people
    POST /credentials/cases/{check_id}/appeals              a person asks for correction or human review (credential.appeal.manage)
    POST /credentials/appeals/{id}:resolve                  resolved by someone other than who raised it
    POST /credentials/monitoring                            a source's status event for a check (credential.monitor.manage)

Every eligibility result is recomputed from the subject's CURRENT checks; nothing is cached across places, services or time.
"""

from __future__ import annotations

import base64
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.application import vc_verify, verifier
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import lifecycle as lc
from zid_platform.modules.credential.domain import outreach as oc
from zid_platform.modules.credential.domain import portable as pt
from zid_platform.modules.credential.domain import trust_graph as tg
from zid_platform.modules.credential.domain.claims import (
    ClaimError,
    CredentialStatus,
    validate_claim,
)
from zid_platform.modules.credential.domain.credentials import CredentialError
from zid_platform.modules.credential.infrastructure import trust_store
from zid_platform.modules.credential.infrastructure.registries import (
    find_policy,
    issuer_registry,
    policies,
)
from zid_platform.modules.verification.ports import PackError, pack_registry
from zid_platform.protected import protected_action

router = APIRouter(tags=["credential-trust"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS: dict[str, int] = {
    "POLICY_NOT_FOUND": 404,
    "GRANT_NOT_FOUND": 404,
    "CHECK_NOT_FOUND": 404,
    "REQUEST_NOT_FOUND": 404,
    "APPEAL_NOT_FOUND": 404,
    "ISSUER_NOT_FOUND": 404,
    "GRANT_NOT_IN_FORCE": 403,
    "CONSUMER_SCOPE_MISMATCH": 403,
    "RECIPIENT_MISMATCH": 403,
    "PURPOSE_MISMATCH": 403,
    "SENDER_NOT_REGISTERED": 403,
    "SIGNATURE_INVALID": 403,
    "REQUESTER_CANNOT_REVIEW": 403,
    "REQUEST_EXPIRED": 410,
}


def _err(e: Exception) -> ZidError:
    code = str(getattr(e, "code", "BAD_REQUEST"))
    return ZidError(
        code, code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(code, 422)
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _subject(caller: Caller, given: uuid.UUID | None) -> str:
    subject = str(given or caller.ctx.principal_id or "")
    if not subject:
        raise ZidError("SUBJECT_REQUIRED", "A subject is required.", http_status=422)
    return subject


async def _gate(conn: Any, caller: Caller, action: str, resource: str) -> None:
    await protected_action(conn, caller, action=action, resource=resource)


# ------------------------------------------------------------------------------------------------------ issuers


class CredIssuerResolveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=300)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    on: str | None = Field(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")


@router.post("/v1/issuers/resolution")
async def resolve_issuer(
    body: CredIssuerResolveIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.read", "issuer:*")
    on = datetime.fromisoformat(body.on).date() if body.on else _now().date()
    res = tg.resolve(issuer_registry(), body.name, country=body.country, on=on)
    return {
        "match": res.match.value,
        "issuer_id": res.issuer_id,
        "via_former_name": res.via_former_name,
        "candidates": list(res.candidates),  # a lookalike lists who it resembles; a person decides
    }


@router.get("/v1/issuers/{issuer_id}/trust")
async def issuer_trust(issuer_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.read", f"issuer:{issuer_id}")
    issuer = issuer_registry().issuers.get(issuer_id)
    if issuer is None:
        raise ZidError("ISSUER_NOT_FOUND", "Issuer not found.", http_status=404)
    return {
        "issuer_id": issuer.issuer_id,
        "kind": issuer.kind.value,
        "authority": issuer.authority.value,
        "countries": sorted(issuer.countries),
        "claim_types": sorted(issuer.claim_types),
        "names": [
            {
                "name": n.name,
                "from": n.from_.isoformat() if n.from_ else None,
                "until": n.until.isoformat() if n.until else None,
            }
            for n in issuer.names
        ],
        "accreditations": [
            {
                "body": a.body_issuer_id,
                "program": a.program,
                "from": a.valid_from.isoformat(),
                "until": a.valid_until.isoformat() if a.valid_until else None,
            }
            for a in issuer.accreditations
        ],
        "revoked_on": issuer.revoked_on.isoformat() if issuer.revoked_on else None,
    }


# -------------------------------------------------------------------------------------------------- eligibility


class CredEligibilityIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: uuid.UUID | None = None
    country: str = Field(pattern=r"^[A-Z]{2}$")
    subdivision: str | None = Field(default=None, pattern=r"^[A-Z0-9]{1,3}$")
    profession_code: str = Field(min_length=1, max_length=32)
    service_code: str = Field(min_length=1, max_length=64)


def _policy(body: CredEligibilityIn) -> el.EligibilityPolicy:
    policy = find_policy(body.country, body.subdivision, body.profession_code, body.service_code)
    if (
        policy is None
    ):  # no policy for exactly this place, profession and service: no decision is made at all
        raise ZidError(
            "POLICY_NOT_FOUND", "No eligibility policy exists for this scope.", http_status=404
        )
    return policy


@router.post("/v1/eligibility/professional/evaluate", status_code=201)
async def evaluate_professional(
    body: CredEligibilityIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    policy = _policy(body)
    subject = _subject(caller, body.subject_principal_id)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(
            conn, caller, "credential.eligibility.evaluate", f"eligibility:{policy.policy_id}"
        )
        r = await svc.evaluate_eligibility(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            subject=subject,
            policy=policy,
            registry=issuer_registry(),
            now=_now(),
        )
    return {
        "decision_id": r.decision_id,
        "eligibility": r.outcome.eligibility.value,
        "reason_codes": list(r.outcome.reasons),
        "restrictions": list(r.outcome.restrictions),
        "policy_ref": dict(r.outcome.policy_ref),
        "check_ids": list(r.check_ids),
    }


class CredEngagementIn(CredEligibilityIn):
    insurance_required: bool = True
    engagement_days: int = Field(default=365, ge=1, le=3650)


async def _engage(
    body: CredEngagementIn, request: Request, caller: Caller, consumer: str
) -> svc.EngagementResult:
    policy = _policy(body)
    rule = policies()[1][0] if policies()[1] else None
    if rule is None:
        raise ZidError("POLICY_NOT_FOUND", "No trust tier rule is configured.", http_status=404)
    req = el.EngagementRequest(
        body.country,
        body.subdivision,
        body.profession_code,
        body.service_code,
        body.insurance_required,
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(
            conn, caller, "credential.eligibility.evaluate", f"engagement:{policy.policy_id}"
        )
        return await svc.evaluate_engagement(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            subject=_subject(caller, body.subject_principal_id),
            request=req,
            policy=policy,
            rule=rule,
            registry=issuer_registry(),
            now=_now(),
            consumer=consumer,
            engagement_days=body.engagement_days,
        )


def _engagement_body(r: svc.EngagementResult) -> dict[str, Any]:
    return {
        "decision_id": r.decision_id,
        "engagement": r.outcome.engagement.value,
        "eligibility": r.outcome.eligibility.value,
        "reason_codes": list(r.outcome.reasons),
        "tier": r.tier.value,
        "tier_gaps": list(r.tier_gaps),
        "dimensions": dict(r.dimensions),  # five separate statuses, never one badge
    }


@router.post("/v1/eligibility/engagement/evaluate", status_code=201)
async def evaluate_engagement(
    body: CredEngagementIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return _engagement_body(await _engage(body, request, caller, "direct"))


@router.post("/v1/consumers/zoikorum/decisions", status_code=201)
async def zoikorum_decision(
    body: CredEngagementIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A machine-verifiable decision for Zoikorum only: signed, bound to its consumer, with no raw credential in it."""
    r = await _engage(body, request, caller, "zoikorum")
    out = _engagement_body(r)
    keyrings = getattr(request.app.state, "keyrings", None)
    if keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No signing key is configured.", http_status=503)
    claims = {"consumer": "zoikorum", **out, "iat": int(_now().timestamp())}
    out["consumer"] = "zoikorum"
    out["signature"] = keyrings.credential.sign(claims, typ="zid-decision+jwt")
    return out


# -------------------------------------------------------------------------------------------------- disclosure


class CredGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    recipient: str = Field(min_length=1, max_length=200)
    consumer: str = Field(min_length=1, max_length=64)
    purpose: str = Field(min_length=1, max_length=64)
    fields: list[str] = Field(min_length=1, max_length=20)
    days: int = Field(default=30, ge=1, le=365)
    parent_grant_id: uuid.UUID | None = None


@router.post("/credentials/disclosures", status_code=201)
async def create_grant(body: CredGrantIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    subject = _subject(
        caller, None
    )  # a person grants their own credentials; nobody grants for someone else
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.disclosure.manage", "disclosure:*")
        try:
            g = await svc.create_grant(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                subject=subject,
                recipient=body.recipient,
                consumer=body.consumer,
                purpose=body.purpose,
                fields=body.fields,
                days=body.days,
                parent_id=str(body.parent_grant_id) if body.parent_grant_id else None,
                now=_now(),
            )
        except pt.DisclosureError as e:
            raise _err(e) from e
    return {
        "grant_id": g.grant_id,
        "expires_at": g.expires_at.isoformat(),
        "fields": sorted(g.fields),
    }


@router.post("/credentials/disclosures/{grant_id}:withdraw")
async def withdraw_grant(
    grant_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.disclosure.manage", f"disclosure:{grant_id}")
        try:
            await svc.withdraw_grant(
                conn, grant_id=str(grant_id), subject=_subject(caller, None), now=_now()
            )
        except pt.DisclosureError as e:
            raise _err(e) from e
    return {"grant_id": str(grant_id), "withdrawn": True}


class CredReadIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: uuid.UUID
    recipient: str
    consumer: str
    purpose: str


@router.post("/credentials/disclosures/{grant_id}:read")
async def read_grant(
    grant_id: uuid.UUID, body: CredReadIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.read", f"disclosure:{grant_id}")
        try:
            data = await svc.disclose_check(
                conn,
                grant_id=str(grant_id),
                check_id=str(body.check_id),
                recipient=body.recipient,
                consumer=body.consumer,
                purpose=body.purpose,
                now=_now(),
            )
        except pt.DisclosureError as e:
            raise _err(e) from e
    return {"grant_id": str(grant_id), "disclosed": data}


@router.get("/credentials/assertions/{ref}/verify")
async def public_verify(
    ref: str, request: Request, grant: Annotated[uuid.UUID, Query()]
) -> dict[str, Any]:
    """No account: the capability is the grant. Only a PUBLIC_VERIFIER grant still in force reveals anything, and only the
    bounded view (what the claim is, the decision, its tier, when it was checked); never a name, a birth date or a document."""
    tenant, _, item = ref.partition(".")
    try:
        tenant_id, check_id = str(uuid.UUID(tenant)), str(uuid.UUID(item))
    except ValueError as e:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404) from e
    from zid_context import RequestContext, current_context

    base = current_context() or RequestContext("req_public", "cor_public", "zoiko-id", "unknown")
    async with tenant_transaction(
        request_engine(request), base.with_identity(tenant_id, None)
    ) as conn:
        try:
            return await svc.public_assertion(
                conn, grant_id=str(grant), check_id=check_id, now=_now()
            )
        except pt.DisclosureError as e:
            # one answer for every failure: a verifier learns nothing about why
            raise ZidError("NOT_FOUND", "Not found.", http_status=404) from e


# ---------------------------------------------------------------------------------------------------- outreach


class CredOutreachIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_id: str = Field(min_length=1, max_length=100)
    claim_type: str = Field(min_length=1, max_length=40)
    days: int = Field(default=14, ge=1, le=60)
    # optional: attach the question so a reviewed answer becomes a claim check (the values are dropped once it has)
    subject_principal_id: uuid.UUID | None = None
    claim: dict[str, str] | None = Field(default=None, max_length=30)
    jurisdiction: dict[str, str] | None = Field(default=None, max_length=3)
    profession_code: str | None = Field(default=None, max_length=32)


@router.post("/credentials/source-outreach", status_code=201)
async def open_outreach(
    body: CredOutreachIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    issuer = issuer_registry().issuers.get(body.issuer_id)
    if issuer is None:
        raise ZidError("ISSUER_NOT_FOUND", "Issuer not found.", http_status=404)
    attached: dict[str, Any] | None = None
    if body.claim is not None:
        if not body.jurisdiction or "country" not in body.jurisdiction:
            raise ZidError(
                "JURISDICTION_REQUIRED", "A claim needs its jurisdiction.", http_status=422
            )
        try:
            validate_claim(
                body.claim_type, body.claim
            )  # a bad question is refused now, not after the issuer answers
        except ClaimError as e:
            raise _err(e) from e
        attached = {
            "fields": body.claim,
            "jurisdiction": body.jurisdiction,
            "profession": body.profession_code,
        }
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.outreach.manage", f"outreach:{body.issuer_id}")
        try:
            req, nonce = await svc.open_outreach(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                issuer_id=body.issuer_id,
                claim_type=body.claim_type,
                requested_by=_subject(caller, None),
                contacts=issuer.outreach_contacts,  # registered out of band, never taken from the request
                days=body.days,
                now=_now(),
                subject=_subject(caller, body.subject_principal_id) if attached else None,
                claim=attached,
            )
        except oc.OutreachError as e:
            raise _err(e) from e
    return {
        "request_id": req.request_id,
        "deadline": req.deadline.isoformat(),
        "state": req.state.value,
        "nonce": nonce,  # shown once; only its hash is kept
    }


class CredResponseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sender: str = Field(min_length=3, max_length=320)
    nonce: str = Field(min_length=1, max_length=128)
    outcome: str = Field(min_length=1, max_length=20)
    payload_b64: str = Field(min_length=1, max_length=20000)
    signature_b64: str = Field(min_length=1, max_length=2000)


@router.post("/credentials/source-outreach/{request_id}:response")
async def record_response(
    request_id: uuid.UUID, body: CredResponseIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.outreach.manage", f"outreach:{request_id}")
        row = await trust_store.load_outreach(conn, str(request_id))
        if row is None:
            raise ZidError("REQUEST_NOT_FOUND", "Request not found.", http_status=404)
        issuer = issuer_registry().issuers.get(row["issuer_id"])
        verifiers = getattr(request.app.state, "outreach_verifiers", {})
        verify = verifiers.get(row["issuer_id"])
        if issuer is None or verify is None:
            # no registered key for this issuer: an answer cannot be authenticated, so it is never accepted
            raise ZidError(
                "SIGNATURE_INVALID", "No registered key for this issuer.", http_status=403
            )
        try:
            payload = base64.b64decode(body.payload_b64, validate=True)
            signature = base64.b64decode(body.signature_b64, validate=True)
        except ValueError as e:
            raise ZidError(
                "PAYLOAD_INVALID", "Payload is not valid base64.", http_status=422
            ) from e
        resp = oc.Response(body.sender, body.nonce, body.outcome, payload, signature, _now())
        try:
            done = await svc.record_response(
                conn,
                request_id=str(request_id),
                response=resp,
                contacts=issuer.outreach_contacts,
                verify=verify,
                now=_now(),
            )
        except oc.OutreachError as e:
            raise _err(e) from e
    return {"request_id": str(request_id), "state": done.state.value}


class CredOutreachReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


@router.post("/credentials/source-outreach/{request_id}:review")
async def review_outreach(
    request_id: uuid.UUID, body: CredOutreachReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.outreach.manage", f"outreach:{request_id}")
        try:
            done = await svc.review_outreach(
                conn,
                request_id=str(request_id),
                reviewer=_subject(caller, None),
                approve=body.approve,
            )
        except oc.OutreachError as e:
            raise _err(e) from e
        check_id = await _conclude(conn, request, caller, str(request_id))
    verdict, reasons = oc.verdict(done)
    return {
        "request_id": str(request_id),
        "state": done.state.value,
        "verdict": verdict,
        "reason_codes": list(reasons),
        "check_id": check_id,
    }


async def _conclude(conn: Any, request: Request, caller: Caller, request_id: str) -> str | None:
    try:
        return await svc.conclude_outreach(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            request_id=request_id,
            packs=pack_registry(),
            registry=issuer_registry(),
            environment=str(getattr(request.app.state, "environment", "production")),
            now=_now(),
        )
    except (ClaimError, PackError) as e:
        raise _err(e) from e


@router.post("/credentials/source-outreach/{request_id}:conclude")
async def conclude_outreach(
    request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Conclude a request that was never answered: after its deadline it becomes an UNVERIFIABLE check, never a finding."""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.outreach.manage", f"outreach:{request_id}")
        check_id = await _conclude(conn, request, caller, str(request_id))
    return {"request_id": str(request_id), "check_id": check_id}


# ------------------------------------------------------------------------------------------------------ appeals


class CredAppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=2000)
    correction: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.post("/credentials/cases/{check_id}/appeals", status_code=201)
async def raise_appeal(
    check_id: uuid.UUID, body: CredAppealIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.appeal.manage", f"appeal:{check_id}")
        try:
            appeal_id = await svc.raise_appeal(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                check_id=str(check_id),
                raised_by=_subject(caller, None),
                reason=body.reason,
                correction=dict(body.correction),
            )
        except LookupError as e:
            raise ZidError("CHECK_NOT_FOUND", "Check not found.", http_status=404) from e
    return {"appeal_id": appeal_id, "state": "OPEN"}


class CredResolveAppealIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upheld: bool
    resolution: str = Field(min_length=1, max_length=2000)


@router.post("/credentials/appeals/{appeal_id}:resolve")
async def resolve_appeal(
    appeal_id: uuid.UUID, body: CredResolveAppealIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.appeal.manage", f"appeal:{appeal_id}")
        ok = await trust_store.resolve_appeal(
            conn,
            str(appeal_id),
            state="UPHELD" if body.upheld else "REJECTED",
            resolved_by=_subject(caller, None),
            resolution=body.resolution,
        )
    if not ok:  # unknown, already resolved, or the person who raised it
        raise ZidError("APPEAL_NOT_FOUND", "No open appeal you may resolve.", http_status=404)
    return {"appeal_id": str(appeal_id), "state": "UPHELD" if body.upheld else "REJECTED"}


# ---------------------------------------------------------------------------------------------------- monitoring


class CredMonitorIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_id: uuid.UUID
    event_id: str = Field(min_length=1, max_length=200)
    status: str = Field(pattern=r"^(ACTIVE|SUSPENDED|REVOKED|EXPIRED|UNKNOWN)$")
    observed_at: datetime
    sequence: int | None = Field(default=None, ge=0)


@router.post("/credentials/monitoring")
async def source_event(body: CredMonitorIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    event = lc.SourceEvent(
        body.event_id,
        str(body.check_id),
        CredentialStatus(body.status),
        body.observed_at if body.observed_at.tzinfo else body.observed_at.replace(tzinfo=UTC),
        body.sequence,
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.monitor.manage", f"monitor:{body.check_id}")
        try:
            changed, status = await svc.apply_source_event(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                check_id=str(body.check_id),
                event=event,
                now=_now(),
            )
        except LookupError as e:
            raise ZidError(str(e), "Check not found or no longer current.", http_status=404) from e
    return {"check_id": str(body.check_id), "changed": changed, "status": status}


# ------------------------------------------------------------------------------------------------ signed credentials


class CredVcVerifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential: str = Field(
        min_length=20, max_length=20000
    )  # a vc+jwt (W3C VCDM 2.0, Open Badges 3.0, CLR 2.0)
    holder_proof: str | None = Field(
        default=None, min_length=20, max_length=20000
    )  # a holder-signed vp+jwt
    jurisdiction: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


@router.post("/credentials/vc/verify")
async def verify_signed_credential(
    body: CredVcVerifyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Verify a signed credential as evidence. The answer states five separate facts and is never a primary-source
    confirmation or a license; an EU Digital Credential e-seal is refused because this path cannot validate it."""
    oauth = getattr(request.app.state, "oauth_settings", None)
    keyrings = getattr(request.app.state, "keyrings", None)
    if oauth is None or keyrings is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No verifier is configured.", http_status=503)
    settings = verifier.VerifierSettings(
        oauth.issuer,
        keyrings.verifier,
        getattr(request.app.state, "credential_transport", None),
        tuple(getattr(request.app.state, "verifier_x5c", ())),
    )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _gate(conn, caller, "credential.claim.verify", "credential-claim:VC")
        try:
            out = await vc_verify.verify_vc(
                conn,
                settings,
                vc_jwt=body.credential,
                holder_proof=body.holder_proof,
                jurisdiction=body.jurisdiction,
                now=_now(),
            )
        except CredentialError as e:
            raise ZidError(
                e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422
            ) from e
    return out.to_contract()
