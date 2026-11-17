"""Biometric method states and proposals in PostgreSQL (ZID-DTV-WF-04 BIO-03; CR-107). Platform configuration: not tenant data, no RLS.

The domain rules live in `domain/biometric_methods.py`; the tables repeat the ones that must hold even for a caller that skips the domain code
(migration verification_0019).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import biometric_methods as bm

_SCOPE = "method, engine_id, engine_version, country, purpose, tenant_id"
_STATE_COLUMNS = (
    f"scope_id, version, {_SCOPE}, state, dpia_ref, evaluation_ref, reason, evidence_ref, recorded_by, proposed_by, "
    "proposal_id, effective_at, recorded_at"
)
_PROPOSAL_COLUMNS = (
    f"proposal_id, scope_id, {_SCOPE}, from_version, target, dpia_ref, evaluation_ref, reason, evidence_ref, proposed_by, "
    "proposed_at, status, decided_by, decided_at, decision_reason"
)


def _scope(r: Any) -> bm.Scope:
    return bm.Scope(
        bm.Method(r.method),
        r.engine_id,
        r.engine_version,
        r.country,
        r.purpose,
        str(r.tenant_id) if r.tenant_id is not None else None,
    )


def _record(r: Any) -> bm.Record:
    return bm.Record(
        scope_id=r.scope_id,
        version=r.version,
        scope=_scope(r),
        state=bm.State(r.state),
        dpia_ref=r.dpia_ref,
        evaluation_ref=r.evaluation_ref,
        reason=r.reason,
        evidence_ref=r.evidence_ref,
        recorded_by=r.recorded_by,
        proposed_by=r.proposed_by,
        proposal_id=str(r.proposal_id) if r.proposal_id is not None else None,
        effective_at=r.effective_at,
        recorded_at=r.recorded_at,
    )


def _proposal(r: Any) -> bm.Proposal:
    return bm.Proposal(
        proposal_id=str(r.proposal_id),
        scope_id=r.scope_id,
        scope=_scope(r),
        from_version=r.from_version,
        target=bm.State(r.target),
        dpia_ref=r.dpia_ref,
        evaluation_ref=r.evaluation_ref,
        reason=r.reason,
        evidence_ref=r.evidence_ref,
        proposed_by=r.proposed_by,
        proposed_at=r.proposed_at,
        status=bm.ProposalStatus(r.status),
        decided_by=r.decided_by,
        decided_at=r.decided_at,
        decision_reason=r.decision_reason,
    )


def _scope_params(s: bm.Scope) -> dict[str, Any]:
    return {
        "method": s.method.value,
        "engine_id": s.engine_id,
        "engine_version": s.engine_version,
        "country": s.country,
        "purpose": s.purpose,
        "tenant_id": uuid.UUID(s.tenant_id) if s.tenant_id is not None else None,
    }


async def history(conn: AsyncConnection, scope_id: str, *, lock: bool = False) -> list[bm.Record]:
    sql = f"SELECT {_STATE_COLUMNS} FROM verify.biometric_method_state WHERE scope_id = :i ORDER BY version"
    rows = (await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"i": scope_id})).all()
    return [_record(r) for r in rows]


async def scope_id_of(conn: AsyncConnection, scope: bm.Scope) -> str | None:
    """The id already used for this exact scope, by a state or an open proposal."""
    p = _scope_params(scope)
    where = (
        "method = :method AND engine_id = :engine_id AND engine_version = :engine_version AND country = :country "
        "AND purpose = :purpose AND tenant_id IS NOT DISTINCT FROM :tenant_id"
    )
    for table in ("biometric_method_state", "biometric_method_proposal"):
        row = (
            await conn.execute(
                text(f"SELECT scope_id FROM verify.{table} WHERE {where} LIMIT 1"), p
            )
        ).first()
        if row is not None:
            return str(row.scope_id)
    return None


async def for_call(conn: AsyncConnection, call: bm.Call) -> list[list[bm.Record]]:
    """Every version of every scope a live call could match (its own tenant's and the all-tenant one), grouped by scope id."""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_STATE_COLUMNS} FROM verify.biometric_method_state "
                "WHERE method = :m AND engine_id = :e AND engine_version = :v AND country = :c AND purpose = :p "
                "AND (tenant_id IS NULL OR tenant_id = :t) ORDER BY scope_id, version"
            ),
            {
                "m": call.method.value,
                "e": call.engine_id,
                "v": call.engine_version,
                "c": call.country,
                "p": call.purpose,
                "t": uuid.UUID(call.tenant_id),
            },
        )
    ).all()
    grouped: dict[str, list[bm.Record]] = {}
    for r in rows:
        grouped.setdefault(r.scope_id, []).append(_record(r))
    return list(grouped.values())


