"""GET /v2/capture-alternatives (ZID-GAP-001 v2.0 GID-062; GLB-30): what a person can do instead of taking a selfie or photographing a document.

Stateless and read-only. It answers "what are the other ways", never asks why, and records nothing. Choosing one of the routes is not a failed
attempt and cannot reject anyone: the response says so in fields a client can rely on, so a screen never has to guess.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.domain import capture_alternatives as alt
from zid_platform.modules.verification.infrastructure.capture_alternatives_registry import (
    capture_alternatives,
)
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
STATEMENT = [
    "You do not need to give a reason, or any proof, to use one of these routes.",
    "Choosing one is not a failed attempt and cannot reject you. The check carries on by the route you choose.",
    "A route can give a different level of assurance than camera capture. That is stated for each route; it is not a judgement about you.",
]


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


@router.get("/v2/capture-alternatives")
async def list_alternatives(
    request: Request,
    caller: CallerDep,
    country: Annotated[str, Query(pattern=r"^[A-Za-z]{2}$")],
    reason: Annotated[str | None, Query(max_length=40)] = None,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn,
            caller,
            action="credential.claim.verify",
            resource="credential-claim:capture-alternatives",
        )
    try:
        offer = alt.offer(capture_alternatives(), country, reason)
    except alt.AlternativeError as e:  # a configuration that cannot occur once it has loaded
        raise ZidError(e.code, "No alternative is configured.", http_status=500) from e
    return {
        **_ids(),
        "country": offer.country,
        "routes": [
            {
                "code": a.code,
                "title": a.title,
                "how": a.how,
                "assurance_note": a.assurance_note,
                "needs_a_person": a.needs_human,
            }
            for a in offer.routes
        ],
        "reason_required": offer.reason_required,
        "counts_as_failed_attempt": offer.counts_as_failed_attempt,
        "rejection_possible": offer.rejection_possible,
        "statement": STATEMENT,
    }
