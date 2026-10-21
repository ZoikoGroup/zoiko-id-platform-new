"""PostgreSQL persistence of the operator_access module (DEC-F4; CR-012).

Operator-plane rows live in the operator tenant; lockbox requests and the tenant's operator access
policy live in the customer tenant, under row-level security. The only cross-tenant step is
`in_tenant`: a transaction-local switch of `app.tenant_id` to the request's *target* tenant for
the lockbox and policy rows of that one request, restored on exit. Nothing here takes a tenant
from a client: callers pass the operator tenant derived by authentication and the target tenant
recorded on the grant.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.operator_access.domain.model import (
    OPEN,
    GrantState,
    LockboxState,
    OperatorAccessError,
    ReviewOutcome,
    ReviewState,
    effective_lockbox_state,
)


def _uuid(value: str, code: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError) as e:
        raise OperatorAccessError(code) from e


@asynccontextmanager
async def in_tenant(conn: AsyncConnection, tenant_id: str) -> AsyncIterator[AsyncConnection]:
    """Run a block under another tenant's RLS scope, inside the same transaction."""
    previous = (await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar()
    await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant_id})
    try:
        yield conn
    finally:
        await conn.execute(
            text("SELECT set_config('app.tenant_id', :t, true)"), {"t": previous or ""}
        )


async def current_tenant(conn: AsyncConnection) -> str:
    return str((await conn.execute(text("SELECT current_setting('app.tenant_id', true)"))).scalar())


# ------------------------------------------------------------------------------------ profiles


@dataclass(frozen=True, slots=True)
class OperatorProfile:
    tenant_id: str
    principal_id: str
    jurisdiction: str
    roles: frozenset[str]
    status: str

    @property
    def active(self) -> bool:
        return self.status == "ACTIVE"


