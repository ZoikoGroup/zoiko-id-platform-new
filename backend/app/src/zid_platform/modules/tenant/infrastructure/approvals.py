"""Approval cases, votes, execution attempts and reviewers in PostgreSQL (slice L-10, CR-047), and the registry the cases are classified by.

Every function runs inside the caller's tenant transaction (row-level security scopes it). The case row is locked (`FOR UPDATE`) by anything
that moves its state, so concurrent votes and retried executions meet one authoritative row.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.tenant.domain.approvals import (
    ApprovalError,
    Registry,
    State,
    Tier,
    build_registry,
    move,
)
from zid_platform.modules.tenant.infrastructure.catalog import CONFIG, _yaml


@lru_cache(maxsize=1)
def registry() -> Registry:
    path = CONFIG / "approvals" / "action-tiers.yaml"
    return build_registry(_yaml(path), hashlib.sha256(path.read_bytes()).hexdigest())


@lru_cache(maxsize=1)
def policy_settings() -> dict[str, Any]:
    return _yaml(CONFIG / "approvals" / "approval-policy.yaml")


def enforcement() -> str:
    """SHADOW or ENFORCE. Anything but an explicit ENFORCE is SHADOW, the mode that keeps the earlier two-person path working."""
    return (
        "ENFORCE"
        if str(policy_settings().get("governance_enforcement")).upper() == "ENFORCE"
        else "SHADOW"
    )


def assured_review_active() -> bool:
    return str(policy_settings().get("assured_review", "INACTIVE")).upper() == "ACTIVE"


def clear_caches() -> None:
    registry.cache_clear()
    policy_settings.cache_clear()


def _u(v: str | None) -> uuid.UUID | None:
    return None if v is None else uuid.UUID(v)


# ---------------------------------------------------------------------------------------------------------- cases


@dataclass(frozen=True, slots=True)
class Case:
    case_id: str
    tenant_id: str
    version: int
    supersedes_case_id: str | None
    action_id: str
    tier: Tier
    target_id: str
    payload_json: str
    request_hash: str
    policy_version: str
    region: str
    requester_principal_id: str
    requester_person_id: str
    editor_persons: tuple[str, ...]
    beneficiary_principal_id: str | None
    beneficiary_person_id: str | None
    summary: str
    reason: str
    evidence_ref: str | None
    state: State
    expires_at: datetime
    created_at: datetime
    state_changed_at: datetime
    executed_at: datetime | None

    @property
    def payload(self) -> dict[str, Any]:
        loaded: dict[str, Any] = json.loads(self.payload_json)
        return loaded


def _case(r: Row[Any]) -> Case:
    m = r._mapping

    def s(k: str) -> str | None:
        return None if m[k] is None else str(m[k])

    return Case(
        str(m["case_id"]),
        str(m["tenant_id"]),
        m["version"],
        s("supersedes_case_id"),
        m["action_id"],
        Tier(m["tier"]),
        m["target_id"],
        m["payload_json"],
        m["request_hash"],
        m["policy_version"],
        m["region"],
        str(m["requester_principal_id"]),
        str(m["requester_person_id"]),
        tuple(str(p) for p in m["editor_persons"]),
        s("beneficiary_principal_id"),
        s("beneficiary_person_id"),
        m["summary"],
        m["reason"],
        m["evidence_ref"],
        State(m["state"]),
        m["expires_at"],
        m["created_at"],
        m["state_changed_at"],
        m["executed_at"],
    )


async def insert_case(
    conn: AsyncConnection,
    *,
    case_id: str,
    tenant_id: str,
    version: int,
    supersedes: str | None,
    action_id: str,
    tier: Tier,
    target_id: str,
    payload_json: str,
    request_hash: str,
    policy_version: str,
    region: str,
    requester_principal_id: str,
    requester_person_id: str,
    editor_persons: list[str],
    beneficiary_principal_id: str | None,
    beneficiary_person_id: str | None,
    summary: str,
    reason: str,
    evidence_ref: str | None,
    expires_at: datetime,
    now: datetime,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.approval_case (case_id, tenant_id, version, supersedes_case_id, action_id, tier, target_id,
                payload_json, request_hash, policy_version, region, requester_principal_id, requester_person_id,
                editor_persons, beneficiary_principal_id, beneficiary_person_id, summary, reason, evidence_ref, state,
                expires_at, created_at, state_changed_at)
            VALUES (:id, :t, :v, :sup, :act, :tier, :target, :payload, :hash, :pol, :region, :req, :reqp, :eds, :ben, :benp,
                :summary, :reason, :ref, 'PENDING_APPROVAL', :exp, :now, :now)
        """),
        {
            "id": uuid.UUID(case_id),
            "t": uuid.UUID(tenant_id),
            "v": version,
            "sup": _u(supersedes),
            "act": action_id,
            "tier": tier.value,
            "target": target_id,
            "payload": payload_json,
            "hash": request_hash,
            "pol": policy_version,
            "region": region,
            "req": uuid.UUID(requester_principal_id),
            "reqp": uuid.UUID(requester_person_id),
            "eds": [uuid.UUID(p) for p in editor_persons],
            "ben": _u(beneficiary_principal_id),
            "benp": _u(beneficiary_person_id),
            "summary": summary,
            "reason": reason,
            "ref": evidence_ref,
            "exp": expires_at,
            "now": now,
        },
    )
    for frm, to in (("DRAFT", "SUBMITTED"), ("SUBMITTED", "PENDING_APPROVAL")):
        await log_transition(
            conn, tenant_id, case_id, frm, to, requester_principal_id, "CASE_CREATED", now
        )


