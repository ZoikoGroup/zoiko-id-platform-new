"""Step-up challenges and proofs (`authn.step_up_challenge`, `authn.step_up_proof`; ZID-ENG-DEC-009 section 05)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


@dataclass(frozen=True, slots=True)
class ChallengeRow:
    challenge_id: str
    tenant_id: str
    actor_id: str
    session_id: str | None
    action_id: str
    family: str
    tier: str
    resource_id: str
    command_hash: str
    summary: str
    requested_assurance: str
    nonce: bytes
    policy_version: str
    status: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class ProofRow:
    proof_id: str
    challenge_id: str
    actor_id: str
    session_id: str | None
    method: str
    verified_at: datetime
    expires_at: datetime
    single_use: bool
    consumed_at: datetime | None
    challenge: ChallengeRow


def _u(v: str | None) -> uuid.UUID | None:
    return uuid.UUID(v) if v else None


def _challenge(r: Any) -> ChallengeRow:
    return ChallengeRow(
        challenge_id=str(r.challenge_id),
        tenant_id=str(r.tenant_id),
        actor_id=str(r.actor_id),
        session_id=str(r.session_id) if r.session_id else None,
        action_id=r.action_id,
        family=r.family,
        tier=r.tier,
        resource_id=r.resource_id,
        command_hash=r.command_hash,
        summary=r.summary,
        requested_assurance=r.requested_assurance,
        nonce=bytes(r.nonce),
        policy_version=r.policy_version,
        status=r.status,
        created_at=r.created_at,
        expires_at=r.expires_at,
    )


async def insert_challenge(conn: AsyncConnection, c: ChallengeRow) -> None:
    await conn.execute(
        text("""
            INSERT INTO authn.step_up_challenge (challenge_id, tenant_id, actor_id, session_id, action_id, family, tier,
                resource_id, command_hash, summary, requested_assurance, nonce, policy_version, created_at, expires_at)
            VALUES (:id, :t, :a, :s, :act, :fam, :tier, :res, :h, :sum, :req, :n, :pv, :c, :e)
        """),
        {
            "id": uuid.UUID(c.challenge_id),
            "t": uuid.UUID(c.tenant_id),
            "a": uuid.UUID(c.actor_id),
            "s": _u(c.session_id),
            "act": c.action_id,
            "fam": c.family,
            "tier": c.tier,
            "res": c.resource_id,
            "h": c.command_hash,
            "sum": c.summary,
            "req": c.requested_assurance,
            "n": c.nonce,
            "pv": c.policy_version,
            "c": c.created_at,
            "e": c.expires_at,
        },
    )


async def load_challenge(conn: AsyncConnection, challenge_id: str) -> ChallengeRow | None:
    row = (
        await conn.execute(
            text("SELECT * FROM authn.step_up_challenge WHERE challenge_id = :i"),
            {"i": uuid.UUID(challenge_id)},
        )
    ).first()
    return _challenge(row) if row else None


async def resolve_challenge(
    conn: AsyncConnection, challenge_id: str, status: str, now: datetime
) -> bool:
    """PENDING to VERIFIED, CANCELLED or EXPIRED, once. False when it was not pending any more."""
    row = await conn.execute(
        text(
            "UPDATE authn.step_up_challenge SET status = :s, resolved_at = :n "
            "WHERE challenge_id = :i AND status = 'PENDING' RETURNING challenge_id"
        ),
        {"s": status, "n": now, "i": uuid.UUID(challenge_id)},
    )
    return row.first() is not None


async def insert_proof(
    conn: AsyncConnection,
    *,
    proof_id: str,
    challenge: ChallengeRow,
    authenticator_id: str,
    method: str,
    assurance: dict[str, Any],
    auth_time: datetime,
    verified_at: datetime,
    binding_hash: str,
    expires_at: datetime,
    single_use: bool,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO authn.step_up_proof (proof_id, challenge_id, tenant_id, actor_id, session_id, authenticator_id,
                method, assurance, auth_time, verified_at, binding_hash, expires_at, single_use)
            VALUES (:p, :c, :t, :a, :s, :auth, :m, CAST(:as AS jsonb), :at, :v, :b, :e, :su)
        """),
        {
            "p": uuid.UUID(proof_id),
            "c": uuid.UUID(challenge.challenge_id),
            "t": uuid.UUID(challenge.tenant_id),
            "a": uuid.UUID(challenge.actor_id),
            "s": _u(challenge.session_id),
            "auth": uuid.UUID(authenticator_id),
            "m": method,
            "as": json.dumps(assurance, sort_keys=True),
            "at": auth_time,
            "v": verified_at,
            "b": binding_hash,
            "e": expires_at,
            "su": single_use,
        },
    )


async def load_proof(conn: AsyncConnection, proof_id: str) -> ProofRow | None:
    row = (
        await conn.execute(
            text("""
                SELECT p.proof_id, p.challenge_id, p.actor_id AS p_actor, p.session_id AS p_session, p.method,
                       p.verified_at, p.expires_at AS p_expires, p.single_use, p.consumed_at, c.*
                FROM authn.step_up_proof p JOIN authn.step_up_challenge c USING (challenge_id)
                WHERE p.proof_id = :i
            """),
            {"i": uuid.UUID(proof_id)},
        )
    ).first()
    if row is None:
        return None
    return ProofRow(
        proof_id=str(row.proof_id),
        challenge_id=str(row.challenge_id),
        actor_id=str(row.p_actor),
        session_id=str(row.p_session) if row.p_session else None,
        method=row.method,
        verified_at=row.verified_at,
        expires_at=row.p_expires,
        single_use=bool(row.single_use),
        consumed_at=row.consumed_at,
        challenge=_challenge(row),
    )


async def consume_proof(
    conn: AsyncConnection, proof_id: str, consumed_for: str, now: datetime
) -> bool:
    """Atomic and once: of any number of parallel or repeated uses, exactly one gets True."""
    row = await conn.execute(
        text(
            "UPDATE authn.step_up_proof SET consumed_at = :n, consumed_for = :f "
            "WHERE proof_id = :i AND consumed_at IS NULL AND expires_at > :n RETURNING proof_id"
        ),
        {"n": now, "f": consumed_for[:160], "i": uuid.UUID(proof_id)},
    )
    return row.first() is not None
