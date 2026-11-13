"""Independent conformance runs, operator plane (ZID-RCG-003 sections 12 and 12.1; CR-083).

    POST /v1/regulatory/conformance-runs                      record a run of the 48 cases (regulatory.conformance.record)
    POST /v1/regulatory/conformance-runs/{run_id}:review      accept or reject it, signing its hash (regulatory.conformance.review; never one's own)
    GET  /v1/regulatory/conformance-runs                      runs, newest first, with their P0 exit status
    GET  /v1/regulatory/conformance-runs/{run_id}             one run: every case's outcome and the reviews
    GET  /v1/regulatory/conformance-matrix                    the matrix in force: each case, what proves it, and its coverage

`tools/scripts/conformance_run.py` runs the tests the matrix names and produces the body of the first route. The run's hash covers the commit, the
environment, the matrix hash and every outcome; a reviewer signs exactly that. Recording and reviewing are audited in the same transaction.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import request_engine
from zid_platform.modules.operator_access.ports import CAPABILITIES, require_operator
from zid_platform.operator_actions import Actions


class _Verification:
    def __getattr__(self, name: str) -> Any:
        import zid_platform.modules.verification.ports as ports  # noqa: PLR0402

        return getattr(ports, name)


v = _Verification()
RECORD = "regulatory.conformance.record"
REVIEW = "regulatory.conformance.review"
READERS = tuple(sorted(n for n in CAPABILITIES if n.startswith("regulatory.")))

router = APIRouter(tags=["regulatory"])
OperatorDep = Annotated[Any, Depends(require_operator)]
RunIdPath = Annotated[str, Path(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")]

actions = Actions(
    namespace=uuid.UUID("0b6f3d2e-74a1-4c58-9e2f-31d8a5c6e907"),
    refused_event="regulatory.conformance.refused",
    status={
        "CONFORMANCE_RUN_EXISTS": 409,
        "CONFORMANCE_RUN_NOT_FOUND": 404,
        "CONFORMANCE_REVIEWER_NOT_INDEPENDENT": 403,
        "CONFORMANCE_ALREADY_REVIEWED": 409,
        "CONFORMANCE_HASH_MISMATCH": 409,
    },
    errors=lambda: (v.conformance_model.ConformanceError,),
    refused_code="CONFORMANCE_REFUSED",
    capability_hint="a regulatory capability",
)


class ConformanceCaseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: str = Field(pattern=r"^T(0[1-9]|[1-3][0-9]|4[0-8])$")
    outcome: Literal["PASS", "FAIL", "NOT_RUN", "PARTIAL", "GAP"]
    passed: list[str] = Field(default_factory=list, max_length=50)
    failed: list[str] = Field(default_factory=list, max_length=50)
    not_run: list[str] = Field(default_factory=list, max_length=50)


class ConformanceRunIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,127}$")
    commit: str = Field(pattern=r"^[0-9a-f]{40}$")
    environment: str = Field(min_length=1, max_length=64)
    matrix_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: AwareDatetime
    finished_at: AwareDatetime
    results: list[ConformanceCaseIn] = Field(min_length=48, max_length=48)


class ConformanceReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["ACCEPTED", "REJECTED"]
    signed_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    note_ref: str = Field(min_length=3, max_length=256)


def _run_view(run: Any, reviews: list[Any]) -> dict[str, Any]:
    status = v.conformance_model.exit_status(run, reviews)
    return {
        "run_id": run.run_id,
        "commit": run.commit,
        "environment": run.environment,
        "matrix_hash": run.matrix_hash,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat(),
        "recorded_by": run.recorded_by,
        "run_hash": run.run_hash,
        "results": [
            {
                "case": r.case,
                "outcome": r.outcome.value,
                "passed": list(r.passed),
                "failed": list(r.failed),
                "not_run": list(r.not_run),
            }
            for r in run.results
        ],
        "reviews": [
            {
                "reviewer": r.reviewer,
                "decision": r.decision.value,
                "note_ref": r.note_ref,
                "reviewed_at": r.reviewed_at.isoformat(),
            }
            for r in reviews
        ],
        "p0_exit": {
            "met": status.met,
            "reasons": list(status.reasons),
            "counts": dict(status.counts),
        },
    }


@router.post("/v1/regulatory/conformance-runs", status_code=201)
async def record_run(body: ConformanceRunIn, request: Request, op: OperatorDep) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        m = v.conformance_model
        run = m.Run(
            run_id=body.run_id,
            commit=body.commit,
            environment=body.environment,
            matrix_hash=body.matrix_hash,
            started_at=body.started_at,
            finished_at=body.finished_at,
            recorded_by=str(op.principal_id),
            results=tuple(
                m.CaseResult(
                    r.case, m.Outcome(r.outcome), tuple(r.passed), tuple(r.failed), tuple(r.not_run)
                )
                for r in body.results
            ),
        )
        saved = await v.conformance.record_run(conn, run)
        status = m.exit_status(saved, [])
        return (
            _run_view(saved, []),
            saved.run_id,
            {
                "run_hash": saved.run_hash,
                "commit": saved.commit,
                "environment": saved.environment,
                "counts": dict(status.counts),
            },
        )

    return await actions.act(
        request, op, event="regulatory.conformance.recorded", action="record", capability=RECORD,
        target_type="conformance_run", target=body.run_id, work=work,
    )  # fmt: skip


@router.post("/v1/regulatory/conformance-runs/{run_id}:review")
async def review_run(
    run_id: RunIdPath, body: ConformanceReviewIn, request: Request, op: OperatorDep
) -> dict[str, Any]:
    async def work(conn: Any, now: datetime) -> tuple[dict[str, Any], str, dict[str, Any]]:
        m = v.conformance_model
        await v.conformance.record_review(
            conn,
            m.Review(
                run_id,
                str(op.principal_id),
                body.signed_hash,
                m.Decision(body.decision),
                body.note_ref,
                now,
            ),
        )
        run = await v.conformance.load_run(conn, run_id)
        reviews = await v.conformance.reviews(conn, run_id)
        return (
            _run_view(run, reviews),
            run_id,
            {"decision": body.decision, "run_hash": body.signed_hash},
        )

    return await actions.act(
        request, op, event="regulatory.conformance.reviewed", action="review", capability=REVIEW,
        target_type="conformance_run", target=run_id, work=work,
    )  # fmt: skip


@router.get("/v1/regulatory/conformance-runs")
async def list_runs(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:conformance")
        out = []
        for run in await v.conformance.list_runs(conn):
            out.append(_run_view(run, await v.conformance.reviews(conn, run.run_id)))
        return {"items": out}


@router.get("/v1/regulatory/conformance-runs/{run_id}")
async def get_run(run_id: RunIdPath, request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:conformance")
        try:
            run = await v.conformance.load_run(conn, run_id)
        except v.conformance_model.ConformanceError as e:
            raise actions.fail(e) from e
        if run is None:
            raise ZidError("CONFORMANCE_RUN_NOT_FOUND", "No such conformance run.", http_status=404)
        return _run_view(run, await v.conformance.reviews(conn, run_id))


@router.get("/v1/regulatory/conformance-matrix")
async def get_matrix(request: Request, op: OperatorDep) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), request.state.operator_ctx) as conn:
        await actions.need_any(conn, op, READERS, "regulatory:conformance")
    entries = v.conformance_matrix()
    counts = {
        c.value: sum(1 for e in entries if e.coverage is c) for c in v.conformance_model.Coverage
    }
    return {
        "matrix_hash": v.conformance_model.matrix_hash(entries),
        "counts": counts,
        "as_of": datetime.now(UTC).isoformat(),
        "cases": [
            {
                "case": e.case,
                "title": e.title,
                "expected": e.expected,
                "coverage": e.coverage.value,
                "tests": list(e.tests),
                "note": e.note,
            }
            for e in entries
        ],
    }
