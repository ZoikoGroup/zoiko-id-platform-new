"""DAARA, the human review queue and governance alert handling (ZID-AG-WF-001 v1.0 section 10; ZID-AG-WF-002 section 15; CR-106).

    GET  /v1/machine/daara/template                          what the template does and never does          agent.daara.read
    GET  /v1/machine/agents/{id}/daara                       setup, stage, blockers, effective scope        agent.daara.read
    PUT  /v1/machine/agents/{id}/daara                       create or change the setup                     agent.daara.manage
    POST /v1/machine/agents/{id}/daara:validate              the authoritative compile                      agent.daara.manage
    POST /v1/machine/agents/{id}/daara/dry-run               synthetic fixture, no writes                   agent.daara.manage
    POST /v1/machine/agents/{id}/daara:request-publish       draft and request the independent approvals    agent.daara.manage
    POST /v1/machine/agents/{id}/daara/runs                  a manual governed run (Idempotency-Key)        agent.daara.run
    GET  /v1/machine/agents/{id}/daara/runs[/{run_id}]       results; detail redacted by role               agent.daara.read
    GET  /v1/machine/agent-review[/{id}]                     the human review queue                         agent.review.read
    POST /v1/machine/agent-review/{id}:dismiss|:assign|:handoff   a person's decision; changes no access     agent.review.decide
    POST /v1/machine/governance/alerts/{key}:acknowledge|:route   alert handling                             agent.alert.manage

A proposal never grants anything on open or on acceptance. The runs execute inline (a queued mode is not built), so a run returns its result.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller
from zid_platform.modules.machine.api.governance import IdemKey, _can_see_detail, _run
from zid_platform.modules.machine.application import daara as svc
from zid_platform.modules.machine.application import governance as gov
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-daara"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]


async def _daara_on(request: Request) -> None:
    """DAARA is retired (ZID_DAARA_ENABLED, default off): every DAARA route answers 404. The governance alert routes below are not DAARA's and stay on."""
    if not getattr(request.app.state, "daara_enabled", False):
        raise ZidError(
            "NOT_FOUND",
            "The DAARA agent has been replaced by the Access Readiness agent (/v1/access-readiness).",
            http_status=404,
        )


DAARA_ONLY = [Depends(_daara_on)]


def _tenant(c: Caller) -> str:
    return c.ctx.tenant_id or ""


def _actor(c: Caller) -> str:
    return c.ctx.principal_id or ""