async def load_case(conn: AsyncConnection, case_id: str, *, lock: bool = False) -> Case | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.approval_case WHERE case_id = :c"
                + (" FOR UPDATE" if lock else "")
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).first()
    return None if row is None else _case(row)


async def list_cases(
    conn: AsyncConnection, *, states: tuple[State, ...] | None = None, limit: int = 100
) -> list[Case]:
    if states:
        rows = (
            await conn.execute(
                text(
                    "SELECT * FROM platform.approval_case WHERE state = ANY(:st) "
                    "ORDER BY created_at DESC, case_id DESC LIMIT :lim"
                ),
                {"st": [s.value for s in states], "lim": limit},
            )
        ).all()
    else:
        rows = (
            await conn.execute(
                text(
                    "SELECT * FROM platform.approval_case "
                    "ORDER BY created_at DESC, case_id DESC LIMIT :lim"
                ),
                {"lim": limit},
            )
        ).all()
    return [_case(r) for r in rows]


async def log_transition(
    conn: AsyncConnection,
    tenant_id: str,
    case_id: str,
    from_state: str,
    to_state: str,
    actor: str | None,
    reason_code: str,
    now: datetime,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.approval_case_transition (transition_id, seq, tenant_id, case_id, from_state, to_state,
                actor_principal_id, reason_code, at)
            VALUES (:id, (SELECT coalesce(max(seq), 0) + 1 FROM platform.approval_case_transition WHERE case_id = :c),
                :t, :c, :f, :to, :a, :r, :at)
        """),
        {
            "id": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case_id),
            "f": from_state,
            "to": to_state,
            "a": _u(actor),
            "r": reason_code[:64],
            "at": now,
        },
    )


async def set_state(
    conn: AsyncConnection,
    case: Case,
    to: State,
    *,
    actor: str | None,
    reason_code: str,
    now: datetime,
) -> Case:
    """The only way a case moves: the domain checks the transition, the database trigger checks it again, and the move is logged."""
    if case.state is to:
        return case
    move(case.state, to)
    await conn.execute(
        text("""
            UPDATE platform.approval_case SET state = :s, state_changed_at = :now,
                executed_at = CASE WHEN :s = 'EXECUTED' THEN :now ELSE executed_at END
            WHERE case_id = :c
        """),
        {"s": to.value, "now": now, "c": uuid.UUID(case.case_id)},
    )
    await log_transition(
        conn, case.tenant_id, case.case_id, case.state.value, to.value, actor, reason_code, now
    )
    reloaded = await load_case(conn, case.case_id)
    assert reloaded is not None
    return reloaded


async def transitions(conn: AsyncConnection, case_id: str) -> list[dict[str, Any]]:
    rows = (
        await conn.execute(
            text(
                "SELECT from_state, to_state, actor_principal_id, reason_code, at "
                "FROM platform.approval_case_transition WHERE case_id = :c ORDER BY seq"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        {
            "from": r.from_state,
            "to": r.to_state,
            "actor": None if r.actor_principal_id is None else str(r.actor_principal_id),
            "reason_code": r.reason_code,
            "at": r.at,
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------------------------------------- votes


@dataclass(frozen=True, slots=True)
class VoteRow:
    vote_id: str
    case_id: str
    request_hash: str
    tier: str
    approver_principal_id: str
    approver_person_id: str
    authority_basis: str
    vote: str
    step_up_proof_ref: str | None
    auth_context: dict[str, Any]
    decided_at: datetime


async def insert_vote(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    case: Case,
    principal_id: str,
    person_id: str,
    authority_basis: str,
    vote: str,
    step_up_proof_ref: str | None,
    auth_context: dict[str, Any],
    now: datetime,
) -> str:
    """One vote per person per case. A second one is refused by the database, not just by a check, so two concurrent votes by one human
    (or by two accounts of one human) leave exactly one."""
    vote_id = str(uuid.uuid4())
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO platform.approval_vote (vote_id, tenant_id, case_id, request_hash, tier, approver_principal_id,
                        approver_person_id, authority_basis, vote, step_up_proof_ref, auth_context, decided_at)
                    VALUES (:id, :t, :c, :h, :tier, :p, :person, :basis, :v, :ref, CAST(:ctx AS jsonb), :at)
                """),
                {
                    "id": uuid.UUID(vote_id),
                    "t": uuid.UUID(tenant_id),
                    "c": uuid.UUID(case.case_id),
                    "h": case.request_hash,
                    "tier": case.tier.value,
                    "p": uuid.UUID(principal_id),
                    "person": uuid.UUID(person_id),
                    "basis": authority_basis[:128],
                    "v": vote,
                    "ref": step_up_proof_ref,
                    "ctx": json.dumps(auth_context, sort_keys=True),
                    "at": now,
                },
            )
    except IntegrityError as e:
        raise ApprovalError("APPROVAL_DUPLICATE_PERSON", "this person has already voted") from e
    return vote_id


