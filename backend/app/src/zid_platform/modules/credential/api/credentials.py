"""Verifiable credential API (ZID-VC-001 §5-7, §13, §15, §18; OID4VP 1.0; OID4VCI 1.0; DEC-E2).

Verifier (baseline role)
    POST /presentations                               RP starts a request  (scope presentation:request)
    GET  /presentations/{tenant}.{id}/request         wallet fetches the signed request object (once)
    POST /presentations/response/{tenant}             wallet direct_post response
    GET  /presentations/{id}                          RP reads the outcome; claims are handed over once
Issuer (controlled)
    GET  /.well-known/openid-credential-issuer | /.well-known/jwt-vc-issuer
    POST /credentials/nonce                           c_nonce for the key proof
    GET  /credentials                                 issued credentials       (credential.read, R1)
    POST /credentials                                 issue (access token scope credential:<config>)
    GET  /credentials/status-lists/{tenant}.{list}    Token Status List
    POST /credentials/{id}:revoke | :suspend | :reinstate      (credential.status.change, R3)
Trust and configuration (protected administration)
    GET  /credential-configurations | /credential-issuer-trust | /relying-party-registrations
                                                                  list (credential.read, R1)
    POST /credential-configurations | /{id}:activate             (credential.trust.manage, R3;
                                                                   + credential.issue to enable issuance)
    POST /credential-issuer-trust | /credential-issuer-trust:status | :rotate-keys
                                                                  (credential.trust.manage)
    POST /relying-party-registrations                             (credential.trust.manage)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext, current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.country_gate import enforce_tenant
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.credential.application import issuer as issuing
from zid_platform.modules.credential.application import verifier
from zid_platform.modules.credential.domain.credentials import (
    MDOC_ADAPTER,
    SD_JWT_ADAPTER,
    CredentialError,
)
from zid_platform.modules.credential.domain.vcdm import VCDM_ADAPTER
from zid_platform.modules.credential.infrastructure import credential_store as store
from zid_platform.modules.verification.ports import Capability
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.protected import protected_action

router = APIRouter(tags=["credentials"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
_STATUS = {
    "CONFIGURATION_NOT_FOUND": 404,
    "PRESENTATION_NOT_FOUND": 404,
    "CREDENTIAL_NOT_FOUND": 404,
    "STATUS_LIST_NOT_FOUND": 404,
    "ROLE_NOT_ENABLED": 403,
    "RP_NOT_REGISTERED_FOR_PURPOSE": 403,
    "CLAIM_NOT_PERMITTED_FOR_PURPOSE": 422,
    "CLAIM_NOT_DEFINED": 422,
    "CLAIMS_REQUIRED": 422,
    "INVALID_PROOF": 400,
    "INVALID_NONCE": 400,
}


def _err(e: CredentialError) -> ZidError:
    return ZidError(
        e.code, e.code.replace("_", " ").capitalize() + ".", http_status=_STATUS.get(e.code, 409)
    )


def _scope(caller: Caller, scope: str) -> None:
    if scope not in caller.scope:
        raise ZidError("SCOPE_INSUFFICIENT", f"{scope} is required.", http_status=403)


def _ctx() -> RequestContext:
    return current_context() or RequestContext("req_unknown", "cor_unknown", "zoiko-id", "unknown")


def _ref(ref: str) -> tuple[str, str]:
    tenant, _, item = ref.partition(".")
    try:
        return str(uuid.UUID(tenant)), str(uuid.UUID(item))
    except ValueError as e:
        raise ZidError("NOT_FOUND", "Not found.", http_status=404) from e


def _verifier(request: Request) -> verifier.VerifierSettings:
    s = getattr(request.app.state, "oauth_settings", None)
    if s is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No issuer configured.", http_status=503)
    return verifier.VerifierSettings(
        s.issuer,
        request.app.state.keyrings.verifier,
        getattr(request.app.state, "credential_transport", None),
        tuple(getattr(request.app.state, "verifier_x5c", ())),
    )


def _issuer(request: Request) -> issuing.IssuerSettings:
    s = getattr(request.app.state, "oauth_settings", None)
    if s is None:
        raise ZidError("DEPENDENCY_UNAVAILABLE", "No issuer configured.", http_status=503)
    return issuing.IssuerSettings(s.issuer, request.app.state.keyrings.credential)


# ------------------------------------------------------------------------------------- verifier


class PresentationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    configuration_id: uuid.UUID
    purpose: str = Field(min_length=1, max_length=128)
    claims: list[str] = Field(min_length=1, max_length=30)
    jurisdiction: str = Field(pattern=r"^[A-Z]{2}$")
    subject_principal_id: uuid.UUID | None = None
    # HAIP: direct_post.jwt has the wallet encrypt its response to a per-presentation key
    response_mode: Literal["direct_post", "direct_post.jwt"] = "direct_post"


@router.post("/presentations", status_code=201)
async def create_presentation(
    body: PresentationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "presentation:request")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            return await verifier.create(
                conn,
                _verifier(request),
                tenant_id=caller.ctx.tenant_id or "",
                client_id=caller.client_id,
                configuration_id=str(body.configuration_id),
                purpose=body.purpose,
                claims=body.claims,
                subject_principal_id=str(body.subject_principal_id)
                if body.subject_principal_id
                else None,
                jurisdiction=body.jurisdiction,
                now=datetime.now(UTC),
                response_mode=body.response_mode,
            )
        except CredentialError as e:
            raise _err(e) from e


@router.get("/presentations/{ref}/request")
async def request_object(ref: str, request: Request) -> Response:
    tenant, pid = _ref(ref)
    async with tenant_transaction(
        request_engine(request), _ctx().with_identity(tenant, None)
    ) as conn:
        try:
            jwt = await verifier.request_object(conn, pid, datetime.now(UTC))
        except CredentialError as e:
            raise _err(e) from e
    return Response(
        jwt, media_type="application/oauth-authz-req+jwt", headers={"Cache-Control": "no-store"}
    )


@router.post("/presentations/response/{tenant_id}")
async def response(tenant_id: uuid.UUID, request: Request) -> Response:
    body = await request.body()
    if len(body) > 512 * 1024:
        return JSONResponse({"error": "invalid_request"}, status_code=400)
    form = dict(parse_qsl(body.decode("utf-8", errors="replace")))
    try:
        await verifier.respond(
            request_engine(request),
            _verifier(request),
            tenant_id=str(tenant_id),
            form=form,
            base_ctx=_ctx(),
            now=datetime.now(UTC),
        )
    except CredentialError as e:
        return JSONResponse(
            {"error": "invalid_request", "error_description": e.code}, status_code=400
        )
    return JSONResponse({}, headers={"Cache-Control": "no-store"})


@router.get("/presentations/{presentation_id}")
async def read_presentation(
    presentation_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _scope(caller, "presentation:request")
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            p = await store.load_presentation(conn, str(presentation_id))
        except CredentialError as e:
            raise _err(e) from e
        if p["relying_client_id"] != caller.client_id:
            raise _err(CredentialError("PRESENTATION_NOT_FOUND"))
        claims = (
            await store.collect_claims(conn, p["presentation_id"])
            if p["status"] == "VERIFIED"
            else None
        )
    return {
        "presentation_id": str(presentation_id),
        "status": p["status"],
        "reason_codes": list(p["reason_codes"]),
        "issuer": p["issuer_identifier"],
        "credential_status": p["credential_status"],
        "claims": claims,  # once; afterwards only commitments remain
    }


# --------------------------------------------------------------------------------------- issuer


@router.get("/.well-known/openid-credential-issuer")
async def issuer_metadata(request: Request) -> dict[str, Any]:
    s = _issuer(request)
    base = s.issuer.rstrip("/")
    return {
        "credential_issuer": s.issuer,
        "authorization_servers": [s.issuer],
        "credential_endpoint": f"{base}/credentials",
        "nonce_endpoint": f"{base}/credentials/nonce",
        "credential_configurations_supported": {},  # per tenant: discovered from the offer
    }


@router.get("/.well-known/jwt-vc-issuer")
async def jwt_vc_issuer(request: Request) -> dict[str, Any]:
    s = _issuer(request)
    return {"issuer": s.issuer, "jwks": s.keyring.jwks()}


@router.post("/credentials/nonce")
async def nonce(request: Request) -> Response:
    return JSONResponse(
        {"c_nonce": issuing.c_nonce(_issuer(request), datetime.now(UTC))},
        headers={"Cache-Control": "no-store"},
    )


class CredentialIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_configuration_id: uuid.UUID
    proofs: dict[str, list[str]]


@router.get("/credentials")
async def list_credentials(
    request: Request,
    caller: CallerDep,
    status: Literal["VALID", "SUSPENDED", "REVOKED", "EXPIRED", "UNKNOWN"] | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="credential.read", resource="credential:*")
        return {"items": await store.list_issued(conn, status=status, limit=limit)}


@router.post("/credentials")
async def issue_credential(body: CredentialIn, request: Request, caller: CallerDep) -> Response:
    _scope(caller, f"credential:{body.credential_configuration_id}")
    proofs = body.proofs.get("jwt") or []
    if len(proofs) != 1:
        return JSONResponse({"error": "invalid_proof"}, status_code=400)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            # EDR-001: no credential is issued for an organization whose country is not enabled for it
            await enforce_tenant(
                request, conn, caller.ctx.tenant_id or "", Capability.CREDENTIAL_ISSUANCE
            )
        except ZidError as e:
            return JSONResponse(
                {"error": "invalid_credential_request", "error_description": e.code},
                status_code=422,
            )
        try:
            out = await issuing.issue(
                conn,
                _issuer(request),
                tenant_id=caller.ctx.tenant_id or "",
                subject_principal_id=caller.ctx.principal_id or "",
                configuration_id=str(body.credential_configuration_id),
                proof=proofs[0],
                now=datetime.now(UTC),
            )
        except CredentialError as e:
            code = {"INVALID_PROOF": "invalid_proof", "INVALID_NONCE": "invalid_nonce"}.get(
                e.code, "invalid_credential_request"
            )
            return JSONResponse({"error": code, "error_description": e.code}, status_code=400)
    return JSONResponse(out, headers={"Cache-Control": "no-store"})


@router.get("/credentials/status-lists/{ref}")
async def status_list(ref: str, request: Request) -> Response:
    tenant, list_id = _ref(ref)
    async with tenant_transaction(
        request_engine(request), _ctx().with_identity(tenant, None)
    ) as conn:
        try:
            token = await issuing.status_list_token(
                conn, _issuer(request), tenant_id=tenant, list_id=list_id, now=datetime.now(UTC)
            )
        except CredentialError as e:
            raise _err(e) from e
    return Response(
        token, media_type="application/statuslist+jwt", headers={"Cache-Control": "max-age=300"}
    )


async def _change(
    request: Request, caller: Caller, credential_id: uuid.UUID, status: str
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.status.change",
            resource=f"credential:{credential_id}",
            change=status,
        )
        try:
            await issuing.change_status(
                conn,
                tenant_id=caller.ctx.tenant_id or "",
                credential_id=str(credential_id),
                status=status,
                now=datetime.now(UTC),
            )
        except CredentialError as e:
            raise _err(e) from e
    return {"credential_id": str(credential_id), "status": status}


@router.post("/credentials/{credential_id}:revoke")
async def revoke(credential_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, credential_id, "REVOKED")


@router.post("/credentials/{credential_id}:suspend")
async def suspend(credential_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _change(request, caller, credential_id, "SUSPENDED")


@router.post("/credentials/{credential_id}:reinstate")
async def reinstate(
    credential_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _change(request, caller, credential_id, "VALID")


# ------------------------------------------------------------------------- trust and configuration


class ConfigurationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    credential_type: str = Field(min_length=1, max_length=256)
    format: Literal["SD_JWT_VC", "MDOC", "VC_JWT"]
    required_claims: list[str] = Field(min_length=1, max_length=50)
    optional_claims: list[str] = Field(default_factory=list, max_length=50)
    holder_binding_required: bool = True
    status_method: Literal["TOKEN_STATUS_LIST", "BITSTRING_STATUS_LIST", "NONE"]
    status_mandatory: bool = True
    risk_class: int = Field(default=2, ge=0, le=3)
    assurance_profile_id: uuid.UUID | None = None
    issuance_enabled: bool = False


@router.get("/credential-configurations")
async def list_configurations(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource="credential_configuration:*"
        )
        return {"items": await store.list_configurations(conn)}


@router.post("/credential-configurations", status_code=201)
async def create_configuration(
    body: ConfigurationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    data = body.model_dump(mode="json")
    data["adapter_version"] = {
        "SD_JWT_VC": SD_JWT_ADAPTER,
        "MDOC": MDOC_ADAPTER,
        "VC_JWT": VCDM_ADAPTER,
    }[body.format]
    if body.status_method != "NONE" and (body.format == "VC_JWT") != (
        body.status_method == "BITSTRING_STATUS_LIST"
    ):
        raise ZidError(
            "VALIDATION_FAILED",
            "VC_JWT uses a Bitstring Status List; SD-JWT VC and mdoc a Token Status List.",
            http_status=422,
        )
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"credential_configuration:{body.credential_type}",
        )
        cid = await store.create_configuration(conn, caller.ctx.tenant_id or "", data)
    return {"configuration_id": cid, "status": "DRAFT", "adapter_version": data["adapter_version"]}


@router.post("/credential-configurations/{configuration_id}:activate")
async def activate_configuration(
    configuration_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            config = await store.load_configuration(conn, str(configuration_id))
        except CredentialError as e:
            raise _err(e) from e
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"credential_configuration:{config.credential_type}",
            change="activate",
        )
        if config.issuance_enabled:  # DEC-E2: issuing is a separate, credential-signing decision
            await enforce_tenant(
                request, conn, caller.ctx.tenant_id or "", Capability.CREDENTIAL_ISSUANCE
            )
            await protected_action(
                conn,
                caller,
                action="credential.issue",
                resource=f"credential_configuration:{config.credential_type}",
            )
        if not await store.activate_configuration(conn, str(configuration_id)):
            raise _err(CredentialError("CONFIGURATION_NOT_DRAFT"))
    return {"configuration_id": str(configuration_id), "status": "ACTIVE"}


Alg = Literal["ES256", "Ed25519", "RS256", "PS256"]


def _default_algs() -> list[Alg]:
    return ["ES256"]


class TrustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_identifier: str = Field(min_length=1, max_length=512)
    credential_types: list[str] = Field(min_length=1, max_length=20)
    jurisdictions: list[str] = Field(default_factory=list)
    trust_source: Literal["PINNED", "FEDERATION"]
    jwks: dict[str, Any] | None = None
    iaca_certificates: list[str] | None = None
    trust_anchor_id: uuid.UUID | None = None
    algorithms: list[Alg] = Field(default_factory=_default_algs)
    valid_to: datetime


@router.get("/credential-issuer-trust")
async def list_trust(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.read", resource="credential_issuer:*"
        )
        return {"items": await store.list_trust(conn)}


@router.post("/credential-issuer-trust", status_code=201)
async def create_trust(body: TrustIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"credential_issuer:{body.issuer_identifier}",
        )
        await require_feature(
            conn,
            "advanced.custom-credential-schemas-trust-policies",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        data = body.model_dump()
        data["trust_anchor_id"] = str(body.trust_anchor_id) if body.trust_anchor_id else None
        tid = await store.create_trust(conn, caller.ctx.tenant_id or "", data)
    return {"trust_id": tid, "issuer_identifier": body.issuer_identifier, "status": "ACTIVE"}


class TrustStatusIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_identifier: str
    status: Literal["SUSPENDED", "REVOKED", "QUARANTINED", "ACTIVE"]


@router.post("/credential-issuer-trust:status")
async def trust_status(body: TrustStatusIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"credential_issuer:{body.issuer_identifier}",
            change=body.status,
        )
        if not await store.set_trust_status(conn, body.issuer_identifier, body.status):
            raise ZidError("ISSUER_NOT_FOUND", "Not found.", http_status=404)
    return {"issuer_identifier": body.issuer_identifier, "status": body.status}


class TrustKeysIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issuer_identifier: str
    jwks: dict[str, Any]


@router.post("/credential-issuer-trust:rotate-keys")
async def rotate_trust_keys(
    body: TrustKeysIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Issuer key rollover for a pinned issuer: the new key set replaces the old one. A key that
    carries `not_after` (epoch seconds) stays valid only until then (bounded overlap)."""
    keys = body.jwks.get("keys")
    if not isinstance(keys, list) or not keys or not all(isinstance(k, dict) for k in keys):
        raise ZidError("VALIDATION_FAILED", "jwks must hold at least one key.", http_status=422)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"credential_issuer:{body.issuer_identifier}",
            change="rotate-keys",
        )
        await require_step_up(
            request,
            conn,
            caller,
            action="rotate-revoke-critical-enterprise-keys-or-production-certificates",
            resource=f"credential_issuer:{body.issuer_identifier}",
            change={
                "operation": "ROTATE_KEYS",
                "issuer": body.issuer_identifier,
                "keys": len(keys),
            },
        )
        if not await store.rotate_trust_keys(conn, body.issuer_identifier, body.jwks):
            raise ZidError("ISSUER_NOT_FOUND", "Not found.", http_status=404)
    return {"issuer_identifier": body.issuer_identifier, "keys": len(keys)}


class RegistrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(min_length=1, max_length=256)
    purpose: str = Field(min_length=1, max_length=128)
    allowed_attributes: list[str] = Field(min_length=1, max_length=50)
    retention_days: int = Field(default=0, ge=0, le=3650)
    valid_to: datetime


@router.get("/relying-party-registrations")
async def list_registrations(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="credential.read", resource="rp_registration:*")
        return {"items": await store.list_registrations(conn)}


@router.post("/relying-party-registrations", status_code=201)
async def register_rp(body: RegistrationIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.trust.manage",
            resource=f"rp_registration:{body.client_id}",
        )
        await require_step_up(
            request,
            conn,
            caller,
            action="authorize-new-high-risk-oauth-scopes-relying-party-trust",
            resource=f"rp_registration:{body.client_id}",
            change={"operation": "REGISTER", "client_id": body.client_id, "purpose": body.purpose},
        )
        rid = await store.create_registration(conn, caller.ctx.tenant_id or "", body.model_dump())
    return {"registration_id": rid, "client_id": body.client_id, "purpose": body.purpose}
