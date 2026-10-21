"""Verification & assurance API (ZID-VERIFY-001 §21-22; FR-VER-001..008).

POST /verifications                                   start           (verification:execute)
GET  /verifications/{id}                              read            (verification:read)
POST /verifications/{id}:cancel | :reverify           (verification:execute)
GET  /principals/{id}/verification-assertions         (verification:read, or self)
GET  /principals/{id}/assurance                       facts only      (verification:read, or self)
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
from zid_platform.modules.verification.application import service
from zid_platform.modules.verification.domain.verification import (
    SessionStatus,
    VerificationError,
    transition,
    validate_requirements,
)
from zid_platform.modules.verification.infrastructure import verification_store as store
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
}


def _err(e: VerificationError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 409)
    )


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _transport(request: Request) -> httpx.AsyncBaseTransport | None:
    return getattr(request.app.state, "verification_transport", None)


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
        )
    except VerificationError as e:
        raise _err(e) from e
    return {
        "verification_id": out.session_id,
        "status": out.status,
        "adapter_id": out.adapter_id,
        "outcome": out.outcome,
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
        )
    except VerificationError as e:
        raise _err(e) from e
    return {"verification_id": out.session_id, "status": out.status, "supersedes": old.session_id}


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
        )
    except ValueError as e:
        code = e.code if isinstance(e, VerificationError) else "PROVIDER_UNTRUSTED"
        return JSONResponse({"error": code}, status_code=_STATUS.get(code, 409))
    return JSONResponse({"outcome": outcome}, status_code=202)


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


class AdapterIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    adapter_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{1,62}$")
    provider: str = Field(min_length=1, max_length=64)
    mapping_version: str = Field(min_length=1, max_length=32)
    submit_endpoint: str = Field(pattern=r"^https://")
    callback_jwks: dict[str, Any]
    callback_algorithms: list[Literal["ES256", "Ed25519", "RS256", "PS256"]] = Field(min_length=1)
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
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.adapter.manage",
            resource=f"verification_adapter:{body.adapter_id}",
        )
        await store.create_adapter(
            conn, {**body.model_dump(), "tenant_id": caller.ctx.tenant_id or ""}
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