async def upsert_profile(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    principal_id: str,
    jurisdiction: str,
    roles: frozenset[str],
    status: str,
    now: datetime,
) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.operator_profile (tenant_id, principal_id, jurisdiction, roles, status,
                created_at, updated_at)
            VALUES (:t, :p, :j, :r, :s, :now, :now)
            ON CONFLICT (tenant_id, principal_id) DO UPDATE SET jurisdiction = EXCLUDED.jurisdiction,
                roles = EXCLUDED.roles, status = EXCLUDED.status, updated_at = EXCLUDED.updated_at
        """),
        {
            "t": uuid.UUID(tenant_id),
            "p": uuid.UUID(principal_id),
            "j": jurisdiction.upper(),
            "r": sorted(roles),
            "s": status,
            "now": now,
        },
    )


async def load_profile(conn: AsyncConnection, principal_id: str) -> OperatorProfile | None:
    row = (
        await conn.execute(
            text("SELECT * FROM platform.operator_profile WHERE principal_id = :p"),
            {"p": uuid.UUID(principal_id)},
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    return OperatorProfile(
        str(m["tenant_id"]),
        str(m["principal_id"]),
        m["jurisdiction"],
        frozenset(m["roles"]),
        m["status"],
    )


# -------------------------------------------------------------------------------------- grants


@dataclass(frozen=True, slots=True)
class Grant:
    jit_grant_id: str
    tenant_id: str
    requester_principal_id: str
    capability: str
    target_tenant_id: str | None
    environment: str
    cell_id: str
    resource_scope: str
    case_ref: str
    reason: str
    risk_class: int
    evidence_class: int
    duration: timedelta
    required_approvals: int
    lockbox_required: bool
    lockbox_request_id: str | None
    break_glass: bool
    requester_jurisdiction: str
    state: GrantState
    version: int
    created_at: datetime
    request_expires_at: datetime
    approved_at: datetime | None = None
    activated_at: datetime | None = None
    expires_at: datetime | None = None
    ended_at: datetime | None = None
    ended_by: str | None = None
    end_reason: str | None = None

    def effective_state(self, now: datetime) -> GrantState:
        """Expiry is time-based: a grant past its end is EXPIRED whether or not the sweeper ran."""
        if self.state is GrantState.ACTIVE and self.expires_at and self.expires_at <= now:
            return GrantState.EXPIRED
        if self.state in (GrantState.REQUESTED, GrantState.APPROVED) and (
            self.request_expires_at <= now
        ):
            return GrantState.EXPIRED
        return self.state


def _grant(row: Any) -> Grant:
    m = row._mapping
    return Grant(
        jit_grant_id=str(m["jit_grant_id"]),
        tenant_id=str(m["tenant_id"]),
        requester_principal_id=str(m["requester_principal_id"]),
        capability=m["capability"],
        target_tenant_id=str(m["target_tenant_id"]) if m["target_tenant_id"] else None,
        environment=m["environment"],
        cell_id=m["cell_id"],
        resource_scope=m["resource_scope"],
        case_ref=m["case_ref"],
        reason=m["reason"],
        risk_class=m["risk_class"],
        evidence_class=m["evidence_class"],
        duration=timedelta(seconds=m["duration_seconds"]),
        required_approvals=m["required_approvals"],
        lockbox_required=m["lockbox_required"],
        lockbox_request_id=str(m["lockbox_request_id"]) if m["lockbox_request_id"] else None,
        break_glass=m["break_glass"],
        requester_jurisdiction=m["requester_jurisdiction"],
        state=GrantState(m["state"]),
        version=m["version"],
        created_at=m["created_at"],
        request_expires_at=m["request_expires_at"],
        approved_at=m["approved_at"],
        activated_at=m["activated_at"],
        expires_at=m["expires_at"],
        ended_at=m["ended_at"],
        ended_by=str(m["ended_by"]) if m["ended_by"] else None,
        end_reason=m["end_reason"],
    )


async def insert_grant(conn: AsyncConnection, g: Grant) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.jit_grant (jit_grant_id, tenant_id, requester_principal_id, capability,
                target_tenant_id, environment, cell_id, resource_scope, case_ref, reason, risk_class,
                evidence_class, duration_seconds, required_approvals, lockbox_required, lockbox_request_id,
                break_glass, requester_jurisdiction, state, version, created_at, request_expires_at,
                approved_at, activated_at, expires_at)
            VALUES (:id, :t, :req, :cap, :tt, :env, :cell, :scope, :case, :reason, :risk, :ev, :dur, :k,
                :lb, :lbid, :bg, :jur, :state, :ver, :created, :rexp, :appr, :act, :exp)
        """),
        {
            "id": uuid.UUID(g.jit_grant_id),
            "t": uuid.UUID(g.tenant_id),
            "req": uuid.UUID(g.requester_principal_id),
            "cap": g.capability,
            "tt": uuid.UUID(g.target_tenant_id) if g.target_tenant_id else None,
            "env": g.environment,
            "cell": g.cell_id,
            "scope": g.resource_scope,
            "case": g.case_ref,
            "reason": g.reason,
            "risk": g.risk_class,
            "ev": g.evidence_class,
            "dur": int(g.duration.total_seconds()),
            "k": g.required_approvals,
            "lb": g.lockbox_required,
            "lbid": uuid.UUID(g.lockbox_request_id) if g.lockbox_request_id else None,
            "bg": g.break_glass,
            "jur": g.requester_jurisdiction,
            "state": g.state.value,
            "ver": g.version,
            "created": g.created_at,
            "rexp": g.request_expires_at,
            "appr": g.approved_at,
            "act": g.activated_at,
            "exp": g.expires_at,
        },
    )


async def load_grant(conn: AsyncConnection, grant_id: str, *, lock: bool = False) -> Grant:
    gid = _uuid(grant_id, "OPERATOR_ACCESS_REQUEST_NOT_FOUND")
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.jit_grant WHERE jit_grant_id = :g"
                + (" FOR UPDATE" if lock else "")
            ),
            {"g": gid},
        )
    ).first()
    if row is None:
        raise OperatorAccessError("OPERATOR_ACCESS_REQUEST_NOT_FOUND")
    return _grant(row)