async def all_states(conn: AsyncConnection, *, limit: int = 2000) -> list[bm.Record]:
    rows = (
        await conn.execute(
            text(
                f"SELECT {_STATE_COLUMNS} FROM verify.biometric_method_state ORDER BY scope_id, version LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_record(r) for r in rows]


async def proposals(
    conn: AsyncConnection, *, open_only: bool, limit: int = 500
) -> list[bm.Proposal]:
    where = "WHERE status = 'OPEN' " if open_only else ""
    rows = (
        await conn.execute(
            text(
                f"SELECT {_PROPOSAL_COLUMNS} FROM verify.biometric_method_proposal {where}ORDER BY proposed_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    return [_proposal(r) for r in rows]


async def proposal(
    conn: AsyncConnection, proposal_id: str, *, lock: bool = False
) -> bm.Proposal | None:
    try:
        pid = uuid.UUID(proposal_id)
    except ValueError:
        return None
    sql = f"SELECT {_PROPOSAL_COLUMNS} FROM verify.biometric_method_proposal WHERE proposal_id = :p"
    row = (await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"p": pid})).first()
    return _proposal(row) if row is not None else None


async def insert_proposal(conn: AsyncConnection, p: bm.Proposal) -> None:
    await conn.execute(
        text(
            f"INSERT INTO verify.biometric_method_proposal ({_PROPOSAL_COLUMNS}) VALUES (:id, :sid, :method, :engine_id, "
            ":engine_version, :country, :purpose, :tenant_id, :fv, :target, :dpia, :ev, :r, :e, :by, :at, 'OPEN', NULL, NULL, NULL)"
        ),
        {
            "id": uuid.UUID(p.proposal_id),
            "sid": p.scope_id,
            **_scope_params(p.scope),
            "fv": p.from_version,
            "target": p.target.value,
            "dpia": p.dpia_ref,
            "ev": p.evaluation_ref,
            "r": p.reason,
            "e": p.evidence_ref,
            "by": p.proposed_by,
            "at": p.proposed_at,
        },
    )


async def decide_proposal(conn: AsyncConnection, p: bm.Proposal) -> None:
    await conn.execute(
        text(
            "UPDATE verify.biometric_method_proposal SET status = :s, decided_by = :by, decided_at = :at, decision_reason = :r "
            "WHERE proposal_id = :id AND status = 'OPEN'"
        ),
        {
            "s": p.status.value,
            "by": p.decided_by,
            "at": p.decided_at,
            "r": p.decision_reason,
            "id": uuid.UUID(p.proposal_id),
        },
    )


async def insert_state(conn: AsyncConnection, r: bm.Record) -> None:
    await conn.execute(
        text(
            f"INSERT INTO verify.biometric_method_state ({_STATE_COLUMNS}) VALUES (:sid, :v, :method, :engine_id, :engine_version, "
            ":country, :purpose, :tenant_id, :state, :dpia, :ev, :r, :e, :by, :pby, :pid, :ea, :at)"
        ),
        {
            "sid": r.scope_id,
            "v": r.version,
            **_scope_params(r.scope),
            "state": r.state.value,
            "dpia": r.dpia_ref,
            "ev": r.evaluation_ref,
            "r": r.reason,
            "e": r.evidence_ref,
            "by": r.recorded_by,
            "pby": r.proposed_by,
            "pid": uuid.UUID(r.proposal_id) if r.proposal_id else None,
            "ea": r.effective_at,
            "at": r.recorded_at,
        },
    )
