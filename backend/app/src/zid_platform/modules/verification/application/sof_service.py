"""Source of funds cases (DTV-28; CR-096): create, answer, submit, decide, appeal. A person decides; this never clears anyone.

Every function takes the connection of an open tenant transaction. The product-line gate belongs to the route, because it needs the app's registry; a
questionnaire and the line state come in as arguments so the rules stay testable without a web app.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.evidence.ports import (
    AuditFacts,
    EvidenceClass,
    record_audit,
    upload_summary,
)
from zid_platform.modules.verification.domain import source_of_funds as sof
from zid_platform.modules.verification.infrastructure import sof_store as store

NOTICE = "A person decides every case. This service never clears anyone by itself, and nothing here is a score."


async def create_case(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    subject: str,
    country: str,
    actor: str,
    questionnaire: sof.Questionnaire,
    line_live: bool,
) -> dict[str, Any]:
    sof.questionnaire_for_case(questionnaire, country, line_live=line_live)
    row = await store.insert_case(
        conn,
        tenant_id=tenant_id,
        subject=subject,
        country=country.upper(),
        code=questionnaire.code,
        version=questionnaire.version,
        content_hash=questionnaire.content_hash,
        created_by=actor,
    )
    return view(row, [])


async def _unusable_uploads(conn: AsyncConnection, evidence: list[dict[str, str]]) -> list[str]:
    """Each reference must name an upload that exists in this company, is ready and clean, and has not been erased."""
    bad: list[str] = []
    for ref in evidence:
        found = await upload_summary(conn, ref["upload_id"]) if ref.get("upload_id") else None  # type: ignore[no-untyped-call]
        if (
            not found
            or found["state"] != "READY"
            or found["scan_state"] != "CLEAN"
            or found["erased_at"] is not None
        ):
            bad.append(f"EVIDENCE_UPLOAD_NOT_USABLE:{ref.get('question_id', '')}")
    return bad


async def submit(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    answers: dict[str, Any],
    evidence: list[dict[str, str]],
    actor: str,
    questionnaire: sof.Questionnaire,
    now: datetime,
) -> dict[str, Any]:
    """Save the answers and move the case to NEEDS_REVIEW when complete, or to INCOMPLETE saying exactly what is missing."""
    case = await _load(conn, case_id, lock=True)
    if case["state"] not in {"DRAFT", "INCOMPLETE"}:
        raise sof.SofError("CASE_NOT_OPEN_FOR_ANSWERS", case["state"])
    if case["questionnaire_hash"] != questionnaire.content_hash:
        raise sof.SofError(
            "QUESTIONNAIRE_CHANGED", "the case was started on another version; start a new case"
        )
    assessment = sof.assess(questionnaire, answers, evidence)
    problems = list(assessment.problems)
    if assessment.complete:
        problems += await _unusable_uploads(conn, evidence)
    state = sof.after_submission(
        sof.Assessment(not problems, tuple(problems), assessment.needed_evidence)
    )
    await store.save_submission(
        conn,
        case_id,
        answers=answers,
        evidence=evidence,
        problems=problems,
        state=state.value,
        submitted_by=actor,
        now=now,
    )
    await _audit(
        conn, tenant_id, "verification.sof.submitted", case_id, actor, now, state.value, []
    )
    return view(await _load(conn, case_id), await store.decisions_for(conn, case_id))


async def decide(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    *,
    decision: sof.Decision,
    reasons: list[str],
    reviewer: str,
    now: datetime,
) -> dict[str, Any]:
    case = await _load(conn, case_id, lock=True)
    if case["erased_at"] is not None:
        raise sof.SofError(
            "CASE_ERASED", "the answers were erased; the case can no longer be decided"
        )
    current = sof.CaseState(case["state"])
    outcome = sof.decide(
        current,
        decision,
        reviewer=reviewer,
        submitted_by=str(case["submitted_by"] or ""),
        first_reviewer=str(case["first_reviewer"]) if case["first_reviewer"] else None,
        reasons=reasons,
        now=now,
    )
    await store.set_state(
        conn,
        case_id,
        outcome.state.value,
        valid_until=outcome.valid_until,
        first_reviewer=None if current is sof.CaseState.APPEALED else reviewer,
        now=now,
    )
    await store.add_decision(
        conn,
        tenant_id=tenant_id,
        case_id=case_id,
        decision=decision.value,
        from_state=current.value,
        to_state=outcome.state.value,
        reasons=list(outcome.reason_codes),
        decided_by=reviewer,
        now=now,
    )
    await _audit(
        conn,
        tenant_id,
        "verification.sof.decided",
        case_id,
        reviewer,
        now,
        outcome.state.value,
        list(outcome.reason_codes),
    )
    return view(await _load(conn, case_id), await store.decisions_for(conn, case_id))


async def appeal(
    conn: AsyncConnection, tenant_id: str, case_id: str, *, by: str, now: datetime
) -> dict[str, Any]:
    case = await _load(conn, case_id, lock=True)
    current = sof.CaseState(case["state"])
    new = sof.appeal(
        current,
        by=by,
        subject=str(case["subject_principal_id"]),
        submitted_by=str(case["submitted_by"] or ""),
    )
    await store.set_state(conn, case_id, new.value, valid_until=None, first_reviewer=None, now=now)
    await store.add_decision(
        conn,
        tenant_id=tenant_id,
        case_id=case_id,
        decision="APPEAL_FILED",
        from_state=current.value,
        to_state=new.value,
        reasons=[],
        decided_by=by,
        now=now,
    )
    await _audit(conn, tenant_id, "verification.sof.appealed", case_id, by, now, new.value, [])
    return view(await _load(conn, case_id), await store.decisions_for(conn, case_id))


async def get_case(conn: AsyncConnection, case_id: str) -> dict[str, Any]:
    return view(await _load(conn, case_id), await store.decisions_for(conn, case_id))


async def queue(conn: AsyncConnection, state: str | None, limit: int) -> list[dict[str, Any]]:
    if state is not None and state not in {s.value for s in sof.CaseState}:
        raise sof.SofError("STATE_UNKNOWN", state)
    return [
        {
            "case_id": str(r["case_id"]),
            "subject_principal_id": str(r["subject_principal_id"]),
            "country": r["country"],
            "questionnaire_version": r["questionnaire_version"],
            "state": r["state"],
            "submitted_at": r["submitted_at"].isoformat() if r["submitted_at"] else None,
            "valid_until": r["valid_until"].isoformat() if r["valid_until"] else None,
        }
        for r in await store.list_cases(conn, state, limit)
    ]


async def _load(conn: AsyncConnection, case_id: str, *, lock: bool = False) -> dict[str, Any]:
    case = await store.load_case(conn, case_id, lock=lock)
    if case is None:
        raise sof.SofError("CASE_NOT_FOUND", case_id)
    return case


def view(case: dict[str, Any], decisions: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "case_id": str(case["case_id"]),
        "subject_principal_id": str(case["subject_principal_id"]),
        "country": case["country"],
        "questionnaire": {
            "code": case["questionnaire_code"],
            "version": case["questionnaire_version"],
            "content_hash": case["questionnaire_hash"],
        },
        "state": case["state"],
        "answers": case["answers"],
        "evidence": case["evidence"],
        "problems": list(case["problems"]),
        "submitted_at": case["submitted_at"].isoformat() if case["submitted_at"] else None,
        "valid_until": case["valid_until"].isoformat() if case["valid_until"] else None,
        "erased": case["erased_at"] is not None,
        "decisions": [
            {
                "decision": d["decision"],
                "from_state": d["from_state"],
                "to_state": d["to_state"],
                "reason_codes": list(d["reason_codes"]),
                "decided_by": str(d["decided_by"]),
                "decided_at": d["decided_at"].isoformat(),
            }
            for d in decisions
        ],
        "notice": NOTICE,
    }


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    event: str,
    case_id: str,
    actor: str,
    now: datetime,
    state: str,
    reasons: list[str],
) -> None:
    """Ids, the state and the fixed reason codes only: never an answer, an amount or an evidence reference."""
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type="HUMAN",
            action=event.rsplit(".", 1)[-1],
            target_type="source_of_funds_case",
            target_ref=case_id,
            outcome="SUCCESS",
            evidence_class=EvidenceClass.E2,
            occurred_at=now,
            actor_principal_id=actor,
            source_service="verification",
            source_event_id=str(uuid.uuid5(uuid.UUID(case_id), f"{event}:{now.isoformat()}")),
            details={"state": state, "reason_codes": reasons},
        ),
    )