async def list_grants(
    conn: AsyncConnection,
    *,
    requester: str | None = None,
    states: tuple[GrantState, ...] = (),
    limit: int = 100,
) -> list[Grant]:
    where = ["true"]
    params: dict[str, Any] = {"lim": limit}
    if requester:
        where.append("requester_principal_id = :req")
        params["req"] = uuid.UUID(requester)
    if states:
        where.append("state = ANY(:states)")
        params["states"] = [s.value for s in states]
    rows = (
        await conn.execute(
            text(
                f"SELECT * FROM platform.jit_grant WHERE {' AND '.join(where)} "  # noqa: S608
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    return [_grant(r) for r in rows]


async def update_grant(conn: AsyncConnection, g: Grant, **fields: Any) -> Grant:
    """Optimistic: the row must still be at `g.version`; returns the new grant."""
    sets = dict(fields)
    if "state" in sets and isinstance(sets["state"], GrantState):
        sets["state"] = sets["state"].value
    if "ended_by" in sets and sets["ended_by"]:
        sets["ended_by"] = uuid.UUID(sets["ended_by"])
    assignments = ", ".join(f"{k} = :{k}" for k in sets)
    row = (
        await conn.execute(
            text(
                f"UPDATE platform.jit_grant SET {assignments}, version = version + 1 "  # noqa: S608
                "WHERE jit_grant_id = :gid AND version = :ver RETURNING *"
            ),
            {**sets, "gid": uuid.UUID(g.jit_grant_id), "ver": g.version},
        )
    ).first()
    if row is None:
        raise OperatorAccessError("CONCURRENT_MODIFICATION")
    return _grant(row)


async def held_capabilities(
    conn: AsyncConnection, principal_id: str, now: datetime, *, include_pending: bool = False
) -> set[str]:
    """Capabilities this operator holds now (ACTIVE, unexpired); optionally also open requests."""
    states = [s.value for s in OPEN] if include_pending else [GrantState.ACTIVE.value]
    rows = (
        await conn.execute(
            text("""
                SELECT capability FROM platform.jit_grant
                WHERE requester_principal_id = :p AND state = ANY(:states)
                  AND (state <> 'ACTIVE' OR expires_at > :now)
                  AND (state = 'ACTIVE' OR request_expires_at > :now)
            """),
            {"p": uuid.UUID(principal_id), "states": states, "now": now},
        )
    ).all()
    return {r.capability for r in rows}


async def active_grants_for(
    conn: AsyncConnection, principal_id: str, capability: str, now: datetime
) -> list[Grant]:
    rows = (
        await conn.execute(
            text("""
                SELECT * FROM platform.jit_grant
                WHERE requester_principal_id = :p AND capability = :c AND state = 'ACTIVE'
                  AND expires_at > :now
            """),
            {"p": uuid.UUID(principal_id), "c": capability, "now": now},
        )
    ).all()
    return [_grant(r) for r in rows]


async def expire_due(conn: AsyncConnection, now: datetime) -> list[Grant]:
    """ACTIVE grants past their end, and requests never approved/activated in time -> EXPIRED."""
    rows = (
        await conn.execute(
            text("""
                UPDATE platform.jit_grant SET state = 'EXPIRED', ended_at = :now,
                    end_reason = CASE WHEN state = 'ACTIVE' THEN 'duration_elapsed'
                                      ELSE 'request_not_completed' END,
                    version = version + 1
                WHERE (state = 'ACTIVE' AND expires_at <= :now)
                   OR (state IN ('REQUESTED', 'APPROVED') AND request_expires_at <= :now)
                RETURNING *
            """),
            {"now": now},
        )
    ).all()
    return [_grant(r) for r in rows]


# ------------------------------------------------------------------------------------ approvals


async def insert_approval(
    conn: AsyncConnection, g: Grant, *, approver: str, verdict: str, now: datetime
) -> None:
    try:
        async with conn.begin_nested():
            await conn.execute(
                text("""
                    INSERT INTO platform.operator_access_approval (approval_id, tenant_id, jit_grant_id,
                        requester_principal_id, approver_principal_id, verdict, decided_at)
                    VALUES (:id, :t, :g, :req, :a, :v, :now)
                """),
                {
                    "id": uuid.uuid4(),
                    "t": uuid.UUID(g.tenant_id),
                    "g": uuid.UUID(g.jit_grant_id),
                    "req": uuid.UUID(g.requester_principal_id),
                    "a": uuid.UUID(approver),
                    "v": verdict,
                    "now": now,
                },
            )
    except IntegrityError as e:  # unique per approver, never the requester (DB guards)
        raise OperatorAccessError("APPROVER_ALREADY_DECIDED") from e


async def decisions(conn: AsyncConnection, grant_id: str) -> list[tuple[str, str]]:
    rows = (
        await conn.execute(
            text(
                "SELECT approver_principal_id, verdict FROM platform.operator_access_approval "
                "WHERE jit_grant_id = :g ORDER BY decided_at"
            ),
            {"g": uuid.UUID(grant_id)},
        )
    ).all()
    return [(str(r.approver_principal_id), r.verdict) for r in rows]


# -------------------------------------------------------------------------------------- lockbox


@dataclass(frozen=True, slots=True)
class Lockbox:
    lockbox_request_id: str
    tenant_id: str
    operator_tenant_id: str
    jit_grant_id: str
    requester_principal_id: str
    capability: str
    resource_scope: str
    case_ref: str
    reason: str
    duration: timedelta
    state: str
    version: int
    created_at: datetime
    expires_at: datetime
    decided_by: str | None
    decided_at: datetime | None

    def effective_state(self, now: datetime) -> LockboxState:
        return effective_lockbox_state(self.state, self.expires_at, now)


def _lockbox(row: Any) -> Lockbox:
    m = row._mapping
    return Lockbox(
        str(m["lockbox_request_id"]),
        str(m["tenant_id"]),
        str(m["operator_tenant_id"]),
        str(m["jit_grant_id"]),
        str(m["requester_principal_id"]),
        m["capability"],
        m["resource_scope"],
        m["case_ref"],
        m["reason"],
        timedelta(seconds=m["duration_seconds"]),
        m["state"],
        m["version"],
        m["created_at"],
        m["expires_at"],
        str(m["decided_by"]) if m["decided_by"] else None,
        m["decided_at"],
    )


async def insert_lockbox(conn: AsyncConnection, lb: Lockbox) -> None:
    """`conn` must be scoped to the customer tenant (`in_tenant`)."""
    await conn.execute(
        text("""
            INSERT INTO platform.lockbox_request (lockbox_request_id, tenant_id, operator_tenant_id,
                jit_grant_id, requester_principal_id, capability, resource_scope, case_ref, reason,
                duration_seconds, state, created_at, expires_at)
            VALUES (:id, :t, :ot, :g, :req, :cap, :scope, :case, :reason, :dur, 'PENDING', :now, :exp)
        """),
        {
            "id": uuid.UUID(lb.lockbox_request_id),
            "t": uuid.UUID(lb.tenant_id),
            "ot": uuid.UUID(lb.operator_tenant_id),
            "g": uuid.UUID(lb.jit_grant_id),
            "req": uuid.UUID(lb.requester_principal_id),
            "cap": lb.capability,
            "scope": lb.resource_scope,
            "case": lb.case_ref,
            "reason": lb.reason,
            "dur": int(lb.duration.total_seconds()),
            "now": lb.created_at,
            "exp": lb.expires_at,
        },
    )


async def load_lockbox(
    conn: AsyncConnection, lockbox_id: str, *, lock: bool = False
) -> Lockbox | None:
    lid = _uuid(lockbox_id, "LOCKBOX_REQUEST_NOT_FOUND")
    row = (
        await conn.execute(
            text(
                "SELECT * FROM platform.lockbox_request WHERE lockbox_request_id = :l"
                + (" FOR UPDATE" if lock else "")
            ),
            {"l": lid},
        )
    ).first()
    return _lockbox(row) if row is not None else None


async def list_lockbox(
    conn: AsyncConnection, *, state: str | None = None, limit: int = 100
) -> list[Lockbox]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.lockbox_request WHERE (CAST(:s AS text) IS NULL OR state = :s) "
                "ORDER BY created_at DESC LIMIT :lim"
            ),
            {"s": state, "lim": limit},
        )
    ).all()
    return [_lockbox(r) for r in rows]


