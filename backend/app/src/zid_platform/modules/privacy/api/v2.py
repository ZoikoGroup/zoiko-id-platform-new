"""/v2 privacy control plane (ZID-PRI-004 sections 07, 11, 13, 16 to 24). Extends the /v1 privacy module; it adds no identity stack.

    POST /v2/privacy/purpose-decisions | /v2/privacy/authorization-evaluations   permission to process, default deny (not identity verification)
    POST /v2/verification-sessions/{id}/notices/acknowledgments | GET .../alternatives
    POST /v2/privacy/erasure-jobs | GET /v2/privacy/erasure-jobs | POST .../erasure-jobs:preview (dry run) | GET .../{id} | GET .../{id}/proof | POST .../{id}/tasks/{task}:attest | POST /v2/providers/{id}/deletion-receipts
    POST /v2/privacy/processors/{id}/receipt-key
    POST /v2/privacy/transfer-decisions | GET /v2/privacy/provider-controls/{service_id}?country=
    POST /v2/privacy/subprocessor-changes | POST .../{id}:decide
    POST /v2/privacy/dpia-packages | GET .../{id} | PUT .../{id}/risks | POST .../{id}/gate-signoffs | POST .../{id}/decisions
    GET  /v2/privacy/release-gates/{market}/{product}/{version}
    POST /v2/decisions/{id}/appeals | /v2/decisions/{id}/human-reviews | POST /v2/privacy/reviews/{id}/decisions | GET /v2/privacy/reviews/{id}
    POST /v2/privacy/breaches | GET /v2/privacy/breaches[?open_only=] | GET .../{id} | POST .../{id}/notifications | POST .../{id}/close
    POST /v2/privacy/breaches/{id}/biometric-capture:pause | :release   containment: stop biometric methods in the case's jurisdictions (CR-108)
    GET  /v2/privacy/kpis
    POST /v2/privacy/models | POST /v2/privacy/model-evaluations | GET .../models/{ref}/versions/{v} | POST .../versions/{v}:promote | :rollback | POST .../models/{ref}:observe

Nothing here returns a face, a template, a document or a birth date. Every decision record is append-only and every denial names its reason.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.privacy.application import control as ctl
from zid_platform.modules.privacy.application import model_governance as mg
from zid_platform.modules.privacy.domain import control as rules
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.protected import protected_action

router = APIRouter(tags=["privacy-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
CODE = r"^[A-Z][A-Z0-9_]{1,63}$"
READ, MANAGE, FULFIL, ACTIVITY = (
    "privacy.read",
    "privacy.rights.manage",
    "privacy.rights.fulfill",
    "privacy.processing_activity.manage",
)

_STATUS = {
    "NOT_FOUND": 404,
    "PRIVACY_INVALID_STATE": 409,
    "PRIVACY_APPROVAL_REQUIRED": 403,
    "RELEASE_BLOCKED": 409,
    "NOTIFICATIONS_OUTSTANDING": 409,
    "ALREADY_NOTIFIED": 409,
    "BREACH_CLOSED": 409,
    "BACKUP_BEFORE_SOURCES": 409,
    "SESSION_CANCELED": 409,
    "ALTERNATIVE_NOT_OFFERED": 422,
    "SESSION_NOT_YOURS": 403,
    "CAPTURE_ALREADY_PAUSED_OR_RELEASED": 409,
    "CAPTURE_NOT_PAUSED": 409,
    "RELEASED_BY_WHOEVER_PAUSED": 403,
    "ROLE_ALREADY_SIGNED": 409,
    "SIGNER_ALREADY_SIGNED_ANOTHER_ROLE": 409,
    "REVIEWER_IS_REQUESTER": 403,
    "SIGNER_IS_AUTHOR": 403,
    "RECEIPT_SIGNATURE_INVALID": 403,
    "PROVIDER_HAS_NO_RECEIPT_KEY": 409,
    "PROMOTION_BLOCKED": 409,
    "PRIVACY_FALSE_CONSENT": 409,
    "CONSENT_IS_THE_SUBJECTS_OWN": 403,
    "PRIVACY_VERIFICATION_REQUIRED": 409,
    "ROLLBACK_BLOCKED": 409,
    "MODEL_VERSION_EXISTS": 409,
    "MODEL_STATE_NOT_PROMOTABLE": 409,
}


def _fail(e: PrivacyError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "A signed-in principal is required.", http_status=401)
    return caller.ctx.principal_id


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


async def _run[T](
    request: Request,
    caller: Caller,
    action: str | None,
    resource: str,
    fn: Callable[[AsyncConnection], Awaitable[T]],
) -> T:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            if action:
                await protected_action(conn, caller, action=action, resource=resource)
            return await fn(conn)
    except PrivacyError as e:
        raise _fail(e) from e
    except ValueError as e:  # a malformed id
        raise ZidError("VALIDATION_FAILED", "A reference is not valid.", http_status=422) from e


# ---------------------------------------------------------------------------------------------------------------- purpose decisions


class PrivV2PurposeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_code: str = Field(pattern=CODE)
    purpose_code: str = Field(pattern=CODE)
    data_category: Literal["D0", "D1", "D2", "D3", "D4"]
    subject_ref: str | None = Field(default=None, max_length=100)
    country: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    subregion: str | None = Field(default=None, max_length=10)
    notice_version: str | None = Field(default=None, max_length=100)
    provider_service_ref: str | None = Field(default=None, max_length=200)
    jurisdiction_pack_id: str | None = Field(default=None, max_length=64)
    release_ref: str | None = Field(default=None, max_length=200)
    retention_policy_ref: str | None = Field(default=None, max_length=200)
    necessity_ref: str | None = Field(default=None, max_length=200)


async def _purpose(body: PrivV2PurposeIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.decide_purpose(
            conn, _tenant(caller), caller.ctx.principal_id, activity_code=body.activity_code, purpose_code=body.purpose_code,
            data_category=body.data_category, subject_ref=body.subject_ref, country=body.country, subregion=body.subregion,
            notice_version=body.notice_version, provider_service_ref=body.provider_service_ref, pack_id=body.jurisdiction_pack_id,
            release_ref=body.release_ref, retention_policy_ref=body.retention_policy_ref, necessity_ref=body.necessity_ref,
            collection_rules_override=getattr(request.app.state, "collection_rules", None),
        )  # fmt: skip

    d = await _run(request, caller, READ, "purpose_decision:new", go)
    return {
        **_ids(), "decision_id": f"pdec_{d['decision_id']}", "purpose_id": d["purpose_code"], "allowed": d["allowed"], "reason_codes": d["reason_codes"],
        "alternative_modes": d["alternative_modes"], "effective_policy_version": d["effective_policy_version"], "evaluation_at": d["evaluated_at"],
        "country": d["country"], "subregion": d["subregion"], "notice_version": d["notice_version"], "provider_service_ref": d["provider_service_ref"],
        "limitations": ["This is permission to process, not identity verification.", "No face, template or document is ever returned."],
    }  # fmt: skip


router.add_api_route("/v2/privacy/purpose-decisions", _purpose, methods=["POST"])
router.add_api_route("/v2/privacy/authorization-evaluations", _purpose, methods=["POST"])


# ---------------------------------------------------------------------------------------------------------------- notices and alternatives


class PrivV2AckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    notice_version: str = Field(max_length=100)  # CODE@version
    language: str = Field(pattern=r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?$")
    subject_ref: str | None = Field(default=None, max_length=100)


@router.post("/v2/verification-sessions/{session_ref}/notices/acknowledgments", status_code=201)
async def acknowledge(
    session_ref: str, body: PrivV2AckIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.acknowledge_notice(
            conn,
            _tenant(caller),
            me,
            session_ref=session_ref,
            notice_version=body.notice_version,
            language=body.language,
            subject_ref=body.subject_ref,
        )

    a = await _run(
        request, caller, None, "", go
    )  # the person acknowledges their own notice: no administrator role is involved
    return {
        **_ids(),
        "ack_id": a["ack_id"],
        "session_ref": session_ref,
        "notice": f"{a['notice_code']}@{a['notice_version']}",
        "language": a["language"],
        "acknowledged_at": a["acknowledged_at"],
    }


@router.get("/v2/verification-sessions/{session_ref}/alternatives")
async def alternatives(session_ref: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    _me(caller)

    async def go(conn: AsyncConnection) -> list[dict[str, Any]]:
        return await ctl.acknowledged(conn, session_ref)

    acks = await _run(request, caller, None, "", go)
    return {
        **_ids(), "session_ref": session_ref,
        "alternatives": [{"id": m["id"], "label": m["label"], "biometric": False} for m in ctl.alternatives_config()],
        "notices_acknowledged": [f"{a['notice_code']}@{a['notice_version']}" for a in acks],
        "statement": "Choosing an alternative never counts against you, and a failed camera capture is never reported as fraud.",
    }  # fmt: skip


# ---------------------------------------------------------------------------------------------------------------- erasure


class PrivV2ErasureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(max_length=100)
    scope: Literal["ALL", "PROVIDERS_ONLY", "FIRST_PARTY_ONLY"] = "ALL"
    reason: str = Field(min_length=3, max_length=500)
    legal_basis: str = Field(min_length=3, max_length=200)


@router.post("/v2/privacy/erasure-jobs", status_code=201)
async def open_job(body: PrivV2ErasureIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.open_erasure_job(
            conn,
            _tenant(caller),
            me,
            subject_ref=body.subject_ref,
            scope=body.scope,
            reason=body.reason,
            legal_basis=body.legal_basis,
        )

    return {**_ids(), **await _run(request, caller, FULFIL, "erasure_job:new", go)}


@router.get("/v2/privacy/erasure-jobs")
async def list_jobs(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The tenant's erasure jobs, newest first, with their per-class state (CR-108)."""

    async def go(conn: AsyncConnection) -> list[dict[str, Any]]:
        return await ctl.list_jobs(conn)

    return {**_ids(), "items": await _run(request, caller, READ, "erasure_job:*", go)}


