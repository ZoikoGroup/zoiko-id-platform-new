"""Setup, approval, run, decision, evidence, proposal and handoff records in PostgreSQL (CR-091; ZID-AG-WF-001 section 10).

Tenant-scoped under row-level security. Decisions, evidence and handoffs are only ever inserted; a proposal is only ever inserted by
the agent and later moved forward by a person; nothing here updates a right, a credential or a mandate.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.access_readiness.domain.policy import (
    CLASSIFICATION,
    RETENTION_POLICY_REF,
    ReadinessError,
)
from zid_platform.modules.access_readiness.domain.rules import digest


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


def _iso(v: Any) -> str | None:
    return v.isoformat() if isinstance(v, datetime) else None


def _map(row: Row[Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    return {k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in row._mapping.items()}


# ---------------------------------------------------------------- setup


async def insert_setup(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str,
    display_name: str,
    purpose: str,
    resource_kinds: tuple[str, ...],
    max_records: int,
    warning_window_days: int,
    home_region: str,
    rule_set_version: str,
    policy_version: str,
    scope_digest: str,
    created_by: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.access_review_setup (setup_id, tenant_id, agent_principal_id, display_name, purpose, resource_kinds, "
                "max_records, warning_window_days, home_region, rule_set_version, policy_version, status, scope_digest, created_by) "
                "VALUES (:id, :t, :a, :n, :p, :k, :m, :w, :r, :rv, :pv, 'DRAFT', :d, :by) "
                "ON CONFLICT (tenant_id, agent_principal_id) DO NOTHING RETURNING *"
            ),
            {
                "id": uuid.UUID(str(uuid7())),
                "t": uuid.UUID(tenant_id),
                "a": uuid.UUID(agent_id),
                "n": display_name,
                "p": purpose,
                "k": list(resource_kinds),
                "m": max_records,
                "w": warning_window_days,
                "r": home_region,
                "rv": rule_set_version,
                "pv": policy_version,
                "d": scope_digest,
                "by": uuid.UUID(created_by),
            },
        )
    ).first()
    if row is None:
        raise ReadinessError("SETUP_EXISTS", "this agent already has a setup")
    return _map(row) or {}


async def load_setup(
    conn: AsyncConnection, agent_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    return _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_setup WHERE agent_principal_id = :a"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"a": uuid.UUID(agent_id)},
            )
        ).first()
    )


async def list_setups(conn: AsyncConnection) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM agent.access_review_setup ORDER BY created_at DESC, setup_id")
        )
    ).all()
    return [m for r in rows if (m := _map(r)) is not None]


async def update_setup(
    conn: AsyncConnection,
    setup_id: str,
    *,
    status: str,
    scope_digest: str | None = None,
    validated_digest: str | None = None,
    clear_validation: bool = False,
    fields: dict[str, Any] | None = None,
    bump_revision: bool = False,
    paused: tuple[str, str] | None = None,
) -> dict[str, Any]:
    sets = ["status = :st", "updated_at = now()"]
    params: dict[str, Any] = {"id": uuid.UUID(setup_id), "st": status}
    if scope_digest is not None:
        sets.append("scope_digest = :sd")
        params["sd"] = scope_digest
    if validated_digest is not None:
        sets.append("validated_digest = :vd")
        params["vd"] = validated_digest
    if clear_validation:
        sets.append("validated_digest = NULL")
    for column, value in (fields or {}).items():
        if column not in {"display_name", "resource_kinds", "max_records", "warning_window_days"}:
            raise ReadinessError("SETUP_FIELD_UNKNOWN", column)
        sets.append(f"{column} = :f_{column}")
        params[f"f_{column}"] = list(value) if column == "resource_kinds" else value
    if bump_revision:
        sets.append("draft_revision = draft_revision + 1")
    if paused is not None:
        sets.append("paused_by = :pb, paused_reason = :pr, paused_at = now()")
        params["pb"], params["pr"] = uuid.UUID(paused[0]), paused[1]
    row = (
        await conn.execute(
            text(
                f"UPDATE agent.access_review_setup SET {', '.join(sets)} WHERE setup_id = :id RETURNING *"
            ),
            params,
        )
    ).first()
    return _map(row) or {}


# ---------------------------------------------------------------- approval


async def insert_approval_request(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    setup_id: str,
    agent_id: str,
    scope_digest: str,
    requested_by: str,
    expires_at: datetime,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.access_review_approval (approval_id, tenant_id, setup_id, agent_principal_id, scope_digest, "
                "requested_by, expires_at) VALUES (:id, :t, :s, :a, :d, :by, :exp) RETURNING *"
            ),
            {
                "id": uuid.UUID(str(uuid7())),
                "t": uuid.UUID(tenant_id),
                "s": uuid.UUID(setup_id),
                "a": uuid.UUID(agent_id),
                "d": scope_digest,
                "by": uuid.UUID(requested_by),
                "exp": expires_at,
            },
        )
    ).first()
    return _map(row) or {}


async def latest_approval(conn: AsyncConnection, setup_id: str) -> dict[str, Any] | None:
    return _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_approval WHERE setup_id = :s AND invalidated_at IS NULL "
                    "ORDER BY requested_at DESC, approval_id DESC LIMIT 1"
                ),
                {"s": uuid.UUID(setup_id)},
            )
        ).first()
    )


async def record_approval(
    conn: AsyncConnection, approval_id: str, approver: str, now: datetime
) -> None:
    await conn.execute(
        text(
            "UPDATE agent.access_review_approval SET approved_by = :by, approved_at = :now WHERE approval_id = :id"
        ),
        {"by": uuid.UUID(approver), "now": now, "id": uuid.UUID(approval_id)},
    )


async def invalidate_approvals(conn: AsyncConnection, setup_id: str, reason: str) -> int:
    result = await conn.execute(
        text(
            "UPDATE agent.access_review_approval SET invalidated_at = now(), invalidated_reason = :r "
            "WHERE setup_id = :s AND invalidated_at IS NULL"
        ),
        {"r": reason, "s": uuid.UUID(setup_id)},
    )
    return int(result.rowcount or 0)


# ---------------------------------------------------------------- run


async def insert_run(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str,
    setup_id: str,
    idempotency_key_hash: str,
    request_hash: str,
    run_definition_hash: str,
    policy_hash: str,
    rule_set_version: str,
    purpose: str,
    selector: dict[str, Any],
    warning_window_days: int,
    home_region: str,
    initiated_by: str,
    trace_id: str,
) -> tuple[dict[str, Any], bool]:
    """(run, created). The same Idempotency-Key returns the same run; the same key with another request is a conflict."""
    import json

    run_id = uuid.UUID(str(uuid7()))
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.access_review_run (run_id, tenant_id, agent_principal_id, setup_id, idempotency_key_hash, request_hash, "
                "run_definition_hash, policy_hash, rule_set_version, mode, purpose, resource_selector, warning_window_days, home_region, "
                "state, initiated_by, trace_id) VALUES (:id, :t, :a, :s, :ik, :rh, :dh, :ph, :rv, 'manual', :p, CAST(:sel AS jsonb), :w, :r, "
                "'QUEUED', :by, :tr) ON CONFLICT (tenant_id, agent_principal_id, idempotency_key_hash) DO NOTHING RETURNING *"
            ),
            {
                "id": run_id,
                "t": uuid.UUID(tenant_id),
                "a": uuid.UUID(agent_id),
                "s": uuid.UUID(setup_id),
                "ik": idempotency_key_hash,
                "rh": request_hash,
                "dh": run_definition_hash,
                "ph": policy_hash,
                "rv": rule_set_version,
                "p": purpose,
                "sel": json.dumps(selector, sort_keys=True),
                "w": warning_window_days,
                "r": home_region,
                "by": uuid.UUID(initiated_by),
                "tr": trace_id,
            },
        )
    ).first()
    if row is not None:
        return _map(row) or {}, True
    existing = _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_run WHERE agent_principal_id = :a AND idempotency_key_hash = :ik"
                ),
                {"a": uuid.UUID(agent_id), "ik": idempotency_key_hash},
            )
        ).first()
    )
    assert existing is not None
    if existing["request_hash"] != request_hash:
        raise ReadinessError(
            "IDEMPOTENCY_KEY_REUSED", "this Idempotency-Key was used for a different request"
        )
    return existing, False


async def load_run(
    conn: AsyncConnection, run_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    return _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_run WHERE run_id = :r"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"r": uuid.UUID(run_id)},
            )
        ).first()
    )


async def list_runs(conn: AsyncConnection, agent_id: str, limit: int = 50) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.access_review_run WHERE agent_principal_id = :a ORDER BY created_at DESC, run_id LIMIT :l"
            ),
            {"a": uuid.UUID(agent_id), "l": limit},
        )
    ).all()
    return [m for r in rows if (m := _map(r)) is not None]


async def queued_run_ids(conn: AsyncConnection, limit: int = 50) -> list[str]:
    rows = (
        await conn.execute(
            text(
                "SELECT run_id FROM agent.access_review_run WHERE state = 'QUEUED' ORDER BY created_at, run_id LIMIT :l"
            ),
            {"l": limit},
        )
    ).all()
    return [str(r.run_id) for r in rows]


async def set_run_state(
    conn: AsyncConnection,
    run_id: str,
    state: str,
    *,
    denial_codes: list[str] | None = None,
    summary: dict[str, Any] | None = None,
    attestation_id: str | None = None,
    started: bool = False,
    terminal: bool = False,
    attempt: bool = False,
) -> None:
    import json

    sets = ["state = :st"]
    params: dict[str, Any] = {"r": uuid.UUID(run_id), "st": state}
    if denial_codes is not None:
        sets.append("denial_codes = :dc")
        params["dc"] = denial_codes
    if summary is not None:
        sets.append("summary = CAST(:sm AS jsonb)")
        params["sm"] = json.dumps(summary, sort_keys=True)
    if attestation_id is not None:
        sets.append("worker_attestation_id = :wa")
        params["wa"] = uuid.UUID(attestation_id)
    if started:
        sets.append("started_at = coalesce(started_at, now())")
    if terminal:
        sets.append("terminal_at = now()")
    if attempt:
        sets.append("attempt = attempt + 1")
    await conn.execute(
        text(f"UPDATE agent.access_review_run SET {', '.join(sets)} WHERE run_id = :r"), params
    )


# ---------------------------------------------------------------- decisions and evidence (append-only)


async def append_decision(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    run_id: str | None,
    actor: str,
    subject: str,
    resource: str,
    action: str,
    purpose: str,
    outcome: str,
    reason_code: str,
    policy_hash: str,
    trace_id: str | None,
) -> str:
    decision_id = uuid.UUID(str(uuid7()))
    await conn.execute(
        text(
            "INSERT INTO agent.access_review_policy_decision (decision_id, tenant_id, run_id, actor, subject, resource, action, purpose, "
            "outcome, reason_code, policy_hash, trace_id) VALUES (:id, :t, :r, :ac, :su, :re, :act, :pu, :o, :rc, :ph, :tr)"
        ),
        {
            "id": decision_id,
            "t": uuid.UUID(tenant_id),
            "r": _u(run_id),
            "ac": actor,
            "su": subject,
            "re": resource,
            "act": action,
            "pu": purpose,
            "o": outcome,
            "rc": reason_code,
            "ph": policy_hash,
            "tr": trace_id,
        },
    )
    return str(decision_id)


def chain_digest(previous: str, seq: int, event_type: str, payload_digest: str) -> str:
    """Each envelope commits to the one before it, so a removed, reordered or edited envelope breaks the chain."""
    return digest([previous, seq, event_type, payload_digest])


GENESIS = "sha256:" + "0" * 64


async def append_evidence(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    run_id: str,
    event_type: str,
    source_id: str,
    source_version: str,
    payload_digest: str,
    region: str,
) -> dict[str, Any]:
    """Locks the run row so two writers cannot pick the same sequence number."""
    await conn.execute(
        text("SELECT 1 FROM agent.access_review_run WHERE run_id = :r FOR UPDATE"),
        {"r": uuid.UUID(run_id)},
    )
    last = (
        await conn.execute(
            text(
                "SELECT seq, chain_digest FROM agent.access_review_evidence WHERE run_id = :r ORDER BY seq DESC LIMIT 1"
            ),
            {"r": uuid.UUID(run_id)},
        )
    ).first()
    seq = (int(last.seq) + 1) if last else 1
    chained = chain_digest(
        str(last.chain_digest) if last else GENESIS, seq, event_type, payload_digest
    )
    event_id = uuid.UUID(str(uuid7()))
    await conn.execute(
        text(
            "INSERT INTO agent.access_review_evidence (event_id, tenant_id, run_id, seq, event_type, source_id, source_version, "
            "payload_digest, chain_digest, classification, region, retention_policy_ref) "
            "VALUES (:id, :t, :r, :seq, :et, :si, :sv, :pd, :cd, :cl, :rg, :rp)"
        ),
        {
            "id": event_id,
            "t": uuid.UUID(tenant_id),
            "r": uuid.UUID(run_id),
            "seq": seq,
            "et": event_type,
            "si": source_id,
            "sv": source_version,
            "pd": payload_digest,
            "cd": chained,
            "cl": CLASSIFICATION,
            "rg": region,
            "rp": RETENTION_POLICY_REF,
        },
    )
    return {"event_id": str(event_id), "seq": seq, "chain_digest": chained}


async def list_decisions(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM agent.access_review_policy_decision WHERE run_id = :r ORDER BY decided_at, decision_id"
            ),
            {"r": uuid.UUID(run_id)},
        )
    ).all()
    return [m for r in rows if (m := _map(r)) is not None]


async def list_evidence(conn: AsyncConnection, run_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text("SELECT * FROM agent.access_review_evidence WHERE run_id = :r ORDER BY seq"),
            {"r": uuid.UUID(run_id)},
        )
    ).all()
    return [m for r in rows if (m := _map(r)) is not None]


def verify_chain(envelopes: list[dict[str, Any]]) -> list[str]:
    """Integrity exceptions found when the chain is replayed from its start; empty means every link verifies (AT-20)."""
    problems: list[str] = []
    previous = GENESIS
    for expected_seq, e in enumerate(envelopes, start=1):
        if e["seq"] != expected_seq:
            problems.append(f"SEQUENCE_GAP:{expected_seq}")
        if (
            chain_digest(previous, e["seq"], e["event_type"], e["payload_digest"])
            != e["chain_digest"]
        ):
            problems.append(f"CHAIN_BROKEN:{e['seq']}")
        previous = e["chain_digest"]
    return problems


# ---------------------------------------------------------------- proposals


async def insert_proposal(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str,
    run_id: str,
    run_step_id: str,
    proposal_key: str,
    target_ref: str,
    rule_id: str,
    rule_version: str,
    finding_code: str,
    severity: str,
    confidence: str,
    evidence_refs: list[str],
) -> tuple[dict[str, Any], bool]:
    """Idempotent on the proposal key and on the run step id: a retry or a replayed request finds the one proposal that exists."""
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.access_review_proposal (proposal_id, tenant_id, created_by_agent_id, run_id, run_step_id, proposal_key, "
                "target_ref, rule_id, rule_version, finding_code, severity, confidence, evidence_refs) "
                "VALUES (:id, :t, :a, :r, :rs, :pk, :tr, :ri, :rv, :fc, :sv, :cf, :ev) ON CONFLICT DO NOTHING RETURNING *"
            ),
            {
                "id": uuid.UUID(str(uuid7())),
                "t": uuid.UUID(tenant_id),
                "a": uuid.UUID(agent_id),
                "r": uuid.UUID(run_id),
                "rs": run_step_id,
                "pk": proposal_key,
                "tr": target_ref,
                "ri": rule_id,
                "rv": rule_version,
                "fc": finding_code,
                "sv": severity,
                "cf": confidence,
                "ev": evidence_refs,
            },
        )
    ).first()
    if row is not None:
        return _map(row) or {}, True
    existing = _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_proposal WHERE proposal_key = :pk OR (run_id = :r AND run_step_id = :rs) "
                    "ORDER BY created_at LIMIT 1"
                ),
                {"pk": proposal_key, "r": uuid.UUID(run_id), "rs": run_step_id},
            )
        ).first()
    )
    assert existing is not None
    return existing, False


async def load_proposal(
    conn: AsyncConnection, proposal_id: str, *, lock: bool = False
) -> dict[str, Any] | None:
    return _map(
        (
            await conn.execute(
                text(
                    "SELECT * FROM agent.access_review_proposal WHERE proposal_id = :p"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"p": uuid.UUID(proposal_id)},
            )
        ).first()
    )


async def list_proposals(
    conn: AsyncConnection, *, status: str | None = None, run_id: str | None = None, limit: int = 100
) -> list[dict[str, Any]]:
    where, params = ["TRUE"], {"l": limit}
    if status:
        where.append("status = :st")
        params["st"] = status  # type: ignore[assignment]
    if run_id:
        where.append("run_id = :r")
        params["r"] = uuid.UUID(run_id)  # type: ignore[assignment]
    rows = (
        await conn.execute(
            text(
                f"SELECT * FROM agent.access_review_proposal WHERE {' AND '.join(where)} ORDER BY created_at DESC, proposal_id LIMIT :l"
            ),
            params,
        )
    ).all()
    return [m for r in rows if (m := _map(r)) is not None]


async def count_proposals(conn: AsyncConnection) -> int:
    return int(
        (await conn.execute(text("SELECT count(*) FROM agent.access_review_proposal"))).scalar_one()
    )


async def review_proposal(
    conn: AsyncConnection,
    proposal_id: str,
    *,
    status: str,
    reviewer: str,
    assignee: str | None = None,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "UPDATE agent.access_review_proposal SET status = :st, reviewed_by = :by, reviewed_at = now(), "
                "assignee = coalesce(:asg, assignee) WHERE proposal_id = :p RETURNING *"
            ),
            {
                "st": status,
                "by": uuid.UUID(reviewer),
                "asg": _u(assignee),
                "p": uuid.UUID(proposal_id),
            },
        )
    ).first()
    return _map(row) or {}


async def insert_handoff(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    proposal_id: str,
    handed_off_by: str,
    target_workflow: str,
    handoff_digest: str,
    signature: str,
) -> dict[str, Any]:
    row = (
        await conn.execute(
            text(
                "INSERT INTO agent.access_review_handoff (handoff_id, tenant_id, proposal_id, handed_off_by, target_workflow, handoff_digest, "
                "signature) VALUES (:id, :t, :p, :by, :tw, :hd, :sig) ON CONFLICT (tenant_id, proposal_id) DO NOTHING RETURNING *"
            ),
            {
                "id": uuid.UUID(str(uuid7())),
                "t": uuid.UUID(tenant_id),
                "p": uuid.UUID(proposal_id),
                "by": uuid.UUID(handed_off_by),
                "tw": target_workflow,
                "hd": handoff_digest,
                "sig": signature,
            },
        )
    ).first()
    if row is None:
        row = (
            await conn.execute(
                text("SELECT * FROM agent.access_review_handoff WHERE proposal_id = :p"),
                {"p": uuid.UUID(proposal_id)},
            )
        ).first()
    return _map(row) or {}


async def load_handoff(conn: AsyncConnection, proposal_id: str) -> dict[str, Any] | None:
    return _map(
        (
            await conn.execute(
                text("SELECT * FROM agent.access_review_handoff WHERE proposal_id = :p"),
                {"p": uuid.UUID(proposal_id)},
            )
        ).first()
    )
