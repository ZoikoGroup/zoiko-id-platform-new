"""The role matrix as a route dependency (ZID-ENG-DEC-007: authorization is always evaluated server-side at the resource boundary).

`matrix_gate(action, mode)` decides in its own short transaction and commits the decision evidence before it raises, so a refused
request still leaves its proof. It judges the role and the tenant's state; the session assurance an action needs is enforced by
the protected action behind the route (and by step-up, ENG-DEC-009), so it is declared established here.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Request
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.tenant.application import access
from zid_platform.modules.tenant.domain.role_matrix import Decision, Mode, Outcome
from zid_platform.modules.tenant.infrastructure import route_map as routes

log = logging.getLogger("zid.route_matrix")

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

    gate.matrix_action = action_id  # type: ignore[attr-defined]  # read by the coverage test: this route is gated inline
    gate.matrix_mode = mode  # type: ignore[attr-defined]
    return gate


async def route_matrix_guard(request: Request, caller: Caller) -> None:
    """The central check (slice L-7): after authentication, a route that `route-map.yaml` maps to a matrix action is judged with the
    caller's live roles. Enforcement follows `route_matrix_enforcement`: REPORT_ONLY records the refusal and lets the request through,
    ENFORCED refuses it. A route the map exempts, or does not map, is not judged here (the coverage test keeps that list complete).
    A failure to decide is a refusal when enforced and a log line when not, never a silent allow of an enforced route."""
    route = request.scope.get("route")
    rule = routes.route_map().classify(
        request.method, getattr(route, "path_format", request.url.path)
    )
    if rule is None or not rule.gates:
        return
    tenant, principal = caller.ctx.tenant_id or "", caller.ctx.principal_id
    if not tenant or not principal:
        return  # not a tenant member's call: the route's own authentication decides
    enforced = routes.route_matrix_enforced()
    try:
        assert rule.action is not None and rule.mode is not None
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            decision = await access.evaluate(
                conn, tenant, principal, rule.action, rule.mode, object_id=f"route:{request.method} {rule.match}"[:128], session_ok=True
            )  # fmt: skip
    except Exception:
        log.exception("route matrix could not decide %s %s", request.method, rule.match)
        if enforced:
            raise ZidError(
                "ROLE_DECISION_UNAVAILABLE", "The role decision could not be made.", http_status=503
            ) from None
        return
    if decision.allowed:
        return
    if enforced:
        raise refusal(decision)
    log.warning(
        "route matrix would refuse %s %s: %s %s %s",
        request.method,
        rule.match,
        rule.action,
        rule.mode.value if rule.mode else "",
        ",".join(decision.reasons),
    )