async def decide_lockbox(
    conn: AsyncConnection,
    lb: Lockbox,
    *,
    state: LockboxState,
    by: str,
    reason: str | None,
    now: datetime,
) -> Lockbox:
    row = (
        await conn.execute(
            text("""
                UPDATE platform.lockbox_request SET state = :s, decided_by = :by, decided_at = :now,
                    decision_reason = :r, version = version + 1
                WHERE lockbox_request_id = :l AND version = :v RETURNING *
            """),
            {
                "s": state.value,
                "by": uuid.UUID(by),
                "now": now,
                "r": reason,
                "l": uuid.UUID(lb.lockbox_request_id),
                "v": lb.version,
            },
        )
    ).first()
    if row is None:
        raise OperatorAccessError("CONCURRENT_MODIFICATION")
    return _lockbox(row)


# ------------------------------------------------------------------------- tenant access policy


@dataclass(frozen=True, slots=True)
class AccessPolicy:
    tenant_id: str
    lockbox_required: bool = True  # default: customer data needs the customer's approval
    allowed_operator_jurisdictions: tuple[str, ...] = ()  # empty: unrestricted
    revision: int = 0


async def load_policy(conn: AsyncConnection, tenant_id: str) -> AccessPolicy:
    """`conn` must be scoped to `tenant_id`. No row: the default (lockbox required)."""
    row = (
        await conn.execute(
            text("SELECT * FROM platform.operator_access_policy WHERE tenant_id = :t"),
            {"t": uuid.UUID(tenant_id)},
        )
    ).first()
    if row is None:
        return AccessPolicy(tenant_id)
    m = row._mapping
    return AccessPolicy(
        tenant_id,
        m["lockbox_required"],
        tuple(m["allowed_operator_jurisdictions"]),
        m["revision"],
    )