async def votes(conn: AsyncConnection, case_id: str) -> list[VoteRow]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.approval_vote WHERE case_id = :c ORDER BY decided_at, vote_id"
            ),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        VoteRow(
            str(r.vote_id),
            str(r.case_id),
            r.request_hash,
            r.tier,
            str(r.approver_principal_id),
            str(r.approver_person_id),
            r.authority_basis,
            r.vote,
            r.step_up_proof_ref,
            dict(r.auth_context or {}),
            r.decided_at,
        )
        for r in rows
    ]


# ------------------------------------------------------------------------------------------------ execution


@dataclass(frozen=True, slots=True)
class ExecutionRow:
    execution_id: str
    attempt: int
    idempotency_key: str
    request_hash: str
    outcome: str
    reason_code: str | None
    actor_principal_id: str
    region: str
    quorum: dict[str, Any]
    executed_at: datetime


async def executions(conn: AsyncConnection, case_id: str) -> list[ExecutionRow]:
    rows = (
        await conn.execute(
            text("SELECT * FROM platform.approval_execution WHERE case_id = :c ORDER BY attempt"),
            {"c": uuid.UUID(case_id)},
        )
    ).all()
    return [
        ExecutionRow(
            str(r.execution_id),
            r.attempt,
            r.idempotency_key,
            r.request_hash,
            r.outcome,
            r.reason_code,
            str(r.actor_principal_id),
            r.region,
            dict(r.quorum or {}),
            r.executed_at,
        )
        for r in rows
    ]


async def insert_execution(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    case: Case,
    attempt: int,
    idempotency_key: str,
    outcome: str,
    reason_code: str | None,
    actor: str,
    quorum: dict[str, Any],
    now: datetime,
) -> str:
    eid = str(uuid.uuid4())
    await conn.execute(
        text("""
            INSERT INTO platform.approval_execution (execution_id, tenant_id, case_id, attempt, idempotency_key, request_hash,
                outcome, reason_code, actor_principal_id, region, quorum, executed_at)
            VALUES (:id, :t, :c, :n, :key, :h, :o, :rc, :a, :region, CAST(:q AS jsonb), :at)
        """),
        {
            "id": uuid.UUID(eid),
            "t": uuid.UUID(tenant_id),
            "c": uuid.UUID(case.case_id),
            "n": attempt,
            "key": idempotency_key,
            "h": case.request_hash,
            "o": outcome,
            "rc": reason_code,
            "a": uuid.UUID(actor),
            "region": case.region,
            "q": json.dumps(quorum, sort_keys=True),
            "at": now,
        },
    )
    return eid


# ------------------------------------------------------------------------------------------------ reviewers


@dataclass(frozen=True, slots=True)
class Reviewer:
    reviewer_id: str
    tenant_id: str
    principal_id: str
    person_id: str
    relationship: str
    authority_basis: str
    independence_declaration: str
    declared_conflicts: tuple[str, ...]
    region: str
    status: str
    expires_at: datetime
    proposed_by: str
    activated_via: str | None
    activation_ref: str | None
    activated_at: datetime | None
    created_at: datetime


