"""Verification workflows: draft, analyze, simulate, approve and run (DTV-30; CR-099). A person approves; the machine only follows.

Every function takes the connection of an open tenant transaction. Which product lines are available comes in as an argument (the route reads the registry), so a
workflow whose regulated check needs a line that is switched off cannot be approved, and a run is refused if the line was switched off after approval.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.verification.domain import workflow as wf
from zid_platform.modules.verification.domain import workflow_run as machine
from zid_platform.modules.verification.infrastructure import workflow_store as store

NOTICE = "A workflow is data. It never declines anyone: an approval needs every required check VERIFIED, and every other result ends with a person."


def _problems(items: tuple[wf.Problem, ...]) -> list[dict[str, str]]:
    return [{"code": p.code, "where": p.where} for p in items]


def _version_view(
    row: dict[str, Any], problems: tuple[wf.Problem, ...] | None = None
) -> dict[str, Any]:
    return {
        "workflow_id": row["workflow_id"],
        "version": row["version"],
        "status": row["status"],
        "content_hash": row["content_hash"],
        "definition": row["definition"],
        "author": str(row["author"]),
        "approved_by": str(row["approved_by"]) if row["approved_by"] else None,
        "approved_at": row["approved_at"].isoformat() if row["approved_at"] else None,
        "problems": _problems(problems) if problems is not None else None,
        "notice": NOTICE,
    }


def check(definition: dict[str, Any], *, available_lines: frozenset[str]) -> dict[str, Any]:
    """Parse and analyze a definition without saving anything: the editor's Check button."""
    try:
        w = wf.parse(definition)
    except wf.WorkflowError as e:
        return {
            "valid": False,
            "parse_error": e.code,
            "detail": str(e),
            "problems": [],
            "content_hash": None,
        }
    problems = wf.analyze(w, available_lines=available_lines)
    return {
        "valid": True,
        "parse_error": None,
        "detail": "",
        "problems": _problems(problems),
        "content_hash": w.content_hash,
        "publishable": not problems,
    }


async def create_draft(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    definition: dict[str, Any],
    author: str,
    available_lines: frozenset[str],
    now: datetime,
) -> dict[str, Any]:
    """Save a draft. A draft may have problems (that is what a draft is for); it just cannot be approved until they are gone."""
    w = wf.parse(
        definition, author=author
    )  # a definition that does not even parse is refused: nothing half-formed is stored
    if await store.load_version(conn, w.workflow_id, w.version) is not None:
        raise wf.WorkflowError(
            "VERSION_EXISTS",
            f"{w.workflow_id}@{w.version}: a version is never overwritten; save a new version",
        )
    row = await store.insert_version(
        conn,
        tenant_id=tenant_id,
        workflow_id=w.workflow_id,
        version=w.version,
        definition=definition,
        content_hash=w.content_hash,
        author=author,
    )
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.drafted",
        f"{w.workflow_id}@{w.version}",
        author,
        now,
        "DRAFT",
    )
    return _version_view(row, wf.analyze(w, available_lines=available_lines))


async def get_version(
    conn: AsyncConnection, workflow_id: str, version: str, *, available_lines: frozenset[str]
) -> dict[str, Any]:
    row = await _version(conn, workflow_id, version)
    w = wf.parse(row["definition"], author=str(row["author"]))
    return _version_view(row, wf.analyze(w, available_lines=available_lines))


async def list_versions(conn: AsyncConnection, status: str | None) -> list[dict[str, Any]]:
    if status is not None and status not in {"DRAFT", "APPROVED", "RETIRED"}:
        raise wf.WorkflowError("STATUS_UNKNOWN", status)
    return [
        {
            "workflow_id": r["workflow_id"],
            "version": r["version"],
            "status": r["status"],
            "content_hash": r["content_hash"],
            "author": str(r["author"]),
            "approved_by": str(r["approved_by"]) if r["approved_by"] else None,
            "created_at": r["created_at"].isoformat(),
        }
        for r in await store.list_versions(conn, status)
    ]


async def simulate(
    conn: AsyncConnection, workflow_id: str, version: str, results: dict[str, str]
) -> dict[str, Any]:
    row = await _version(conn, workflow_id, version)
    trace = wf.simulate(wf.parse(row["definition"]), results)
    return {
        "steps": list(trace.steps),
        "outcome": trace.outcome,
        "needs_person": list(trace.needs_person),
        "side_effects": "none: a simulation stores and sends nothing",
    }


async def approve(
    conn: AsyncConnection,
    tenant_id: str,
    workflow_id: str,
    version: str,
    *,
    approver: str,
    available_lines: frozenset[str],
    now: datetime,
) -> dict[str, Any]:
    row = await _version(conn, workflow_id, version)
    if row["status"] != "DRAFT":
        raise wf.WorkflowError("VERSION_NOT_A_DRAFT", row["status"])
    w = wf.parse(row["definition"], author=str(row["author"]))
    problems = wf.may_publish(w, approver=approver, available_lines=available_lines)
    if problems:
        raise ApprovalRefused(problems)
    await store.approve_version(conn, workflow_id, version, approver, now)
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.approved",
        f"{workflow_id}@{version}",
        approver,
        now,
        "APPROVED",
    )
    return _version_view(await _version(conn, workflow_id, version), ())


