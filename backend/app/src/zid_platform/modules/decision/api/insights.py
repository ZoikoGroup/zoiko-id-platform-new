"""Risk dashboards and assurance reporting from the company's own decision records.

    GET /v1/insights/risk?days=30          what was decided, where it was refused and why   (plan feature advanced.advanced-risk-dashboards)
    GET /v1/insights/assurance?days=30     controls evidenced by those decisions            (plan feature advanced.advanced-policy-packs-assurance-reporting)

Both read only `authz.policy_decision`, which is append-only, so a figure here is a count of records that cannot have been edited. Neither returns a
subject, a resource or any content: counts, codes and action names only.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_db import tenant_transaction
from zid_errors import ZidError

from zid_platform.api_auth import Caller, authenticated_caller, request_engine
from zid_platform.modules.commercial.ports import require_feature
from zid_platform.modules.decision.application.unit_scope import unit_principals
from zid_platform.protected import protected_action

router = APIRouter(prefix="/v1/insights", tags=["decision"])
CallerDep = Annotated[Caller, Depends(authenticated_caller)]
Days = Annotated[int, Query(ge=1, le=366)]
Unit = Annotated[uuid.UUID | None, Query(description="Only decisions about people in this organization unit and the units beneath it")]


async def _subjects(conn: AsyncConnection, unit: uuid.UUID | None) -> list[str] | None:
    if unit is None:
        return None
    people = await unit_principals(conn, str(unit))
    if people is None:
        raise ZidError("ORG_UNIT_NOT_FOUND", "No such live unit.", http_status=404)
    return people


def _since(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


async def risk_figures(conn: AsyncConnection, since: datetime, subjects: list[str] | None = None) -> dict[str, Any]:
    p: dict[str, Any] = {"s": since, "ids": subjects or [], "all": subjects is None}
    by_outcome = (await conn.execute(text("SELECT outcome::text AS k, count(*) AS n FROM authz.policy_decision WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) GROUP BY 1"), p)).all()
    by_risk = (
        await conn.execute(
            text("SELECT risk_class AS k, outcome::text AS o, count(*) AS n FROM authz.policy_decision WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) GROUP BY 1, 2"), p
        )
    ).all()
    reasons = (
        await conn.execute(
            text(
                "SELECT r AS k, count(*) AS n FROM authz.policy_decision, unnest(reason_codes) AS r "
                "WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) AND outcome::text <> 'ALLOW' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
            ),
            p,
        )
    ).all()
    actions = (
        await conn.execute(
            text(
                "SELECT action AS k, count(*) AS n, count(*) FILTER (WHERE outcome::text <> 'ALLOW') AS refused "
                "FROM authz.policy_decision WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 10"
            ),
            p,
        )
    ).all()
    daily = (
        await conn.execute(
            text(
                "SELECT date_trunc('day', decided_at)::date AS d, count(*) AS n, count(*) FILTER (WHERE outcome::text <> 'ALLOW') AS refused "
                "FROM authz.policy_decision WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) GROUP BY 1 ORDER BY 1"
            ),
            p,
        )
    ).all()
    risk: dict[str, dict[str, int]] = {}
    for r in by_risk:
        risk.setdefault(r.k, {})[r.o] = int(r.n)
    total = sum(int(r.n) for r in by_outcome)
    return {
        "total": total,
        "by_outcome": {r.k: int(r.n) for r in by_outcome},
        "by_risk_class": risk,
        "refusal_rate": round(sum(int(r.n) for r in by_outcome if r.k != "ALLOW") / total, 4) if total else 0.0,
        "top_refusal_reasons": [{"code": r.k, "count": int(r.n)} for r in reasons],
        "top_actions": [{"action": r.k, "count": int(r.n), "refused": int(r.refused)} for r in actions],
        "daily": [{"day": r.d.isoformat(), "decisions": int(r.n), "refused": int(r.refused)} for r in daily],
    }


async def assurance_figures(conn: AsyncConnection, since: datetime, subjects: list[str] | None = None) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "SELECT count(*) FILTER (WHERE outcome::text = 'ALLOW') AS allows, "
                "count(*) FILTER (WHERE outcome::text = 'ALLOW' AND risk_class IN ('R2', 'R3')) AS high_allows, "
                "count(*) FILTER (WHERE outcome::text = 'ALLOW' AND risk_class IN ('R2', 'R3') AND authority_chain_commitment IS NOT NULL) AS high_with_authority, "
                "count(*) FILTER (WHERE outcome::text = 'ALLOW' AND risk_class IN ('R2', 'R3') AND policy_release_id IS NOT NULL "
                "AND relation_revision IS NOT NULL AND security_revision IS NOT NULL) AS high_bound, "
                "count(*) FILTER (WHERE outcome::text = 'ALLOW' AND risk_class = 'R3' AND evidence_class IN ('E2', 'E3')) AS r3_evidenced, "
                "count(*) FILTER (WHERE outcome::text = 'ALLOW' AND risk_class = 'R3') AS r3_allows, "
                "count(*) FILTER (WHERE outcome::text = 'STATE_UNAVAILABLE') AS unavailable, "
                "count(*) AS total FROM authz.policy_decision WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[])))"
            ),
            {"s": since, "ids": subjects or [], "all": subjects is None},
        )
    ).one()
    releases = (
        await conn.execute(
            text(
                "SELECT policy_release_id::text AS k, count(*) AS n FROM authz.policy_decision "
                "WHERE decided_at >= :s AND (:all OR subject_principal_id = ANY(CAST(:ids AS uuid[]))) AND policy_release_id IS NOT NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 10"
            ),
            {"s": since, "ids": subjects or [], "all": subjects is None},
        )
    ).all()

    def check(code: str, label: str, passed: int, of: int) -> dict[str, Any]:
        rate = round(passed / of, 4) if of else None
        return {"control": code, "label": label, "passed": passed, "of": of, "rate": rate, "met": (passed == of) if of else None}

    return {
        "decisions": int(row.total),
        "controls": [
            check("HIGH_RISK_ALLOW_HAS_AUTHORITY", "Every allowed high-risk decision is backed by an authority chain", int(row.high_with_authority), int(row.high_allows)),
            check("HIGH_RISK_ALLOW_IS_BOUND", "Every allowed high-risk decision names its policy release and revisions", int(row.high_bound), int(row.high_allows)),
            check("CRITICAL_ALLOW_IS_EVIDENCED", "Every allowed critical (R3) decision leaves signed evidence", int(row.r3_evidenced), int(row.r3_allows)),
        ],
        "state_unavailable": int(row.unavailable),
        "policy_releases_in_force": [{"policy_release_id": r.k, "decisions": int(r.n)} for r in releases],
    }


@router.get("/risk")
async def risk(request: Request, caller: CallerDep, days: Days = 30, unit_id: Unit = None) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.read", resource="decision_receipt:*")
        await require_feature(
            conn,
            "advanced.advanced-risk-dashboards",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        since = _since(days)
        out = await risk_figures(conn, since, await _subjects(conn, unit_id))
    return {"days": days, "since": since.isoformat(), "unit_id": str(unit_id) if unit_id else None, **out}


@router.get("/assurance")
async def assurance(request: Request, caller: CallerDep, days: Days = 30, unit_id: Unit = None) -> dict[str, Any]:
    async with tenant_transaction(request_engine(request), caller.ctx) as conn:
        await protected_action(conn, caller, action="evidence.read", resource="decision_receipt:*")
        await require_feature(
            conn,
            "advanced.advanced-policy-packs-assurance-reporting",
            enforcement=getattr(request.app.state, "commercial_enforcement", "shadow"),
        )
        since = _since(days)
        out = await assurance_figures(conn, since, await _subjects(conn, unit_id))
    return {"days": days, "since": since.isoformat(), "unit_id": str(unit_id) if unit_id else None, **out}
