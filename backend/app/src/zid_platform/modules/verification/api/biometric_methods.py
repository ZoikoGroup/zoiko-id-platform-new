"""Biometric method eligibility for a tenant (ZID-DTV-WF-04 section 19; BIO-03; QA-001, QA-043; CR-107).

    GET /v1/biometric-methods/eligibility?country=GB&purpose=IDENTITY_VERIFICATION      verification:read

Which biometric methods (face match, liveness) this tenant's live sessions may use for the country and purpose, and every reason a method may not:
the deployment's accepted engine, the method's approved state for this exact scope (a pause always wins), this tenant's own DPIA read live from the
privacy release gate, and the country's signed privacy overlay. No biometric method is ever available by default. The answer carries `policy_hash`, the
digest of exactly what it rested on. It reads no face, template or media, and says nothing about any person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import biometric_eligibility
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/v1/biometric-methods/eligibility")
async def eligibility(
    request: Request,
    caller: CallerDep,
    country: Annotated[str, Query(pattern=r"^[A-Za-z]{2}$")],
    purpose: Annotated[str, Query(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")] = "IDENTITY_VERIFICATION",
) -> dict[str, Any]:
    _scope(caller, "verification:read")
    overlays = getattr(request.app.state, "privacy_overlays", None) or privacy_overlays()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        result = await biometric_eligibility.eligibility(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            country=country,
            purpose=purpose,
            overlays=overlays,
            now=datetime.now(UTC),
        )
    return result.view()
