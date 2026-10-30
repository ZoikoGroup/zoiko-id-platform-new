"""The role matrix as a route dependency (ZID-ENG-DEC-007: authorization is always evaluated server-side at the resource boundary).

`matrix_gate(action, mode)` decides in its own short transaction and commits the decision evidence before it raises, so a refused
request still leaves its proof. It judges the role and the tenant's state; the session assurance an action needs is enforced by
the protected action behind the route (and by step-up, ENG-DEC-009), so it is declared established here.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.tenant.application import access
from zid_platform.modules.tenant.domain.role_matrix import Decision, Mode, Outcome

CallerDep = Annotated[Caller, Depends(authenticated_caller)]


def refusal(decision: Decision, code: str = "ROLE_NOT_PERMITTED") -> ZidError:
    return ZidError(
        code,
        "The caller's role does not permit this action.",
        http_status=403 if decision.outcome is Outcome.DENY else 503,
        details=[ErrorDetail(field="role", issue=r) for r in decision.reasons],
    )


def matrix_gate(action_id: str, mode: Mode) -> Callable[[Request, CallerDep], Awaitable[None]]:
    async def gate(request: Request, caller: CallerDep) -> None:
        if not caller.ctx.principal_id:
            raise ZidError(
                "UNAUTHENTICATED", "An authenticated principal is required.", http_status=401
            )
        tenant = caller.ctx.tenant_id or ""
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            decision = await access.evaluate(
                conn,
                tenant,
                caller.ctx.principal_id,
                action_id,
                mode,
                object_id=f"tenant:{tenant}",
                session_ok=True,
            )
        if not decision.allowed:
            raise refusal(decision)

    return gate
