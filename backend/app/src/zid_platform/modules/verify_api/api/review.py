"""Case review and four-eyes control for the company's own reviewers (CR-167; ZID-CP-WF04 v2.0). A signed-in person, behind the PDP, with three separate actions:

verify_api.review.read      list and read cases (redacted evidence), read a case's event trail         (R1, an administrator by default)
verify_api.review.work      claim, record findings, ask the applicant, escalate, propose, reveal, appeal  (R2, granted by the owner)
verify_api.review.approve   give the independent second review, review an escalated case                (R2, granted by the owner; never the same person as the proposer)

GET  /v1/verify-api/review-cases                          the queue (fixed filters: status, queue)
POST /v1/verify-api/review-cases                          open a case for a session waiting for review
GET  /v1/verify-api/review-cases/{id}                     one case: state, version, lease, redacted evidence, the open proposal
POST /v1/verify-api/review-cases/{id}:claim | :heartbeat | :release | :findings | :request-info | :info-received | :escalate | :propose
POST /v1/verify-api/review-cases/{id}:second-review       approve (finality runs in the same transaction), return or escalate
POST /v1/verify-api/review-cases/{id}/evidence/{eid}:reveal   one sensitive item, with a reason, for five minutes, counted
POST /v1/verify-api/review-cases/{id}:appeal              a separate case that may supersede a final decision
GET  /v1/verify-api/review-cases/{id}/events              the hash-chained trail and whether the chain is intact
POST /v1/verify-api/review-cases/{id}:agent-advice        advisory only; answers 404 unless the cell enables the adviser
POST /v1/verify-api/review-cases/{id}/advice/{aid}:feedback

Every change carries `expected_case_version` (the version the person read). A stale one changes nothing and answers CASE_STALE_VERSION.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.application import review_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

READ, WORK, APPROVE = (
    "verify_api.review.read",
    "verify_api.review.work",
    "verify_api.review.approve",
)
RESOURCE = "verify_api_review:*"

STATUS = {
    "CASE_NOT_FOUND": 404,
    "NOT_FOUND": 404,
    "CASE_STALE_VERSION": 409,
    "ASSIGNMENT_CONFLICT": 409,
    "CASE_STATE_INVALID": 409,
    "CASE_FINAL": 409,
    "CASE_OPEN": 409,
    "CASE_NOT_ALLOWED": 409,
    "EVIDENCE_CHANGED": 409,
    "FINALITY_REFUSED": 409,
    "REVIEWER_NOT_INDEPENDENT": 403,
    "EVIDENCE_RESTRICTED": 403,
    "REGULATORY_HOLD": 423,
    "MANDATORY_CHECK_MISSING": 422,
    "CHALLENGE_INCOMPLETE": 422,
    "NOTE_REQUIRED": 422,
    "VALIDATION_FAILED": 422,
    "RATE_LIMITED": 429,
    "ADVISER_NOT_ENABLED": 404,
}


def fail(e: PartnerError) -> ZidError:
    return ZidError(e.code, e.detail, http_status=STATUS.get(e.code, 422))


async def _allowed(conn: Any, caller: Caller, *actions: str) -> None:
    """Allowed if the person holds ANY of the named actions; denied with the last refusal otherwise."""
    last: ZidError | None = None
    for a in actions:
        try:
            await protected_action(conn, caller, action=a, resource=RESOURCE)
            return
        except ZidError as e:
            last = e
    assert last is not None
    raise last


class ReviewOpenBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    session_id: str = Field(max_length=64)


class ReviewVersionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_case_version: int | None = None


class ReviewNoteBody(ReviewVersionBody):
    note: str | None = Field(default=None, max_length=1000)


class ReviewFindingsBody(ReviewNoteBody):
    findings: dict[str, str] = Field(max_length=8)


class ReviewInfoBody(ReviewVersionBody):
    template: str = Field(max_length=40)


class ReviewProposeBody(ReviewVersionBody):
    proposed_outcome: str = Field(max_length=24)
    reason_code: str = Field(max_length=40)
    evidence_manifest_hash: str | None = Field(default=None, max_length=80)


class ReviewSecondBody(ReviewVersionBody):
    decision: str = Field(max_length=10)
    challenge_answers: dict[str, bool] = Field(default_factory=dict, max_length=8)
    reason_code: str | None = Field(default=None, max_length=40)
    note: str | None = Field(default=None, max_length=1000)


class ReviewRevealBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(max_length=1000)


class ReviewFeedbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feedback: str = Field(max_length=12)


async def _run(
    request: Request, caller: Caller, actions: tuple[str, ...], fn: Any
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await _allowed(conn, caller, *actions)
        try:
            return await fn(conn, caller.ctx.principal_id or "")
        except PartnerError as e:
            raise fail(e) from e


async def _with_viewer(result: Any, me: str) -> dict[str, Any]:
    """Who is asking, so the screen can tell 'mine' from 'someone else's'."""
    out = await result
    out["viewer"] = me
    return out


