"""Scored sandbox test runs for an application (ZID-CP-WF10 v2.0, WF-10.12; CR-173). Runs in the caller's tenant transaction."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verify_api.domain import fixtures as fx
from zid_platform.modules.verify_api.domain.model import PartnerError
from zid_platform.modules.verify_api.infrastructure import store


def _run_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(r["run_id"]),
        "passed": r["passed"],
        "personas_passed": r["personas_passed"],
        "personas_total": r["personas_total"],
        "report_digest": r["report_digest"],
        "ran_at": r["created_at"].isoformat(),
        "run_by": str(r["run_by"]),
    }


async def record_run(
    conn: AsyncConnection,
    tenant_id: str,
    application_id: str,
    run_by: str,
    observations: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """Score what the partner's server did against the persona catalog and keep the result, pass or fail, with its digest. Only a SANDBOX application is tested."""
    app = await store.get_application(conn, application_id)
    if app is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    if str(app["environment"]) != "SANDBOX":
        raise PartnerError(
            "ENVIRONMENT_MISMATCH", "Synthetic test runs are for a sandbox application."
        )
    report = fx.report([fx.parse_observation(o) for o in observations])
    run_id = await store.insert_certification_run(conn, tenant_id, application_id, report, run_by)
    return {
        "run_id": run_id,
        **{
            k: report[k]
            for k in (
                "passed",
                "personas_passed",
                "personas_total",
                "missing_personas",
                "report_digest",
                "is_certification",
                "statement",
            )
        },
        "results": report["results"],
    }


async def status(
    conn: AsyncConnection, application_id: str, now: datetime | None = None
) -> dict[str, Any]:
    if await store.get_application(conn, application_id) is None:
        raise PartnerError("NOT_FOUND", "No such application.")
    runs = await store.certification_runs(conn, application_id)
    return {
        "application_id": application_id,
        "suite": fx.suite_status(runs[0] if runs else None, now or datetime.now(UTC)),
        "runs": [_run_view(r) for r in runs],
        "catalog_version": fx.CATALOG_VERSION,
    }


async def suite_for_go_live(
    conn: AsyncConnection, tenant_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """The best current suite result among the company's sandbox applications, shown beside the go-live checks. Informational: it does not block a request."""
    best: dict[str, Any] | None = None
    for a in await store.list_applications(conn):
        if str(a["environment"]) != "SANDBOX":
            continue
        s = (await status(conn, str(a["application_id"]), now))["suite"]
        if best is None or (s["met"] and not best["met"]):
            best = {**s, "application_id": str(a["application_id"])}
    return best or fx.suite_status(None, now or datetime.now(UTC))
