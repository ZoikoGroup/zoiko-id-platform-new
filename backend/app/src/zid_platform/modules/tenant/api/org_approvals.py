"""Organization-scoped approval API (ZID-UX-WF-013C screens 2 and 3; slice L-13, CR-049).

    GET  /v1/organizations/{id}/approvals?scope=review|mine|history   the inbox: authorized rows only, filters, a cursor, honest counts
    POST /v1/organizations/{id}/approvals                              create a request; the server derives the level
    GET  /v1/organizations/{id}/approvals/{requestId}                  the exact change, evidence summary, progress and what the caller may do
    POST /v1/organizations/{id}/approvals/{requestId}/decisions        APPROVE, REJECT or REQUEST_CHANGES (If-Match, Idempotency-Key)

The organization in the path is the caller's own organization or nothing: any other id is "no such organization". The inbox is authorized
object by object before any filter or count is applied, so a filter, a cursor or a count can reveal nothing the caller could not open. These
routes are a thin face on the approval service (`tenant/application/approvals.py`); the earlier `/v1/approval-cases` routes remain.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.tenant.api.approvals import (
    STEP_UP_ACTION,
    _commit_then,
    _platform,
    _who,
    case_view,
    http,
)
from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.domain.approvals import ApprovalError, State, Tier
from zid_platform.modules.tenant.infrastructure import approval_forms as forms
from zid_platform.modules.tenant.infrastructure import approvals as store

router = APIRouter(tags=["approvals"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

# The plain words of WF-013C section 13 for each state (never "Approved" for a registry match, never colour alone).
STATUS_WORDS = {
    "PENDING_APPROVAL": "Pending approval",
    "PARTIALLY_APPROVED": "Pending approval",
    "APPROVED_READY": "Approved, waiting to be applied",
    "EXECUTED": "Executed",
    "FAILED": "Needs action",
    "REJECTED": "Denied",
    "EXPIRED": "Expired",
    "INVALIDATED": "Replaced",
    "CANCELLED": "Cancelled",
    "NEEDS_CHANGES": "Needs changes",
}


def _org(org_id: uuid.UUID, caller: Caller) -> tuple[str, str]:
    tenant, me = _who(caller)
    if (
        str(org_id) != tenant
    ):  # another organization's id is indistinguishable from one that does not exist
        raise ZidError("ORGANIZATION_NOT_FOUND", "No such organization.", http_status=404)
    return tenant, me


def _item(v: svc.CaseView, *, mine: tuple[str, ...] | None, me: str) -> dict[str, Any]:
    out = case_view(v, mine=mine, me=me)
    out["status_label"] = STATUS_WORDS.get(v.case.state.value, v.case.state.value)
    out["level"] = v.case.tier.value
    out["version"] = v.case.version
    return out


def _summary(v: svc.CaseView) -> dict[str, Any]:
    """The inbox card (WF-013C-02B): title, requester, level, quorum, expiry and next action. No payload, no evidence."""
    c = v.case
    return {
        "request_id": c.case_id,
        "title": c.summary,
        "requester_principal_id": c.requester_principal_id,
        "level": c.tier.value,
        "state": c.state.value,
        "status_label": STATUS_WORDS.get(c.state.value, c.state.value),
        "approvals_received": v.approvals,
        "approvals_required": v.required,
        "shortage": "Independent approvers required"
        if v.hold
        else None,  # never auto-downgraded, never simulated
        "policy_version": c.policy_version,
        "expires_at": c.expires_at.isoformat(),
        "created_at": c.created_at.isoformat(),
        "next_action": _next(v),
    }


def _next(v: svc.CaseView) -> str:
    s = v.case.state
    if v.hold:
        return "Invite or appoint independent approvers"
    if s in (State.PENDING_APPROVAL, State.PARTIALLY_APPROVED):
        return "Review and decide"
    if s is State.APPROVED_READY:
        return "Apply the change" if not v.execution_hold else f"Held: {v.execution_hold}"
    if s is State.NEEDS_CHANGES:
        return "Revise and resubmit"
    if s is State.FAILED:
        return "Run again or start a new request"
    return "None"


@router.get("/v1/organizations/{org_id}/approvals")
async def inbox(
    org_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    scope: Literal["review", "mine", "history"] = "review",
    state: State | None = None,
    risk: Literal["A1", "A2"] | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    q: Annotated[str | None, Query(max_length=64)] = None,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            page = await svc.list_visible(
                conn, tenant, me, scope=scope, states=(state,) if state else None, tier=risk,
                created_from=created_from, created_to=created_to, query=q, cursor=cursor, limit=limit,
            )  # fmt: skip
        except ApprovalError as e:
            raise http(e) from e
    return {
        "scope": scope,
        "items": [_summary(v) for v in page.items],
        "counts": page.counts,
        "next_cursor": page.next_cursor,
    }


class ApprovalCreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    ttl_seconds: int | None = Field(default=None, ge=60)
    evidence_ref: str | None = Field(default=None, max_length=128)


@router.post("/v1/organizations/{org_id}/approvals", status_code=201)
async def create(
    org_id: uuid.UUID, body: ApprovalCreateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    v: svc.CaseView = await _commit_then(
        request,
        caller,
        lambda conn: svc.create_case(
            conn, tenant, me, action_id=body.action_id, target_id=body.target_id, payload=body.payload,
            reason=body.reason, ttl_seconds=body.ttl_seconds, evidence_ref=body.evidence_ref,
            platform_tenant_id=_platform(request),
        ),
    )  # fmt: skip
    return _item(v, mine=(), me=me)


@router.get("/v1/organizations/{org_id}/approvals/forms")
async def request_forms(org_id: uuid.UUID, caller: CallerDep) -> dict[str, Any]:
    """What "New request" asks for, per action: the same for everyone in the organization. It collects; the server still classifies the
    action, checks who may ask and decides who must approve."""
    _org(org_id, caller)
    return {"forms": [forms.view(f) for f in forms.request_forms()]}


@router.get("/v1/organizations/{org_id}/approvals/{request_id}")
async def read(
    org_id: uuid.UUID, request_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    _tenant, me = _org(org_id, caller)

    async def work(conn: Any) -> dict[str, Any]:
        case = await store.load_case(conn, str(request_id))
        if case is None or not await svc.can_view(
            conn, case, me
        ):  # not yours to see is the same answer as not there
            raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
        from datetime import UTC

        now = datetime.now(UTC)
        case = await svc._lapse(conn, case, now)
        v = await svc.view(conn, case, now=now)
        return _item(v, mine=await svc.my_refusals(conn, case, me, now), me=me)

    out: dict[str, Any] = await _commit_then(request, caller, work)
    return out


class ApprovalDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["APPROVE", "REJECT", "REQUEST_CHANGES"]
    reason: str | None = Field(default=None, max_length=500)


def _etag(raw: str | None) -> str:
    """The request version the approver reviewed: its immutable digest, quoted or not (If-Match)."""
    if not raw:
        raise ZidError(
            "PRECONDITION_REQUIRED",
            "Send If-Match with the request version you reviewed.",
            http_status=428,
        )
    value = raw.strip()
    value = value.removeprefix("W/")
    return value.strip('"')


@router.post("/v1/organizations/{org_id}/approvals/{request_id}/decisions", status_code=201)
async def decide(
    org_id: uuid.UUID,
    request_id: uuid.UUID,
    body: ApprovalDecisionBody,
    request: Request,
    caller: CallerDep,
    if_match: Annotated[str | None, Header(alias="If-Match", max_length=200)] = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict[str, Any]:
    tenant, me = _org(org_id, caller)
    version = _etag(if_match)
    if body.decision in ("REJECT", "REQUEST_CHANGES") and not (body.reason or "").strip():
        raise ZidError("VALIDATION_FAILED", "A reason is required.", http_status=422)

    async def work(conn: Any) -> dict[str, Any]:
        case = await store.load_case(conn, str(request_id))
        if case is None or not await svc.can_view(conn, case, me):
            raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
        if (
            version != case.request_hash
        ):  # the request changed since it was read: refresh, no side effects (409 VERSION_CONFLICT)
            raise ApprovalError(
                "VERSION_CONFLICT", "The request changed. Read it again before deciding."
            )
        if body.decision == "REQUEST_CHANGES":
            v = await svc.request_changes(
                conn, tenant, me, str(request_id), reason=body.reason or ""
            )
        else:

            async def step_up(c: store.Case) -> dict[str, Any] | None:
                return await require_step_up(
                    request, conn, caller, action=STEP_UP_ACTION, resource=f"approval-case:{c.case_id}",
                    change={"case_id": c.case_id, "request_hash": c.request_hash, "vote": body.decision},
                    critical=c.tier is Tier.A2, force_enforce=True,
                )  # fmt: skip

            v = await svc.cast_vote(
                conn, tenant, me, str(request_id), vote="APPROVE" if body.decision == "APPROVE" else "REJECT",
                presented_hash=version, step_up=step_up, idempotency_key=idempotency_key, reason=body.reason,
            )  # fmt: skip
        from datetime import UTC

        return _item(v, mine=await svc.my_refusals(conn, v.case, me, datetime.now(UTC)), me=me)

    try:
        out: dict[str, Any] = await _commit_then(request, caller, work)
    except ZidError as e:
        if getattr(e, "code", "") == "VERSION_CONFLICT":
            raise ZidError(
                "VERSION_CONFLICT",
                "The request changed. Read it again before deciding.",
                http_status=409,
            ) from e
        raise
    return out
