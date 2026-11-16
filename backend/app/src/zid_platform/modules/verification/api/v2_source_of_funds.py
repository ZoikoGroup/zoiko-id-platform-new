"""Source of funds and wealth over HTTP (ZID-GAP-001 v2.0 DTV-28; CR-096). A questionnaire, evidence references and a person's decision.

    GET  /v2/source-of-funds/questionnaire?country=XX      the questions a case will ask                            verification.sof.submit
    POST /v2/source-of-funds/cases                         start a case for a person                               verification.sof.submit
    POST /v2/source-of-funds/cases/{id}:submit             answers and evidence references; complete -> NEEDS_REVIEW verification.sof.submit
    GET  /v2/source-of-funds/cases/{id}                    one case, with its answers and its decisions            verification.sof.read
    GET  /v2/source-of-funds/cases?state=                  the review queue (no answers)                           verification.sof.review
    POST /v2/source-of-funds/cases/{id}:decide             a reviewer clears, does not clear, or asks for more      verification.sof.review
    POST /v2/source-of-funds/cases/{id}:appeal             the person concerned appeals a first adverse decision    verification.sof.submit

The product line gate comes first on every route that starts or changes a case: the shipped configuration lists `source_of_funds` as DESIGN, so every
such route answers SERVICE_UNAVAILABLE until the owners record a legal basis and switch the line on. A SAMPLE questionnaire cannot be used on a LIVE line.
Nothing here clears anyone: the system only says a case is INCOMPLETE (and what is missing) or waiting for a person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application import sof_service as service
from zid_platform.modules.verification.domain import source_of_funds as sof
from zid_platform.modules.verification.domain.product_lines import LineState, ProductLineError
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)
from zid_platform.modules.verification.infrastructure.questionnaire_registry import (
    current_questionnaire,
)
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
UUID36 = Field(min_length=36, max_length=36)
STATUS = {
    "SERVICE_UNAVAILABLE": 409,
    "SERVICE_UNAVAILABLE_IN_MARKET": 409,
    "CASE_NOT_FOUND": 404,
    "REVIEWER_NOT_INDEPENDENT": 403,
    "APPEAL_NEEDS_A_DIFFERENT_REVIEWER": 403,
    "APPEAL_NOT_BY_THE_PERSON": 403,
    "QUESTIONNAIRE_NOT_FOR_COUNTRY": 422,
    "REASON_CODE_REQUIRED": 422,
    "STATE_UNKNOWN": 422,
}


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _fail(e: sof.SofError | ProductLineError) -> ZidError:
    detail = str(e).split(": ", 1)[1] if ": " in str(e) else ""
    message = e.code.replace("_", " ").capitalize() + "." + (f" {detail}." if detail else "")
    return ZidError(e.code, message, http_status=STATUS.get(e.code, 409))


def _line_live(request: Request, country: str) -> bool:
    """Whether the product line may run for this country (an error otherwise) and whether it is LIVE (a SAMPLE questionnaire is then refused)."""
    from zid_platform.modules.verification.domain.product_lines import require_available

    registry = getattr(request.app.state, "product_lines", None) or product_line_registry()
    try:
        line = require_available(registry, sof.LINE, country)
    except ProductLineError as e:
        raise _fail(e) from e
    return line.state is LineState.LIVE


def _questionnaire(request: Request) -> sof.Questionnaire:
    override = getattr(request.app.state, "sof_questionnaire", None)
    try:
        return override or current_questionnaire()
    except sof.SofError as e:
        raise _fail(e) from e


class SofEvidenceRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question_id: str = Field(pattern=r"^[A-Z][A-Z0-9-]{1,31}$")
    type: str = Field(pattern=r"^[A-Z_]{3,48}$")
    upload_id: str = UUID36


class SofCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: str = UUID36
    country: str = Field(pattern=r"^[A-Z]{2}$")


class SofSubmitIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answers: dict[str, Any] = Field(max_length=sof.MAX_QUESTIONS)
    evidence: list[SofEvidenceRef] = Field(default_factory=list, max_length=sof.MAX_QUESTIONS * 2)


class SofDecideIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["CLEAR", "NOT_CLEAR", "ASK_FOR_MORE"]
    reason_codes: list[str] = Field(min_length=1, max_length=6)


async def _run(
    request: Request, caller: Caller, action: str, resource: str, work: Any
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=action, resource=resource)
        try:
            return {**_ids(), **(await work(conn))}
        except sof.SofError as e:
            raise _fail(e) from e


@router.get("/v2/source-of-funds/questionnaire")
async def read_questionnaire(
    request: Request, caller: CallerDep, country: Annotated[str, Query(pattern=r"^[A-Z]{2}$")]
) -> dict[str, Any]:
    async def work(_conn: Any) -> dict[str, Any]:
        _line_live(request, country)
        q = _questionnaire(request)
        sof.questionnaire_for_case(q, country, line_live=False)
        return {
            "code": q.code,
            "version": q.version,
            "status": q.status.value,
            "content_hash": q.content_hash,
            "questions": [
                {
                    "question_id": x.question_id,
                    "text": x.text,
                    "answer": x.kind.value,
                    "required": x.required,
                    "choices": list(x.choices),
                    "evidence_required_for": {k: list(v) for k, v in x.evidence_by_answer.items()},
                }
                for x in q.questions
            ],
        }

    return await _run(
        request, caller, "verification.sof.submit", "source-of-funds:questionnaire", work
    )


@router.post("/v2/source-of-funds/cases", status_code=201)
async def start_case(body: SofCaseIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        live = _line_live(request, body.country)
        return await service.create_case(
            conn,
            caller.ctx.tenant_id or "",
            subject=body.subject_principal_id,
            country=body.country,
            actor=caller.ctx.principal_id or "",
            questionnaire=_questionnaire(request),
            line_live=live,
        )

    return await _run(request, caller, "verification.sof.submit", "source-of-funds:new", work)


@router.post("/v2/source-of-funds/cases/{case_id}:submit")
async def submit_case(
    case_id: str, body: SofSubmitIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        existing = await service.get_case(conn, case_id)
        _line_live(request, existing["country"])
        return await service.submit(
            conn,
            caller.ctx.tenant_id or "",
            case_id,
            answers=body.answers,
            evidence=[e.model_dump() for e in body.evidence],
            actor=caller.ctx.principal_id or "",
            questionnaire=_questionnaire(request),
            now=datetime.now(UTC),
        )

    return await _run(
        request, caller, "verification.sof.submit", f"source-of-funds:{case_id}", work
    )


@router.get("/v2/source-of-funds/cases/{case_id}")
async def read_case(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.get_case(conn, case_id)

    return await _run(request, caller, "verification.sof.read", f"source-of-funds:{case_id}", work)


@router.get("/v2/source-of-funds/cases")
async def case_queue(
    request: Request,
    caller: CallerDep,
    state: Annotated[str | None, Query(max_length=40)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return {"items": await service.queue(conn, state, limit), "notice": service.NOTICE}

    return await _run(request, caller, "verification.sof.review", "source-of-funds:queue", work)


@router.post("/v2/source-of-funds/cases/{case_id}:decide")
async def decide_case(
    case_id: str, body: SofDecideIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        existing = await service.get_case(conn, case_id)
        _line_live(request, existing["country"])
        return await service.decide(
            conn,
            caller.ctx.tenant_id or "",
            case_id,
            decision=sof.Decision(body.decision),
            reasons=body.reason_codes,
            reviewer=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )

    return await _run(
        request, caller, "verification.sof.review", f"source-of-funds:{case_id}", work
    )


@router.post("/v2/source-of-funds/cases/{case_id}:appeal")
async def appeal_case(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        existing = await service.get_case(conn, case_id)
        _line_live(request, existing["country"])
        return await service.appeal(
            conn,
            caller.ctx.tenant_id or "",
            case_id,
            by=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )

    return await _run(
        request, caller, "verification.sof.submit", f"source-of-funds:{case_id}", work
    )