async def save_policy(
    conn: AsyncConnection,
    *,
    tenant_id: str,
    lockbox_required: bool,
    jurisdictions: tuple[str, ...],
    by: str,
    now: datetime,
) -> AccessPolicy:
    row = (
        await conn.execute(
            text("""
                INSERT INTO platform.operator_access_policy (tenant_id, lockbox_required,
                    allowed_operator_jurisdictions, revision, updated_by, updated_at)
                VALUES (:t, :lb, :j, 1, :by, :now)
                ON CONFLICT (tenant_id) DO UPDATE SET lockbox_required = EXCLUDED.lockbox_required,
                    allowed_operator_jurisdictions = EXCLUDED.allowed_operator_jurisdictions,
                    revision = platform.operator_access_policy.revision + 1,
                    updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at
                RETURNING revision
            """),
            {
                "t": uuid.UUID(tenant_id),
                "lb": lockbox_required,
                "j": [j.upper() for j in jurisdictions],
                "by": uuid.UUID(by),
                "now": now,
            },
        )
    ).scalar_one()
    return AccessPolicy(tenant_id, lockbox_required, tuple(j.upper() for j in jurisdictions), row)


# ------------------------------------------------------------------------- break-glass reviews


@dataclass(frozen=True, slots=True)
class Review:
    review_id: str
    tenant_id: str
    jit_grant_id: str
    requester_principal_id: str
    state: ReviewState
    reviewer_principal_id: str | None
    outcome: ReviewOutcome | None
    created_at: datetime
    reviewed_at: datetime | None


def _review(row: Any) -> Review:
    m = row._mapping
    return Review(
        str(m["review_id"]),
        str(m["tenant_id"]),
        str(m["jit_grant_id"]),
        str(m["requester_principal_id"]),
        ReviewState(m["state"]),
        str(m["reviewer_principal_id"]) if m["reviewer_principal_id"] else None,
        ReviewOutcome(m["outcome"]) if m["outcome"] else None,
        m["created_at"],
        m["reviewed_at"],
    )


async def insert_review(conn: AsyncConnection, g: Grant, review_id: str, now: datetime) -> None:
    await conn.execute(
        text("""
            INSERT INTO platform.break_glass_review (review_id, tenant_id, jit_grant_id,
                requester_principal_id, state, created_at)
            VALUES (:id, :t, :g, :req, 'REVIEW_PENDING', :now)
        """),
        {
            "id": uuid.UUID(review_id),
            "t": uuid.UUID(g.tenant_id),
            "g": uuid.UUID(g.jit_grant_id),
            "req": uuid.UUID(g.requester_principal_id),
            "now": now,
        },
    )


async def load_review(conn: AsyncConnection, review_id: str) -> Review:
    rid = _uuid(review_id, "REVIEW_NOT_FOUND")
    row = (
        await conn.execute(
            text("SELECT * FROM platform.break_glass_review WHERE review_id = :r FOR UPDATE"),
            {"r": rid},
        )
    ).first()
    if row is None:
        raise OperatorAccessError("REVIEW_NOT_FOUND")
    return _review(row)


async def list_reviews(
    conn: AsyncConnection, *, state: ReviewState | None, limit: int = 100
) -> list[Review]:
    rows = (
        await conn.execute(
            text(
                "SELECT * FROM platform.break_glass_review "
                "WHERE (CAST(:s AS text) IS NULL OR state = :s) ORDER BY created_at DESC LIMIT :lim"
            ),
            {"s": state.value if state else None, "lim": limit},
        )
    ).all()
    return [_review(r) for r in rows]


async def complete_review(
    conn: AsyncConnection,
    r: Review,
    *,
    reviewer: str,
    outcome: ReviewOutcome,
    notes: str,
    now: datetime,
) -> Review:
    try:
        async with conn.begin_nested():
            row = (
                await conn.execute(
                    text("""
                        UPDATE platform.break_glass_review SET state = 'REVIEWED',
                            reviewer_principal_id = :rv, outcome = :o, notes = :n, reviewed_at = :now
                        WHERE review_id = :id AND state = 'REVIEW_PENDING' RETURNING *
                    """),
                    {
                        "rv": uuid.UUID(reviewer),
                        "o": outcome.value,
                        "n": notes,
                        "now": now,
                        "id": uuid.UUID(r.review_id),
                    },
                )
            ).first()
    except IntegrityError as e:  # reviewer == requester (DB guard)
        raise OperatorAccessError("REVIEWER_SELF_REVIEW") from e
    if row is None:
        raise OperatorAccessError("OPERATOR_ACCESS_INVALID_STATE", "Review already completed.")
    return _review(row)
