"""WF08 evidence services (ZID-CP-WF08 v2.0 E02, E06, E09; CR-170): authority history, status lists, disclosure-tier exports.

POST /v1/evidence/authority-snapshots                       record who held which authority at a decision (evidence.annotate), once per decision
GET  /v1/evidence/authority-snapshots/{decision_ref}        read it back, integrity re-checked (evidence.read)
POST /v1/evidence/status-lists/{purpose}/subjects           give a credential or agent its random position (evidence.annotate)
POST /v1/evidence/status-lists/{purpose}/subjects/{ref}:revoke | :reinstate     a recorded change, never an edit
POST /v1/evidence/status-lists/{purpose}:publish            sign and publish the list
GET  /v1/status-lists/{list_id}                             PUBLIC: the newest signed list; it cannot be used to look a credential up
POST /v1/evidence/export-requests                           ask for a disclosure tier with a named recipient (evidence.export)
GET  /v1/evidence/export-requests
POST /v1/evidence/export-requests/{id}:approve | :refuse    a second person decides; never the person who asked
POST /v1/evidence/export-requests/{id}:issue                build the package for the tier and sign the manifest
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.application import wf08_services as svc
from zid_platform.modules.evidence.infrastructure import wf08_store as store
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])
public_router = APIRouter(tags=["evidence"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Purpose = Annotated[str, Path(pattern="^(revocation|suspension)$")]


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _who(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


class EdgeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    edge_type: str = Field(max_length=32)
    edge_id: str = Field(max_length=128)
    version: int = Field(ge=0)
    subject: str = Field(max_length=128)
    state: str = Field(max_length=32)
    issuer: str = Field(max_length=128)
    obtained_at: datetime
    effective_from: datetime
    effective_until: datetime | None = None
    evidence_digest: str = Field(max_length=140)


class AuthoritySnapshotBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_ref: str = Field(min_length=1, max_length=128)
    subject_ref: str = Field(min_length=1, max_length=128)
    decision_at: datetime
    edges: list[EdgeBody] = Field(max_length=200)


@router.post("/authority-snapshots", status_code=201)
async def record_snapshot(
    body: AuthoritySnapshotBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    if body.decision_at.tzinfo is None:
        raise ZidError("EVIDENCE_SCHEMA_INVALID", "decision_at needs a time zone", http_status=422)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="evidence.annotate",
            resource=f"authority_snapshot:{body.decision_ref}",
        )
        return await svc.record_authority_snapshot(
            conn,
            _tenant(caller),
            decision_ref=body.decision_ref,
            subject_ref=body.subject_ref,
            decision_at=body.decision_at,
            edges=[e.model_dump() for e in body.edges],
            created_by=_who(caller) or None,
        )


@router.get("/authority-snapshots/{decision_ref}")
async def read_snapshot(
    decision_ref: Annotated[str, Path(min_length=1, max_length=128)],
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.read", resource=f"authority_snapshot:{decision_ref}"
        )
        row = await store.load_snapshot(conn, decision_ref)
        if row is None:
            raise ZidError(
                "NOT_FOUND", "no authority history was recorded for that decision", http_status=404
            )
        return svc.snapshot_view(row)


class SubjectBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(min_length=1, max_length=128)


class ChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=300)


@router.post("/status-lists/{purpose}/subjects", status_code=201)
async def allocate(
    purpose: Purpose, body: SubjectBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.annotate", resource=f"status_list:{purpose}"
        )
        return await svc.allocate_slot(conn, _tenant(caller), purpose, body.subject_ref)


@router.post("/status-lists/{purpose}/subjects/{subject_ref}:revoke")
async def revoke(
    purpose: Purpose,
    subject_ref: Annotated[str, Path(max_length=128)],
    body: ChangeBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.annotate", resource=f"status_list:{purpose}"
        )
        return await svc.change_status(
            conn,
            _tenant(caller),
            purpose,
            subject_ref,
            revoke=True,
            reason=body.reason,
            by=_who(caller) or None,
        )


@router.post("/status-lists/{purpose}/subjects/{subject_ref}:reinstate")
async def reinstate(
    purpose: Purpose,
    subject_ref: Annotated[str, Path(max_length=128)],
    body: ChangeBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.annotate", resource=f"status_list:{purpose}"
        )
        return await svc.change_status(
            conn,
            _tenant(caller),
            purpose,
            subject_ref,
            revoke=False,
            reason=body.reason,
            by=_who(caller) or None,
        )


@router.post("/status-lists/{purpose}:publish", status_code=201)
async def publish(purpose: Purpose, request: Request, caller: CallerDep) -> dict[str, Any]:
    issuer = str(getattr(getattr(request.app.state, "token_settings", None), "issuer", "zoiko-id"))
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.annotate", resource=f"status_list:{purpose}"
        )
        return await svc.publish_list(
            conn,
            _tenant(caller),
            purpose,
            issuer=issuer,
            environment=str(getattr(request.app.state, "environment", "unknown")),
        )


@public_router.get("/v1/status-lists/{list_id}")
async def public_list(list_id: uuid.UUID, request: Request, response: Response) -> dict[str, Any]:
    """The newest signed list for anyone. It is the same bytes for every caller and takes no subject, so asking for it reveals nothing about which credential is being checked."""
    async with request_engine(request).begin() as conn:
        jws = await store.public_publication(conn, list_id)
    if jws is None:
        raise ZidError("NOT_FOUND", "no such list", http_status=404)
    response.headers["Cache-Control"] = "public, max-age=60"
    return {
        "list": jws,
        "typ": "zid-status-list+jwt",
        "verify_with": "keys from the signed trust bundle (GET /v1/trust/issuer); look your own position up locally",
    }


class RecipientBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    kind: str = Field(max_length=32)
    contact: str = Field(min_length=1, max_length=200)


class ExportRequestBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: uuid.UUID
    tier: Literal["PUBLIC_MINIMAL", "CUSTOMER_EVIDENCE", "AUDITOR_DOSSIER"]
    recipient: RecipientBody | None = None
    purpose: str = Field(default="", max_length=300)
    legal_basis: str | None = Field(default=None, max_length=40)
    retention_days: int = Field(default=30, ge=1, le=365)
    recipient_country: str | None = Field(default=None, max_length=2)


class DecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str | None = Field(default=None, max_length=300)


@router.post("/export-requests", status_code=201)
async def request_export(
    body: ExportRequestBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.export", resource=f"decision_receipt:{body.receipt_id}"
        )
        return await svc.request_export(
            conn, _tenant(caller), receipt_id=str(body.receipt_id), tier=body.tier, recipient=body.recipient.model_dump() if body.recipient else None, purpose=body.purpose,
            legal_basis=body.legal_basis, retention_days=body.retention_days, recipient_country=body.recipient_country, tenant_country=str(getattr(request.app.state, "home_country", "")),
            requested_by=_who(caller),
        )  # fmt: skip


@router.get("/export-requests")
async def list_requests(request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.read", resource="evidence_export:requests"
        )
        return {"items": [svc.row_view(r) for r in await store.list_export_requests(conn)]}


@router.post("/export-requests/{request_id}:approve")
async def approve(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.export", resource=f"evidence_export:{request_id}"
        )
        return await svc.decide_export(
            conn, str(request_id), approve=True, decided_by=_who(caller), reason=body.reason
        )


@router.post("/export-requests/{request_id}:refuse")
async def refuse(
    request_id: uuid.UUID, body: DecisionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.export", resource=f"evidence_export:{request_id}"
        )
        return await svc.decide_export(
            conn, str(request_id), approve=False, decided_by=_who(caller), reason=body.reason
        )


@router.post("/export-requests/{request_id}:issue", status_code=201)
async def issue(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="evidence.export", resource=f"evidence_export:{request_id}"
        )
        gate = getattr(request.app.state, "plan_feature_gate", None)
        if gate is not None:
            await gate(conn, "advanced.advanced-evidence-export-and-analytics")
        return await svc.issue_export(
            conn, _tenant(caller), str(request_id), issued_by=_who(caller)
        )
