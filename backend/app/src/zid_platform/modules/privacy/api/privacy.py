"""Privacy and data-rights API (ZID-PRIV-001 §27; UX-001 §17; CR-013).

Subject (the authenticated caller acts for themselves; no administrator role involved):
    GET  /v1/privacy/purposes                              active purposes
    GET  /v1/privacy/consents | POST /v1/privacy/consents | POST /v1/privacy/consents/{id}:withdraw
    GET  /v1/privacy/preferences | PUT /v1/privacy/preferences/{type}
    POST /v1/privacy/preferences:signal                    Sec-GPC: 1 (no verification, PRIV-21)
    POST /v1/privacy/rights-requests                       a request about the caller
    GET  /v1/privacy/rights-requests/mine | GET /v1/privacy/rights-requests/{id}
    GET  /v1/privacy/rights-requests/{id}/package          the caller's own export, fresh auth
Administration (PDP-authorized catalogued actions; two-person changes need an approval):
    GET/POST /v1/privacy/admin/rights-requests             privacy.rights.manage (R2)
    POST .../admin/rights-requests/{id}:verify | :close    privacy.rights.manage
    POST .../admin/rights-requests/{id}:fulfill            privacy.rights.fulfill (R3/E3)
    POST .../admin/rights-requests/{id}/items/{item}:acknowledge   privacy.rights.fulfill
    GET  /v1/privacy/admin/data-map/{subject_id}           privacy.rights.manage
    GET/POST /v1/privacy/admin/purposes, POST .../{id}:activate   privacy.processing_activity.manage
    GET/POST /v1/privacy/admin/processing-activities, POST .../{id}:activate
    POST .../admin/processing-activities/{id}/role-assignments    privacy.role_assignment.manage
    GET  /v1/privacy/admin/role-map | /admin/release-check privacy.read
    GET/POST /v1/privacy/admin/retention-policies          privacy.retention.manage
    POST /v1/privacy/admin/restrictions/{id}:lift          privacy.rights.manage
    GET  /v1/privacy/jurisdiction-packs                    pack ids, versions, deployability
    GET/POST /v1/privacy/admin/notices                     privacy.processing_activity.manage (+ approval)
    GET/POST /v1/privacy/admin/processors, POST .../{id}:approve|:suspend   (approver != registrant)
    GET/POST /v1/privacy/admin/automated-decision-profiles privacy.processing_activity.manage
Legal holds live in the evidence API (/v1/evidence/legal-holds): one hold model for both.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.privacy.application import registry, service
from zid_platform.modules.privacy.domain.model import PrivacyError
from zid_platform.modules.privacy.infrastructure import store
from zid_platform.modules.privacy.infrastructure.packs import packs
from zid_platform.protected import approved_by_second_admin, protected_action

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
CODE = r"^[A-Z][A-Z0-9_]{1,63}$"

_STATUS = {
    "NOT_FOUND": 404,
    "PRIVACY_VERIFICATION_REQUIRED": 403,
    "PRIVACY_APPROVAL_REQUIRED": 403,
    "PRIVACY_PROCESSOR_RESIDENCY": 409,
    "PRIVACY_INVALID_STATE": 409,
    "PRIVACY_SOURCE_AUTHORITATIVE": 409,
    "PRIVACY_RATE_LIMITED": 429,
    "PRIVACY_CONSENT_TERMINAL": 409,
    "PRIVACY_ROLE_ASSIGNMENT_MISSING": 409,
    "PRIVACY_DPIA_REQUIRED": 409,
}


def http(e: PrivacyError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError("UNAUTHENTICATED", "A signed-in subject is required.", http_status=401)
    return caller.ctx.principal_id


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _age(caller: Caller) -> timedelta | None:
    return datetime.now(UTC) - caller.session_auth_time if caller.session_auth_time else None


def _plain(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, list):
        return [_plain(x) for x in v]
    return v


def _rows(items: Any) -> list[dict[str, Any]]:
    return [{k: _plain(v) for k, v in dict(r._mapping).items()} for r in items]


async def _run[T](
    request: Request, caller: Caller, fn: Callable[[AsyncConnection], Awaitable[T]]
) -> T:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn)
    except PrivacyError as e:
        raise http(e) from e
    except LookupError as e:
        raise ZidError("CONCURRENT_MODIFICATION", "Retry.", http_status=409, retryable=True) from e


# --------------------------------------------------------------------------------- subject side


@router.get("/jurisdiction-packs")
async def jurisdiction_packs() -> dict[str, Any]:
    return {
        "items": [
            {
                "pack_id": p.pack_id,
                "version": p.version,
                "framework": p.framework,
                "deployable": p.deployable,
                "approval_ref": p.approval_ref,
                "deadlines_configured": p.deadlines is not None,
            }
            for p in packs().values()
        ]
    }


@router.get("/purposes")
async def purposes(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        rows = await store.list_purposes(conn, status="ACTIVE")
        return {
            "items": [
                {
                    "purpose_code": r.purpose_code,
                    "purpose_version": r.purpose_version,
                    "description": r.description,
                    "data_categories": list(r.allowed_data_categories),
                    "recipient_classes": list(r.allowed_recipient_classes),
                }
                for r in rows
            ]
        }

    return await _run(request, caller, go)


@router.get("/consent-options")
async def consent_options(request: Request, caller: CallerDep) -> dict[str, Any]:
    """What the signed-in person can consent to, each with the exact notice to cite when giving consent."""
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return {"items": await service.consent_options(conn, me)}

    return await _run(request, caller, go)


class ConsentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_code: str = Field(pattern=CODE)
    purpose_code: str = Field(pattern=CODE)
    notice_version: str = Field(min_length=1, max_length=64)
    capture_method: str = Field(pattern="^(WEB|MOBILE|API|PAPER_VERIFIED|OTHER)$")
    affirmative_action: str = Field(min_length=3, max_length=128)
    expires_at: datetime | None = None


@router.post("/consents", status_code=201)
async def give_consent(body: ConsentBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        cid = await service.give_consent(
            conn, _tenant(caller), subject_principal_id=me, **body.model_dump()
        )
        return {"consent_id": cid, "status": "ACTIVE"}

    return await _run(request, caller, go)


@router.get("/consents")
async def my_consents(request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return {"items": _rows(await store.consents_of(conn, me))}

    return await _run(request, caller, go)


class WithdrawBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="withdrawn by subject", max_length=200)


@router.post("/consents/{consent_id}:withdraw")
async def withdraw(
    consent_id: uuid.UUID, body: WithdrawBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.withdraw_consent(
            conn, _tenant(caller), str(consent_id), subject_principal_id=me, reason=body.reason
        )
        return {"consent_id": str(r.consent_id), "status": r.status}

    return await _run(request, caller, go)


@router.get("/preferences")
async def my_preferences(request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return {"items": _rows(await store.preferences_of(conn, me))}

    return await _run(request, caller, go)


class PreferenceBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: bool


@router.put("/preferences/{preference_type}")
async def put_preference(
    preference_type: str, body: PreferenceBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.set_preference(
            conn, _tenant(caller), me, preference_type, body.value, source="USER"
        )
        return {"preference_type": r.preference_type, "value": r.value, "revision": r.revision}

    return await _run(request, caller, go)


@router.post("/preferences:signal")
async def preference_signal(request: Request, caller: CallerDep) -> dict[str, Any]:
    """Global Privacy Control: a valid `Sec-GPC: 1` opts out of sale/sharing (PRIV-21)."""
    me = _me(caller)
    if request.headers.get("sec-gpc") != "1":
        return {"applied": False}

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await service.set_preference(
            conn, _tenant(caller), me, "OPT_OUT_SALE_SHARING", True, source="GPC_SIGNAL"
        )
        return {"applied": True, "preference_type": "OPT_OUT_SALE_SHARING"}

    return await _run(request, caller, go)


class RightsBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_type: str = Field(pattern=CODE)
    jurisdiction_pack_id: str = Field(min_length=1, max_length=64)
    scope: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.post("/rights-requests", status_code=201)
async def open_request(body: RightsBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.open_request(
            conn,
            _tenant(caller),
            request_type=body.request_type,
            subject_principal_id=me,
            requester_principal_id=me,
            jurisdiction_pack_id=body.jurisdiction_pack_id,
            authn_level=caller.authn.level,
            auth_age=_age(caller),
            scope=body.scope,
        )
        return service.request_view(r)

    return await _run(request, caller, go)


@router.get("/rights-requests/mine")
async def my_requests(request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        rows = await store.list_requests(conn, status=None, subject=me, limit=100)
        return {"items": [service.request_view(r) for r in rows]}

    return await _run(request, caller, go)


@router.get("/rights-requests/{request_id}")
async def get_request(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await store.load_request(conn, str(request_id))
        if r is None:
            raise PrivacyError("NOT_FOUND", "unknown rights request")
        if str(r.subject_principal_id) != me:
            await protected_action(
                conn,
                caller,
                action="privacy.rights.manage",
                resource=f"rights_request:{request_id}",
            )
        return service.request_view(r)

    return await _run(request, caller, go)


@router.get("/rights-requests/{request_id}/package")
async def package(request_id: uuid.UUID, request: Request, caller: CallerDep) -> Response:
    """Delivery needs a fresh strong authentication (§15 "step-up authenticated delivery")."""
    me = _me(caller)
    age = _age(caller)
    if caller.authn.level < 2 or age is None or age > timedelta(minutes=15):
        raise ZidError(
            "STEP_UP_REQUIRED",
            "Sign in again to download your data.",
            http_status=403,
            headers={"WWW-Authenticate": 'Bearer error="insufficient_user_authentication"'},
        )

    async def go(conn: AsyncConnection) -> bytes:
        return await service.read_package(
            conn, _tenant(caller), str(request_id), subject_principal_id=me
        )

    content = await _run(request, caller, go)
    return Response(
        content=content,
        media_type="application/json",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": "attachment; filename=zoiko-data.json",
        },
    )


# ------------------------------------------------------------------------------ administration


class OnBehalfBody(RightsBody):
    subject_principal_id: uuid.UUID
    authorized_agent_ref: str | None = Field(default=None, max_length=128)


@router.get("/admin/rights-requests")
async def list_requests(
    request: Request,
    caller: CallerDep,
    status: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource="rights_request:*"
        )
        rows = await store.list_requests(conn, status=status, subject=None, limit=limit)
        return {"items": [service.request_view(r) for r in rows]}

    return await _run(request, caller, go)


@router.post("/admin/rights-requests", status_code=201)
async def open_on_behalf(body: OnBehalfBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource="rights_request:*"
        )
        r = await service.open_request(
            conn,
            _tenant(caller),
            request_type=body.request_type,
            subject_principal_id=str(body.subject_principal_id),
            requester_principal_id=_me(caller),
            jurisdiction_pack_id=body.jurisdiction_pack_id,
            authorized_agent_ref=body.authorized_agent_ref,
            scope=body.scope,
        )
        return service.request_view(r)

    return await _run(request, caller, go)


class VerifyBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verified: bool
    level: int = Field(ge=0, le=3)
    method: str = Field(min_length=2, max_length=64)


@router.post("/admin/rights-requests/{request_id}:verify")
async def verify(
    request_id: uuid.UUID, body: VerifyBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource=f"rights_request:{request_id}"
        )
        r = await service.verify_request(
            conn, _tenant(caller), str(request_id), actor=_me(caller), **body.model_dump()
        )
        return service.request_view(r)

    return await _run(request, caller, go)


@router.post("/admin/rights-requests/{request_id}:fulfill")
async def fulfill(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.fulfill", resource=f"rights_request:{request_id}"
        )
        r = await service.fulfil_request(conn, _tenant(caller), str(request_id), actor=_me(caller))
        return service.request_view(r)

    return await _run(request, caller, go)


class CloseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: str = Field(pattern="^(COMPLETED|PARTIALLY_DENIED|DENIED|WITHDRAWN)$")
    reason_code: str = Field(pattern=CODE)
    exemptions: list[dict[str, str]] = Field(default_factory=list, max_length=20)


@router.post("/admin/rights-requests/{request_id}:close")
async def close(
    request_id: uuid.UUID, body: CloseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource=f"rights_request:{request_id}"
        )
        r = await service.close_request(
            conn, _tenant(caller), str(request_id), actor=_me(caller), **body.model_dump()
        )
        return service.request_view(r)

    return await _run(request, caller, go)


class AckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ack_ref: str = Field(min_length=3, max_length=200)


@router.post("/admin/rights-requests/{request_id}/items/{item_id}:acknowledge")
async def acknowledge(
    request_id: uuid.UUID, item_id: str, body: AckBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.fulfill", resource=f"rights_request:{request_id}"
        )
        r = await service.acknowledge_item(
            conn, _tenant(caller), str(request_id), item_id, ack_ref=body.ack_ref, actor=_me(caller)
        )
        return service.request_view(r)

    return await _run(request, caller, go)


@router.get("/admin/data-map/{subject_id}")
async def data_map(subject_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource=f"data_map:{subject_id}"
        )
        return {"items": _rows(await store.subject_entries(conn, str(subject_id)))}

    return await _run(request, caller, go)


class PurposeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=CODE)
    description: str = Field(min_length=10, max_length=1000)
    data_categories: list[str] = Field(min_length=1, max_length=5)
    recipient_classes: list[str] = Field(default_factory=list, max_length=20)
    compatible_secondary_purposes: list[str] = Field(default_factory=list, max_length=20)
    prohibited_uses: list[str] = Field(default_factory=list, max_length=20)
    retention_policy_code: str | None = Field(default=None, pattern=CODE)


@router.get("/admin/purposes")
async def list_purposes(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action="privacy.read", resource="purpose:*")
        return {"items": _rows(await store.list_purposes(conn, status=None))}

    return await _run(request, caller, go)


@router.post("/admin/purposes", status_code=201)
async def create_purpose(body: PurposeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.processing_activity.manage", resource="purpose:*"
        )
        pid = await service.create_purpose(
            conn, _tenant(caller), actor=_me(caller), **body.model_dump()
        )
        return {"purpose_id": pid, "status": "DRAFT"}

    return await _run(request, caller, go)


@router.post("/admin/purposes/{purpose_id}:activate")
async def activate_purpose(
    purpose_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The activating administrator reviews the draft and must not be its author (DB check)."""

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="privacy.processing_activity.manage",
            resource=f"purpose:{purpose_id}",
        )
        r = await service.activate_purpose(
            conn, _tenant(caller), str(purpose_id), approver=_me(caller)
        )
        return {
            "purpose_id": str(r.purpose_id),
            "status": r.status,
            "purpose_version": r.purpose_version,
        }

    return await _run(request, caller, go)


class ActivityBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=CODE)
    name: str = Field(min_length=3, max_length=200)
    purpose_codes: list[str] = Field(min_length=1, max_length=20)
    data_categories: list[str] = Field(min_length=1, max_length=5)
    lawful_basis: str
    jurisdiction_pack_id: str
    data_subject_classes: list[str] = Field(default_factory=list, max_length=20)
    special_category_condition: str | None = Field(default=None, max_length=200)
    source_classes: list[str] = Field(default_factory=list, max_length=20)
    recipient_classes: list[str] = Field(default_factory=list, max_length=20)
    retention_policy_code: str | None = Field(default=None, pattern=CODE)
    residency_profile: str | None = Field(default=None, pattern="^SRP-[0-3]$")
    dpia_ref: str | None = Field(default=None, max_length=128)
    launch_flow: bool = False


@router.get("/admin/processing-activities")
async def list_activities(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.read", resource="processing_activity:*"
        )
        return {"items": _rows(await store.list_activities(conn))}

    return await _run(request, caller, go)


@router.post("/admin/processing-activities", status_code=201)
async def create_activity(
    body: ActivityBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="privacy.processing_activity.manage",
            resource="processing_activity:*",
        )
        aid = await service.create_activity(
            conn, _tenant(caller), actor=_me(caller), **body.model_dump()
        )
        return {"processing_activity_id": aid, "status": "DRAFT"}

    return await _run(request, caller, go)


