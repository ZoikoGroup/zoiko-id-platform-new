"""Verification & assurance API (ZID-VERIFY-001 §21-22; FR-VER-001..008).

POST /verifications                                   start           (verification:execute)
GET  /verifications/{id}                              read            (verification:read)
POST /verifications/{id}:cancel | :reverify           (verification:execute)
GET  /principals/{id}/verification-assertions         (verification:read, or self)
GET  /principals/{id}/assurance                       facts only      (verification:read, or self)
POST /verified-claims                                  OpenID IDA verified_claims projection (VERIFY-25)
POST /verification-reliance-decisions                 explicit reuse  (verification:execute)
POST /verification-review-cases/{id}/decisions        protected reviewer (verification.review.decide)
POST /verification-provider-callbacks/{tenant}.{adapter}   signed by the provider
GET  /verification-profiles                           (verification:read)
POST /verification-profiles | /{id}:activate          protected admin (verification.profile.manage)
POST /verification-adapters | /{id}:quarantine        protected admin (verification.adapter.manage)
POST /verification-assertions/{id}:revoke             protected (verification.assertion.revoke)
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError
from zid_outbox import emit

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.verification.application import service
from zid_platform.modules.verification.domain.countries import Capability
from zid_platform.modules.verification.domain.providers import (
    ProviderKind,
    validate_provider_config,
)
from zid_platform.modules.verification.domain.verification import (
    SessionStatus,
    VerificationError,
    transition,
    validate_requirements,
)
from zid_platform.modules.verification.infrastructure import verification_store as store
from zid_platform.modules.verification.infrastructure.country_registry import registry
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {
    "VERIFICATION_NOT_FOUND": 404,
    "PROFILE_NOT_FOUND": 404,
    "REVIEW_CASE_NOT_FOUND": 404,
    "PROFILE_NOT_AVAILABLE": 422,
    "ASSURANCE_NOT_MET": 422,
    "PROFILE_INVALID": 422,
    "REVIEWER_IS_SUBJECT": 403,
    "PROVIDER_UNTRUSTED": 401,
    "CALLBACK_SIGNATURE_INVALID": 401,
    "CALLBACK_SCHEMA_INVALID": 400,
    "CALLBACK_STALE": 400,
    "APPLICANT_DATA_UNAVAILABLE": 503,  # nothing was recorded: the provider retries
    "COUNTRY_CAPABILITY_UNAVAILABLE": 503,  # the provider retries once the country is enabled again
}


def _err(e: VerificationError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 409)
    )


def _country_gate(request: Request, jurisdiction: str) -> None:
    """EDR-001: no identity verification in a country that has not been enabled for it (fail closed)."""
    enforce(request, jurisdiction, Capability.INDIVIDUAL_VERIFICATION)


def _callback_country_check(request: Request, country: str) -> None:
    """A provider result for a country switched off since the session began is held, never applied."""
    try:
        _country_gate(request, country)
    except ZidError as e:
        raise VerificationError("COUNTRY_CAPABILITY_UNAVAILABLE", country) from e


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    return getattr(request.app.state, "verification_transport", None)


def _secrets(request: Request) -> Callable[[str], str | None]:
    """Provider secrets by reference, read from the secrets broker at the moment of use and never kept."""
    broker = getattr(request.app.state, "secret_broker", None)

    def get(name: str) -> str | None:
        if broker is None:
            return None
        try:
            return str(broker.get(name))
        except Exception:  # noqa: BLE001 - a missing secret is "not available", never a crash or a pass
            return None

    return get


class StartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] | None = None


def _session_view(s: store.VSession) -> dict[str, Any]:
    return {
        "verification_id": s.session_id,
        "status": s.status.value,
        "outcome": s.outcome_code,
        "profile_id": s.profile_id,
        "subject_principal_id": s.subject_principal_id,
        "purpose": s.purpose,
        "jurisdiction": s.jurisdiction,
        "adapter_id": s.adapter_id,
        "expires_at": s.expires_at.isoformat(),
    }


@router.post("/verifications", status_code=201)
async def start(body: StartIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    # CR-068: count this verification attempt (best effort, never blocks it; a refused attempt still counts, so probing is seen). The contact key is the person's stable id here.
    from zid_platform.attempt_context import attempt_inputs
    from zid_platform.modules.screening.ports import AttemptAction, note_attempt

    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await note_attempt(
            conn,
            caller.ctx.tenant_id or "",
            subject_ref=f"verification:{body.subject_principal_id}",
            action=AttemptAction.VERIFICATION,
            now=datetime.now(UTC),
            contact=str(body.subject_principal_id),
            **attempt_inputs(request),
        )
    _country_gate(request, body.jurisdiction)
    try:
        out = await service.start(
            request_engine(request),
            ctx=caller.ctx,
            profile_id=str(body.profile_id),
            subject_principal_id=str(body.subject_principal_id),
            purpose=body.purpose,
            jurisdiction=body.jurisdiction,
            floor=body.required_level,
            transport=_transport(request),
            now=datetime.now(UTC),
            country_registry_version=registry().version,
            secrets=_secrets(request),
        )
    except VerificationError as e:
        raise _err(e) from e
    started: dict[str, Any] = {
        "verification_id": out.session_id,
        "status": out.status,
        "adapter_id": out.adapter_id,
        "outcome": out.outcome,
    }
    if out.handoff:
        started["handoff"] = (
            out.handoff
        )  # the applicant's short-lived link or token: shown once, never stored
    return started


class BulkStartIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    profile_id: uuid.UUID
    subject_principal_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    purpose: str = Field(min_length=1, max_length=128)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] | None = None


@router.post("/verifications:bulk", status_code=207)
async def start_bulk(body: BulkStartIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Start the same verification for up to 50 people in one request (plan feature advanced.bulk-identity-company-verification).
    Each person is started on its own, exactly as POST /verifications would: one person's failure never stops or hides another's, and each
    result says started or why not. A person's short-lived handoff link is returned once, here, and never stored."""
    _scope(caller, "verification:execute")
    _country_gate(request, body.jurisdiction)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await require_feature(
            conn,
            "advanced.bulk-identity-company-verification",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
    results: list[dict[str, Any]] = []
    for subject in dict.fromkeys(body.subject_principal_ids):
        try:
            out = await service.start(
                request_engine(request),
                ctx=caller.ctx,
                profile_id=str(body.profile_id),
                subject_principal_id=str(subject),
                purpose=body.purpose,
                jurisdiction=body.jurisdiction,
                floor=body.required_level,
                transport=_transport(request),
                now=datetime.now(UTC),
                country_registry_version=registry().version,
                secrets=_secrets(request),
            )
        except VerificationError as e:
            results.append({"subject_principal_id": str(subject), "started": False, "code": e.code})
            continue
        item: dict[str, Any] = {
            "subject_principal_id": str(subject),
            "started": True,
            "verification_id": out.session_id,
            "status": out.status,
        }
        if out.handoff:
            item["handoff"] = out.handoff
        results.append(item)
    started = sum(1 for r in results if r["started"])
    return {
        "requested": len(results),
        "started": started,
        "failed": len(results) - started,
        "items": results,
    }


@router.get("/verifications/{verification_id}")
async def read(verification_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return _session_view(await store.load_session(conn, str(verification_id)))
        except VerificationError as e:
            raise _err(e) from e


@router.post("/verifications/{verification_id}:cancel")
async def cancel(verification_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            s = await store.load_session(conn, str(verification_id), lock=True)
            s = await store.update_session(
                conn,
                s,
                status=transition(s.status, SessionStatus.CANCELED),
                now=now,
                outcome="CANCELED",
            )
        except VerificationError as e:
            raise _err(e) from e
    return _session_view(s)


@router.post("/verifications/{verification_id}:reverify", status_code=201)
async def reverify(
    verification_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """A new session (and new assertions); prior history stays immutable (§18)."""
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            old = await store.load_session(conn, str(verification_id))
        except VerificationError as e:
            raise _err(e) from e
    _country_gate(request, old.jurisdiction)
    try:
        out = await service.start(
            request_engine(request),
            ctx=caller.ctx,
            profile_id=old.profile_id,
            subject_principal_id=old.subject_principal_id,
            purpose=old.purpose,
            jurisdiction=old.jurisdiction,
            floor=None,
            transport=_transport(request),
            now=datetime.now(UTC),
            supersedes=old.session_id,
            country_registry_version=registry().version,
            secrets=_secrets(request),
        )
    except VerificationError as e:
        raise _err(e) from e
    again: dict[str, Any] = {
        "verification_id": out.session_id,
        "status": out.status,
        "supersedes": old.session_id,
    }
    if out.handoff:
        again["handoff"] = out.handoff
    return again


def _self_or(caller: Caller, principal_id: uuid.UUID, scope: str) -> None:
    if str(principal_id) != caller.ctx.principal_id:
        _scope(caller, scope)


@router.get("/principals/{principal_id}/verification-assertions")
async def assertions(
    principal_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _self_or(caller, principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = await store.assertions_for(conn, str(principal_id))
    return {
        "items": [
            {
                "assertion_id": str(r["assertion_id"]),
                "claim_type": r["claim_type"],
                "verification_method": r["verification_method"],
                "evidence_types": list(r["evidence_types"]),
                "assurance_level": r["assurance_level"],
                "status": r["status"],
                "verified_at": r["performed_at"].isoformat(),
                "fresh_until": r["valid_until"].isoformat() if r["valid_until"] else None,
                "reason_codes": list(r["reason_codes"]),
            }
            for r in rows
        ]
    }


@router.get("/principals/{principal_id}/assurance")
async def assurance(principal_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _self_or(caller, principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.assurance(conn, str(principal_id), datetime.now(UTC))


class VerifiedClaimsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    claims: list[str] = Field(min_length=1, max_length=20)
    minimum_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"] = "IAL-Z1"


@router.post("/verified-claims")
async def verified_claims(
    body: VerifiedClaimsIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """OpenID IDA `verified_claims` projection (VERIFY-25): attested claims only, for the stated purpose."""
    _self_or(caller, body.subject_principal_id, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.verified_claims(
            conn,
            str(body.subject_principal_id),
            purpose=body.purpose,
            requested=body.claims,
            now=datetime.now(UTC),
            min_level=body.minimum_level,
        )


class RelianceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    required_level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"]
    claims: list[str] = Field(min_length=1, max_length=20)
    jurisdiction: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


@router.post("/verification-reliance-decisions", status_code=201)
async def rely(body: RelianceIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:execute")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return await service.rely(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            principal_id=str(body.subject_principal_id),
            client_id=caller.client_id,
            purpose=body.purpose,
            required_level=body.required_level,
            claims=body.claims,
            jurisdiction=body.jurisdiction,
            now=datetime.now(UTC),
        )


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT", "REQUEST_MORE_EVIDENCE", "ESCALATE"]
    reason_codes: list[str] = Field(min_length=1, max_length=10)


@router.post("/verification-review-cases/{case_id}/decisions")
async def review(
    case_id: uuid.UUID, body: ReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.review.decide",
            resource=f"verification_review_case:{case_id}",
        )
        try:
            outcome = await service.review(
                conn,
                case_id=str(case_id),
                reviewer=caller.ctx.principal_id or "",
                decision=body.decision,
                reasons=body.reason_codes,
                now=datetime.now(UTC),
            )
        except VerificationError as e:
            raise _err(e) from e
    return {"case_id": str(case_id), "outcome": outcome}


async def _callback_ok_status(request: Request, tenant: str, adapter_id: str) -> int:
    async with tenant_transaction(
        request_engine(request), _ctx().with_identity(tenant, None)
    ) as conn:
        adapter = await store.load_adapter(conn, adapter_id)
    return 202 if adapter is None or adapter.get("provider_kind", "GENERIC") == "GENERIC" else 200


@router.post("/verification-provider-callbacks/{adapter_ref}", status_code=202)
async def callback(adapter_ref: str, request: Request) -> Response:
    tenant, _, adapter_id = adapter_ref.partition(".")
    try:
        uuid.UUID(tenant)
        body = await request.body()
        if len(body) > 64 * 1024:
            raise VerificationError("CALLBACK_SCHEMA_INVALID", "too large")
        outcome = await service.provider_callback(
            request_engine(request),
            tenant_id=tenant,
            adapter_id=adapter_id,
            body=body,
            base_ctx=_ctx(),
            now=datetime.now(UTC),
            country_check=lambda country: _callback_country_check(request, country),
            headers=dict(request.headers),
            secrets=_secrets(request),
            transport=_transport(request),
        )
    except ValueError as e:
        code = e.code if isinstance(e, VerificationError) else "PROVIDER_UNTRUSTED"
        return JSONResponse({"error": code}, status_code=_STATUS.get(code, 409))
    # Veriff and Sumsub retry anything that is not a plain 200, so their callbacks are answered 200; Zoiko's own protocol keeps 202
    return JSONResponse(
        {"outcome": outcome}, status_code=await _callback_ok_status(request, tenant, adapter_id)
    )


class ProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    level: Literal["IAL-Z1", "IAL-Z2", "IAL-Z3"]
    requirements: dict[str, Any]


@router.get("/verification-profiles")
async def profiles(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        return {"items": await store.list_profiles(conn)}


@router.post("/verification-profiles", status_code=201)
async def create_profile(body: ProfileIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    try:
        validate_requirements(body.level, body.requirements)
    except VerificationError as e:
        raise _err(e) from e
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.profile.manage",
            resource=f"verification_profile:{body.name}",
        )
        pid, version = await store.create_profile(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            name=body.name,
            level=body.level,
            requirements=body.requirements,
        )
    return {"profile_id": pid, "name": body.name, "version": version, "status": "DRAFT"}


@router.post("/verification-profiles/{profile_id}:activate")
async def activate_profile(
    profile_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            p = await store.load_profile(conn, str(profile_id))
            await protected_action(
                conn,
                caller,
                action="verification.profile.manage",
                resource=f"verification_profile:{p.name}",
                change=f"activate:{p.version}",
            )
            await store.activate_profile(conn, p, now)
        except VerificationError as e:
            raise _err(e) from e
        await emit(
            conn,
            name="verification.profile.activated",
            tenant_id=p.tenant_id,
            aggregate_type="verification_profile",
            aggregate_id=p.profile_id,
            aggregate_version=1,
            data={"profile_id": p.profile_id, "name": p.name, "version": p.version},
        )
    return {"profile_id": str(profile_id), "status": "ACTIVE"}


def _default_algorithms() -> list[Literal["ES256", "Ed25519", "RS256", "PS256"]]:
    return ["ES256"]


class AdapterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    provider: str = Field(min_length=1, max_length=64)
    mapping_version: str = Field(min_length=1, max_length=32)
    submit_endpoint: str = Field(
        pattern=r"^https://"
    )  # for Veriff and Sumsub, the provider's base address
    callback_jwks: dict[str, Any] = Field(default_factory=lambda: {"keys": []})  # GENERIC only
    callback_algorithms: list[Literal["ES256", "Ed25519", "RS256", "PS256"]] = Field(
        default_factory=_default_algorithms, min_length=1
    )
    provider_kind: Literal["GENERIC", "VERIFF", "SUMSUB"] = "GENERIC"
    provider_config: dict[str, Any] = Field(
        default_factory=dict
    )  # secret names and settings, never the secrets
    equivalence_group: str = Field(min_length=1, max_length=64)
    supported_evidence: list[str] = Field(min_length=1)
    jurisdictions: list[str] = Field(min_length=1)


@router.get("/verification-adapters")
async def adapters(request: Request, caller: CallerDep) -> dict[str, Any]:
    _scope(caller, "verification:read")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        items = await store.list_adapters(conn)
    return {"items": [store.adapter_view(a) for a in sorted(items, key=lambda a: a["adapter_id"])]}


@router.post("/verification-adapters", status_code=201)
async def create_adapter(body: AdapterIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    kind = ProviderKind(body.provider_kind)
    try:
        config = validate_provider_config(kind, body.provider_config, body.jurisdictions)
    except VerificationError as e:
        raise _err(e) from e
    if kind is ProviderKind.GENERIC and not body.callback_jwks.get("keys"):
        raise ZidError(
            "VALIDATION_FAILED",
            "A generic adapter needs its pinned callback keys.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.adapter.manage",
            resource=f"verification_adapter:{body.adapter_id}",
        )
        await store.create_adapter(
            conn,
            {
                **body.model_dump(),
                "provider_config": config,
                "tenant_id": caller.ctx.tenant_id or "",
            },
        )
    return {
        "adapter_id": body.adapter_id,
        "status": "ACTIVE",
        "callback_path": f"/verification-provider-callbacks/{caller.ctx.tenant_id}.{body.adapter_id}",
    }


@router.post("/verification-adapters/{adapter_id}:quarantine")
async def quarantine(adapter_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.adapter.manage",
            resource=f"verification_adapter:{adapter_id}",
            change="quarantine",
        )
        if not await store.set_adapter_status(conn, adapter_id, "QUARANTINED"):
            raise ZidError("ADAPTER_NOT_FOUND", "Not found.", http_status=404)
        await emit(
            conn,
            name="verification.provider.quarantined",
            tenant_id=caller.ctx.tenant_id or "",
            aggregate_type="verification_adapter",
            aggregate_id=adapter_id,
            aggregate_version=1,
            data={"adapter_id": adapter_id},
        )
    return {"adapter_id": adapter_id, "status": "QUARANTINED"}


@router.post("/verification-assertions/{assertion_id}:revoke")
async def revoke_assertion(
    assertion_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.assertion.revoke",
            resource=f"verification_assertion:{assertion_id}",
        )
        ids = await service.revoke(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            principal_id=None,
            assertion_id=str(assertion_id),
            now=datetime.now(UTC),
        )
    if not ids:
        raise ZidError("ASSERTION_NOT_FOUND", "Not found.", http_status=404)
    return {"assertion_id": str(assertion_id), "status": "REVOKED"}
