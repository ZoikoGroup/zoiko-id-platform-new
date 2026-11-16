"""Storage for the DAARA agent and for governance alert state (ZID-AG-WF-001, ZID-AG-WF-002; CR-092; machine_0009).

Tenant transactions, RLS on, fixed SQL only (no assembled statements). The one write DAARA may make is `upsert_proposal`: a row in the
review queue. Nothing here touches a mandate, a grant, a role or a credential.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


def _u(value: str | None) -> uuid.UUID | None:
    return uuid.UUID(value) if value else None


def _row(row: Any) -> dict[str, Any]:
    return dict(row._mapping)


# ---------------------------------------------------------------- instance


async def get_instance(
    conn: AsyncConnection, agent_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT * FROM agent.daara_instance WHERE agent_principal_id = :a" + (
        " FOR UPDATE" if lock else ""
    )
    row = (await conn.execute(text(sql), {"a": uuid.UUID(agent_id)})).first()
    return _row(row) if row else None


async def save_instance(conn: AsyncConnection, tenant_id: str, i: dict[str, Any]) -> None:
    """Insert, or change the setup. A change clears both gate hashes unless the new configuration hash is the one they hold."""
    await conn.execute(
        text(
            "INSERT INTO agent.daara_instance (agent_principal_id, tenant_id, template, purpose, display_label, rationale, warn_days, "
            "region, ai_explain, schedule, config_hash, created_by) VALUES (:a, :t, 'DAARA', 'access_posture_review', :label, :why, :days, :reg, :ai, :sch, :h, :by) "
            "ON CONFLICT (agent_principal_id) DO UPDATE SET display_label = EXCLUDED.display_label, rationale = EXCLUDED.rationale, "
            "warn_days = EXCLUDED.warn_days, region = EXCLUDED.region, ai_explain = EXCLUDED.ai_explain, schedule = EXCLUDED.schedule, config_hash = EXCLUDED.config_hash, "
            "validated_hash = CASE WHEN agent.daara_instance.validated_hash = EXCLUDED.config_hash THEN agent.daara_instance.validated_hash END, "
            "sandbox_passed_hash = CASE WHEN agent.daara_instance.sandbox_passed_hash = EXCLUDED.config_hash "
            "THEN agent.daara_instance.sandbox_passed_hash END, updated_at = now()"
        ),
        {
            "a": uuid.UUID(i["agent_id"]), "t": uuid.UUID(tenant_id), "label": i["label"], "why": i.get("rationale"), "days": i["warn_days"],
            "reg": i["region"], "ai": i["ai_explain"], "sch": i.get("schedule", "MANUAL"), "h": i["config_hash"], "by": uuid.UUID(i["created_by"]),
        },
    )  # fmt: skip


async def scheduled_instances(conn: AsyncConnection) -> list[dict[str, Any]]:
    """Every DAARA setup that asks for a daily run, across companies (worker pass, BYPASSRLS). Asking is not enough: the caller checks the policy."""
    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id, agent_principal_id FROM agent.daara_instance WHERE schedule = 'DAILY'"
            )
        )
    ).all()
    return [_row(r) for r in rows]


async def mark_validated(conn: AsyncConnection, agent_id: str, config_hash: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.daara_instance SET validated_hash = :h, updated_at = now() WHERE agent_principal_id = :a AND config_hash = :h"
        ),
        {"h": config_hash, "a": uuid.UUID(agent_id)},
    )


async def mark_sandbox_passed(conn: AsyncConnection, agent_id: str, config_hash: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.daara_instance SET sandbox_passed_hash = :h, updated_at = now() WHERE agent_principal_id = :a AND config_hash = :h"
        ),
        {"h": config_hash, "a": uuid.UUID(agent_id)},
    )


async def set_policy(conn: AsyncConnection, agent_id: str, policy_id: str) -> None:
    await conn.execute(
        text(
            "UPDATE agent.daara_instance SET policy_id = :p, updated_at = now() WHERE agent_principal_id = :a"
        ),
        {"p": uuid.UUID(policy_id), "a": uuid.UUID(agent_id)},
    )


# ---------------------------------------------------------------- run results


async def insert_result(conn: AsyncConnection, tenant_id: str, r: dict[str, Any]) -> None:
    await conn.execute(
        text(
            "INSERT INTO agent.daara_run_result (run_id, tenant_id, agent_principal_id, mode, state, incomplete, checked, issues, proposals, "
            "rule_set_version, policy_hash, summary, explanation, explanation_source, reason_code, completed_at) VALUES "
            "(:run, :t, :a, :mode, :state, :inc, :chk, :iss, :prop, :rv, :ph, CAST(:sum AS jsonb), :ex, :exs, :why, :at)"
        ),
        {
            "run": uuid.UUID(r["run_id"]), "t": uuid.UUID(tenant_id), "a": uuid.UUID(r["agent_id"]), "mode": r["mode"], "state": r["state"],
            "inc": r["incomplete"], "chk": r["checked"], "iss": r["issues"], "prop": r["proposals"], "rv": r["rule_set_version"],
            "ph": r.get("policy_hash"), "sum": json.dumps(r["summary"], sort_keys=True, default=str), "ex": r.get("explanation"),
            "exs": r.get("explanation_source", "FIXED"), "why": r.get("reason_code"), "at": r["completed_at"],
        },
    )  # fmt: skip


async def get_result(conn: AsyncConnection, run_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text("SELECT * FROM agent.daara_run_result WHERE run_id = :r"), {"r": uuid.UUID(run_id)}
        )
    ).first()
    return _row(row) if row else None


async def list_results(
    conn: AsyncConnection, agent_id: str, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.daara_run_result WHERE agent_principal_id = :a ORDER BY completed_at DESC LIMIT :n"
            ),
            {"a": uuid.UUID(agent_id), "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


# ---------------------------------------------------------------- review proposals


async def upsert_proposal(
    conn: AsyncConnection, tenant_id: str, *, agent_id: str, run_id: str, target_ref: str, rule_id: str, rule_version: str,
    finding_code: str, severity: str, evidence_refs: Sequence[str], dedupe_key: str,
) -> tuple[str, bool]:  # fmt: skip
    """One row per dedupe key. A repeat finds the row, counts the sighting and writes nothing new (idempotent under retry, replay and
    rollback). Returns (proposal id, created)."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.review_proposal (proposal_id, tenant_id, agent_principal_id, run_id, target_ref, rule_id, rule_version, "
                "finding_code, severity, evidence_refs, dedupe_key, last_run_id) VALUES "
                "(:id, :t, :a, :run, :target, :rule, :rv, :code, :sev, CAST(:ev AS jsonb), :k, :run) "
                "ON CONFLICT (tenant_id, dedupe_key) DO UPDATE SET seen_count = agent.review_proposal.seen_count + 1, "
                "last_run_id = EXCLUDED.last_run_id RETURNING proposal_id, (xmax = 0) AS created"
            ),
            {
                "id": uuid.uuid4(),
                "t": uuid.UUID(tenant_id),
                "a": uuid.UUID(agent_id),
                "run": uuid.UUID(run_id),
                "target": target_ref,
                "rule": rule_id,
                "rv": rule_version,
                "code": finding_code,
                "sev": severity,
                "ev": json.dumps(list(evidence_refs)),
                "k": dedupe_key,
            },
        )
    ).first()
    return str(row[0]), bool(row[1])  # type: ignore[index]


