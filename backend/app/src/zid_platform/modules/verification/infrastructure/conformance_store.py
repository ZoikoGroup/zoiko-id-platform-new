"""Conformance runs and reviews in PostgreSQL (ZID-RCG-003 section 12; CR-083). Platform records, append-only (migration verification_0011)."""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import conformance as cf


def _result_json(r: cf.CaseResult) -> dict[str, Any]:
    return {
        "case": r.case,
        "outcome": r.outcome.value,
        "passed": list(r.passed),
        "failed": list(r.failed),
        "not_run": list(r.not_run),
    }


def _run(row: Any) -> cf.Run:
    results = row.results if isinstance(row.results, list) else json.loads(row.results)
    return cf.Run(
        run_id=row.run_id,
        commit=row.commit_sha,
        environment=row.environment,
        matrix_hash=row.matrix_hash,
        started_at=row.started_at,
        finished_at=row.finished_at,
        recorded_by=row.recorded_by,
        results=tuple(
            cf.CaseResult(
                r["case"],
                cf.Outcome(r["outcome"]),
                tuple(r["passed"]),
                tuple(r["failed"]),
                tuple(r["not_run"]),
            )
            for r in results
        ),
        run_hash=row.run_hash,
    )


_RCOLS = "run_id, commit_sha, environment, matrix_hash, started_at, finished_at, recorded_by, results, run_hash"


async def record_run(conn: AsyncConnection, run: cf.Run) -> cf.Run:
    sealed = run if run.run_hash else cf.Run(**{**run.__dict__, "run_hash": cf.run_hash(run)})
    cf.check_run(sealed)
    if await load_run(conn, sealed.run_id) is not None:
        raise cf.ConformanceError("CONFORMANCE_RUN_EXISTS", sealed.run_id)
    await conn.execute(
        text(
            f"INSERT INTO verify.conformance_run ({_RCOLS}) VALUES (:id, :c, :e, :m, :s, :f, :by, CAST(:r AS jsonb), :h)"
        ),
        {
            "id": sealed.run_id,
            "c": sealed.commit,
            "e": sealed.environment,
            "m": sealed.matrix_hash,
            "s": sealed.started_at,
            "f": sealed.finished_at,
            "by": sealed.recorded_by,
            "r": json.dumps([_result_json(r) for r in sealed.results]),
            "h": sealed.run_hash,
        },
    )
    return sealed


async def load_run(conn: AsyncConnection, run_id: str) -> cf.Run | None:
    row = (
        await conn.execute(
            text(f"SELECT {_RCOLS} FROM verify.conformance_run WHERE run_id = :i"), {"i": run_id}
        )
    ).first()
    if row is None:
        return None
    run = _run(row)
    if (
        cf.run_hash(run) != run.run_hash
    ):  # a run edited behind the application's back is never served
        raise cf.ConformanceError("CONFORMANCE_STORED_HASH_MISMATCH", run_id)
    return run


async def list_runs(conn: AsyncConnection, *, limit: int = 50) -> list[cf.Run]:
    rows = (
        await conn.execute(
            text(f"SELECT {_RCOLS} FROM verify.conformance_run ORDER BY finished_at DESC LIMIT :n"),
            {"n": limit},
        )
    ).all()
    return [_run(r) for r in rows]


async def reviews(conn: AsyncConnection, run_id: str) -> list[cf.Review]:
    rows = (
        await conn.execute(
            text(
                "SELECT run_id, reviewer, signed_hash, decision, note_ref, reviewed_at FROM verify.conformance_review "
                "WHERE run_id = :i ORDER BY reviewed_at"
            ),
            {"i": run_id},
        )
    ).all()
    return [
        cf.Review(
            r.run_id, r.reviewer, r.signed_hash, cf.Decision(r.decision), r.note_ref, r.reviewed_at
        )
        for r in rows
    ]


async def record_review(conn: AsyncConnection, review: cf.Review) -> cf.Review:
    run = await load_run(conn, review.run_id)
    if run is None:
        raise cf.ConformanceError("CONFORMANCE_RUN_NOT_FOUND", review.run_id)
    cf.check_review(review, run, await reviews(conn, review.run_id))
    await conn.execute(
        text(
            "INSERT INTO verify.conformance_review (run_id, reviewer, signed_hash, decision, note_ref, reviewed_at) "
            "VALUES (:i, :r, :h, :d, :n, :at)"
        ),
        {
            "i": review.run_id,
            "r": review.reviewer,
            "h": review.signed_hash,
            "d": review.decision.value,
            "n": review.note_ref,
            "at": review.reviewed_at,
        },
    )
    return review


async def exit_status(conn: AsyncConnection, run_id: str) -> cf.ExitStatus:
    run = await load_run(conn, run_id)
    return cf.exit_status(run, await reviews(conn, run_id) if run else [])
