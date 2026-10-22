"""PostgreSQL persistence of onboarding plans, stages and shadow observations (G-W4; CR-015)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Row
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.onboarding.domain.plan import (
    ExitCriteria,
    PlanStatus,
    Scope,
    ShadowStats,
    StageStatus,
)


def _s(v: Any) -> str | None:
    return str(v) if v is not None else None


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    tenant_id: str
    version: int
    supersedes: str | None
    status: PlanStatus
    target_mode: str
    scope: dict[str, Any]
    exit_criteria: ExitCriteria
    cutover_windows: list[Any]
    rollback_triggers: dict[str, Any]
    support: dict[str, Any]
    data_migration: str
    author: str
    approved_by: str | None
    shadow_started_at: datetime | None
    pilot_started_at: datetime | None
    rollback_tested_at: datetime | None
    created_at: datetime


def _plan(r: Row[Any]) -> Plan:
    m = r._mapping
    return Plan(
        str(m["plan_id"]),
        str(m["tenant_id"]),
        int(m["version"]),
        _s(m["supersedes"]),
        PlanStatus(m["status"]),
        m["target_mode"],
        dict(m["scope"]),
        ExitCriteria.parse(m["exit_criteria"]),
        list(m["cutover_windows"]),
        dict(m["rollback_triggers"]),
        dict(m["support"]),
        m["data_migration"],
        str(m["author_principal_id"]),
        _s(m["approved_by"]),
        m["shadow_started_at"],
        m["pilot_started_at"],
        m["rollback_tested_at"],
        m["created_at"],
    )


async def insert_plan(conn: AsyncConnection, tenant_id: str, v: Mapping[str, Any]) -> Plan:
    previous = (
        await conn.execute(
            text(
                "SELECT plan_id, version FROM platform.onboarding_plan "
                "ORDER BY version DESC LIMIT 1 FOR UPDATE"
            )
        )
    ).first()
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.onboarding_plan (plan_id, tenant_id, version, supersedes,
                    target_mode, scope, exit_criteria, cutover_windows, rollback_triggers, support,
                    data_migration, acceptance_suite, author_principal_id)
                VALUES (:id, :t, :v, :sup, :mode, CAST(:scope AS jsonb), CAST(:crit AS jsonb),
                    CAST(:windows AS jsonb), CAST(:triggers AS jsonb), CAST(:support AS jsonb),
                    :migration, :suite, :author)
                RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "v": (int(previous.version) + 1) if previous else 1,
                "sup": previous.plan_id if previous else None,
                "mode": v["target_mode"],
                "scope": json.dumps(v["scope"]),
                "crit": json.dumps(v["exit_criteria"]),
                "windows": json.dumps(v.get("cutover_windows", [])),
                "triggers": json.dumps(v.get("rollback_triggers", {})),
                "support": json.dumps(v.get("support", {})),
                "migration": v.get("data_migration", "NONE"),
                "suite": list(v.get("acceptance_suite", [])),
                "author": uuid.UUID(v["author"]),
            },
        )
    ).one()
    return _plan(row)


async def load_plan(conn: AsyncConnection, plan_id: str, *, lock: bool = False) -> Plan | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.onboarding_plan WHERE plan_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(plan_id)},
        )
    ).first()
    return _plan(row) if row else None


async def approved_plan(conn: AsyncConnection) -> Plan | None:
    row = (
        await conn.execute(text("SELECT * FROM platform.onboarding_plan WHERE status = 'APPROVED'"))
    ).first()
    return _plan(row) if row else None


async def plans(conn: AsyncConnection) -> list[Plan]:
    rows = (
        await conn.execute(text("SELECT * FROM platform.onboarding_plan ORDER BY version DESC"))
    ).all()
    return [_plan(r) for r in rows]


async def approve_plan(conn: AsyncConnection, plan_id: str, approver: str, now: datetime) -> Plan:
    await conn.execute(
        text(
            "UPDATE platform.onboarding_plan SET status = 'SUPERSEDED' "
            "WHERE status = 'APPROVED' AND plan_id <> :id"
        ),
        {"id": uuid.UUID(plan_id)},
    )
    row = (
        await conn.execute(
            text(
                "UPDATE platform.onboarding_plan SET status = 'APPROVED', approved_by = :a, "
                "approved_at = :now WHERE plan_id = :id RETURNING *"
            ),
            {"a": uuid.UUID(approver), "now": now, "id": uuid.UUID(plan_id)},
        )
    ).one()
    return _plan(row)


async def stamp_plan(conn: AsyncConnection, plan_id: str, column: str, now: datetime) -> None:
    assert column in ("shadow_started_at", "pilot_started_at", "rollback_tested_at")
    await conn.execute(
        text(
            f"UPDATE platform.onboarding_plan SET {column} = coalesce({column}, :now) "
            "WHERE plan_id = :id"
        ),
        {"now": now, "id": uuid.UUID(plan_id)},
    )


# ------------------------------------------------------------------------------------- stages


@dataclass(frozen=True, slots=True)
class Stage:
    stage_id: str
    plan_id: str
    name: str
    scope: Scope
    status: StageStatus
    created_by: str
    approved_by: str | None
    activated_at: datetime | None


def _stage(r: Row[Any]) -> Stage:
    m = r._mapping
    return Stage(
        str(m["stage_id"]),
        str(m["plan_id"]),
        m["name"],
        Scope.parse(m["scope"]),
        StageStatus(m["status"]),
        str(m["created_by"]),
        _s(m["approved_by"]),
        m["activated_at"],
    )


async def insert_stage(
    conn: AsyncConnection, *, tenant_id: str, plan_id: str, name: str, scope: Scope, created_by: str
) -> Stage:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.onboarding_stage (stage_id, tenant_id, plan_id, name, scope,
                    max_risk, created_by)
                VALUES (:id, :t, :p, :n, CAST(:scope AS jsonb), :risk, :by) RETURNING *
            """),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(plan_id),
                "n": name,
                "scope": json.dumps(scope.view()),
                "risk": scope.max_risk,
                "by": uuid.UUID(created_by),
            },
        )
    ).one()
    return _stage(row)