async def list_proposals(
    conn: AsyncConnection, *, status: str | None, agent_id: str | None, limit: int
) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.review_proposal "
                "WHERE (CAST(:st AS text) IS NULL OR status = CAST(:st AS text)) "
                "AND (CAST(:a AS uuid) IS NULL OR agent_principal_id = CAST(:a AS uuid)) "
                "ORDER BY created_at DESC, ref_no DESC LIMIT :n"
            ),
            {"st": status, "a": _u(agent_id), "n": limit},
        )
    ).all()
    return [_row(r) for r in rows]


async def get_proposal(
    conn: AsyncConnection, proposal_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT * FROM agent.review_proposal WHERE proposal_id = :p" + (
        " FOR UPDATE" if lock else ""
    )
    row = (await conn.execute(text(sql), {"p": uuid.UUID(proposal_id)})).first()
    return _row(row) if row else None


async def proposals_for_run(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM agent.review_proposal WHERE last_run_id = :r ORDER BY ref_no"),
            {"r": uuid.UUID(run_id)},
        )
    ).all()
    return [_row(r) for r in rows]


async def decide_proposal(
    conn: AsyncConnection,
    proposal_id: str,
    *,
    status: str,
    by: str,
    reason: str | None,
    assignee: str | None,
    handoff_digest: str | None,
    at: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.review_proposal SET status = :s, decided_by = :by, decided_at = :at, decision_reason = :why, "
            "assignee = :as_, handoff_digest = :hd WHERE proposal_id = :p"
        ),
        {
            "s": status,
            "by": uuid.UUID(by),
            "at": at,
            "why": reason,
            "as_": _u(assignee),
            "hd": handoff_digest,
            "p": uuid.UUID(proposal_id),
        },
    )


