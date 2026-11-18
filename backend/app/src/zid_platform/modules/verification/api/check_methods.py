"""WF-05 check method eligibility for a tenant (ZID-DTV-WF-05 §19 requirements, A01; CR-112).

    GET /v1/check-methods/eligibility?country=GB&purpose=IDENTITY_VERIFICATION      verification:read

Which WF-05 methods (email, phone, carrier, address, postal, device) this tenant's live cases may use for the country and purpose, and every reason a
method may not: no adapter, its lifecycle state (a pause always wins), a data class without an approved retention rule, an unentitled licensed route, a
missing or unsigned privacy overlay. No method is available by default ("If no lawful basis, region or approved source exists: this step is
unavailable; no silent opt-in"). The answer carries `policy_hash`. It says nothing about any person.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.api.verification import _scope
from zid_platform.modules.verification.application import check_methods
from zid_platform.modules.verification.infrastructure.privacy_overlays import privacy_overlays

router = APIRouter(tags=["verification"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/v1/check-methods/eligibility")
async def eligibility(
    request: Request,
    caller: CallerDep,
    country: Annotated[str, Query(pattern=r"^[A-Za-z]{2}$")],
    purpose: Annotated[str, Query(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")] = "IDENTITY_VERIFICATION",
) -> dict[str, Any]:
    _scope(caller, "verification:read")
    overlays = getattr(request.app.state, "privacy_overlays", None) or privacy_overlays()
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        result = await check_methods.eligibility(
            conn,
            tenant_id=caller.ctx.tenant_id or "",
            country=country,
            purpose=purpose,
            overlays=overlays,
            now=datetime.now(UTC),
        )
    return result.view()