async def retire(
    conn: AsyncConnection,
    tenant_id: str,
    workflow_id: str,
    version: str,
    *,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    row = await _version(conn, workflow_id, version)
    if row["status"] != "APPROVED":
        raise wf.WorkflowError("VERSION_NOT_APPROVED", row["status"])
    await store.retire_version(conn, workflow_id, version)
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.retired",
        f"{workflow_id}@{version}",
        actor,
        now,
        "RETIRED",
    )
    return _version_view(await _version(conn, workflow_id, version), ())


class ApprovalRefused(Exception):
    def __init__(self, problems: tuple[wf.Problem, ...]) -> None:
        super().__init__("APPROVAL_REFUSED")
        self.problems = problems


# ----------------------------------------------------------------------------------------------------------------------- runs


def run_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(row["run_id"]),
        "workflow_id": row["workflow_id"],
        "version": row["version"],
        "content_hash": row["content_hash"],
        "subject_principal_id": str(row["subject_principal_id"]),
        "status": row["status"],
        "step": row["step"],
        "awaiting_check": row["awaiting_check"],
        "awaiting_role": row["awaiting_role"],
        "outcome": row["outcome"],
        "results": row["results"],
        "events": row["events"],
        "notifications": row["notifications"],
        "erased": row["erased_at"] is not None,
        "notice": NOTICE,
    }


async def start_run(
    conn: AsyncConnection,
    tenant_id: str,
    workflow_id: str,
    version: str,
    *,
    subject: str,
    actor: str,
    available_lines: frozenset[str],
    now: datetime,
) -> dict[str, Any]:
    row = await _version(conn, workflow_id, version)
    if row["status"] != "APPROVED":
        raise wf.WorkflowError("VERSION_NOT_APPROVED", row["status"])
    w = wf.parse(row["definition"], author=str(row["author"]))
    problems = wf.analyze(
        w, available_lines=available_lines
    )  # again, now: a line switched off since approval stops a new run
    if problems:
        raise ApprovalRefused(problems)
    state = machine.begin(w)
    created = await store.insert_run(
        conn,
        tenant_id=tenant_id,
        workflow_id=workflow_id,
        version=version,
        subject=subject,
        started_by=actor,
        state=state,
    )
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.run_started",
        str(created["run_id"]),
        actor,
        now,
        state.status.value,
    )
    return run_view(created)


async def _run(conn: AsyncConnection, run_id: str) -> tuple[dict[str, Any], wf.Workflow]:
    row = await store.load_run(conn, run_id, lock=True)
    if row is None:
        raise wf.WorkflowError("RUN_NOT_FOUND", run_id)
    if row["erased_at"] is not None:
        raise machine.RunError("RUN_ERASED", "the run's data was erased; it can no longer move")
    version = await _version(conn, row["workflow_id"], row["version"])
    return row, wf.parse(version["definition"], author=str(version["author"]))


async def report_check(
    conn: AsyncConnection,
    tenant_id: str,
    run_id: str,
    *,
    check: str,
    outcome: str,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    row, w = await _run(conn, run_id)
    state = machine.report_check(w, store.state_of(row), check, outcome)
    await store.save_run(conn, run_id, state, now)
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.check_reported",
        run_id,
        actor,
        now,
        state.status.value,
    )
    return run_view(await _fresh(conn, run_id))


async def complete_review(
    conn: AsyncConnection, tenant_id: str, run_id: str, *, reviewer: str, now: datetime
) -> dict[str, Any]:
    row, w = await _run(conn, run_id)
    state = machine.complete_review(
        w, store.state_of(row), reviewer=reviewer, applicant=str(row["subject_principal_id"])
    )
    await store.save_run(conn, run_id, state, now)
    await _audit(
        conn,
        tenant_id,
        "verification.workflow.review_completed",
        run_id,
        reviewer,
        now,
        state.status.value,
    )
    return run_view(await _fresh(conn, run_id))


async def get_run(conn: AsyncConnection, run_id: str) -> dict[str, Any]:
    return run_view(await _fresh(conn, run_id))


async def list_runs(conn: AsyncConnection, status: str | None, limit: int) -> list[dict[str, Any]]:
    if status is not None and status not in {s.value for s in machine.Status}:
        raise wf.WorkflowError("STATUS_UNKNOWN", status)
    return [
        {
            "run_id": str(r["run_id"]),
            "workflow_id": r["workflow_id"],
            "version": r["version"],
            "subject_principal_id": str(r["subject_principal_id"]),
            "status": r["status"],
            "outcome": r["outcome"],
            "awaiting_check": r["awaiting_check"],
            "awaiting_role": r["awaiting_role"],
            "created_at": r["created_at"].isoformat(),
            "updated_at": r["updated_at"].isoformat(),
        }
        for r in await store.list_runs(conn, status, limit)
    ]


async def _fresh(conn: AsyncConnection, run_id: str) -> dict[str, Any]:
    row = await store.load_run(conn, run_id)
    if row is None:
        raise wf.WorkflowError("RUN_NOT_FOUND", run_id)
    return row


async def _version(conn: AsyncConnection, workflow_id: str, version: str) -> dict[str, Any]:
    row = await store.load_version(conn, workflow_id, version)
    if row is None:
        raise wf.WorkflowError("WORKFLOW_NOT_FOUND", f"{workflow_id}@{version}")
    return row


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event: str,
    ref: str,
    actor: str,
    now: datetime,
    state: str,
) -> None:
    """Ids, the state and nothing else: never a definition, a result or a person's data."""
    import uuid

    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=event.rsplit(".", 1)[-1],
            target_type="verification_workflow",
            target_ref=ref[:128],
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="verification",
            source_event_id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{event}:{ref}:{now.isoformat()}")),
            details={"state": state},
        ),
    )
