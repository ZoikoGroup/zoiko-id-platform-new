"""Approval API (ZID-ENG-DEC-010, 011, 012; slice L-10, CR-047).

    GET  /v1/approval-policy                          the tiers and the action registry, for the UI to explain (never to decide)
    GET  /v1/approval-policy/actions/{id}             one action's class: tier, votes needed, gate, whether it can run
    GET  /v1/governance/solo-status                   the computed governance state of the organization (no client-controlled quorum)
    POST /v1/approval-cases                           request an A1 or A2 change; the server canonicalizes, classifies and freezes it
    GET  /v1/approval-cases                           the organization's cases, newest first
    GET  /v1/approval-cases/{id}                      one case: progress, holds, reasons, what the caller may do
    POST /v1/approval-cases/{id}/votes                one person's vote on one version (fresh S1 or S2 for this approver and this hash)
    POST /v1/approval-cases/{id}:revise               a material change is a new version; every vote on the old one is void
    POST /v1/approval-cases/{id}:cancel               the requester withdraws it
    POST /v1/approval-cases/{id}/execute              apply an APPROVED_READY case once (header Idempotency-Key)
    GET  /v1/approval-cases/{id}/evidence             the replay: version, people, proofs, result and audit linkage
    POST /v1/recovery-cases                           open the A2 case for privileged recovery
    POST /v1/containment-actions                      an immediate defensive action; never queued behind an approval
    GET/POST /v1/governance/reviewers                 appointed independent reviewers (an appointment is proposed until verified)

The server decides quorum. The body never says how many approvals exist, who counts, or that a vote is fresh.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.authentication.ports import require_step_up
from zid_platform.modules.tenant.application import approvals as svc
from zid_platform.modules.tenant.application import people
from zid_platform.modules.tenant.application import reviewers as reviewer_svc
from zid_platform.modules.tenant.domain.approvals import ApprovalError, State, Tier
from zid_platform.modules.tenant.infrastructure import approvals as store

router = APIRouter(tags=["approvals"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

STEP_UP_ACTION = "execute-sensitive-approval-rejection"
_STATUS = {
    "VALIDATION_FAILED": 422,
    "APPROVAL_PAYLOAD_INVALID": 422,
    "APPROVAL_ACTION_UNKNOWN": 422,
    "APPROVAL_NOT_REQUIRED": 422,
    "APPROVAL_USE_CONTAINMENT": 422,
    "CONTAINMENT_ACTION_UNKNOWN": 422,
    "APPROVAL_EXPIRED": 410,
    "APPROVAL_STATE_INVALID": 409,
    "APPROVAL_NOT_READY": 409,
    "APPROVAL_INVALIDATED": 409,
    "APPROVAL_QUORUM_LOST": 409,
    "APPROVAL_DUPLICATE_PERSON": 409,
    "APPROVAL_HASH_MISMATCH": 409,
    "VERSION_CONFLICT": 409,
    "APPROVAL_WOULD_ORPHAN_TENANT": 409,
    "APPROVAL_TENANT_NOT_SERVING": 409,
    "REVIEWER_ALREADY_APPOINTED": 409,
}


def http(e: ApprovalError) -> ZidError:
    if e.code in _STATUS:
        status = _STATUS[e.code]
    elif e.code.endswith("NOT_FOUND"):
        status = 404
    else:
        status = (
            403  # a refusal of who may do this: requester, voter, platform, forbidden, executable
        )
    return ZidError(
        e.code, e.detail or e.code.replace("_", " ").capitalize() + ".", http_status=status
    )


def _platform(request: Request) -> str | None:
    """The organization where Zoiko iD operations approve platform actions (the operator tenant); None when none is configured."""
    found = getattr(request.app.state, "platform_tenant_id", None)
    return str(found) if found else None


def _who(caller: Caller) -> tuple[str, str]:
    if not caller.ctx.principal_id or not caller.ctx.tenant_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.tenant_id, caller.ctx.principal_id


def _person_ref(person_id: str) -> str:
    """Enough to see that two votes are two people, not who they are."""
    return hashlib.sha256(person_id.encode("utf-8")).hexdigest()[:16]


def _vote(v: store.VoteRow) -> dict[str, Any]:
    return {
        "vote_id": v.vote_id,
        "approver_principal_id": v.approver_principal_id,
        "person_ref": _person_ref(v.approver_person_id),
        "authority_basis": v.authority_basis,
        "vote": v.vote,
        "step_up_tier": v.auth_context.get("tier"),
        "step_up_proof_ref": v.step_up_proof_ref,
        "decided_at": v.decided_at.isoformat(),
    }


def case_view(
    v: svc.CaseView, *, mine: tuple[str, ...] | None = None, me: str | None = None
) -> dict[str, Any]:
    c = v.case
    out: dict[str, Any] = {
        "case_id": c.case_id,
        "version": c.version,
        "supersedes_case_id": c.supersedes_case_id,
        "action_id": c.action_id,
        "tier": c.tier.value,
        "state": c.state.value,
        "hold": v.hold,  # BLOCKED_INSUFFICIENT_APPROVERS / PENDING_INDEPENDENT_REVIEW: held, never waived
        "approvals_received": v.approvals,
        "approvals_required": v.required,
        "independent_people_available": v.independent_available,
        "summary": c.summary,
        "reason": c.reason,
        "payload": c.payload,
        "gate": v.policy.gate,
        "execution_hold": v.execution_hold,  # why an approved case cannot run now; derived, the votes stay
        "evidence": {
            "reference": c.evidence_ref,
            "access": "SEPARATELY_AUTHORIZED",
        },  # restricted evidence is never inline
        "gates": [
            {"code": g.code, "ok": g.ok, "blocking": g.blocking, "note": g.note} for g in v.gates
        ],
        "request_hash": c.request_hash,
        "policy_version": c.policy_version,
        "region": c.region,
        "requester_principal_id": c.requester_principal_id,
        "beneficiary_principal_id": c.beneficiary_principal_id,
        "expires_at": c.expires_at.isoformat(),
        "created_at": c.created_at.isoformat(),
        "approved_at": next(
            (x.decided_at.isoformat() for x in reversed(v.votes) if x.vote == "APPROVE"), None
        )
        if c.state in (State.APPROVED_READY, State.EXECUTED, State.FAILED)
        else None,
        "applied_at": c.executed_at.isoformat() if c.executed_at else None,
        "votes": [_vote(x) for x in v.votes],
        "notice": _notice(v),
    }
    if mine is not None:
        out["you"] = {
            "may_vote": not mine and c.state in (State.PENDING_APPROVAL, State.PARTIALLY_APPROVED),
            "reasons": list(mine),
            "is_requester": me == c.requester_principal_id,
        }
    return out


def _notice(v: svc.CaseView) -> str:
    c = v.case
    if c.state is State.PARTIALLY_APPROVED:
        return f"{v.approvals} of {v.required} independent approvals received. No changes have taken effect."
    if c.state is State.APPROVED_READY:
        return "Approved. No changes have taken effect until it is applied."
    if c.state is State.EXECUTED:
        return "Applied."
    if v.hold:
        return "Held: no independent approver can decide this yet. Nothing has changed."
    return "Waiting for independent approval. No changes have taken effect."


# ----------------------------------------------------------------------------------------------- policy


@router.get("/v1/approval-policy")
async def policy(request: Request, caller: CallerDep) -> dict[str, Any]:
    _who(caller)
    reg = store.registry()
    return {
        "version": reg.version,
        "policy_version": reg.policy_version,
        "enforcement": store.enforcement(),
        "assured_review": "ACTIVE" if store.assured_review_active() else "INACTIVE",
        "tiers": {"A0": 0, "A1": 1, "A2": 2},
        "actions": [_policy_row(p) for p in reg.actions.values()],
    }


def _policy_row(p: Any) -> dict[str, Any]:
    return {
        "action_id": p.action_id,
        "tier": p.tier.value,
        "scope": p.scope.value,
        "votes_required": p.required_votes,
        "approver_roles": sorted(p.approvers),
        "executable": p.executor is not None and p.executor in svc.executor_names(),
        "no_operation": p.no_operation,
        "gate": p.gate,
    }


@router.get("/v1/approval-policy/actions/{action_id}")
async def policy_action(action_id: str, caller: CallerDep) -> dict[str, Any]:
    _who(caller)
    try:
        return _policy_row(store.registry().classify(action_id))
    except ApprovalError as e:
        raise http(e) from e


@router.get("/v1/governance/solo-status")
async def solo_status(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, _ = _who(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            g = await svc.governance_status(conn, tenant)
        except ApprovalError as e:
            raise http(e) from e
    return {
        "eligible_human_member_count": g.eligible_human_member_count,
        "solo_mode": g.solo_mode,
        "region": g.region,
        "policy_version": g.policy_version,
        "legal_form_resolved": g.legal_form_resolved,
        "independent_routes": list(g.independent_routes),
        "a2_requires": "requester plus two independent people, never waived",
        "message": (
            "You can manage your organization on your own. Certain high-risk actions need independent approval."
            if g.solo_mode
            else None
        ),
    }


# ------------------------------------------------------------------------------------------------ cases


class CaseCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=128)
    target_id: str = Field(min_length=1, max_length=256)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)
    ttl_seconds: int | None = Field(default=None, ge=60)
    evidence_ref: str | None = Field(default=None, max_length=128)


class CaseRevise(BaseModel):
    model_config = ConfigDict(extra="forbid")
    payload: dict[str, Any]
    reason: str = Field(min_length=1, max_length=500)


class VoteBody(BaseModel):
    """The vote and the hash the approver reviewed. Nothing about quorum, identity or freshness is taken from here."""

    model_config = ConfigDict(extra="forbid")
    vote: Literal["APPROVE", "REJECT"]
    request_hash: str = Field(min_length=64, max_length=64)


async def _commit_then(request: Request, caller: Caller, work: Any) -> Any:
    """Run `work(conn)` in the caller's tenant transaction. An ApprovalError that says `persist` is raised after the commit, so the
    expiry, invalidation or denial evidence it wrote survives; any other refusal rolls back."""
    refusal_error: ZidError | None = None
    out: Any = None
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        try:
            out = await work(conn)
        except ApprovalError as e:
            if not e.persist:
                raise http(e) from e
            refusal_error = http(e)
    if refusal_error is not None:
        raise refusal_error
    return out


@router.post("/v1/approval-cases", status_code=201)
async def create_case(body: CaseCreate, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _who(caller)
    v: svc.CaseView = await _commit_then(
        request,
        caller,
        lambda conn: svc.create_case(
            conn, tenant, me, action_id=body.action_id, target_id=body.target_id, payload=body.payload,
            reason=body.reason, ttl_seconds=body.ttl_seconds, evidence_ref=body.evidence_ref,
            platform_tenant_id=_platform(request),
        ),
    )  # fmt: skip
    return case_view(v, mine=(), me=me)


@router.get("/v1/approval-cases")
async def list_cases(
    request: Request, caller: CallerDep, state: State | None = None, limit: int = 100
) -> dict[str, Any]:
    _, me = _who(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        cases = await store.list_cases(
            conn, states=(state,) if state else None, limit=max(1, min(limit, 200))
        )
        out = []
        for c in cases:
            if not await svc.can_view(conn, c, me):
                continue
            now = datetime.now(UTC)
            v = await svc.view(conn, c, now=now)
            out.append(case_view(v, mine=await svc.my_refusals(conn, c, me, now), me=me))
    return {"items": out}


@router.get("/v1/approval-cases/{case_id}")
async def read_case(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _, me = _who(caller)

    async def work(conn: Any) -> dict[str, Any]:
        c = await store.load_case(conn, str(case_id))
        if c is None or not await svc.can_view(
            conn, c, me
        ):  # not yours to see is the same answer as not there
            raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
        now = datetime.now(UTC)
        c = await svc._lapse(conn, c, now)
        v = await svc.view(conn, c, now=now)
        return case_view(v, mine=await svc.my_refusals(conn, c, me, now), me=me)

    out: dict[str, Any] = await _commit_then(request, caller, work)
    return out


@router.post("/v1/approval-cases/{case_id}/votes", status_code=201)
async def cast_vote(
    case_id: uuid.UUID, body: VoteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me = _who(caller)

    async def work(conn: Any) -> dict[str, Any]:
        async def step_up(case: store.Case) -> dict[str, Any] | None:
            # every approver proves themselves, for this request and this hash, and a shadow period is not allowed for a vote
            return await require_step_up(
                request, conn, caller, action=STEP_UP_ACTION, resource=f"approval-case:{case.case_id}",
                change={"case_id": case.case_id, "request_hash": case.request_hash, "vote": body.vote},
                critical=case.tier is Tier.A2, force_enforce=True,
            )  # fmt: skip

        v = await svc.cast_vote(
            conn,
            tenant,
            me,
            str(case_id),
            vote=body.vote,
            presented_hash=body.request_hash,
            step_up=step_up,
        )
        return case_view(v, mine=await svc.my_refusals(conn, v.case, me, datetime.now(UTC)), me=me)

    out: dict[str, Any] = await _commit_then(request, caller, work)
    return out


@router.post("/v1/approval-cases/{case_id}:revise", status_code=201)
async def revise(
    case_id: uuid.UUID, body: CaseRevise, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me = _who(caller)
    v: svc.CaseView = await _commit_then(
        request,
        caller,
        lambda conn: svc.revise_case(
            conn,
            tenant,
            me,
            str(case_id),
            payload=body.payload,
            reason=body.reason,
            platform_tenant_id=_platform(request),
        ),
    )
    return case_view(v, mine=(), me=me)


@router.post("/v1/approval-cases/{case_id}:cancel")
async def cancel(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _who(caller)
    v: svc.CaseView = await _commit_then(
        request, caller, lambda conn: svc.cancel_case(conn, tenant, me, str(case_id))
    )
    return case_view(v, mine=(), me=me)


@router.post("/v1/approval-cases/{case_id}/execute")
async def execute(
    case_id: uuid.UUID,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None,
) -> dict[str, Any]:
    tenant, me = _who(caller)
    if not idempotency_key:
        raise ZidError(
            "IDEMPOTENCY_KEY_REQUIRED", "Send an Idempotency-Key header.", http_status=400
        )
    r: svc.ExecutionResult = await _commit_then(
        request,
        caller,
        lambda conn: svc.execute_case(
            conn, tenant, me, str(case_id), idempotency_key=idempotency_key
        ),
    )
    out: dict[str, Any] = {
        "outcome": r.outcome,
        "replayed": r.replayed,
        "attempt": r.attempt,
        "reason_code": r.reason_code,
        "result": r.result,
        "case": case_view(r.view, mine=(), me=me),
    }
    if r.once:
        out["shown_once"] = r.once  # an enrollment link: never stored, never shown again
    if r.outcome == "FAILED" and not r.replayed:
        out["guidance"] = (
            "The change was not applied. The approved request is unchanged and may be run again until it expires."
        )
    return out


@router.get("/v1/approval-cases/{case_id}/evidence")
async def evidence(case_id: uuid.UUID, request: Request, caller: CallerDep) -> dict[str, Any]:
    _, me = _who(caller)

    async def work(conn: Any) -> dict[str, Any]:
        c = await store.load_case(conn, str(case_id))
        if c is None or not await svc.can_view(conn, c, me):
            raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
        votes = await store.votes(conn, c.case_id)
        return {
            "case_id": c.case_id,
            "version": c.version,
            "supersedes_case_id": c.supersedes_case_id,
            "action_id": c.action_id,
            "tier": c.tier.value,
            "request_hash": c.request_hash,
            "policy_version": c.policy_version,
            "region": c.region,
            "maker": {
                "principal_id": c.requester_principal_id,
                "person_ref": _person_ref(c.requester_person_id),
            },
            "editors": len(c.editor_persons),
            "checkers": [_vote(v) for v in votes],
            "distinct_checkers": len({v.approver_person_id for v in votes if v.vote == "APPROVE"}),
            "transitions": [
                {**t, "at": t["at"].isoformat()} for t in await store.transitions(conn, c.case_id)
            ],
            "executions": [
                {
                    "attempt": x.attempt,
                    "outcome": x.outcome,
                    "reason_code": x.reason_code,
                    "executed_at": x.executed_at.isoformat(),
                    "idempotency_key_hash": hashlib.sha256(x.idempotency_key.encode()).hexdigest()[
                        :16
                    ],
                    "quorum": x.quorum,
                }
                for x in await store.executions(conn, c.case_id)
            ],
            "state": c.state.value,
            "approved_vs_applied": {
                "applied_at": c.executed_at.isoformat() if c.executed_at else None,
            },
            "audit": {
                "target_type": "approval_case",
                "target_ref": c.case_id,
                "event_prefix": "governance.",
            },
        }

    out: dict[str, Any] = await _commit_then(request, caller, work)
    return out


# ------------------------------------------------------------------------------- recovery and containment


class RecoveryOpen(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    identity_reproof_ref: str = Field(min_length=1, max_length=128)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/v1/recovery-cases", status_code=201)
async def open_recovery(body: RecoveryOpen, request: Request, caller: CallerDep) -> dict[str, Any]:
    """A privileged recovery is an A2 case: independent re-proofing is named, two independent people approve the unmodified request,
    and the result is a narrow, expiring, single-use capability. Nothing is restored silently."""
    tenant, me = _who(caller)
    v: svc.CaseView = await _commit_then(
        request,
        caller,
        lambda conn: svc.create_case(
            conn, tenant, me, action_id="privileged-owner-or-admin-recovery", target_id=f"principal:{body.principal_id}",
            payload={"principal_id": str(body.principal_id), "identity_reproof_ref": body.identity_reproof_ref},
            reason=body.reason, evidence_ref=body.identity_reproof_ref,
        ),
    )  # fmt: skip
    return case_view(v, mine=(), me=me)


class Containment(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_id: str = Field(min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    reason: str = Field(min_length=1, max_length=500)


@router.post("/v1/containment-actions", status_code=201)
async def contain(body: Containment, request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant, me = _who(caller)
    out: dict[str, Any] = await _commit_then(
        request,
        caller,
        lambda conn: svc.contain(
            conn, tenant, me, action_id=body.action_id, payload=body.payload, reason=body.reason
        ),
    )
    return out


# ---------------------------------------------------------------------------------------------- reviewers


class ReviewerAppoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: uuid.UUID
    relationship: Literal["EXTERNAL_PROFESSIONAL", "CO_DIRECTOR", "TRUSTEE", "AUTHORIZED_DELEGATE"]
    authority_basis: str = Field(min_length=1, max_length=500)
    independence_declaration: str = Field(min_length=1, max_length=500)
    declared_conflicts: list[uuid.UUID] = Field(default_factory=list, max_length=50)


@router.get("/v1/governance/reviewers")
async def list_reviewers(request: Request, caller: CallerDep) -> dict[str, Any]:
    _who(caller)
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        rows = await store.reviewers(conn)
        refs = await people.person_refs(conn, [r.principal_id for r in rows])
    return {
        "items": [
            {**reviewer_svc.public(r), "person": refs[r.principal_id].view()} for r in rows
        ]
    }


@router.post("/v1/governance/reviewers", status_code=201)
async def appoint_reviewer(
    body: ReviewerAppoint, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me = _who(caller)
    r, case = await _commit_then(
        request,
        caller,
        lambda conn: reviewer_svc.appoint(
            conn, tenant, me, principal_id=str(body.principal_id), relationship=body.relationship,
            authority_basis=body.authority_basis, independence_declaration=body.independence_declaration,
            declared_conflict_principals=[str(c) for c in body.declared_conflicts],
        ),
    )  # fmt: skip
    return {**reviewer_svc.public(r), "activation_case": case_view(case, mine=(), me=me)}


@router.post("/v1/governance/reviewers/{reviewer_id}:revoke")
async def revoke_reviewer(
    reviewer_id: uuid.UUID, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant, me = _who(caller)
    r = await _commit_then(
        request, caller, lambda conn: reviewer_svc.revoke(conn, tenant, me, str(reviewer_id))
    )
    return reviewer_svc.public(r)