async def count_open(conn: AsyncConnection) -> int:
    row = (
        await conn.execute(text("SELECT count(*) FROM agent.review_proposal WHERE status = 'OPEN'"))
    ).first()
    return int(row[0])  # type: ignore[index]


# ---------------------------------------------------------------- alert state


async def see_alert(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    key: str,
    rule: str,
    agent_id: str | None,
    severity: str,
    now: datetime,
    sla_due: datetime,
) -> dict[str, Any]:
    """Record that an alert condition holds now. A new one opens with its SLA clock; one seen again counts the sighting and keeps its
    state; one that was RESOLVED and came back opens afresh. Duplicates are grouped by key, never listed twice."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.alert_state (alert_key, tenant_id, rule, agent_principal_id, severity, state, first_seen, last_seen, sla_due) "
                "VALUES (:k, :t, :rule, :a, :sev, 'OPEN', :now, :now, :sla) "
                "ON CONFLICT (tenant_id, alert_key) DO UPDATE SET last_seen = EXCLUDED.last_seen, "
                "occurrences = agent.alert_state.occurrences + 1, "
                "state = CASE WHEN agent.alert_state.state = 'RESOLVED' THEN 'OPEN' ELSE agent.alert_state.state END, "
                "first_seen = CASE WHEN agent.alert_state.state = 'RESOLVED' THEN EXCLUDED.first_seen ELSE agent.alert_state.first_seen END, "
                "sla_due = CASE WHEN agent.alert_state.state = 'RESOLVED' THEN EXCLUDED.sla_due ELSE agent.alert_state.sla_due END, "
                "acknowledged_by = CASE WHEN agent.alert_state.state = 'RESOLVED' THEN NULL ELSE agent.alert_state.acknowledged_by END, "
                "acknowledged_at = CASE WHEN agent.alert_state.state = 'RESOLVED' THEN NULL ELSE agent.alert_state.acknowledged_at END "
                "RETURNING *"
            ),
            {
                "k": key,
                "t": uuid.UUID(tenant_id),
                "rule": rule,
                "a": _u(agent_id),
                "sev": severity,
                "now": now,
                "sla": sla_due,
            },
        )
    ).first()
    return _row(row)


async def resolve_absent(conn: AsyncConnection, present: Sequence[str]) -> int:
    rows = (
        await conn.execute(
            text(
                "UPDATE agent.alert_state SET state = 'RESOLVED' "
                "WHERE state <> 'RESOLVED' AND NOT (alert_key = ANY(CAST(:k AS text[]))) RETURNING alert_key"
            ),
            {"k": list(present)},
        )
    ).all()
    return len(rows)


async def get_alert(
    conn: AsyncConnection, key: str, *, lock: bool = False
) -> dict[str, Any] | None:
    sql = "SELECT * FROM agent.alert_state WHERE alert_key = :k" + (" FOR UPDATE" if lock else "")
    row = (await conn.execute(text(sql), {"k": key})).first()
    return _row(row) if row else None


async def acknowledge_alert(
    conn: AsyncConnection, key: str, *, by: str, note: str | None, at: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.alert_state SET state = CASE WHEN state = 'ROUTED' THEN 'ROUTED' ELSE 'ACKNOWLEDGED' END, "
            "acknowledged_by = :by, acknowledged_at = :at, note = coalesce(:note, note) WHERE alert_key = :k AND state <> 'RESOLVED'"
        ),
        {"by": uuid.UUID(by), "at": at, "note": note, "k": key},
    )


async def route_alert(
    conn: AsyncConnection,
    key: str,
    *,
    route: str,
    owner: str | None,
    note: str | None,
    at: datetime,
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.alert_state SET state = 'ROUTED', route = :route, owner = :owner, routed_at = :at, note = coalesce(:note, note) "
            "WHERE alert_key = :k AND state <> 'RESOLVED'"
        ),
        {"route": route, "owner": _u(owner), "at": at, "note": note, "k": key},
    )


async def list_alert_states(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.alert_state WHERE state <> 'RESOLVED' ORDER BY severity, first_seen"
            )
        )
    ).all()
    return [_row(r) for r in rows]
