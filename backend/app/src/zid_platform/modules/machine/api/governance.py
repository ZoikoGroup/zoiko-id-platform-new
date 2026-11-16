"""Agent governance API (ZID-AG-WF-002 v1.0, section 14; CR-105).

    GET  /v1/machine/governance/overview                          fleet overview, KPIs, attention     agent.governance.read
    GET  /v1/machine/governance/baseline                          the company baseline                agent.governance.read
    PUT  /v1/machine/governance/baseline                          set the company baseline            agent.governance.manage
    GET  /v1/machine/governance/delegates                         who may sign as SECURITY...         agent.governance.read
    POST /v1/machine/governance/delegates[:revoke]                give or take a signer slot          agent.governance.delegate (R3)
    GET  /v1/machine/governance/requests[?agent_id&state]         the approval queue                  agent.governance.read
    GET  /v1/machine/governance/requests/{id}                     one request with its diff           agent.governance.read
    POST /v1/machine/governance/requests/{id}:approve|:reject|:clarify   decide, step-up, SoD        agent.approval.sign (R3)
    POST /v1/machine/governance/requests/{id}:apply               make an approved version live       agent.approval.apply (R3)
    GET  /v1/machine/governance/events                            fleet evidence, redacted by role    agent.audit.read
    GET  /v1/machine/governance/evidence:verify                   recompute the evidence chain        agent.audit.read
    POST /v1/machine/governance/pause                             company-wide emergency freeze       agent.security.pause
    GET  /v1/machine/governance/freezes                           emergency cases                     agent.governance.read
    POST /v1/machine/governance/freezes/{case}:request-restore    never immediate                     agent.security.request_restore (R3)
    GET  /v1/machine/agents/{id}/effective-access                 compiled rights, denies, hierarchy  agent.policy.read
    GET  /v1/machine/agents/{id}/policy                           policy versions                     agent.policy.read
    POST /v1/machine/agents/{id}/policy/drafts                    draft a version (requested only)    agent.policy.request
    POST /v1/machine/agents/{id}/policy/simulate                  synthetic request, no effect        agent.policy.simulate
    POST /v1/machine/agents/{id}/change-requests                  put a draft forward                 agent.policy.request
    GET  /v1/machine/agents/{id}/budget                           committed, reserved, available      agent.budget.read
    POST /v1/machine/budgets                                      set a cap (higher = pending)        agent.budget.request
    POST /v1/machine/budget-reservations[:settle|:release]        the governed paid step              agent.run.manage
    POST /v1/machine/budget-adjustments                           late invoice, refund, reversal      agent.budget.adjust (R3)
    POST /v1/machine/agents/{id}/runs                             open a governed run                 agent.run.manage
    POST /v1/machine/runs/{id}:progress | :finish                 count records, end the run          agent.run.manage
    GET  /v1/machine/agents/{id}/events                           the agent's evidence                agent.audit.read
    GET  /v1/machine/agents/{id}/monitoring                       metrics, alerts, incident           agent.audit.read
    POST /v1/machine/agents/{id}/pause                            emergency pause, no approvals       agent.security.pause

A refusal is persisted before it is returned: each handler returns the service's Outcome from inside the transaction and raises the HTTP
error after it committed, so the evidence row is never rolled back with the error. Nothing here returns a secret or an identity artifact.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ErrorDetail, ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.machine.application import governance as svc
from zid_platform.modules.machine.domain import governance as g
from zid_platform.modules.machine.domain.agents import MachineError
from zid_platform.modules.machine.infrastructure import governance_store as store
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/machine", tags=["agent-governance"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
UUID36 = Field(min_length=36, max_length=36)
IdemKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]

_FORBIDDEN = frozenset(
    {
        "SEPARATION_OF_DUTIES",
        "SIGNER_NOT_QUALIFIED",
        "SIGNER_NOT_ACTIVE",
        "STEP_UP_REQUIRED",
        "REQUEST_FORBIDDEN",
        "RESTORE_NOT_INDEPENDENT",
        "RESTORE_NEEDS_SECURITY",
        "RESTORE_NEEDS_AUTHORITY",
        "RESTORE_NEEDS_TWO_PEOPLE",
    }
)
_BAD_INPUT = (
    "_INVALID",
    "WILDCARD",
    "BUDGET_",
    "QUOTA_",
    "MONEY_",
    "CURRENCY_",
    "SIMULATION_",
    "GRANT_",
    "NO_PARENT",
    "WARN_",
    "TOOL_HARD",
    "SLOT_",
)
_MESSAGE = "This was refused by agent governance. The evidence record explains which rule applied."


def gov_http(e: MachineError) -> ZidError:
    code = e.code
    if code.endswith("NOT_FOUND"):
        status = 404
    elif code in _FORBIDDEN:
        status = 403
    elif code.startswith(_BAD_INPUT) or code.endswith("_INVALID"):
        status = 422
    else:
        status = 409
    return ZidError(code, str(e), http_status=status)


def refusal(out: svc.Outcome) -> ZidError:
    first = out.codes[0]
    try:
        status = out.status or g.HTTP_STATUS[g.Code(first)]
    except ValueError:
        status = 403 if first in _FORBIDDEN else 409
    details = [ErrorDetail(field="reason", issue=c) for c in out.codes]
    for key in (
        "quota",
        "ceiling",
        "retry_after",
        "resets_at",
    ):  # the caller's own cap and when it resets, never another tenant's balance
        if out.data.get(key):
            details.append(ErrorDetail(field=key, issue=str(out.data[key])))
    return ZidError(first, _MESSAGE, http_status=status, details=details)


async def _run(request: Request, caller: Caller, fn: Any) -> Any:
    """Run `fn(conn)` in a tenant transaction. If it returns an Outcome that refused, the transaction commits first (the evidence
    row stays) and the refusal is raised afterwards."""
    result: Any = None
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            result = await fn(conn)
    except MachineError as e:
        raise gov_http(e) from e
    if isinstance(result, svc.Outcome):
        if not result.ok:
            raise refusal(result)
        return result.data
    return result


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _actor(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


# ---------------------------------------------------------------- reads


@router.get("/governance/overview")
async def overview(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        return await svc.overview(conn, _tenant(caller), _actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/governance/baseline")
async def read_baseline(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        return await svc.get_baseline(conn, _tenant(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovBaselineBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allowed_tools: list[str] = Field(default_factory=list, max_length=200)
    allowed_regions: list[str] = Field(default_factory=list, max_length=50)
    ai_providers: list[str] = Field(default_factory=list, max_length=20)
    quotas: dict[str, int | bool] | None = None
    schedules_allowed: bool = False


@router.put("/governance/baseline")
async def put_baseline(
    body: GovBaselineBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.manage", resource="agent:*")
        return await svc.set_baseline(conn, _tenant(caller), _actor(caller), **body.model_dump())

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/governance/delegates")
async def read_delegates(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        return {"items": await store.list_delegates(conn), "slots": list(g.SLOTS)}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovDelegateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    principal_id: str = UUID36
    slot: Literal["SECURITY", "AUTHORITY", "PRIVACY", "FINANCE"]


@router.post("/governance/delegates", status_code=201)
async def give_slot(body: GovDelegateBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.governance.delegate", resource=f"agent:delegate:{body.slot}"
        )
        return await svc.grant_slot(
            conn, _tenant(caller), _actor(caller), body.principal_id, body.slot
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/governance/delegates:revoke")
async def take_slot(body: GovDelegateBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.governance.delegate", resource=f"agent:delegate:{body.slot}"
        )
        return await svc.revoke_slot(
            conn, _tenant(caller), _actor(caller), body.principal_id, body.slot
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- approvals


@router.get("/governance/requests")
async def list_requests(
    request: Request,
    caller: CallerDep,
    agent_id: str | None = Query(default=None, min_length=36, max_length=36),
    state: Literal[
        "PENDING", "CLARIFICATION", "APPROVED", "REJECTED", "EXPIRED", "INVALIDATED", "APPLIED"
    ]
    | None = None,
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        return {
            "items": await svc.list_change_requests(
                conn, agent_id=agent_id, states=[state] if state else None, viewer=_actor(caller)
            )
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/governance/requests/{request_id}")
async def read_request(request_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        loaded = await store.load_request(conn, request_id)
        if loaded is None:
            raise svc.GovernanceError("REQUEST_NOT_FOUND", "no such request")
        rows = await svc.list_change_requests(
            conn, agent_id=None, states=None, viewer=_actor(caller)
        )
        match = [r for r in rows if r["request_id"] == request_id]
        return match[0] if match else svc.request_view(loaded[0], loaded[1])

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovDecisionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_hash: str = Field(
        pattern=r"^sha256:[0-9a-f]{64}$"
    )  # the exact hash the signer was shown
    reason: str | None = Field(default=None, max_length=64)


def _decide(decision: str, action: str = "agent.approval.sign") -> Any:
    async def route(
        request_id: str, body: GovDecisionBody, request: Request, caller: CallerDep
    ) -> dict[str, Any]:
        async def go(conn: Any) -> Any:
            await protected_action(
                conn, caller, action=action, resource=f"agent:request:{request_id}"
            )
            return await svc.decide_request(
                conn,
                _tenant(caller),
                request_id,
                _actor(caller),
                decision=decision,
                presented_hash=body.policy_hash,
                reason=body.reason,
            )

        return await _run(request, caller, go)  # type: ignore[no-any-return]

    return route


for _name, _decision in (("approve", "approve"), ("reject", "reject"), ("clarify", "clarify")):
    router.add_api_route(
        f"/governance/requests/{{request_id}}:{_name}",
        _decide(_decision),
        methods=["POST"],
        name=f"{_name}_request",
    )


@router.post("/governance/requests/{request_id}:apply")
async def apply_request(request_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.approval.apply", resource=f"agent:request:{request_id}"
        )
        return await svc.apply_request(conn, _tenant(caller), request_id, _actor(caller))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- policy


@router.get("/agents/{agent_id}/effective-access")
async def effective_access(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.policy.read", resource=f"agent:{agent_id}"
        )
        return await svc.effective_access(conn, _tenant(caller), agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/policy")
async def list_policy_versions(
    agent_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.policy.read", resource=f"agent:{agent_id}"
        )
        from datetime import UTC

        now = datetime.now(UTC)
        return {
            "items": [svc.policy_view(p, now) for p in await store.list_policies(conn, agent_id)]
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovLeaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=1, max_length=120)
    resource_kind: str = Field(min_length=1, max_length=64)
    resource_selector: str = Field(min_length=1, max_length=200)
    purpose: str = Field(min_length=1, max_length=64)
    region: str | None = Field(default=None, min_length=2, max_length=64)
    data_class: Literal["metadata", "status", "evidence_reference"] = "metadata"
    valid_until: datetime


class GovDraftBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    leases: list[GovLeaseBody] = Field(default_factory=list, max_length=50)
    quotas: dict[str, int | bool] | None = None
    region: str = Field(min_length=2, max_length=64)
    ai_enabled: bool = False
    risk_tier: Literal["LOW", "MEDIUM", "HIGH"] = "LOW"
    expires_at: datetime | None = None


@router.post("/agents/{agent_id}/policy/drafts", status_code=201)
async def draft(
    agent_id: str, body: GovDraftBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.policy.request", resource=f"agent:{agent_id}"
        )
        return await svc.draft_policy(
            conn,
            _tenant(caller),
            agent_id,
            _actor(caller),
            leases=[x.model_dump() for x in body.leases],
            quotas=body.quotas,
            region=body.region,
            ai_enabled=body.ai_enabled,
            risk_tier=body.risk_tier,
            expires_at=body.expires_at,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovSimulateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool_id: str = Field(min_length=1, max_length=120)
    resource_kind: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(
        min_length=3, max_length=200
    )  # must be synthetic, e.g. mandate:synthetic-1
    purpose: str | None = Field(default=None, max_length=64)
    region: str | None = Field(default=None, max_length=64)
    data_class: str = "metadata"
    action: str | None = Field(
        default=None, max_length=120
    )  # lets the simulator show a platform prohibition such as role.modify
    side_effect: Literal["NONE", "REVERSIBLE", "IRREVERSIBLE", "EXTERNAL_TRANSACTION"] | None = None


@router.post("/agents/{agent_id}/policy/simulate")
async def simulate(
    agent_id: str, body: GovSimulateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.policy.simulate", resource=f"agent:{agent_id}"
        )
        return await svc.simulate(
            conn, _tenant(caller), agent_id, _actor(caller), request=body.model_dump()
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovChangeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    policy_id: str = UUID36
    reason_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z0-9_]+$")


@router.post("/agents/{agent_id}/change-requests", status_code=201)
async def change_request(
    agent_id: str, body: GovChangeBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.policy.request", resource=f"agent:{agent_id}"
        )
        return await svc.request_change(
            conn,
            _tenant(caller),
            agent_id,
            _actor(caller),
            policy_id=body.policy_id,
            reason_code=body.reason_code,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- budgets, reservations, runs


@router.get("/agents/{agent_id}/budget")
async def read_budget(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.budget.read", resource=f"agent:{agent_id}"
        )
        return await svc.budget_view(conn, agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovBudgetBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    scope: Literal["COMPANY", "WORKSPACE", "AGENT", "CALL"]
    agent_id: str | None = Field(default=None, min_length=36, max_length=36)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    period: Literal["DAY", "MONTH", "CALL"]
    hard_cap: str = Field(max_length=32)  # a decimal string, never a float
    warn_pct: tuple[int, int] = (70, 90)
    reason_code: str = Field(min_length=2, max_length=64, pattern=r"^[A-Z0-9_]+$")


@router.post("/budgets", status_code=201)
async def set_budget(body: GovBudgetBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.budget.request", resource=f"agent:{body.agent_id or '*'}"
        )
        return await svc.propose_budget(conn, _tenant(caller), _actor(caller), **body.model_dump())

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovReserveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    agent_id: str = UUID36
    tool_id: str = Field(min_length=1, max_length=120)
    resource_kind: str = Field(min_length=1, max_length=64)
    resource_id: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=64)
    spend_class: Literal["AI_INFERENCE", "INTERNAL", "EXTERNAL_TRANSACTION", "PAID_VENDOR_CHECK"]
    amount: str = Field(max_length=32)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    run_id: str | None = Field(default=None, min_length=36, max_length=36)


@router.post("/budget-reservations", status_code=201)
async def reserve_budget(
    body: GovReserveBody, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.run.manage", resource=f"agent:{body.agent_id}"
        )
        data = body.model_dump()
        return await svc.reserve(
            conn,
            _tenant(caller),
            data.pop("agent_id"),
            _actor(caller),
            idempotency_key=idempotency_key,
            region=getattr(request.app.state, "region", None),
            **data,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovSettleBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    actual: str = Field(max_length=32)


@router.post("/budget-reservations/{reservation_id}:settle")
async def settle_reservation(
    reservation_id: str, body: GovSettleBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(conn, caller, action="agent.run.manage", resource="agent:*")
        return await svc.settle(
            conn, _tenant(caller), reservation_id, _actor(caller), actual=body.actual
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovReleaseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="TOOL_FAILED", pattern=r"^[A-Z0-9_]{2,64}$")


@router.post("/budget-reservations/{reservation_id}:release")
async def release_reservation(
    reservation_id: str, body: GovReleaseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(conn, caller, action="agent.run.manage", resource="agent:*")
        return await svc.release(
            conn, _tenant(caller), reservation_id, _actor(caller), reason=body.reason
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovAdjustBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reservation_id: str = UUID36
    reason: Literal["LATE_INVOICE", "REFUND", "REVERSAL"]
    amount: str = Field(max_length=32)


@router.post("/budget-adjustments", status_code=201)
async def adjust(body: GovAdjustBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.budget.adjust", resource="agent:*")
        return await svc.adjust(
            conn,
            _tenant(caller),
            body.reservation_id,
            _actor(caller),
            reason=body.reason,
            amount=body.amount,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/runs", status_code=201)
async def start_run(
    agent_id: str, request: Request, caller: CallerDep, idempotency_key: IdemKey
) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn, caller, action="agent.run.manage", resource=f"agent:{agent_id}"
        )
        return await svc.start_run(
            conn, _tenant(caller), agent_id, _actor(caller), idempotency_key=idempotency_key
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovProgressBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    records: int = Field(default=0, ge=0, le=100000)
    proposals: int = Field(default=0, ge=0, le=100000)


@router.post("/runs/{run_id}:progress")
async def run_progress(
    run_id: str, body: GovProgressBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(conn, caller, action="agent.run.manage", resource="agent:*")
        return await svc.record_progress(
            conn,
            _tenant(caller),
            run_id,
            _actor(caller),
            records=body.records,
            proposals=body.proposals,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovFinishBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    state: Literal["COMPLETED", "FAILED", "CANCELED"]
    reason: str | None = Field(default=None, max_length=64)


@router.post("/runs/{run_id}:finish")
async def run_finish(
    run_id: str, body: GovFinishBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.run.manage", resource="agent:*")
        return await svc.finish_run(
            conn, _tenant(caller), run_id, _actor(caller), state=body.state, reason=body.reason
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- evidence, monitoring, emergency stop


async def _can_see_detail(conn: Any, caller: Caller) -> bool:
    try:
        await protected_action(conn, caller, action="agent.audit.read_detail", resource="agent:*")
        return True
    except ZidError:
        return False


async def _events(
    request: Request,
    caller: Caller,
    agent_id: str | None,
    since: datetime | None,
    until: datetime | None,
    cursor: int | None,
    outcome: str | None,
    limit: int,
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.audit.read", resource=f"agent:{agent_id or '*'}"
        )
        return await svc.events(
            conn,
            agent_id=agent_id,
            since=since,
            until=until,
            before_seq=cursor,
            outcome=outcome,
            limit=limit,
            detail=await _can_see_detail(conn, caller),
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/governance/events")
async def fleet_events(
    request: Request,
    caller: CallerDep,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: int | None = None,
    outcome: Literal["ALLOW", "DENY", "SHADOW_DENY", "INFO"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _events(request, caller, None, since, until, cursor, outcome, limit)


@router.get("/agents/{agent_id}/events")
async def agent_events(
    agent_id: str,
    request: Request,
    caller: CallerDep,
    since: datetime | None = None,
    until: datetime | None = None,
    cursor: int | None = None,
    outcome: Literal["ALLOW", "DENY", "SHADOW_DENY", "INFO"] | None = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    return await _events(request, caller, agent_id, since, until, cursor, outcome, limit)


async def _export(
    request: Request,
    caller: Caller,
    agent_id: str | None,
    since: datetime | None,
    until: datetime | None,
    limit: int,
    name: str,
) -> Response:
    holder: dict[str, Any] = {}

    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.audit.export", resource=f"agent:{agent_id or '*'}"
        )
        detail = await _can_see_detail(conn, caller)
        body, rows = await svc.export_events(
            conn,
            _tenant(caller),
            _actor(caller),
            agent_id=agent_id,
            since=since,
            until=until,
            limit=limit,
            detail=detail,
        )
        holder.update(body=body, rows=rows, detail=detail)
        return {}

    await _run(request, caller, go)
    headers = {
        "Content-Disposition": f'attachment; filename="{name}.csv"',
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Export-Rows": str(holder["rows"]),
        "X-Export-Redacted": "false" if holder["detail"] else "true",
    }
    return Response(content=holder["body"], media_type="text/csv; charset=utf-8", headers=headers)


@router.get("/governance/events:export")
async def export_fleet_events(
    request: Request,
    caller: CallerDep,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> Response:
    return await _export(request, caller, None, since, until, limit, "agent-evidence")


@router.get("/agents/{agent_id}/events:export")
async def export_agent_events(
    agent_id: str, request: Request, caller: CallerDep, since: datetime | None = None, until: datetime | None = None,
    limit: int = Query(default=1000, ge=1, le=5000),
) -> Response:  # fmt: skip
    return await _export(
        request, caller, agent_id, since, until, limit, f"agent-evidence-{agent_id[:8]}"
    )


@router.get("/governance/evidence:verify")
async def verify_evidence(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.audit.read", resource="agent:*")
        return await store.verify_chain(conn)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/monitoring")
async def monitoring(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.audit.read", resource=f"agent:{agent_id}"
        )
        return await svc.agent_monitoring(conn, _tenant(caller), agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class GovPauseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=3, max_length=500)
    severity: Literal["P0", "P1", "P2"] = "P1"


@router.post("/agents/{agent_id}/pause", status_code=201)
async def pause_agent(
    agent_id: str, body: GovPauseBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(
            conn, caller, action="agent.security.pause", resource=f"agent:{agent_id}"
        )
        return await svc.pause(
            conn,
            _tenant(caller),
            _actor(caller),
            scope="AGENT",
            agent_id=agent_id,
            reason=body.reason,
            severity=body.severity,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/governance/pause", status_code=201)
async def pause_company(body: GovPauseBody, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.security.pause", resource="agent:*")
        return await svc.pause(
            conn,
            _tenant(caller),
            _actor(caller),
            scope="TENANT",
            agent_id=None,
            reason=body.reason,
            severity=body.severity,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/governance/freezes")
async def list_freezes(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await protected_action(conn, caller, action="agent.governance.read", resource="agent:*")
        return {"items": [svc.freeze_view(r) for r in await store.list_freezes(conn)]}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/governance/freezes/{case_id}:request-restore", status_code=201)
async def request_restore(case_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> Any:
        await protected_action(
            conn,
            caller,
            action="agent.security.request_restore",
            resource=f"agent:freeze:{case_id}",
        )
        return await svc.request_restore(conn, _tenant(caller), _actor(caller), case_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]
