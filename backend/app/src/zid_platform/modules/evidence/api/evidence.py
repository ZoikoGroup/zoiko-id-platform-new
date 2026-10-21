"""Evidence, audit and provenance API (EVIDENCE-001 §21, §23; UX-001 §16; CR-011).

    GET  /v1/evidence/keys                                 public evidence verification keys
    GET  /v1/evidence/receipts?action=&outcome=&evidence_class=&before=   evidence.read (R1)
    GET  /v1/evidence/receipts/{id}                        evidence.read
    POST /v1/evidence/receipts/{id}:verify                 evidence.verify (R1)
    GET  /v1/evidence/receipts/{id}/proof                  evidence.verify
    POST /v1/evidence/receipts/{id}:replay                 evidence.replay (R2/E2)
    POST /v1/evidence/receipts/{id}:annotate               evidence.annotate (R2/E2)
    POST /v1/evidence/packages                             evidence.export (R3/E3)
    GET  /v1/evidence/audit-events?event_type=&before=     evidence.read
    POST /v1/evidence/partition:verify                     evidence.verify
    GET  /v1/evidence/legal-holds?status=                  evidence.legal_hold.manage (R3/E3)
    POST /v1/evidence/legal-holds                          evidence.legal_hold.manage + an
                                                           APPROVED second-administrator approval
    POST /v1/evidence/legal-holds/{id}:release             evidence.legal_hold.manage
    POST /v1/evidence/handles:resolve                      evidence.identity.resolve (R3/E3)

Default deny and tenant-scoped (RLS): a tenant never sees another tenant's evidence, and the
platform-security partition is not reachable from here at all. Every call is itself a PDP
decision, so reading, verifying, replaying or exporting evidence leaves its own receipt.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.evidence.application import ledger, receipts, replay, vault
from zid_platform.modules.evidence.application.runtime import current_runtime
from zid_platform.modules.evidence.domain.ledger import LedgerError, Partition
from zid_platform.modules.evidence.domain.receipts import ANNOTATION_KINDS, EXPORT_MODES
from zid_platform.modules.evidence.infrastructure import receipt_store, vault_store
from zid_platform.protected import approved_by_second_admin, protected_action

router = APIRouter(prefix="/v1/evidence", tags=["evidence"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
HOLD_ACTION = "evidence.legal_hold.manage"

_STATUS = {
    "NOT_FOUND": 404,
    "EVIDENCE_ACCESS_DENIED": 403,
    "EVIDENCE_LEGAL_HOLD": 409,
    "EVIDENCE_RAW_OBJECT_ERASED": 410,
    "EVIDENCE_RETENTION_EXPIRED": 410,
    "EVIDENCE_CHECKPOINT_MISSING": 409,
    "EVIDENCE_KEY_UNAVAILABLE": 503,
    "EVIDENCE_TIME_UNTRUSTED": 503,
}


def http(e: LedgerError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def receipt_summary(r: Any) -> dict[str, Any]:
    return {
        "receipt_id": str(r.receipt_id),
        "decision_id": str(r.decision_id),
        "evidence_class": r.evidence_class,
        "decision_type": r.decision_type,
        "outcome": r.outcome,
        "reason_codes": list(r.reason_codes),
        "actor_handle": r.actor_handle,
        "action": r.action,
        "resource_class": r.resource_class,
        "risk_class": r.risk_class,
        "policy_release_id": str(r.policy_release_id) if r.policy_release_id else None,
        "receipt_hash": r.receipt_hash,
        "effective_at": _iso(r.effective_at),
        "created_at": _iso(r.created_at),
    }


@router.get("/keys")
async def keys() -> dict[str, Any]:
    """Every public key that verifies Zoiko evidence, historic ones included (DEC-F2 §5)."""
    return {"keys": current_runtime().trusted_keys()}


@router.get("/receipts")
async def list_receipts(
    request: Request,
    caller: CallerDep,
    action: str | None = None,
    outcome: str | None = None,
    evidence_class: Annotated[str | None, Query(pattern="^E[1-3]$")] = None,
    actor_handle: str | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.read", resource="decision_receipt:*")
        rows = await receipt_store.list_receipts(
            conn,
            action=action,
            outcome=outcome,
            evidence_class=evidence_class,
            actor_handle=actor_handle,
            before=before,
            limit=limit,
        )
    return {"items": [receipt_summary(r) for r in rows]}


@router.get("/receipts/{receipt_id}")
async def get_receipt(receipt_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    rid = str(receipt_id)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.read", resource=f"decision_receipt:{rid}")
        row = await receipt_store.load_receipt(conn, rid)
        if row is None:
            raise ZidError("NOT_FOUND", "No such receipt.", http_status=404)
        notes = await receipt_store.annotations(conn, rid)
    return {
        **receipt_summary(row),
        "receipt": dict(row.body),
        "annotations": [
            {
                "annotation_id": str(a.annotation_id),
                "kind": a.kind,
                "body": dict(a.body),
                "record_hash": a.record_hash,
                "created_at": _iso(a.created_at),
            }
            for a in notes
        ],
    }


@router.post("/receipts/{receipt_id}:verify")
async def verify(receipt_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    rid = str(receipt_id)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.verify", resource=f"decision_receipt:{rid}")
            result = await replay.verify_receipt(conn, rid)
    except LedgerError as e:
        raise http(e) from e
    return result.to_dict()


@router.get("/receipts/{receipt_id}/proof")
async def proof(receipt_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    rid = str(receipt_id)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.verify", resource=f"decision_receipt:{rid}")
            p = await ledger.inclusion_proof(conn, rid, record_type="decision_receipt")
    except LedgerError as e:
        raise http(e) from e
    return p.to_dict()


@router.post("/receipts/{receipt_id}:replay")
async def run_replay(receipt_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    rid = str(receipt_id)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.replay", resource=f"decision_receipt:{rid}")
            return await replay.replay_receipt(conn, _tenant(caller), rid)
    except LedgerError as e:
        raise http(e) from e


class AnnotateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern="^(" + "|".join(sorted(ANNOTATION_KINDS)) + ")$")
    reason_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    note_commitment: str | None = Field(default=None, pattern=r"^sha-(256|384|512):[0-9a-f]+$")
    superseded_by: uuid.UUID | None = None


@router.post("/receipts/{receipt_id}:annotate", status_code=201)
async def annotate(
    receipt_id: uuid.UUID, body: AnnotateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    rid = str(receipt_id)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.annotate", resource=f"decision_receipt:{rid}")
            annotation_id = await receipts.annotate_receipt(
                conn,
                _tenant(caller),
                rid,
                kind=body.kind,
                author_principal_id=caller.ctx.principal_id or "",
                reason_code=body.reason_code,
                note_commitment=body.note_commitment,
                superseded_by=str(body.superseded_by) if body.superseded_by else None,
            )
    except LedgerError as e:
        raise http(e) from e
    return {"annotation_id": annotation_id, "receipt_id": rid, "kind": body.kind}


class PackageBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    receipt_id: uuid.UUID
    mode: str = Field(pattern="^(" + "|".join(sorted(EXPORT_MODES)) + ")$")
    purpose: str = Field(min_length=3, max_length=200)
    case_ref: str = Field(min_length=1, max_length=128)


@router.post("/packages", status_code=201)
async def export(body: PackageBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    rid = str(body.receipt_id)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.export", resource=f"decision_receipt:{rid}")
            return await replay.export_package(
                conn,
                _tenant(caller),
                rid,
                mode=body.mode,
                purpose=body.purpose,
                case_ref=body.case_ref,
                custodian_principal_id=caller.ctx.principal_id or "",
            )
    except LedgerError as e:
        raise http(e) from e


@router.get("/audit-events")
async def audit_events(
    request: Request,
    caller: CallerDep,
    event_type: str | None = None,
    before: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.read", resource="audit_record:*")
        rows = await receipt_store.list_audit(conn, event_type=event_type, before=before, limit=limit)
    return {
        "items": [
            {
                "audit_id": str(r.audit_id),
                "event_type": r.event_type,
                "actor_type": r.actor_type,
                "actor_handle": r.actor_handle,
                "target_type": r.target_type,
                "target_ref": r.target_ref,
                "action": r.action,
                "outcome": r.outcome,
                "reason_code": r.reason_code,
                "evidence_class": r.evidence_class,
                "record_hash": r.record_hash,
                "occurred_at": _iso(r.occurred_at),
                "created_at": _iso(r.created_at),
            }
            for r in rows
        ]
    }


@router.post("/partition:verify")
async def verify_partition(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Recompute the tenant's whole hash chain and every checkpoint; a failure is an incident."""
    tenant = _tenant(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.verify", resource="evidence_partition:*")
        report = await ledger.verify_partition(
            conn, Partition(tenant, current_runtime().cell_id).id, keys=current_runtime().trusted_keys()
        )
    return report.to_dict()


def hold_view(h: vault_store.Hold, now: datetime) -> dict[str, Any]:
    return {
        "legal_hold_id": h.legal_hold_id,
        "basis": h.basis,
        "reference": h.reference,
        "requested_by": h.requested_by,
        "approved_by": h.approved_by,
        "subject_count": len(h.subject_refs),
        "object_types": list(h.object_types),
        "effective_at": _iso(h.effective_at),
        "review_at": _iso(h.review_at),
        "review_overdue": h.status == "ACTIVE" and h.review_at <= now,
        "expires_at": _iso(h.expires_at),
        "status": "EXPIRED" if h.status == "ACTIVE" and not h.active(now) and h.expires_at and h.expires_at <= now else h.status,
        "released_at": _iso(h.released_at),
    }


@router.get("/legal-holds")
async def list_holds(
    request: Request,
    caller: CallerDep,
    status: Annotated[str | None, Query(pattern="^(ACTIVE|RELEASED|EXPIRED)$")] = None,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=HOLD_ACTION, resource="legal_hold:*")
        items = await vault_store.holds(conn, status=status)
    return {"items": [hold_view(h, now) for h in items]}


class HoldBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: str = Field(min_length=3, max_length=200)
    reference: str = Field(min_length=1, max_length=128)
    subject_principal_ids: list[uuid.UUID] = Field(default_factory=list, max_length=1000)
    object_types: list[Annotated[str, Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")]] = Field(
        default_factory=list, max_length=50
    )
    review_at: datetime
    expires_at: datetime | None = None
    approval_request_id: uuid.UUID

    def change(self) -> str:
        subjects = ",".join(sorted(str(s) for s in self.subject_principal_ids))
        return f"{self.reference}|{subjects}|{','.join(sorted(self.object_types))}"


@router.post("/legal-holds", status_code=201)
async def create_hold(body: HoldBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """A hold needs a second administrator's approval of exactly this scope (two-person rule)."""
    now = datetime.now(UTC)
    tenant = _tenant(caller)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            approver = await approved_by_second_admin(
                conn,
                caller,
                action=HOLD_ACTION,
                resource="legal_hold:*",
                change=body.change(),
                approval_request_id=str(body.approval_request_id),
                now=now,
            )
            hold = await vault.create_hold(
                conn,
                tenant,
                basis=body.basis,
                reference=body.reference,
                requested_by=caller.ctx.principal_id or "",
                approved_by=approver,
                subject_principal_ids=[str(s) for s in body.subject_principal_ids],
                object_types=body.object_types,
                review_at=body.review_at,
                expires_at=body.expires_at,
                now=now,
            )
    except LedgerError as e:
        raise http(e) from e
    return hold_view(hold, now)


class ReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)


@router.post("/legal-holds/{hold_id}:release")
async def release_hold(
    hold_id: uuid.UUID, body: ReleaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action=HOLD_ACTION, resource=f"legal_hold:{hold_id}")
            hold = await vault.release_hold(
                conn, _tenant(caller), str(hold_id), by=caller.ctx.principal_id or "", reason=body.reason, now=now
            )
    except LedgerError as e:
        raise http(e) from e
    except LookupError as e:
        raise ZidError("CONCURRENT_MODIFICATION", "Retry.", http_status=409, retryable=True) from e
    return hold_view(hold, now)


class ResolveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    handle: str = Field(pattern=r"^ph1\.[0-9]+\.[A-Za-z0-9_-]+$")
    purpose: str = Field(min_length=3, max_length=200)
    case_ref: str = Field(min_length=1, max_length=128)


@router.post("/handles:resolve")
async def resolve(body: ResolveBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Re-identification (R3/E3): case-bound, recorded in chain of custody (EVIDENCE §13, §18)."""
    tenant = _tenant(caller)
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            await protected_action(conn, caller, action="evidence.identity.resolve", resource="evidence_handle:*")
            principal_id = await vault.resolve_handle(conn, tenant, body.handle)
            await replay.record_custody(
                conn,
                tenant,
                case_ref=body.case_ref,
                item_id=body.handle,
                custodian_principal_id=caller.ctx.principal_id or "",
                action="ACCESSED",
                purpose=body.purpose,
            )
    except LedgerError as e:
        raise http(e) from e
    return {"handle": body.handle, "principal_id": principal_id}
