"""POST /v2/age-checks (ZID-INT-002; ZID-GAP-001 v2.0 DTV-19): "is this person at least N", never a date of birth.

A stateless threshold calculation over the rules in `verification/domain/age.py`. Nothing is stored and no birth date, age or estimate is
returned: the answer is AT_LEAST, UNDER or UNDETERMINED with its method, how long it can be relied on, and whether a documented check is
offered instead of a refusal.

What this route cannot do, and says so: it has no attested source of birth dates (a passport chip check, a government age-verification
service, a verified identity case). A birth date sent here is therefore always treated as SELF_DECLARED and gives UNDETERMINED
(BIRTH_DATE_NOT_VERIFIED), because anyone can type anything. An age ESTIMATE from the caller's own model decides only when its whole margin
plus a safety buffer sits on one side of the line. When a source of verified birth dates exists, it is a change to `verified=` below.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.domain import age
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
LIMITATIONS = [
    "No attested birth-date source is connected: a birth date sent here is self-declared and never gives AT_LEAST.",
    "An estimate decides only when its whole margin plus a safety buffer is on one side of the threshold; near the line it is UNDETERMINED.",
    "UNDETERMINED is neither a pass nor a refusal: offer the person a documented check.",
    "Nothing is stored and no birth date, age or estimate is returned.",
]


class EstimateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    estimated_age: float = Field(ge=0, le=130)
    margin: float = Field(ge=0, le=60)  # the model's own stated error, in years


class V2AgeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    threshold: int
    birth_date: date | None = None
    estimate: EstimateIn | None = None

    @model_validator(mode="after")
    def _some_evidence(self) -> V2AgeIn:
        if self.birth_date is None and self.estimate is None:
            raise ValueError("send a birth_date or an estimate")
        return self


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


@router.post("/v2/age-checks")
async def age_check(body: V2AgeIn, request: Request, caller: CallerDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="credential.claim.verify", resource="age-check:new"
        )
    now = datetime.now(UTC)
    try:
        found: list[age.AgeAssertion] = []
        if body.birth_date is not None:
            found.append(
                age.from_birth_date(body.birth_date, body.threshold, on=now.date(), verified=False)
            )
        if body.estimate is not None:
            found.append(
                age.from_estimate(
                    body.estimate.estimated_age, body.estimate.margin, body.threshold, now=now
                )
            )
        # one piece of evidence stands as it is (combine() would drop its reason); several are weighed together
        result = found[0] if len(found) == 1 else age.combine(found)
    except age.AgeError as e:
        raise ZidError(e.code, e.code.replace("_", " ").capitalize() + ".", http_status=422) from e
    return {**_ids(), **result.to_contract(), "limitations": LIMITATIONS}
