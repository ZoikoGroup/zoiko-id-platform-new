"""Verify API usage for the company (CR-154; ZID-EXT-IDV-001 epic E17, WF-15). A signed-in administrator, behind the PDP.

GET /v1/verify-api/usage?month=2026-11    sessions started and their results, per application, sandbox and production kept apart, with the reconciliation against the usage ledger   verify_api.application.read
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verify_api.api.go_live import fail
from zid_platform.modules.verify_api.application import usage_service as svc
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.protected import protected_action

router = APIRouter(tags=["verify-api"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/v1/verify-api/usage")
async def usage(
    request: Request, caller: CallerDep, month: Annotated[str | None, Query(max_length=7)] = None
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(
            conn, caller, action="verify_api.application.read", resource="verify_api_application:*"
        )
        try:
            return await svc.report(conn, svc.check_month(month))
        except PartnerError as e:
            raise fail(e) from e