@router.get("/daara/template", dependencies=DAARA_ONLY)
async def template(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.daara.read", resource="agent:*")
        return svc.template_view()

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/daara", dependencies=DAARA_ONLY)
async def read_setup(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.daara.read", resource=f"agent:{agent_id}"
        )
        return await svc.get_setup(conn, _tenant(caller), agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class DaaraSetupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=80)
    rationale: str | None = Field(default=None, max_length=500)
    warn_days: int = Field(default=14, ge=1, le=90)
    region: str = Field(min_length=2, max_length=64)
    ai_explain: bool = False
    schedule: Literal["MANUAL", "DAILY"] = "MANUAL"


@router.put("/agents/{agent_id}/daara", dependencies=DAARA_ONLY)
async def save_setup(
    agent_id: str, body: DaaraSetupBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.daara.manage", resource=f"agent:{agent_id}"
        )
        return await svc.save_setup(
            conn, _tenant(caller), agent_id, _actor(caller), **body.model_dump()
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/daara:validate", dependencies=DAARA_ONLY)
async def validate(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.daara.manage", resource=f"agent:{agent_id}"
        )
        return await svc.validate(conn, _tenant(caller), agent_id, _actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/daara/dry-run", dependencies=DAARA_ONLY)
async def dry_run(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.daara.manage", resource=f"agent:{agent_id}"
        )
        return await svc.dry_run(conn, _tenant(caller), agent_id, _actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/daara:request-publish", status_code=201, dependencies=DAARA_ONLY)
async def request_publish(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.daara.manage", resource=f"agent:{agent_id}"
        )
        return await svc.request_publish(conn, _tenant(caller), agent_id, _actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/daara/runs", status_code=201, dependencies=DAARA_ONLY)
async def start_run(
    agent_id: str,
    request: Request,
    response: Response,
    caller: CallerDep,
    idempotency_key: IdemKey,
    mode: Literal["inline", "queued"] = "inline",
) -> dict[str, Any]:
    """`inline` runs now and returns the result (201). `queued` accepts the run and returns 202 with state QUEUED; a worker starts it."""

    async def go(conn: Any) -> Any:
        await protected_action(conn, caller, action="agent.daara.run", resource=f"agent:{agent_id}")
        if mode == "queued":
            return await svc.enqueue(
                conn, _tenant(caller), agent_id, _actor(caller), idempotency_key=idempotency_key
            )
        return await svc.run(
            conn, _tenant(caller), agent_id, _actor(caller), idempotency_key=idempotency_key
        )

    data: dict[str, Any] = await _run(request, caller, go)
    response.status_code = 202 if mode == "queued" and data.get("state") == "QUEUED" else 201
    return data


@router.get("/agents/{agent_id}/daara/runs", dependencies=DAARA_ONLY)
async def list_runs(
    agent_id: str, request: Request, caller: CallerDep, limit: int = Query(default=20, ge=1, le=100)
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.daara.read", resource=f"agent:{agent_id}"
        )
        return {"items": await svc.list_runs(conn, agent_id, limit)}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/daara/runs/{run_id}", dependencies=DAARA_ONLY)
async def read_run(
    agent_id: str, run_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.daara.read", resource=f"agent:{agent_id}"
        )
        return await svc.run_detail(
            conn, _tenant(caller), agent_id, run_id, detail=await _can_see_detail(conn, caller)
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- the review queue


@router.get("/agent-review", dependencies=DAARA_ONLY)
async def review_queue(
    request: Request, caller: CallerDep, status: Literal["OPEN", "DISMISSED", "ASSIGNED", "HANDED_OFF"] | None = None,
    agent_id: str | None = Query(default=None, min_length=36, max_length=36), limit: int = Query(default=100, ge=1, le=200),
) -> dict[str, Any]:  # fmt: skip
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.review.read", resource="agent:*")
        return await svc.list_proposals(conn, status=status, agent_id=agent_id, limit=limit)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agent-review/{proposal_id}", dependencies=DAARA_ONLY)
async def read_proposal(proposal_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.review.read", resource="agent:*")
        return await svc.get_proposal(conn, proposal_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class DaaraDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Literal["NOT_A_RISK", "ALREADY_HANDLED", "DUPLICATE", "ACCEPTED_RISK"] | None = None
    assignee: str | None = Field(default=None, min_length=36, max_length=36)


def _decide(action: str) -> Any:
    async def route(
        proposal_id: str, body: DaaraDecisionBody, request: Request, caller: CallerDep
    ) -> dict[str, Any]:
        async def go(conn: Any) -> Any:
            await protected_action(
                conn, caller, action="agent.review.decide", resource=f"agent:review:{proposal_id}"
            )
            return await svc.decide_proposal(
                conn,
                _tenant(caller),
                _actor(caller),
                proposal_id,
                action=action,
                reason=body.reason,
                assignee=body.assignee,
            )

        return await _run(request, caller, go)  # type: ignore[no-any-return]

    return route


for _action in ("dismiss", "assign", "handoff"):
    router.add_api_route(
        f"/agent-review/{{proposal_id}}:{_action}",
        _decide(_action),
        methods=["POST"],
        name=f"review_{_action}",
        dependencies=DAARA_ONLY,
    )


# ---------------------------------------------------------------- alerts


class DaaraAckBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str | None = Field(default=None, max_length=500)


class DaaraRouteBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    route: Literal["SECURITY", "AUTHORITY", "PRIVACY", "FINANCE", "SRE"]
    owner: str | None = Field(default=None, min_length=36, max_length=36)
    note: str | None = Field(default=None, max_length=500)


@router.post("/governance/alerts/{alert_key}:acknowledge")
async def acknowledge(
    alert_key: str, body: DaaraAckBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.alert.manage", resource="agent:*")
        return await gov.acknowledge_alert(
            conn, _tenant(caller), _actor(caller), alert_key, note=body.note
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/governance/alerts/{alert_key}:route")
async def route_alert(
    alert_key: str, body: DaaraRouteBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.alert.manage", resource="agent:*")
        return await gov.route_alert(
            conn,
            _tenant(caller),
            _actor(caller),
            alert_key,
            route=body.route,
            owner=body.owner,
            note=body.note,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]
