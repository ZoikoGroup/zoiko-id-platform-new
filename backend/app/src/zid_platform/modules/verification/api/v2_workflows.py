"""Verification workflows over HTTP (ZID-GAP-001 v2.0 DTV-30; CR-099). A workflow is data; a person approves it; the machine only follows.

    POST /v2/workflows:check                                  analyze a definition without saving it                verification.workflow.manage
    POST /v2/workflows                                        save a draft (a draft may have problems)              verification.workflow.manage
    GET  /v2/workflows | /v2/workflows/{id}/versions/{v}      list, read (with the analysis)                         verification.workflow.read
    POST /v2/workflows/{id}/versions/{v}:simulate             replay against supplied results; stores nothing       verification.workflow.manage
    POST /v2/workflows/{id}/versions/{v}:approve              a DIFFERENT person approves; the analysis must be clean verification.workflow.approve
    POST /v2/workflows/{id}/versions/{v}:retire               stop new runs on this version                           verification.workflow.approve
    POST /v2/workflows/{id}/versions/{v}/runs                 start a run for a person                                verification.workflow.run
    GET  /v2/workflow-runs | /v2/workflow-runs/{run}          read                                                    verification.workflow.read
    POST /v2/workflow-runs/{run}:report-check                 the result of the check the run is waiting for         verification.workflow.run
    POST /v2/workflow-runs/{run}:complete-review              a person finishes the review (never the applicant)     verification.workflow.run

The executor runs nothing: it says which check it waits for and the caller reports the result. Which product lines are available comes from the product line
registry, so a regulated check whose line is off cannot be approved or started.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from zid_context import current_context
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.verification.application import workflow_service as service
from zid_platform.modules.verification.domain import workflow as wf
from zid_platform.modules.verification.domain import workflow_run as machine
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)
from zid_platform.protected import protected_action

router = APIRouter(tags=["verification-v2"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
UUID36 = Field(min_length=36, max_length=36)
STATUS = {
    "WORKFLOW_NOT_FOUND": 404,
    "RUN_NOT_FOUND": 404,
    "APPROVER_IS_AUTHOR": 403,
    "REVIEWER_NOT_INDEPENDENT": 403,
    "STATUS_UNKNOWN": 422,
    "OUTCOME_UNKNOWN": 422,
}


def _ids() -> dict[str, str]:
    ctx = current_context()
    return {
        "request_id": ctx.request_id if ctx else "req_unknown",
        "correlation_id": ctx.correlation_id if ctx else "cor_unknown",
    }


def _lines(request: Request) -> frozenset[str]:
    """The product lines that are available right now (STAGING or LIVE)."""
    registry = getattr(request.app.state, "product_lines", None) or product_line_registry()
    return frozenset(code for code, line in registry.lines.items() if line.available)


def _fail(e: Exception) -> ZidError:
    code = getattr(e, "code", "WORKFLOW_ERROR")
    return ZidError(
        code,
        code.replace("_", " ").capitalize() + (f". {e}" if str(e) and str(e) != code else "."),
        http_status=STATUS.get(code, 409),
    )


class WorkflowDefinitionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    definition: dict[str, Any]


class WorkflowSimulateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    results: dict[str, str] = Field(default_factory=dict, max_length=20)


class WorkflowRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subject_principal_id: str = UUID36


class WorkflowCheckReportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check: str = Field(pattern=r"^[A-Z_]{3,40}$")
    outcome: str = Field(pattern=r"^[A-Z_]{3,40}$")


async def _run(
    request: Request, caller: Caller, action: str, resource: str, work: Any
) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action=action, resource=resource)
        try:
            return {**_ids(), **(await work(conn))}
        except service.ApprovalRefused as e:
            raise ZidError(
                "WORKFLOW_NOT_SAFE", "The workflow does not pass the safety rules.", http_status=409
            ) from e
        except (wf.WorkflowError, machine.RunError) as e:
            raise _fail(e) from e


@router.post("/v2/workflows:check")
async def check_definition(
    body: WorkflowDefinitionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(_conn: Any) -> dict[str, Any]:
        return service.check(body.definition, available_lines=_lines(request))

    return await _run(request, caller, "verification.workflow.manage", "workflow:check", work)


@router.post("/v2/workflows", status_code=201)
async def create_draft(
    body: WorkflowDefinitionIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.create_draft(
            conn,
            caller.ctx.tenant_id or "",
            definition=body.definition,
            author=caller.ctx.principal_id or "",
            available_lines=_lines(request),
            now=datetime.now(UTC),
        )

    return await _run(request, caller, "verification.workflow.manage", "workflow:new", work)


@router.get("/v2/workflows")
async def list_workflows(
    request: Request, caller: CallerDep, status: Annotated[str | None, Query(max_length=20)] = None
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return {"items": await service.list_versions(conn, status), "notice": service.NOTICE}

    return await _run(request, caller, "verification.workflow.read", "workflow:*", work)


@router.get("/v2/workflows/{workflow_id}/versions/{version}")
async def read_version(
    workflow_id: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.get_version(
            conn, workflow_id, version, available_lines=_lines(request)
        )

    return await _run(
        request, caller, "verification.workflow.read", f"workflow:{workflow_id}", work
    )


@router.post("/v2/workflows/{workflow_id}/versions/{version}:simulate")
async def simulate(
    workflow_id: str, version: str, body: WorkflowSimulateIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.simulate(conn, workflow_id, version, body.results)

    return await _run(
        request, caller, "verification.workflow.manage", f"workflow:{workflow_id}", work
    )


@router.post("/v2/workflows/{workflow_id}/versions/{version}:approve")
async def approve(
    workflow_id: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        try:
            return await service.approve(
                conn,
                caller.ctx.tenant_id or "",
                workflow_id,
                version,
                approver=caller.ctx.principal_id or "",
                available_lines=_lines(request),
                now=datetime.now(UTC),
            )
        except service.ApprovalRefused as e:
            codes = {p.code for p in e.problems}
            if "APPROVER_IS_AUTHOR" in codes:
                raise ZidError(
                    "APPROVER_IS_AUTHOR", "A different person approves a workflow.", http_status=403
                ) from e
            raise ZidError(
                "WORKFLOW_NOT_SAFE",
                "The workflow does not pass the safety rules: "
                + ", ".join(sorted(f"{p.code}" for p in e.problems))
                + ".",
                http_status=409,
            ) from e

    return await _run(
        request, caller, "verification.workflow.approve", f"workflow:{workflow_id}", work
    )


@router.post("/v2/workflows/{workflow_id}/versions/{version}:retire")
async def retire(
    workflow_id: str, version: str, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.retire(
            conn,
            caller.ctx.tenant_id or "",
            workflow_id,
            version,
            actor=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )

    return await _run(
        request, caller, "verification.workflow.approve", f"workflow:{workflow_id}", work
    )


@router.post("/v2/workflows/{workflow_id}/versions/{version}/runs", status_code=201)
async def start_run(
    workflow_id: str, version: str, body: WorkflowRunIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.start_run(
            conn,
            caller.ctx.tenant_id or "",
            workflow_id,
            version,
            subject=body.subject_principal_id,
            actor=caller.ctx.principal_id or "",
            available_lines=_lines(request),
            now=datetime.now(UTC),
        )

    return await _run(
        request, caller, "verification.workflow.run", f"workflow-run:{workflow_id}", work
    )


@router.get("/v2/workflow-runs")
async def list_runs(
    request: Request,
    caller: CallerDep,
    status: Annotated[str | None, Query(max_length=30)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return {"items": await service.list_runs(conn, status, limit), "notice": service.NOTICE}

    return await _run(request, caller, "verification.workflow.read", "workflow-run:*", work)


@router.get("/v2/workflow-runs/{run_id}")
async def read_run(run_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.get_run(conn, run_id)

    return await _run(request, caller, "verification.workflow.read", f"workflow-run:{run_id}", work)


@router.post("/v2/workflow-runs/{run_id}:report-check")
async def report_check(
    run_id: str, body: WorkflowCheckReportIn, request: Request, caller: CallerDep
) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.report_check(
            conn,
            caller.ctx.tenant_id or "",
            run_id,
            check=body.check,
            outcome=body.outcome,
            actor=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )

    return await _run(request, caller, "verification.workflow.run", f"workflow-run:{run_id}", work)


@router.post("/v2/workflow-runs/{run_id}:complete-review")
async def complete_review(run_id: str, request: Request, caller: CallerDep) -> dict[str, Any]:
    async def work(conn: Any) -> dict[str, Any]:
        return await service.complete_review(
            conn,
            caller.ctx.tenant_id or "",
            run_id,
            reviewer=caller.ctx.principal_id or "",
            now=datetime.now(UTC),
        )

    return await _run(request, caller, "verification.workflow.run", f"workflow-run:{run_id}", work)