@router.post("/admin/processing-activities/{activity_id}:activate")
async def activate_activity(
    activity_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn,
            caller,
            action="privacy.processing_activity.manage",
            resource=f"processing_activity:{activity_id}",
        )
        r = await service.activate_activity(
            conn, _tenant(caller), str(activity_id), actor=_me(caller)
        )
        return {
            "processing_activity_id": str(r.processing_activity_id),
            "status": r.status,
            "version": r.version,
        }

    return await _run(request, caller, go)


class RoleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_controller_principal_id: uuid.UUID
    zoiko_role: str = Field(
        pattern="^(CONTROLLER|PROCESSOR|JOINT_CONTROLLER|INDEPENDENT_CONTROLLER)$"
    )
    jurisdiction_pack_id: str
    approval_ref: str = Field(min_length=3, max_length=200)  # counsel's approval reference
    processor_principal_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    recipient_classes: list[str] = Field(default_factory=list, max_length=20)
    contract_or_dpa_ref: str | None = Field(default=None, max_length=200)
    valid_to: datetime | None = None
    approval_request_id: uuid.UUID

    def change(self, activity_id: uuid.UUID) -> str:
        return f"{activity_id}|{self.zoiko_role}|{self.jurisdiction_pack_id}|{self.approval_ref}"


@router.post("/admin/processing-activities/{activity_id}/role-assignments", status_code=201)
async def assign_role(
    activity_id: uuid.UUID, body: RoleBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        approver = await approved_by_second_admin(
            conn,
            caller,
            action="privacy.role_assignment.manage",
            resource=f"processing_activity:{activity_id}",
            change=body.change(activity_id),
            approval_request_id=str(body.approval_request_id),
        )
        r = await service.assign_role(
            conn,
            _tenant(caller),
            str(activity_id),
            data_controller_principal_id=str(body.data_controller_principal_id),
            zoiko_role=body.zoiko_role,
            jurisdiction_pack_id=body.jurisdiction_pack_id,
            approval_ref=body.approval_ref,
            created_by=_me(caller),
            approved_by=approver,
            processor_principal_ids=[str(p) for p in body.processor_principal_ids],
            recipient_classes=body.recipient_classes,
            contract_or_dpa_ref=body.contract_or_dpa_ref,
            valid_to=body.valid_to,
        )
        return _rows([r])[0]

    return await _run(request, caller, go)


@router.get("/admin/role-map")
async def role_map(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.read", resource="processing_role_assignment:*"
        )
        return {"items": _rows(await store.assignments(conn))}

    return await _run(request, caller, go)


@router.get("/admin/release-check")
async def release_check(request: Request, caller: CallerDep) -> dict[str, Any]:
    """DEC-F3: launch flows without an approved role assignment block G4."""

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.read", resource="processing_activity:*"
        )
        blockers = await service.release_check(conn)
        return {
            "ready": not blockers,
            "blockers": [{k: _plain(v) for k, v in b.items()} for b in blockers],
        }

    return await _run(request, caller, go)


class RetentionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=CODE)
    object_type: str = Field(pattern=r"^[a-z][a-z0-9_.]{0,63}$")
    jurisdiction_pack_id: str
    trigger: str = Field(
        pattern="^(CREATED_AT|LAST_USED|VERIFIED_AT|CONTRACT_END|ACCOUNT_CLOSE|GRANT_EXPIRY|EVENT_DATE)$"
    )
    retention_days: int = Field(ge=0, le=36500)
    action: str = Field(pattern="^(DELETE|CRYPTO_SHRED|ANONYMIZE|ARCHIVE_RESTRICTED)$")
    purpose_code: str | None = Field(default=None, pattern=CODE)
    backup_erasure_window_days: int | None = Field(default=None, ge=0, le=3650)
    legal_hold_suspends: bool = True
    legally_required: bool = False
    approval_request_id: uuid.UUID

    def change(self) -> str:
        return f"{self.code}|{self.object_type}|{self.retention_days}|{self.action}|{self.backup_erasure_window_days}"


@router.get("/admin/retention-policies")
async def list_retention(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action="privacy.read", resource="retention_policy:*")
        return {"items": _rows(await store.list_retention_policies(conn))}

    return await _run(request, caller, go)


@router.post("/admin/retention-policies", status_code=201)
async def create_retention(
    body: RetentionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        approver = await approved_by_second_admin(
            conn,
            caller,
            action="privacy.retention.manage",
            resource="retention_policy:*",
            change=body.change(),
            approval_request_id=str(body.approval_request_id),
        )
        await require_feature(
            conn,
            "advanced.custom-data-retention-configuration",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        r = await service.create_retention_policy(
            conn,
            _tenant(caller),
            created_by=_me(caller),
            approved_by=approver,
            **body.model_dump(exclude={"approval_request_id"}),
        )
        return _rows([r])[0]

    return await _run(request, caller, go)


@router.post("/admin/restrictions/{restriction_id}:lift")
async def lift(restriction_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="privacy.rights.manage", resource=f"restriction:{restriction_id}"
        )
        await service.lift_restriction(
            conn, _tenant(caller), str(restriction_id), actor=_me(caller)
        )
        return {"restriction_id": str(restriction_id), "active": False}

    return await _run(request, caller, go)


MANAGE = "privacy.processing_activity.manage"


class NoticeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: str = Field(pattern=CODE)
    audience: str = Field(min_length=2, max_length=64)
    content: str = Field(min_length=20, max_length=200_000)
    jurisdictions: list[str] = Field(default_factory=list, max_length=50)
    processing_activity_codes: list[str] = Field(default_factory=list, max_length=50)
    content_uri: str | None = Field(default=None, max_length=500)
    approval_request_id: uuid.UUID


@router.get("/admin/notices")
async def list_notices(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action="privacy.read", resource="privacy_notice:*")
        return {"items": _rows(await registry.notices(conn))}

    return await _run(request, caller, go)


@router.post("/admin/notices", status_code=201)
async def publish_notice(body: NoticeBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    from zid_evidence import hash_bytes

    change = f"{body.code}|{hash_bytes(body.content.encode('utf-8'), alg='sha-256')}"

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        approver = await approved_by_second_admin(
            conn,
            caller,
            action=MANAGE,
            resource="privacy_notice:*",
            change=change,
            approval_request_id=str(body.approval_request_id),
        )
        row = await registry.publish_notice(
            conn,
            _tenant(caller),
            code=body.code,
            audience=body.audience,
            content=body.content,
            created_by=_me(caller),
            approved_by=approver,
            jurisdictions=body.jurisdictions,
            processing_activity_codes=body.processing_activity_codes,
            content_uri=body.content_uri,
        )
        return {**_rows([row])[0], "notice_version": f"{row.notice_code}@{row.version}"}

    return await _run(request, caller, go)


class ProcessorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    processor_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_.-]{0,63}$")
    name: str = Field(min_length=2, max_length=200)
    role: str = Field(pattern="^(PROCESSOR|SUBPROCESSOR|INDEPENDENT_CONTROLLER|RECIPIENT)$")
    data_categories: list[str] = Field(min_length=1, max_length=5)
    locations: list[str] = Field(min_length=1, max_length=50)
    processing_activity_codes: list[str] = Field(default_factory=list, max_length=50)
    purposes: list[str] = Field(default_factory=list, max_length=50)
    transfer_mechanism: str | None = Field(default=None, max_length=64)
    deletion_sla_days: int | None = Field(default=None, ge=0, le=3650)
    security_assurance: str | None = Field(default=None, max_length=200)
    contract_ref: str | None = Field(default=None, max_length=200)


@router.get("/admin/processors")
async def list_processors(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action="privacy.read", resource="processor:*")
        return {"items": _rows(await registry.processors(conn))}

    return await _run(request, caller, go)


@router.post("/admin/processors", status_code=201)
async def register_processor(
    body: ProcessorBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action=MANAGE, resource="processor:*")
        row = await registry.register_processor(
            conn, _tenant(caller), created_by=_me(caller), **body.model_dump()
        )
        return _rows([row])[0]

    return await _run(request, caller, go)


@router.post("/admin/processors/{processor_id}:approve")
async def approve_processor(
    processor_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action=MANAGE, resource=f"processor:{processor_id}")
        return _rows(
            [
                await registry.set_processor_status(
                    conn, _tenant(caller), processor_id, "APPROVED", actor=_me(caller)
                )
            ]
        )[0]

    return await _run(request, caller, go)


@router.post("/admin/processors/{processor_id}:suspend")
async def suspend_processor(
    processor_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action=MANAGE, resource=f"processor:{processor_id}")
        return _rows(
            [
                await registry.set_processor_status(
                    conn, _tenant(caller), processor_id, "SUSPENDED", actor=_me(caller)
                )
            ]
        )[0]

    return await _run(request, caller, go)


class AdmBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision_class: str = Field(min_length=3, max_length=128)
    purpose_code: str = Field(pattern=CODE)
    input_data_categories: list[str] = Field(min_length=1, max_length=5)
    model_or_rules_ref: str = Field(min_length=1, max_length=200)
    solely_automated: bool
    significance_class: str = Field(pattern="^(NONE|MATERIAL|LEGAL_OR_SIMILARLY_SIGNIFICANT)$")
    explanation_profile: str = Field(min_length=10, max_length=4000)
    human_review_route: str = Field(min_length=2, max_length=200)
    special_category_use: bool = False
    jurisdiction_rules: dict[str, str] = Field(default_factory=dict, max_length=20)
    risk_assessment_ref: str | None = Field(default=None, max_length=200)


@router.get("/admin/automated-decision-profiles")
async def list_adm(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action="privacy.read", resource="adm_profile:*")
        return {"items": _rows(await registry.adm_profiles(conn))}

    return await _run(request, caller, go)


@router.post("/admin/automated-decision-profiles", status_code=201)
async def create_adm(body: AdmBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        await protected_action(conn, caller, action=MANAGE, resource="adm_profile:*")
        row = await registry.create_adm_profile(
            conn, _tenant(caller), created_by=_me(caller), **body.model_dump()
        )
        return _rows([row])[0]

    return await _run(request, caller, go)