def _reviewer(r: Row[Any]) -> Reviewer:
    m = r._mapping
    return Reviewer(
        str(m["reviewer_id"]),
        str(m["tenant_id"]),
        str(m["principal_id"]),
        str(m["person_id"]),
        m["relationship"],
        m["authority_basis"],
        m["independence_declaration"],
        tuple(str(c) for c in m["declared_conflicts"]),
        m["region"],
        m["status"],
        m["expires_at"],
        str(m["proposed_by"]),
        m["activated_via"],
        m["activation_ref"],
        m["activated_at"],
        m["created_at"],
    )


async def insert_reviewer(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    person_id: str,
    relationship: str,
    authority_basis: str,
    independence_declaration: str,
    declared_conflicts: list[str],
    region: str,
    expires_at: datetime,
    proposed_by: str,
) -> str:
    rid = str(uuid.uuid4())
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO platform.governance_reviewer (reviewer_id, tenant_id, principal_id, person_id, relationship,
                        authority_basis, independence_declaration, declared_conflicts, region, status, expires_at, proposed_by)
                    VALUES (:id, :t, :p, :person, :rel, :basis, :decl, :conf, :region, 'PROPOSED', :exp, :by)
                """),
                {
                    "id": uuid.UUID(rid),
                    "t": uuid.UUID(tenant_id),
                    "p": uuid.UUID(principal_id),
                    "person": uuid.UUID(person_id),
                    "rel": relationship,
                    "basis": authority_basis,
                    "decl": independence_declaration,
                    "conf": [uuid.UUID(c) for c in declared_conflicts],
                    "region": region,
                    "exp": expires_at,
                    "by": uuid.UUID(proposed_by),
                },
            )
    except IntegrityError as e:
        raise ApprovalError(
            "REVIEWER_ALREADY_APPOINTED", "this person is already a reviewer"
        ) from e
    return rid


async def reviewers(
    conn: AsyncConnection, *, status: str | None = None, lock: bool = False
) -> list[Reviewer]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.governance_reviewer"
                + (" WHERE status = :s" if status else "")
                + " ORDER BY created_at"
                + (" FOR UPDATE" if lock else "")
            ),
            {"s": status} if status else {},
        )
    ).all()
    return [_reviewer(r) for r in rows]


async def load_reviewer(
    conn: AsyncConnection, reviewer_id: str, *, lock: bool = False
) -> Reviewer | None:
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.governance_reviewer WHERE reviewer_id = :r"
                + (" FOR UPDATE" if lock else "")
            ),
            {"r": uuid.UUID(reviewer_id)},
        )
    ).first()
    return None if row is None else _reviewer(row)


async def set_reviewer_status(
    conn: AsyncConnection,
    reviewer_id: str,
    status: str,
    *,
    via: str | None,
    ref: str | None,
    now: datetime,
) -> None:
    await conn.execute(
        text("""
            UPDATE platform.governance_reviewer SET status = :s,
                activated_via = COALESCE(:via, activated_via), activation_ref = COALESCE(:ref, activation_ref),
                activated_at = CASE WHEN :s = 'ACTIVE' THEN :now ELSE activated_at END,
                revoked_at = CASE WHEN :s IN ('REVOKED', 'EXPIRED') THEN :now ELSE revoked_at END
            WHERE reviewer_id = :r
        """),
        {"s": status, "via": via, "ref": ref, "now": now, "r": uuid.UUID(reviewer_id)},
    )


# --------------------------------------------------------------------------------------- platform authorizations


async def executed_cases(conn: AsyncConnection, action_id: str, target_id: str) -> list[Case]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.approval_case WHERE action_id = :a AND target_id = :t AND state = 'EXECUTED' "
                "ORDER BY executed_at DESC"
            ),
            {"a": action_id, "t": target_id},
        )
    ).all()
    return [_case(r) for r in rows]


async def consume(conn: AsyncConnection, tenant_id: str, case_id: str, consumer: str) -> bool:
    """Use a platform authorization up. False if somebody already did: it authorizes one operation, once."""
    try:
        async with conn.begin_nested():
            await conn.execute(
                text(
                    "INSERT INTO platform.approval_consumption (case_id, tenant_id, consumer) VALUES (:c, :t, :by)"
                ),
                {"c": uuid.UUID(case_id), "t": uuid.UUID(tenant_id), "by": consumer[:128]},
            )
    except IntegrityError:
        return False
    return True
