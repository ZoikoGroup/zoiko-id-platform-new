"""Delegated Authority & Access Readiness agent API (ZID-AG-WF-001 sections 06-08, 10; CR-091). Default OFF: every route answers 404.

The document's illustrative `/v1/tenants/{t}/agent-instances/...` becomes this repository's style, `/v1/access-readiness/agents/{agent_id}/...`:
the tenant is never in the path; it comes from the authenticated principal. The agent is an existing machine agent (`/v1/machine/agents`).

    POST   /v1/access-readiness/agents                                   create the setup (DRAFT)   access_readiness.manage
    GET    /v1/access-readiness/agents | /{id}                           read                       access_readiness.read
    PATCH  /v1/access-readiness/agents/{id}                              draft change; an approval no longer applies
    POST   /v1/access-readiness/agents/{id}/dry-runs                     synthetic fixture, writes nothing live
    POST   /v1/access-readiness/agents/{id}/publish-requests             request release of the exact scope digest
    POST   /v1/access-readiness/agents/{id}/publish-requests:approve     a different person signs it    access_readiness.approve (R3)
    POST   /v1/access-readiness/agents/{id}:pause | :resume              the kill switch for this agent
    POST   /v1/access-readiness/agents/{id}/runs                         Idempotency-Key -> 202 and the run
    GET    /v1/access-readiness/agents/{id}/runs | /{run_id} | /{run_id}/decisions | /{run_id}/evidence
    POST   /v1/access-readiness/agents/{id}/runs/{run_id}/evidence:export  purpose required; redacted without the review role
    POST   /v1/access-readiness/agents/{id}/runs/{run_id}:retry
    GET    /v1/access-readiness/review-proposals | /{proposal_id}
    POST   /v1/access-readiness/review-proposals/{id}:dismiss | :assign | :handoff   access_readiness.review

A proposal never changes a right. The handoff only records that a person asked for the governed access workflow, signed; that workflow
has its own permissions, a fresh policy evaluation and an explicit human execution.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.access_readiness.application import export, runs, setup
from zid_platform.modules.access_readiness.application import review as review_service
from zid_platform.modules.access_readiness.application.common import Runtime
from zid_platform.modules.access_readiness.domain.policy import ProposalStatus, ReadinessError
from zid_platform.modules.access_readiness.domain.rules import POLICY_VERSION, PURPOSE
from zid_platform.modules.access_readiness.infrastructure import store
from zid_platform.protected import protected_action

HANDOFF_TYP = "zid-access-review-handoff+jwt"


def feature_on(request: Request) -> None:
    """Default OFF. While the switch is off nothing exists here: the same 404 for everyone, before any authentication."""
    if not getattr(request.app.state, "access_readiness_enabled", False):
        raise ZidError("NOT_FOUND", "This resource is not available.", http_status=404)


router = APIRouter(
    prefix="/v1/access-readiness", tags=["access-readiness"], dependencies=[Depends(feature_on)]
)
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
UUID36 = Field(min_length=36, max_length=36)

_UNPROCESSABLE = {
    "AGENT_SPONSOR_REQUIRED",
    "BLUEPRINT_NOT_ALLOWED",
    "TOOL_NOT_ALLOWED",
    "REGION_UNSUPPORTED",
    "SOURCE_NOT_APPROVED",
    "PURPOSE_NOT_ALLOWED",
    "SCOPE_INVALID",
    "NAME_INVALID",
    "FIXTURE_UNKNOWN",
    "POLICY_VERSION_UNKNOWN",
    "IDEMPOTENCY_KEY_REQUIRED",
    "MODE_NOT_ALLOWED",
    "SELECTOR_INVALID",
    "USE_HANDOFF",
    "AI_EXPLANATION_DISABLED",
}


def http(e: ReadinessError) -> ZidError:
    code = e.code
    if code == "FEATURE_DISABLED" or code.endswith("NOT_FOUND"):
        status = 404
    elif code in _UNPROCESSABLE:
        status = 422
    elif code == "APPROVAL_NOT_INDEPENDENT":
        status = 403
    elif code == "SIGNING_UNAVAILABLE":
        status = 503
    else:
        status = 409
    return ZidError(code, str(e), http_status=status)


def runtime(request: Request) -> Runtime:
    state = request.app.state
    keyrings = getattr(state, "keyrings", None)
    signer = (
        (lambda body: str(keyrings.set.sign(body, typ=HANDOFF_TYP)))
        if keyrings is not None
        else None
    )
    return Runtime(
        enabled=bool(getattr(state, "access_readiness_enabled", False)),
        regions=frozenset(getattr(state, "access_readiness_regions", ())),
        region=str(getattr(state, "home_cell", "") or ""),
        cell_uuid=getattr(state, "cell_uuid", None),
        signer=signer,
        ai_explain=bool(getattr(state, "access_readiness_ai_explain", False)),
        explainer=getattr(state, "access_readiness_explainer", None),
    )


async def _run(request: Request, caller: Caller, fn: Any) -> Any:
    try:
        async with tenant_transaction(request_engine(request), caller.ctx) as conn:
            return await fn(conn)
    except ReadinessError as e:
        raise http(e) from e


def _tenant(caller: Caller) -> str:
    return caller.ctx.tenant_id or ""


def _actor(caller: Caller) -> str:
    return caller.ctx.principal_id or ""


async def _allow(conn: Any, caller: Caller, action: str, resource: str) -> None:
    await protected_action(conn, caller, action=action, resource=resource)


# ---------------------------------------------------------------- setup


class ReadinessCreateBody(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )  # no tenant or region from the client: both are server-set
    agent_principal_id: str = UUID36
    display_name: str = Field(min_length=1, max_length=80)
    resource_kinds: list[str] = Field(default_factory=lambda: ["mandates"], max_length=4)
    max_records: int = Field(default=100, ge=1, le=500)
    warning_window_days: int = Field(default=14, ge=1, le=90)
    ai_explanation: bool = (
        False  # the optional explanation; refused while the cell has it switched off
    )


class ReadinessPatchBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str | None = Field(default=None, min_length=1, max_length=80)
    resource_kinds: list[str] | None = Field(default=None, max_length=4)
    max_records: int | None = Field(default=None, ge=1, le=500)
    warning_window_days: int | None = Field(default=None, ge=1, le=90)
    ai_explanation: bool | None = None
    expected_revision: int | None = Field(default=None, ge=1)


@router.get("/agents")
async def list_setups(request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", "access_readiness:*")
        return {"items": [setup.view(s) for s in await store.list_setups(conn)]}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents", status_code=201)
async def create_setup(
    body: ReadinessCreateBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{body.agent_principal_id}")
        return await setup.create_setup(
            conn,
            runtime(request),
            _tenant(caller),
            actor=_actor(caller),
            agent_id=body.agent_principal_id,
            display_name=body.display_name,
            resource_kinds=tuple(body.resource_kinds),
            max_records=body.max_records,
            warning_window_days=body.warning_window_days,
            ai_explanation=body.ai_explanation,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}")
async def read_setup(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        return await setup.get_setup(conn, agent_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.patch("/agents/{agent_id}")
async def change_setup(
    agent_id: str, body: ReadinessPatchBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        changes = body.model_dump(exclude_none=True, exclude={"expected_revision"})
        return await setup.change_setup(
            conn,
            runtime(request),
            _tenant(caller),
            actor=_actor(caller),
            agent_id=agent_id,
            changes=changes,
            expected_revision=body.expected_revision,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ReadinessDryRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fixture: str = Field(default="baseline", max_length=64)
    policy_version: str = Field(default=POLICY_VERSION, max_length=64)


@router.post("/agents/{agent_id}/dry-runs")
async def dry_run(
    agent_id: str, body: ReadinessDryRunBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        return await setup.dry_run(
            conn,
            runtime(request),
            _tenant(caller),
            actor=_actor(caller),
            agent_id=agent_id,
            fixture=body.fixture,
            policy_version=body.policy_version,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/publish-requests", status_code=201)
async def request_publish(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        return await setup.request_publish(
            conn, runtime(request), _tenant(caller), actor=_actor(caller), agent_id=agent_id
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}/publish-requests:approve")
async def approve_publish(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.approve", f"agent:{agent_id}")
        return await setup.approve_publish(
            conn, runtime(request), _tenant(caller), approver=_actor(caller), agent_id=agent_id
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ReadinessPauseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(default="OPERATOR_DECISION", pattern=r"^[A-Z][A-Z0-9_]{2,63}$")


@router.post("/agents/{agent_id}:pause")
async def pause(
    agent_id: str, request: Request, caller: CallerDep, body: ReadinessPauseBody | None = None
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        return await setup.pause_setup(
            conn,
            _tenant(caller),
            actor=_actor(caller),
            agent_id=agent_id,
            reason=(body or ReadinessPauseBody()).reason,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/agents/{agent_id}:resume")
async def resume(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        return await setup.resume_setup(
            conn, runtime(request), _tenant(caller), actor=_actor(caller), agent_id=agent_id
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- runs


class ReadinessRunBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(default=PURPOSE, max_length=64)
    resource_selector: dict[str, Any] = Field(default_factory=lambda: {"kind": "mandates"})
    mode: str = Field(default="manual", max_length=16)


@router.post("/agents/{agent_id}/runs", status_code=202)
async def start_run(
    agent_id: str,
    body: ReadinessRunBody,
    request: Request,
    caller: CallerDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    """Accepted means queued and judged: a refused run is a recorded DENIED run with its reason codes, not a missing one."""
    rt = runtime(request)

    async def queue(conn: Any) -> tuple[dict[str, Any], bool]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        return await runs.start_run(
            conn,
            rt,
            _tenant(caller),
            actor=_actor(caller),
            agent_id=agent_id,
            idempotency_key=idempotency_key,
            purpose=body.purpose,
            selector=body.resource_selector,
            mode=body.mode,
            trace_id=caller.ctx.correlation_id,
        )

    run, created = await _run(request, caller, queue)

    async def execute(conn: Any) -> dict[str, Any]:
        return await runs.execute_run(conn, rt, _tenant(caller), run["run_id"])

    final = await _run(request, caller, execute) if created else run
    return runs.run_view(final)


@router.get("/agents/{agent_id}/runs")
async def list_runs(agent_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        return {"items": [runs.run_view(r) for r in await store.list_runs(conn, agent_id)]}

    return await _run(request, caller, go)  # type: ignore[no-any-return]


async def _run_of(conn: Any, agent_id: str, run_id: str) -> dict[str, Any]:
    run = await store.load_run(conn, run_id)
    if run is None or run["agent_principal_id"] != agent_id:
        raise ReadinessError("RUN_NOT_FOUND")  # another tenant's run is the same answer
    return run


@router.get("/agents/{agent_id}/runs/{run_id}")
async def read_run(
    agent_id: str, run_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        run = await _run_of(conn, agent_id, run_id)
        proposals = await store.list_proposals(conn, run_id=run_id)
        return {
            **runs.run_view(run),
            "proposals": [review_service.proposal_view(p) for p in proposals],
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/runs/{run_id}/decisions")
async def run_decisions(
    agent_id: str, run_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        await _run_of(conn, agent_id, run_id)
        return {
            "items": [
                {**d, "decided_at": d["decided_at"].isoformat()}
                for d in await store.list_decisions(conn, run_id)
            ]
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/agents/{agent_id}/runs/{run_id}/evidence")
async def run_evidence(
    agent_id: str, run_id: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    """The envelopes with their chain verified now. A broken link is reported as an integrity exception, never as a green result."""

    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        await _run_of(conn, agent_id, run_id)
        envelopes = await store.list_evidence(conn, run_id)
        problems = store.verify_chain(envelopes)
        return {
            "verified": not problems and bool(envelopes),
            "integrity_exceptions": problems,
            "items": [{**e, "recorded_at": e["recorded_at"].isoformat()} for e in envelopes],
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ReadinessExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: str = Field(
        default="", max_length=200
    )  # checked in the handler so a missing one is refused AND recorded, as for every evidence export


@router.post("/agents/{agent_id}/runs/{run_id}/evidence:export")
async def export_run_evidence(
    agent_id: str,
    run_id: str,
    body: ReadinessExportBody,
    request: Request,
    caller: CallerDep,
) -> dict[str, Any]:
    """WF-AG-03 'Export permitted evidence': references and digests only, the chain verified now. Reading the run is required; without
    the review role the export is redacted (and says so). A stated purpose is required and every export or refusal is recorded."""

    async def go(conn: Any) -> dict[str, Any] | None:
        await _allow(conn, caller, "access_readiness.read", f"agent:{agent_id}")
        run = await _run_of(conn, agent_id, run_id)
        if not export.has_purpose(body.purpose):
            return None
        try:
            await _allow(conn, caller, "access_readiness.review", f"agent:{agent_id}")
            full = True
        except ZidError as e:
            if e.http_status != 403:
                raise
            full = False
        return await export.export_evidence(
            conn, _tenant(caller), actor=_actor(caller), run=run, purpose=body.purpose, full=full
        )

    out = await _run(request, caller, go)
    if out is not None:
        return out  # type: ignore[no-any-return]

    async def refuse(conn: Any) -> None:
        await export.record_refusal(
            conn,
            _tenant(caller),
            actor=_actor(caller),
            run_id=run_id,
            code="EXPORT_PURPOSE_MISSING",
        )

    # written in its own transaction: the refused request's own would not keep the record
    await _run(request, caller, refuse)
    raise ZidError("EXPORT_PURPOSE_REQUIRED", "State the purpose of the export.", http_status=422)


@router.post("/agents/{agent_id}/runs/{run_id}:retry", status_code=202)
async def retry(agent_id: str, run_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.manage", f"agent:{agent_id}")
        await _run_of(conn, agent_id, run_id)
        return runs.run_view(await runs.retry_run(conn, runtime(request), _tenant(caller), run_id))

    return await _run(request, caller, go)  # type: ignore[no-any-return]


# ---------------------------------------------------------------- review proposals


@router.get("/review-proposals")
async def list_proposals(
    request: Request, caller: CallerDep, status: ProposalStatus | None = None
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", "review-proposal:*")
        found = await store.list_proposals(conn, status=status.value if status else None)
        return {
            "items": [review_service.proposal_view(p) for p in found],
            "notice": review_service.NOTICE,
        }

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.get("/review-proposals/{proposal_id}")
async def read_proposal(proposal_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.read", f"review-proposal:{proposal_id}")
        return await review_service.get_proposal(conn, proposal_id)

    return await _run(request, caller, go)  # type: ignore[no-any-return]


class ReadinessAssignBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    assignee: str = UUID36


@router.post("/review-proposals/{proposal_id}:dismiss")
async def dismiss(proposal_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.review", f"review-proposal:{proposal_id}")
        return await review_service.review(
            conn,
            _tenant(caller),
            actor=_actor(caller),
            proposal_id=proposal_id,
            target=ProposalStatus.DISMISSED,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/review-proposals/{proposal_id}:assign")
async def assign(
    proposal_id: str, body: ReadinessAssignBody, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.review", f"review-proposal:{proposal_id}")
        return await review_service.review(
            conn,
            _tenant(caller),
            actor=_actor(caller),
            proposal_id=proposal_id,
            target=ProposalStatus.ASSIGNED,
            assignee=body.assignee,
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]


@router.post("/review-proposals/{proposal_id}:handoff")
async def handoff(proposal_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def go(conn: Any) -> dict[str, Any]:
        await _allow(conn, caller, "access_readiness.review", f"review-proposal:{proposal_id}")
        return await review_service.hand_off(
            conn, runtime(request), _tenant(caller), actor=_actor(caller), proposal_id=proposal_id
        )

    return await _run(request, caller, go)  # type: ignore[no-any-return]
