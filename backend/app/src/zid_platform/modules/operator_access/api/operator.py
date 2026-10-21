"""Operator plane API: JIT access, break-glass and post-review (DEC-F4; SEC-001 §11-13; UX §21).

    GET  /operator/capabilities                         the fixed capability catalog (+ SoD sets)
    POST /operator/access-requests                      request JIT access
    GET  /operator/access-requests?view=mine|pending|all&state=
    GET  /operator/access-requests/{id}
    POST /operator/access-requests/{id}:approve         independent approver (never the requester)
    POST /operator/access-requests/{id}:deny
    POST /operator/access-requests/{id}:cancel          requester
    POST /operator/access-requests/{id}:activate        requester; APPROVED (+ lockbox) -> ACTIVE
    POST /operator/access-requests/{id}:revoke          requester or an approver; immediate
    POST /operator/break-glass                          emergency: ACTIVE now, alert, review
    GET  /operator/reviews?state=REVIEW_PENDING
    POST /operator/reviews/{id}:complete                reviewer != break-glass requester

Who is an operator: a principal of the dedicated operator tenant (ZID_OPERATOR_TENANT_ID),
signed in through an operator OIDC client (ZID_OPERATOR_CLIENT_IDS, first-party `zoiko-operator`
with its own origin and redirect, never `zoiko-web`), at AAL-Z3 (fresh passkey step-up), with
no token-exchange actor, and holding an ACTIVE operator profile. A customer-tenant token fails
here whatever its client; an operator-tenant token from another client fails too. There is no
impersonation endpoint and no endpoint that reaches the database directly.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import RequestContext
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.operator_access.application import service
from zid_platform.modules.operator_access.application.service import (
    AccessInput,
    Operator,
    OperatorSettings,
)
from zid_platform.modules.operator_access.domain.model import (
    CAPABILITIES,
    CONFLICT_SETS,
    GrantState,
    OperatorAccessError,
    OperatorRole,
    ReviewOutcome,
    ReviewState,
)
from zid_platform.modules.operator_access.infrastructure import store
from zid_platform.modules.operator_access.infrastructure.store import Grant, Review

router = APIRouter(prefix="/operator", tags=["operator-access"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_UNPROCESSABLE = (
    "OPERATOR_CAPABILITY_UNKNOWN",
    "OPERATOR_DURATION_INVALID",
    "OPERATOR_DURATION_EXCEEDED",
    "OPERATOR_SCOPE_INVALID",
)
_FORBIDDEN = (
    "APPROVER_",
    "REVIEWER_",
    "OPERATOR_PROFILE_MISSING",
    "OPERATOR_ROLE_MISSING",
    "OPERATOR_SECURITY_STATE",
    "OPERATOR_SOD_CONFLICT",
    "OPERATOR_NOT_REQUESTER",
    "SOVEREIGN_OPERATOR_DENIED",
    "LOCKBOX_APPROVAL_REQUIRED",
    "BREAK_GLASS_NOT_ALLOWED",
)


def http(e: OperatorAccessError) -> ZidError:
    if e.code.endswith("NOT_FOUND"):
        status = 404
    elif e.code.startswith(_UNPROCESSABLE):
        status = 422
    elif e.code.startswith(_FORBIDDEN):
        status = 403
    else:
        status = 409  # invalid transitions, expiry, concurrent modification
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=status,
        retryable=e.code == "CONCURRENT_MODIFICATION",
    )


def _refuse(code: str, detail: str, **kw: Any) -> ZidError:
    return ZidError(code, detail, http_status=403, **kw)


def operator_settings(request: Request) -> OperatorSettings:
    s: OperatorSettings | None = getattr(request.app.state, "operator_settings", None)
    if s is None or not s.tenant_id:
        raise _refuse("OPERATOR_PLANE_DISABLED", "No operator tenant is configured in this cell.")
    return s


def _same_uuid(a: str | None, b: str | None) -> bool:
    try:
        return a is not None and b is not None and uuid.UUID(a) == uuid.UUID(b)
    except ValueError:
        return False


async def operator_caller(request: Request, caller: CallerDep) -> Operator:
    """The operator gate: operator client AND operator tenant AND AAL-Z3 fresh AND no actor."""
    s = operator_settings(request)
    if caller.client_id not in s.client_ids:
        raise _refuse("OPERATOR_CLIENT_REQUIRED", "Sign in through the Operator Console.")
    if not _same_uuid(caller.ctx.tenant_id, s.tenant_id):
        raise _refuse("OPERATOR_TENANT_REQUIRED", "Customer-tenant identities are not operators.")
    if caller.actor is not None:
        raise _refuse("OPERATOR_DELEGATION_FORBIDDEN", "Operators act only as themselves.")
    if not caller.ctx.principal_id:
        raise _refuse("OPERATOR_PROFILE_MISSING", "An authenticated operator is required.")
    if caller.authn.level < 3:
        raise _refuse(
            "STEP_UP_REQUIRED",
            "Operator actions need a fresh phishing-resistant authentication (AAL-Z3).",
            headers={
                "WWW-Authenticate": 'Bearer error="insufficient_user_authentication", '
                'acr_values="urn:zoiko:aal:z3"'
            },
        )
    request.state.operator_ctx = caller.ctx
    return Operator(caller.ctx.tenant_id or "", caller.ctx.principal_id, caller.session_auth_time)


OperatorDep = Annotated[Operator, Depends(operator_caller)]


# ------------------------------------------------------------------------------------ bodies


class AccessBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    capability: str = Field(min_length=1, max_length=64)
    case_ref: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=8, max_length=2000)
    target_tenant_id: uuid.UUID | None = None
    resource_scope: str = Field(min_length=1, max_length=256)
    duration_minutes: int = Field(ge=1, le=24 * 60)

    def input(self, s: OperatorSettings) -> AccessInput:
        """Environment and cell are the serving cell's (INV-13: never caller input); a grant is
        region-bound to the cell it was requested in."""
        return AccessInput(
            capability=self.capability,
            case_ref=self.case_ref,
            reason=self.reason,
            target_tenant_id=str(self.target_tenant_id) if self.target_tenant_id else None,
            environment=s.environment,
            cell_id=s.cell_id,
            resource_scope=self.resource_scope,
            duration=timedelta(minutes=self.duration_minutes),
        )


class BreakGlassBody(AccessBody):
    reason: str = Field(min_length=20, max_length=2000)  # the justification


class ReasonBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="", max_length=2000)


class ReviewBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: ReviewOutcome
    notes: str = Field(min_length=1, max_length=4000)


class ListView(StrEnum):
    MINE = "mine"
    PENDING = "pending"  # REQUESTED by someone else: the approver's queue
    ALL = "all"


# ------------------------------------------------------------------------------------- views


def _iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def grant_view(
    g: Grant, now: datetime, approvals: list[tuple[str, str]] | None = None
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "jit_grant_id": g.jit_grant_id,
        "requester_principal_id": g.requester_principal_id,
        "capability": g.capability,
        "target_tenant_id": g.target_tenant_id,
        "environment": g.environment,
        "cell_id": g.cell_id,
        "resource_scope": g.resource_scope,
        "case_ref": g.case_ref,
        "reason": g.reason,
        "risk_class": f"R{g.risk_class}",
        "evidence_class": f"E{g.evidence_class}",
        "duration_minutes": int(g.duration.total_seconds() // 60),
        "required_approvals": g.required_approvals,
        "lockbox_required": g.lockbox_required,
        "lockbox_request_id": g.lockbox_request_id,
        "break_glass": g.break_glass,
        "state": g.effective_state(now).value,
        "created_at": _iso(g.created_at),
        "request_expires_at": _iso(g.request_expires_at),
        "approved_at": _iso(g.approved_at),
        "activated_at": _iso(g.activated_at),
        "expires_at": _iso(g.expires_at),
        "ended_at": _iso(g.ended_at),
        "end_reason": g.end_reason,
    }
    if approvals is not None:
        out["approvals"] = [{"approver_principal_id": a, "verdict": v} for a, v in approvals]
        out["approvals_received"] = sum(1 for _, v in approvals if v == "APPROVE")
    return out


def review_view(r: Review) -> dict[str, Any]:
    return {
        "review_id": r.review_id,
        "jit_grant_id": r.jit_grant_id,
        "requester_principal_id": r.requester_principal_id,
        "state": r.state.value,
        "reviewer_principal_id": r.reviewer_principal_id,
        "outcome": r.outcome.value if r.outcome else None,
        "created_at": _iso(r.created_at),
        "reviewed_at": _iso(r.reviewed_at),
    }


# --------------------------------------------------------------------------------- endpoints


@router.get("/capabilities")
async def capabilities(request: Request, op: OperatorDep) -> dict[str, Any]:
    s = operator_settings(request)
    return {
        "items": [c.view(s.ceiling(c)) for c in CAPABILITIES.values()],
        "separation_of_duties": [
            {"rule": r.name, "capabilities": sorted(r.capabilities), "rationale": r.rationale}
            for r in CONFLICT_SETS
        ],
        "break_glass_max_minutes": int(s.break_glass_max.total_seconds() // 60),
    }


@router.post("/access-requests", status_code=201)
async def create(body: AccessBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    s, now = operator_settings(request), datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            g = await service.request_access(conn, s, op, body.input(s), now)
    except OperatorAccessError as e:
        raise http(e) from e
    return grant_view(g, now, [])


@router.get("/access-requests")
async def list_requests(
    request: Request,
    op: OperatorDep,
    view: ListView = ListView.MINE,
    state: GrantState | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            if view is ListView.MINE:
                items = await store.list_grants(
                    conn, requester=op.principal_id, states=(state,) if state else (), limit=limit
                )
            else:
                await service.operator_profile(conn, op, OperatorRole.APPROVER)
                states = (GrantState.REQUESTED,) if view is ListView.PENDING else ()
                items = await store.list_grants(
                    conn, states=(state,) if state else states, limit=limit
                )
                if view is ListView.PENDING:
                    items = [
                        g
                        for g in items
                        if g.requester_principal_id != op.principal_id
                        and g.effective_state(now) is GrantState.REQUESTED
                    ]
    except OperatorAccessError as e:
        raise http(e) from e
    return {"items": [grant_view(g, now) for g in items]}


@router.get("/access-requests/{grant_id}")
async def get_request(grant_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            g = await store.load_grant(conn, str(grant_id))
            if g.requester_principal_id != op.principal_id:
                await service.operator_profile(conn, op, OperatorRole.APPROVER)
            approvals = await store.decisions(conn, g.jit_grant_id)
    except OperatorAccessError as e:
        raise http(e) from e
    return grant_view(g, now, approvals)


async def _transition(
    request: Request, op: Operator, grant_id: uuid.UUID, verb: str, reason: str = ""
) -> dict[str, Any]:
    s, now = operator_settings(request), datetime.now(UTC)
    gid = str(grant_id)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            if verb == "approve":
                g = await service.approve(conn, s, op, gid, now)
            elif verb == "deny":
                g = await service.deny(conn, op, gid, reason, now)
            elif verb == "cancel":
                g = await service.cancel(conn, op, gid, now)
            elif verb == "activate":
                g = await service.activate(conn, s, op, gid, now)
            else:
                g = await service.revoke(conn, op, gid, reason, now)
            approvals = await store.decisions(conn, g.jit_grant_id)
    except OperatorAccessError as e:
        raise http(e) from e
    return grant_view(g, now, approvals)


@router.post("/access-requests/{grant_id}:approve")
async def approve(grant_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _transition(request, op, grant_id, "approve")


@router.post("/access-requests/{grant_id}:deny")
async def deny(
    grant_id: uuid.UUID, body: ReasonBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _transition(request, op, grant_id, "deny", body.reason or "denied")


@router.post("/access-requests/{grant_id}:cancel")
async def cancel(grant_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _transition(request, op, grant_id, "cancel")


@router.post("/access-requests/{grant_id}:activate")
async def activate(grant_id: uuid.UUID, request: Request, op: OperatorDep) -> dict[str, Any]:
    return await _transition(request, op, grant_id, "activate")


@router.post("/access-requests/{grant_id}:revoke")
async def revoke(
    grant_id: uuid.UUID, body: ReasonBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    return await _transition(request, op, grant_id, "revoke", body.reason or "revoked")


@router.post("/break-glass", status_code=201)
async def break_glass(body: BreakGlassBody, request: Request, op: OperatorDep) -> dict[str, Any]:
    s, now = operator_settings(request), datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            g, review_id = await service.break_glass(conn, s, op, body.input(s), now)
    except OperatorAccessError as e:
        raise http(e) from e
    return {**grant_view(g, now, []), "review_id": review_id, "review_state": "REVIEW_PENDING"}


@router.get("/reviews")
async def list_reviews(
    request: Request,
    op: OperatorDep,
    state: ReviewState | None = ReviewState.REVIEW_PENDING,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            await service.operator_profile(conn, op, OperatorRole.REVIEWER)
            items = await store.list_reviews(conn, state=state, limit=limit)
    except OperatorAccessError as e:
        raise http(e) from e
    return {"items": [review_view(r) for r in items]}


@router.post("/reviews/{review_id}:complete")
async def complete_review(
    review_id: uuid.UUID, body: ReviewBody, request: Request, op: OperatorDep
) -> dict[str, Any]:
    now = datetime.now(UTC)
    try:
        async with tenant_transaction(request_engine(request), _ctx(request)) as conn:
            r = await service.complete_review(
                conn, op, str(review_id), body.outcome, body.notes, now
            )
    except OperatorAccessError as e:
        raise http(e) from e
    return review_view(r)


def _ctx(request: Request) -> RequestContext:
    """The authenticated request context (tenant = the operator tenant), set by the gate."""
    ctx: RequestContext = request.state.operator_ctx
    return ctx
