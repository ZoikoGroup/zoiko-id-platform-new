"""Principal directory for tenant administration (ZID-DATA-001 §3; ZID-AUTH-001 §24; CR-010).

    GET  /admin/principals        list, newest first        (identity.read, R1)

Directory rows carry identifiers, type, lifecycle state and times only: no display labels,
identifier values or other personal data.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from zid_db import tenant_transaction

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.principal.domain.principal import PrincipalStatus, PrincipalType
from zid_platform.modules.principal.infrastructure.principal_store import list_principals
from zid_platform.protected import protected_action

router = APIRouter(tags=["principals"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


@router.get("/admin/principals")
async def list_all(
    request: Request,
    caller: CallerDep,
    principal_type: PrincipalType | None = None,
    status: PrincipalStatus | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="identity.read", resource="principal:*")
        items = await list_principals(
            conn, principal_type=principal_type, status=status, limit=limit
        )
    return {"items": items}