@router.get("/v1/verify-api/review-cases")
async def list_cases(
    request: Request,
    caller: CallerDep,
    status: str | None = Query(default=None, max_length=30),
    queue: str | None = Query(default=None, max_length=12),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (READ,),
        lambda conn, me: _with_viewer(
            svc.list_cases(conn, status=status, queue=queue, limit=limit), me
        ),
    )


@router.post("/v1/verify-api/review-cases", status_code=201)
async def open_case(body: ReviewOpenBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK,),
        lambda conn, me: svc.open_case(conn, caller.ctx.tenant_id or "", me, body.session_id),
    )


@router.get("/v1/verify-api/review-cases/{case_id}")
async def get_case(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request, caller, (READ,), lambda conn, me: _with_viewer(svc.get(conn, case_id), me)
    )


@router.post("/v1/verify-api/review-cases/{case_id}:claim")
async def claim(
    case_id: str, body: ReviewVersionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    # an escalated case is for a reviewer who may give second reviews; any other is for a reviewer who may work
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        from zid_platform.modules.verify_api.infrastructure import review_store as db

        c = await db.get_case(conn, case_id)
        await _allowed(
            conn, caller, *((APPROVE,) if c is not None and c["status"] == "ESCALATED" else (WORK,))
        )
        try:
            return await svc.claim(
                conn, case_id, caller.ctx.principal_id or "", body.expected_case_version
            )
        except PartnerError as e:
            raise fail(e) from e


@router.post("/v1/verify-api/review-cases/{case_id}:heartbeat")
async def heartbeat(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(
        request, caller, (WORK, APPROVE), lambda conn, me: svc.heartbeat(conn, case_id, me)
    )


@router.post("/v1/verify-api/review-cases/{case_id}:release")
async def release(
    case_id: str, body: ReviewNoteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.release(conn, case_id, me, body.expected_case_version, body.note),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:findings")
async def findings(
    case_id: str, body: ReviewFindingsBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.record_findings(
            conn, case_id, me, body.expected_case_version, body.findings, body.note
        ),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:request-info")
async def request_info(
    case_id: str, body: ReviewInfoBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.request_information(
            conn, case_id, me, body.expected_case_version, body.template
        ),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:info-received")
async def info_received(
    case_id: str, body: ReviewVersionBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK,),
        lambda conn, me: svc.information_received(conn, case_id, me, body.expected_case_version),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:escalate")
async def escalate(
    case_id: str, body: ReviewNoteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK,),
        lambda conn, me: svc.escalate(conn, case_id, me, body.expected_case_version, body.note),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:propose")
async def propose(
    case_id: str, body: ReviewProposeBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.propose(
            conn,
            case_id,
            me,
            body.expected_case_version,
            body.proposed_outcome,
            body.reason_code,
            body.evidence_manifest_hash,
        ),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:second-review")
async def second_review(
    case_id: str, body: ReviewSecondBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (APPROVE,),
        lambda conn, me: svc.second_review(
            conn,
            case_id,
            me,
            body.expected_case_version,
            body.decision,
            body.challenge_answers,
            body.reason_code,
            body.note,
        ),
    )


@router.post("/v1/verify-api/review-cases/{case_id}/evidence/{evidence_id}:reveal")
async def reveal(
    case_id: str, evidence_id: str, body: ReviewRevealBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.reveal(conn, case_id, me, evidence_id, body.reason),
    )


@router.post("/v1/verify-api/review-cases/{case_id}:appeal", status_code=201)
async def appeal(
    case_id: str, body: ReviewNoteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK,),
        lambda conn, me: svc.appeal(conn, caller.ctx.tenant_id or "", me, case_id, body.note),
    )


@router.get("/v1/verify-api/review-cases/{case_id}/events")
async def events(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, (READ,), lambda conn, me: svc.audit(conn, case_id))


@router.post("/v1/verify-api/review-cases/{case_id}:agent-advice")
async def agent_advice(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    adviser = getattr(request.app.state, "review_adviser", None)
    enabled = bool(getattr(request.app.state, "review_agent_enabled", False))
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.ask_adviser(conn, case_id, me, adviser, enabled),
    )


@router.get("/v1/verify-api/review-cases/{case_id}/advice")
async def advice(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    return await _run(request, caller, (READ,), lambda conn, me: svc.list_advice(conn, case_id))


@router.post("/v1/verify-api/review-cases/{case_id}/advice/{advice_id}:feedback")
async def advice_feedback(
    case_id: str,
    advice_id: uuid.UUID,
    body: ReviewFeedbackBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    return await _run(
        request,
        caller,
        (WORK, APPROVE),
        lambda conn, me: svc.advice_feedback(conn, case_id, str(advice_id), body.feedback),
    )