async def load_stage(conn: AsyncConnection, stage_id: str, *, lock: bool = False) -> Stage | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.onboarding_stage WHERE stage_id = :id"
                + (" FOR UPDATE" if lock else "")
            ),
            {"id": uuid.UUID(stage_id)},
        )
    ).first()
    return _stage(row) if row else None


async def stages(conn: AsyncConnection, *, active_only: bool = False) -> list[Stage]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.onboarding_stage"
                + (" WHERE status = 'ACTIVE'" if active_only else "")
                + " ORDER BY created_at"
            )
        )
    ).all()
    return [_stage(r) for r in rows]


async def set_stage(
    conn: AsyncConnection,
    stage_id: str,
    status: StageStatus,
    *,
    approver: str | None,
    now: datetime,
) -> Stage:
    row = (
        await conn.execute(
            text("""
                UPDATE platform.onboarding_stage SET status = :s,
                    approved_by = coalesce(CAST(:a AS uuid), approved_by),
                    activated_at = CASE WHEN :s = 'ACTIVE' THEN CAST(:now AS timestamptz)
                                        ELSE activated_at END,
                    ended_at = CASE WHEN :s IN ('ROLLED_BACK', 'COMPLETED')
                                    THEN CAST(:now AS timestamptz) END
                WHERE stage_id = :id RETURNING *
            """),
            {"s": status.value, "a": approver, "now": now, "id": uuid.UUID(stage_id)},
        )
    ).one()
    return _stage(row)


async def roll_back_active_stages(conn: AsyncConnection, now: datetime) -> int:
    r = await conn.execute(
        text(
            "UPDATE platform.onboarding_stage SET status = 'ROLLED_BACK', ended_at = :now "
            "WHERE status = 'ACTIVE'"
        ),
        {"now": now},
    )
    return int(r.rowcount or 0)


# ------------------------------------------------------------------------------------- shadow


async def record_observation(conn: AsyncConnection, tenant_id: str, v: Mapping[str, Any]) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.shadow_observation (observation_id, tenant_id, plan_id, decision_id,
                application_id, action, risk_class, zoiko_outcome, zoiko_allow, incumbent_outcome,
                mismatch, context_complete, enforcement, subject_commitment, latency_ms)
            VALUES (:id, :t, :plan, :decision, :app, :action, :risk, :outcome, :allow, :incumbent,
                :mismatch, :complete, :enforcement, :subject, :latency)
        """),
        {
            **v,
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "plan": uuid.UUID(v["plan"]) if v.get("plan") else None,
            "app": uuid.UUID(v["app"]) if v.get("app") else None,
        },
    )


async def shadow_stats(conn: AsyncConnection, since: datetime) -> ShadowStats:
    r = (
        await conn.execute(
            text("""
                SELECT count(*) AS n,
                    count(*) FILTER (WHERE context_complete) AS complete,
                    count(*) FILTER (WHERE risk_class = 3) AS r3,
                    count(*) FILTER (WHERE risk_class = 3 AND context_complete) AS r3_complete,
                    count(*) FILTER (WHERE mismatch <> 'NO_INCUMBENT') AS compared,
                    count(*) FILTER (WHERE mismatch IN ('FALSE_ALLOW', 'FALSE_DENY')) AS mismatches,
                    count(*) FILTER (WHERE mismatch = 'FALSE_ALLOW') AS false_allows,
                    percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) AS p95
                FROM platform.shadow_observation WHERE observed_at >= :since
            """),
            {"since": since},
        )
    ).one()
    return ShadowStats(
        int(r.n),
        int(r.complete),
        int(r.r3),
        int(r.r3_complete),
        int(r.compared),
        int(r.mismatches),
        int(r.false_allows),
        float(r.p95) if r.p95 is not None else None,
    )


async def false_allow_actions(conn: AsyncConnection, since: datetime) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT DISTINCT action FROM platform.shadow_observation "
                "WHERE mismatch = 'FALSE_ALLOW' AND observed_at >= :since ORDER BY action"
            ),
            {"since": since},
        )
    ).all()
    return [r.action for r in rows]
