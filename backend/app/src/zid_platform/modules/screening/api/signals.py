"""The reviewer queue for fraud and adverse-media signals (CR-068; ZID-GAP-001 WP-14).

    GET  /v1/reviews/signals                         everything still waiting for a person           (verification.screening.signal.read)
    POST /v1/reviews/signals/{id}/challenge          dispute a signal; it stays open as CHALLENGED   (verification.screening.signal.review)
    POST /v1/reviews/signals/{id}/resolution         a person's ruling: an outcome, a reason, evidence (verification.screening.signal.review)

A signal is a flag for a person to look at. It never blocks, approves or clears anything: only a ruling here closes an item, and the signal
and its counts stay exactly as they were raised. The queue holds hashes, counts and provider findings, never an address, phone or device id.
These are reviewer routes (protected actions: a tenant role never grants them), like the other `/v1/reviews/*` routes.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.screening.application import signals as service
from zid_platform.modules.screening.domain.decision import ScreeningError
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/reviews/signals", tags=["screening-signals"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]

_STATUS = {
    "REVIEW_NOT_FOUND": 404,
    "SIGNAL_ALREADY_RESOLVED": 409,
}


def _error(e: ScreeningError) -> ZidError:
    return ZidError(
        e.code,
        e.detail or e.code.replace("_", " ").capitalize() + ".",
        http_status=_STATUS.get(e.code, 422),
    )


def _me(caller: Caller) -> str:
    if not caller.ctx.principal_id:
        raise ZidError(
            "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
        )
    return caller.ctx.principal_id


class ChallengeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    statement: str = Field(
        min_length=1, max_length=2000
    )  # the service enforces the 20-character floor with a clear code
    evidence_ref: str = Field(min_length=1, max_length=512)


class ResolutionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    outcome: Literal["CLEARED_BY_REVIEWER", "CONFIRMED_RISK", "ESCALATED"]
    rationale: str = Field(
        min_length=1, max_length=2000
    )  # at least a sentence: the service says so with a clear code
    evidence_ref: str = Field(min_length=1, max_length=512)


@router.get("")
async def queue(request: Request, caller: CallerDep) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.signal.read",
            resource="screening_signal:queue",
        )
        rows = await service.open_queue(conn, tenant)
    return {"items": [service.review_view(r) for r in rows]}


@router.post("/{review_id}/challenge")
async def challenge(
    review_id: uuid.UUID, body: ChallengeIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.signal.review",
            resource=f"screening_signal:{review_id}",
        )
        try:
            row = await service.challenge(
                conn,
                tenant,
                str(review_id),
                statement=body.statement,
                evidence_ref=body.evidence_ref,
                now=datetime.now(UTC),
            )
        except ScreeningError as e:
            raise _error(e) from e
    return service.review_view(row)


@router.post("/{review_id}/resolution")
async def resolution(
    review_id: uuid.UUID, body: ResolutionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    tenant = caller.ctx.tenant_id or ""
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="verification.screening.signal.review",
            resource=f"screening_signal:{review_id}",
        )
        try:
            row = await service.resolve(
                conn,
                tenant,
                str(review_id),
                reviewer_id=_me(caller),
                outcome=body.outcome,
                rationale=body.rationale,
                evidence_ref=body.evidence_ref,
                now=datetime.now(UTC),
            )
        except ScreeningError as e:
            raise _error(e) from e
    return service.review_view(row)