class PrivV2ErasurePreviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_ref: str = Field(max_length=100)
    scope: Literal["ALL", "PROVIDERS_ONLY", "FIRST_PARTY_ONLY"] = "ALL"


@router.post("/v2/privacy/erasure-jobs:preview")
async def preview_job(
    body: PrivV2ErasurePreviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """B05 dry run (CR-108): what an erasure would dispose, hold or send to providers, in deletion order. Changes nothing."""

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.preview_erasure(
            conn, _tenant(caller), subject_ref=body.subject_ref, scope=body.scope
        )

    return {**_ids(), **await _run(request, caller, FULFIL, "erasure_job:preview", go)}


@router.get("/v2/privacy/erasure-jobs/{job_id}")
async def get_job(job_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.job_view(conn, job_id)

    return {**_ids(), **await _run(request, caller, READ, f"erasure_job:{job_id}", go)}


@router.get("/v2/privacy/erasure-jobs/{job_id}/proof")
async def get_proof(job_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.erasure_proof(conn, job_id)

    return {**_ids(), **await _run(request, caller, READ, f"erasure_job:{job_id}", go)}


class PrivV2AttestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=3, max_length=1000)


@router.post("/v2/privacy/erasure-jobs/{job_id}/tasks/{task_id}:attest")
async def attest(
    job_id: str, task_id: str, body: PrivV2AttestIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.acknowledge_task(
            conn, _tenant(caller), me, job_id=job_id, task_id=task_id, statement=body.statement
        )

    return {**_ids(), **await _run(request, caller, FULFIL, f"erasure_job:{job_id}", go)}


class PrivV2ReceiptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    job_id: str
    task_id: str
    deleted_at: str = Field(max_length=40)
    object_count: int = Field(ge=0)
    signature: str = Field(max_length=200)  # base64 Ed25519 over the canonical receipt


@router.post("/v2/providers/{provider_id}/deletion-receipts")
async def deletion_receipt(
    provider_id: str, body: PrivV2ReceiptIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.provider_receipt(conn, _tenant(caller), provider_id=provider_id, task_id=body.task_id, job_id=body.job_id,
                                          deleted_at=body.deleted_at, object_count=body.object_count, signature_b64=body.signature)  # fmt: skip

    return {**_ids(), **await _run(request, caller, MANAGE, f"processor:{provider_id}", go)}


class PrivV2KeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    public_key: str = Field(max_length=100)  # base64 raw Ed25519 public key


@router.post("/v2/privacy/processors/{processor_id}/receipt-key")
async def receipt_key(
    processor_id: str, body: PrivV2KeyIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> None:
        await ctl.register_receipt_key(conn, _tenant(caller), me, processor_id, body.public_key)

    await _run(request, caller, ACTIVITY, f"processor:{processor_id}", go)
    return {**_ids(), "processor_id": processor_id, "receipt_key_registered": True}


# ---------------------------------------------------------------------------------------------------------------- transfers


class PrivV2TransferIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor_id: str = Field(max_length=64)
    purpose_code: str = Field(pattern=CODE)
    data_category: Literal["D0", "D1", "D2", "D3", "D4"]
    origin_country: str = Field(pattern=r"^[A-Z]{2}$")
    destination_country: str = Field(pattern=r"^[A-Z]{2}$")
    remote_access_countries: list[str] = Field(default_factory=list, max_length=20)
    jurisdiction_pack_id: str | None = Field(default=None, max_length=64)


@router.post("/v2/privacy/transfer-decisions")
async def transfer(body: PrivV2TransferIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.decide_transfer(
            conn, _tenant(caller), caller.ctx.principal_id, processor_id=body.processor_id, purpose_code=body.purpose_code, data_category=body.data_category,
            origin_country=body.origin_country, destination_country=body.destination_country, remote_access_countries=body.remote_access_countries,
            pack_id=body.jurisdiction_pack_id,
        )  # fmt: skip

    d = await _run(request, caller, READ, "transfer_decision:new", go)
    return {
        **_ids(), "decision_id": f"tdec_{d['decision_id']}", "allowed": d["allowed"], "reason_codes": d["reason_codes"], "mechanism": d["mechanism"],
        "policy_snapshot": d["policy_snapshot"], "evaluated_at": d["evaluated_at"],
        "limitations": ["A contract is necessary but does not replace technical residency, access and minimization enforcement."],
    }  # fmt: skip


@router.get("/v2/privacy/provider-controls/{service_id}")
async def provider_controls(
    service_id: str,
    request: Request,
    caller: CallerDep,
    country: Annotated[str, Query(pattern=r"^[A-Za-z]{2}$")],
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.provider_controls(conn, service_id, country)

    return {**_ids(), **await _run(request, caller, READ, f"processor:{service_id}", go)}


class PrivV2ChangeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor_id: str = Field(max_length=64)
    kind: Literal[
        "SUBPROCESSOR_ADDED", "SUBPROCESSOR_REMOVED", "LOCATION_ADDED", "LOCATION_REMOVED"
    ]
    detail: str = Field(min_length=2, max_length=500)
    notified_at: datetime


@router.post("/v2/privacy/subprocessor-changes", status_code=201)
async def notify_change(
    body: PrivV2ChangeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.notify_change(
            conn,
            _tenant(caller),
            me,
            processor_id=body.processor_id,
            kind=body.kind,
            detail=body.detail,
            notified_at=body.notified_at,
        )

    return {
        **_ids(),
        **await _run(request, caller, ACTIVITY, f"processor:{body.processor_id}", go),
        "effect": "Egress to this processor is blocked until the change is approved or rejected.",
    }


class PrivV2DecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool


@router.post("/v2/privacy/subprocessor-changes/{change_id}:decide")
async def decide_change(
    change_id: str, body: PrivV2DecideIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.decide_change(conn, _tenant(caller), me, change_id, body.approve)

    return {
        **_ids(),
        **await _run(request, caller, ACTIVITY, f"subprocessor_change:{change_id}", go),
    }


# ---------------------------------------------------------------------------------------------------------------- DPIA packages and release gates


class PrivV2PackageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    market: str = Field(pattern=r"^[A-Za-z]{2}(-[A-Za-z0-9]{1,8})?$")
    product: str = Field(min_length=2, max_length=64)
    version: str = Field(min_length=1, max_length=40)
    controller: str = Field(min_length=2, max_length=200)
    purpose_code: str = Field(pattern=CODE)
    provider_service_ref: str | None = Field(default=None, max_length=200)
    model_version: str | None = Field(default=None, max_length=100)


@router.post("/v2/privacy/dpia-packages", status_code=201)
async def create_package(
    body: PrivV2PackageIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.create_package(conn, _tenant(caller), me, **body.model_dump())

    return {**_ids(), **await _run(request, caller, ACTIVITY, "dpia_package:new", go)}


@router.get("/v2/privacy/dpia-packages")
async def list_packages(request: Request, caller: CallerDep) -> dict[str, Any]:
    """The tenant's DPIA packages with their release state (the workbench list; CR-108)."""

    async def go(conn: AsyncConnection) -> list[dict[str, Any]]:
        return await ctl.list_packages(conn)

    return {**_ids(), "items": await _run(request, caller, READ, "dpia_package:*", go)}


@router.get("/v2/privacy/dpia-packages/{package_id}")
async def get_package(package_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.package_view(conn, package_id)

    return {**_ids(), **await _run(request, caller, READ, f"dpia_package:{package_id}", go)}


class PrivV2RisksIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    risks: list[dict[str, Any]] = Field(min_length=1, max_length=100)
    consultation_ref: str | None = Field(default=None, max_length=200)


@router.put("/v2/privacy/dpia-packages/{package_id}/risks")
async def set_risks(
    package_id: str, body: PrivV2RisksIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.set_risks(
            conn, _tenant(caller), me, package_id, body.risks, body.consultation_ref
        )

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"dpia_package:{package_id}", go)}


class PrivV2GateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gate: Literal["G0", "G1", "G2", "G3", "G4", "G5", "G6", "G7"]
    role: str = Field(pattern=CODE)
    evidence_uri: str = Field(min_length=3, max_length=500)


@router.post("/v2/privacy/dpia-packages/{package_id}/gate-signoffs")
async def sign_gate(
    package_id: str, body: PrivV2GateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.sign_gate(
            conn,
            _tenant(caller),
            me,
            package_id,
            gate=body.gate,
            role=body.role,
            evidence_uri=body.evidence_uri,
        )

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"dpia_package:{package_id}", go)}


class PrivV2PackageDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool
    rationale: str = Field(min_length=3, max_length=2000)
    valid_days: int = Field(default=365, ge=1, le=1095)


@router.post("/v2/privacy/dpia-packages/{package_id}/decisions")
async def decide_package(
    package_id: str, body: PrivV2PackageDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.decide_package(
            conn,
            _tenant(caller),
            me,
            package_id,
            approve=body.approve,
            rationale=body.rationale,
            valid_days=body.valid_days,
        )

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"dpia_package:{package_id}", go)}


@router.get("/v2/privacy/release-gates/{market}/{product}/{version}")
async def release_gate(
    market: str, product: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.release_gate(conn, market, product, version)

    out = await _run(request, caller, READ, "dpia_package:*", go)
    return {
        **_ids(),
        **out,
        "gates": {
            g: {"name": n, "accountable_roles": sorted(r)} for g, (n, r) in rules.GATES.items()
        },
    }


# ---------------------------------------------------------------------------------------------------------------- appeals and human review


class PrivV2ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(min_length=1, max_length=2000)


async def _open(
    kind: str, decision_id: str, body: PrivV2ReviewIn, request: Request, caller: Caller
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.open_review(
            conn,
            _tenant(caller),
            me,
            decision_ref=decision_id,
            kind=kind,
            subject_ref=me,
            statement=body.statement,
        )

    r = await _run(
        request, caller, None, "", go
    )  # a person contests a decision about themselves: no administrator role is involved
    return {**_ids(), "review_id": r["review_id"], "decision_id": decision_id, "kind": kind, "state": r["state"], "due_at": r["due_at"],
            "statement": "A person other than you will review this. Asking has no penalty."}  # fmt: skip


@router.post("/v2/decisions/{decision_id}/appeals", status_code=201)
async def appeal(
    decision_id: str, body: PrivV2ReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _open("APPEAL", decision_id, body, request, caller)


@router.post("/v2/decisions/{decision_id}/human-reviews", status_code=201)
async def human_review(
    decision_id: str, body: PrivV2ReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _open("HUMAN_REVIEW", decision_id, body, request, caller)


class PrivV2ReviewDecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["UPHELD", "OVERTURNED", "CORRECTED"]
    rationale: str = Field(min_length=3, max_length=2000)
    correction_ref: str | None = Field(default=None, max_length=200)


@router.post("/v2/privacy/reviews/{review_id}/decisions")
async def decide_review(
    review_id: str, body: PrivV2ReviewDecisionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.decide_review(
            conn,
            _tenant(caller),
            me,
            review_id,
            outcome=body.outcome,
            rationale=body.rationale,
            correction_ref=body.correction_ref,
        )

    return {**_ids(), **await _run(request, caller, MANAGE, f"decision_review:{review_id}", go)}


@router.get("/v2/privacy/reviews/{review_id}")
async def get_review(review_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await ctl.get_review(conn, review_id)
        if (
            r["requested_by"] != me
        ):  # the requester sees their own; anyone else needs the reader role
            await protected_action(
                conn, caller, action=READ, resource=f"decision_review:{review_id}"
            )
        return r

    return {**_ids(), **await _run(request, caller, None, "", go)}


# ---------------------------------------------------------------------------------------------------------------- breaches


class PrivV2BreachIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary: str = Field(min_length=3, max_length=2000)
    severity: Literal["LOW", "MODERATE", "HIGH", "CRITICAL"]
    jurisdictions: list[str] = Field(min_length=1, max_length=30)
    subjects_estimate: int | None = Field(default=None, ge=0)
    detected_at: datetime
    pause_biometric_capture: bool | None = (
        None  # CR-108: None takes the severity default (HIGH and CRITICAL pause)
    )


class PrivV2CaptureReleaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=10, max_length=1000)


def _config(request: Request) -> dict[str, Any] | None:
    return getattr(
        request.app.state, "breach_clock_config", None
    )  # tests supply counsel-like values; deployments read config/privacy


@router.post("/v2/privacy/breaches", status_code=201)
async def open_breach(body: PrivV2BreachIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.open_breach(conn, _tenant(caller), me, summary=body.summary, severity=body.severity, jurisdictions=body.jurisdictions,
                                     subjects_estimate=body.subjects_estimate, detected_at=body.detected_at, config=_config(request),
                                     pause_biometric_capture=body.pause_biometric_capture)  # fmt: skip

    return {**_ids(), **await _run(request, caller, MANAGE, "breach_case:new", go)}


@router.post("/v2/privacy/breaches/{breach_id}/biometric-capture:pause")
async def pause_capture(breach_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Containment (WF-04 B09, CR-108): no biometric method runs for this tenant in the case's jurisdictions until it is released or closed."""
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.pause_capture(conn, _tenant(caller), me, breach_id, _config(request))

    return {**_ids(), **await _run(request, caller, MANAGE, f"breach_case:{breach_id}", go)}


@router.post("/v2/privacy/breaches/{breach_id}/biometric-capture:release")
async def release_capture(
    breach_id: str, body: PrivV2CaptureReleaseIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """Lift the containment: by someone other than whoever paused it, with a reason (CR-108)."""
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.release_capture(
            conn, _tenant(caller), me, breach_id, reason=body.reason, config=_config(request)
        )

    return {**_ids(), **await _run(request, caller, MANAGE, f"breach_case:{breach_id}", go)}


@router.get("/v2/privacy/breaches")
async def list_breaches(
    request: Request, caller: CallerDep, open_only: bool = False
) -> dict[str, Any]:
    """The tenant's breach cases with clocks and capture state (CR-108)."""

    async def go(conn: AsyncConnection) -> list[dict[str, Any]]:
        return await ctl.list_breaches(conn, open_only=open_only, config=_config(request))

    return {**_ids(), "items": await _run(request, caller, READ, "breach_case:*", go)}


@router.get("/v2/privacy/breaches/{breach_id}")
async def get_breach(breach_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.get_breach(conn, breach_id, _config(request))

    return {**_ids(), **await _run(request, caller, READ, f"breach_case:{breach_id}", go)}


class PrivV2NotifiedIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    jurisdiction: str = Field(pattern=r"^[A-Za-z]{2}$")
    notified_at: datetime
    reference: str = Field(min_length=2, max_length=200)


@router.post("/v2/privacy/breaches/{breach_id}/notifications")
async def notified(
    breach_id: str, body: PrivV2NotifiedIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.record_notification(
            conn,
            _tenant(caller),
            me,
            breach_id,
            jurisdiction=body.jurisdiction,
            notified_at=body.notified_at,
            reference=body.reference,
            config=_config(request),
        )

    return {**_ids(), **await _run(request, caller, MANAGE, f"breach_case:{breach_id}", go)}


@router.post("/v2/privacy/breaches/{breach_id}/close")
async def close_breach(breach_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.close_breach(conn, _tenant(caller), me, breach_id, _config(request))

    return {**_ids(), **await _run(request, caller, MANAGE, f"breach_case:{breach_id}", go)}


# ---------------------------------------------------------------------------------------------------------------- KPIs


@router.get("/v2/privacy/kpis")
async def kpis(
    request: Request, caller: CallerDep, days: Annotated[int, Query(ge=1, le=366)] = 30
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await ctl.kpis(conn, days)

    return {
        **_ids(),
        "as_of": datetime.now(UTC).isoformat(),
        **await _run(request, caller, READ, "privacy_kpi:*", go),
    }


# ---------------------------------------------------------------------------------------------------------------- model governance


class PrivV2ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,62}$")
    version: str = Field(min_length=1, max_length=64)
    kind: Literal["face_match", "pad", "fraud", "document"]
    provider_service_ref: str | None = Field(default=None, max_length=200)
    artifact_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    live_features: list[str] = Field(default_factory=list, max_length=200)


@router.post("/v2/privacy/models", status_code=201)
async def register_model(
    body: PrivV2ModelIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.register(conn, _tenant(caller), me, **body.model_dump())

    return {**_ids(), **await _run(request, caller, ACTIVITY, "model:new", go)}


class PrivV2EvaluationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_ref: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{1,62}$")
    version: str = Field(min_length=1, max_length=64)
    evaluator_ref: str = Field(min_length=1, max_length=120)
    independent: bool
    cohorts: int = Field(ge=0, le=1000)
    metrics: dict[str, Any] = Field(default_factory=dict)
    accessible_alternative_confirmed: bool = False
    audit_attributes: list[str] = Field(default_factory=list, max_length=50)
    report_ref: str | None = Field(default=None, max_length=300)


@router.post("/v2/privacy/model-evaluations", status_code=201)
async def record_model_evaluation(
    body: PrivV2EvaluationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.record_evaluation(conn, _tenant(caller), me, **body.model_dump())

    return {**_ids(), **await _run(request, caller, ACTIVITY, "model:new", go)}


@router.get("/v2/privacy/models/{model_ref}/versions/{version}")
async def get_model(
    model_ref: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.get(conn, model_ref, version)

    return {**_ids(), **await _run(request, caller, READ, f"model:{model_ref}", go)}


@router.post("/v2/privacy/models/{model_ref}/versions/{version}:promote")
async def promote_model(
    model_ref: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.promote(conn, _tenant(caller), me, model_ref, version)

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"model:{model_ref}", go)}


@router.post("/v2/privacy/models/{model_ref}/versions/{version}:rollback")
async def rollback_model(
    model_ref: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.rollback(conn, _tenant(caller), me, model_ref, version)

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"model:{model_ref}", go)}


class PrivV2ObserveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    seen_version: str = Field(min_length=1, max_length=64)


@router.post("/v2/privacy/models/{model_ref}:observe")
async def observe_model(
    model_ref: str, body: PrivV2ObserveIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await mg.observe(conn, _tenant(caller), me, model_ref, body.seen_version)

    return {**_ids(), **await _run(request, caller, ACTIVITY, f"model:{model_ref}", go)}
