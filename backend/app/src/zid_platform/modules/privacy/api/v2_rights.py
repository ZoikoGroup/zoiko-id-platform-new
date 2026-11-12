"""/v2 rights, consent and retention routes named by ZID-PRI-004 sections 11, 13 and 19, over the same rules as /v1 (no second implementation).

    POST /v2/privacy/requests                                 a request about a person, by them or a representative (privacy.rights.manage)
    GET  /v2/privacy/requests/{id}/status                     the person or a rights manager
    POST /v2/privacy/requests/{id}/proof-of-authority         the requester (or a representative) was verified, or was not
    POST /v2/privacy/requests/{id}/review-decisions           a human decision with the rule and evidence behind it; never by the requester
    POST /v2/privacy/requests/{id}/processor-tasks            a processor's explicit acknowledgement; only that moves its stage on
    GET  /v2/privacy/requests/{id}/export                     the person's own package, with fresh strong authentication
    POST /v2/privacy/consent-records | POST .../{id}:withdraw  consent for a consent-based activity, and its withdrawal
    GET  /v2/privacy/retention-policies/{policy_id}?version=   the recorded period (the privacy officer's value, not a legal assertion)
    POST /v2/privacy/retention-evaluations                    what the rules would do with one object now; changes nothing

The actor of a decision is always the authenticated caller, never a name in the body.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.privacy.api.v2 import MANAGE, READ, _ids, _me, _run, _tenant
from zid_platform.modules.privacy.application import retention_v2, service
from zid_platform.modules.privacy.domain.model import PrivacyError, RightsType
from zid_platform.modules.privacy.infrastructure import store

router = APIRouter(tags=["privacy-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
CODE = r"^[A-Z][A-Z0-9_]{1,63}$"
SUB = r"^(sub_)?[0-9a-fA-F-]{36}$"


def _subject(ref: str) -> str:
    return str(uuid.UUID(ref.removeprefix("sub_")))


class RequestIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    right: Literal[tuple(t.value for t in RightsType)]  # type: ignore[valid-type]
    subject_ref: str = Field(pattern=SUB)
    jurisdiction: str = Field(min_length=1, max_length=64)  # the jurisdiction pack id
    represented_by: str | None = Field(default=None, max_length=200)
    scope: dict[str, str] = Field(default_factory=dict, max_length=20)


@router.post("/v2/privacy/requests", status_code=201)
async def open_request(body: RequestIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.open_request(
            conn,
            _tenant(caller),
            request_type=body.right,
            subject_principal_id=_subject(body.subject_ref),
            requester_principal_id=me,
            jurisdiction_pack_id=body.jurisdiction,
            authn_level=caller.authn.level,
            authorized_agent_ref=body.represented_by,
            scope=body.scope,
        )
        return service.request_view(r)

    return {**_ids(), **await _run(request, caller, MANAGE, "rights_request:new", go)}


async def _visible(conn: AsyncConnection, caller: Caller, request_id: str) -> Any:
    r = await store.load_request(conn, request_id)
    if r is None:
        raise PrivacyError("NOT_FOUND", "unknown rights request")
    return r


@router.get("/v2/privacy/requests/{request_id}/status")
async def status(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await _visible(conn, caller, str(request_id))
        if (
            str(r.subject_principal_id) != me
        ):  # anyone else is a rights manager, checked by the engine
            from zid_platform.protected import protected_action

            await protected_action(
                conn, caller, action=MANAGE, resource=f"rights_request:{request_id}"
            )
        return service.request_view(r)

    return {**_ids(), **await _run(request, caller, None, "", go)}


class ProofIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    verified: bool
    level: int = Field(ge=0, le=4)
    method: str = Field(min_length=2, max_length=64)


@router.post("/v2/privacy/requests/{request_id}/proof-of-authority")
async def proof_of_authority(
    request_id: uuid.UUID, body: ProofIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.verify_request(
            conn, _tenant(caller), str(request_id), verified=body.verified, level=body.level,
            method=body.method, actor=me,
        )  # fmt: skip
        return service.request_view(r)

    return {**_ids(), **await _run(request, caller, MANAGE, f"rights_request:{request_id}", go)}


class PrivV2RequestReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rule: str = Field(min_length=2, max_length=128)
    evidence: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(
        default_factory=list, max_length=20
    )
    action: Literal["FULFIL", "DENY", "PARTIAL", "WITHDRAW"]


_OUTCOME = {"DENY": "DENIED", "PARTIAL": "PARTIALLY_DENIED", "WITHDRAW": "WITHDRAWN"}


@router.post("/v2/privacy/requests/{request_id}/review-decisions")
async def review_decision(
    request_id: uuid.UUID, body: PrivV2RequestReviewIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)
    if body.action in _OUTCOME and not body.evidence:
        raise ZidError(
            "EVIDENCE_REQUIRED",
            "A refusal or withdrawal names the evidence behind it.",
            http_status=422,
        )

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await _visible(conn, caller, str(request_id))
        if me in (str(r.subject_principal_id), str(r.requester_principal_id or "")):
            raise PrivacyError("REVIEWER_IS_REQUESTER", "a request is not decided by its requester")
        if body.action == "FULFIL":
            out = await service.fulfil_request(conn, _tenant(caller), str(request_id), actor=me)
        else:
            out = await service.close_request(
                conn, _tenant(caller), str(request_id), outcome=_OUTCOME[body.action],
                reason_code=body.rule, actor=me,
                exemptions=[{"rule": body.rule, "evidence_refs": body.evidence}],
            )  # fmt: skip
        return service.request_view(out)

    action = "privacy.rights.fulfill" if body.action == "FULFIL" else MANAGE
    return {**_ids(), **await _run(request, caller, action, f"rights_request:{request_id}", go)}


class TaskIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    item_id: str = Field(min_length=1, max_length=100)
    ack_ref: str = Field(min_length=3, max_length=200)


@router.post("/v2/privacy/requests/{request_id}/processor-tasks")
async def processor_task(
    request_id: uuid.UUID, body: TaskIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.acknowledge_item(
            conn, _tenant(caller), str(request_id), body.item_id, ack_ref=body.ack_ref, actor=me
        )
        return service.request_view(r)

    return {
        **_ids(),
        **await _run(request, caller, "privacy.rights.fulfill", f"rights_request:{request_id}", go),
    }


@router.get("/v2/privacy/requests/{request_id}/export")
async def export(request_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    """Only the person the data is about, with fresh strong authentication; never an administrator, never another person's data."""
    me = _me(caller)
    age = datetime.now(UTC) - caller.session_auth_time if caller.session_auth_time else None
    if caller.authn.level < 2 or age is None or age > timedelta(minutes=15):
        raise ZidError(
            "STEP_UP_REQUIRED",
            "Sign in again to download your data.",
            http_status=403,
            headers={"WWW-Authenticate": 'Bearer error="insufficient_user_authentication"'},
        )

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        raw = await service.read_package(
            conn, _tenant(caller), str(request_id), subject_principal_id=me
        )
        return {"package": json.loads(raw), "redacted": True}

    return {**_ids(), **await _run(request, caller, None, "", go)}


class ConsentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    activity_code: str = Field(pattern=CODE)
    purpose_code: str = Field(pattern=CODE)
    notice_version: str = Field(min_length=1, max_length=64)
    capture_method: str = Field(pattern="^(WEB|MOBILE|API|PAPER_VERIFIED|OTHER)$")
    affirmative_action: str = Field(min_length=3, max_length=128)
    expires_at: datetime | None = None


@router.post("/v2/privacy/consent-records", status_code=201)
async def give_consent(body: ConsentIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        cid = await service.give_consent(
            conn, _tenant(caller), subject_principal_id=me, **body.model_dump()
        )
        return {
            "consent_id": cid,
            "status": "ACTIVE",
            "note": "Valid only for a consent-based activity; another lawful basis is never turned into consent.",
        }

    return {**_ids(), **await _run(request, caller, None, "", go)}


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="withdrawn by subject", max_length=200)


@router.post("/v2/privacy/consent-records/{consent_id}:withdraw")
async def withdraw_consent(
    consent_id: uuid.UUID, body: WithdrawIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    me = _me(caller)

    async def go(conn: AsyncConnection) -> dict[str, Any]:
        r = await service.withdraw_consent(
            conn, _tenant(caller), str(consent_id), subject_principal_id=me, reason=body.reason
        )
        return {
            "consent_id": str(r.consent_id),
            "status": r.status,
            "note": "Stops processing that relies on this consent. A separate legal obligation to keep a record is not ended by it.",
        }

    return {**_ids(), **await _run(request, caller, None, "", go)}


@router.get("/v2/privacy/retention-policies/{policy_id}")
async def get_policy(
    policy_id: Annotated[str, Field(pattern=CODE)],
    request: Request,
    caller: CallerDep,
    version: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await retention_v2.policy(conn, policy_id, version)

    return {**_ids(), **await _run(request, caller, READ, f"retention_policy:{policy_id}", go)}


class EvaluationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    object_ref: str = Field(min_length=1, max_length=200)
    purpose_id: str = Field(pattern=CODE)
    legal_hold_refs: list[str] = Field(default_factory=list, max_length=50)


@router.post("/v2/privacy/retention-evaluations")
async def evaluate_retention(
    body: EvaluationIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: AsyncConnection) -> dict[str, Any]:
        return await retention_v2.evaluate(conn, _tenant(caller), **body.model_dump())

    return {**_ids(), **await _run(request, caller, READ, "retention_evaluation:new", go)}
