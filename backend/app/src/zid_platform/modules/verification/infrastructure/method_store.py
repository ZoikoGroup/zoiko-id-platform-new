"""Method lifecycle states and proposals in PostgreSQL, for any family of methods that shares the `biometric_methods` lifecycle (CR-107, CR-112).
Platform configuration: not tenant data, no RLS.

One `MethodTables` per family: the biometric methods (`verify.biometric_method_*`, WF-04) and the WF-05 check methods (`verify.check_method_*`). Both
pairs of tables have the same columns and repeat the same domain rules in their triggers. The table names are constants chosen here, never input.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.verification.domain import biometric_methods as bm

_SCOPE = "method, engine_id, engine_version, country, purpose, tenant_id"
_STATE_COLUMNS = (
    f"scope_id, version, {_SCOPE}, state, dpia_ref, evaluation_ref, reason, evidence_ref, recorded_by, proposed_by, "
    "proposal_id, effective_at, recorded_at, exposure_percent, decision_hash"
)
_PROPOSAL_COLUMNS = (
    f"proposal_id, scope_id, {_SCOPE}, from_version, target, dpia_ref, evaluation_ref, reason, evidence_ref, proposed_by, "
    "proposed_at, status, decided_by, decided_at, decision_reason, exposure_percent"
)
_KNOWN_TABLES = frozenset(
    {
        ("verify.biometric_method_state", "verify.biometric_method_proposal"),
        ("verify.check_method_state", "verify.check_method_proposal"),
    }
)


@dataclass(frozen=True, slots=True)
class MethodTables:
    state_table: str
    proposal_table: str
    method_enum: type[StrEnum]

    def __post_init__(self) -> None:
        if (self.state_table, self.proposal_table) not in _KNOWN_TABLES:
            raise ValueError(
                "unknown method tables"
            )  # the names go into SQL text: only the fixed pairs

    # ------------------------------------------------------------------------------------------------------------------ mapping

    def _scope(self, r: Any) -> bm.Scope:
        return bm.Scope(
            self.method_enum(r.method),
            r.engine_id,
            r.engine_version,
            r.country,
            r.purpose,
            str(r.tenant_id) if r.tenant_id is not None else None,
        )

    def _record(self, r: Any) -> bm.Record:
        return bm.Record(
            scope_id=r.scope_id,
            version=r.version,
            scope=self._scope(r),
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
            exposure_percent=int(r.exposure_percent),
            decision_hash=r.decision_hash,
        )

    def _proposal(self, r: Any) -> bm.Proposal:
        return bm.Proposal(
            proposal_id=str(r.proposal_id),
            scope_id=r.scope_id,
            scope=self._scope(r),
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
            exposure_percent=int(r.exposure_percent),
        )

    def _scope_params(self, s: bm.Scope) -> dict[str, Any]:
        if not isinstance(s.method, self.method_enum):
            raise bm.MethodError("METHOD_FAMILY_MISMATCH", str(s.method))
        return {
            "method": s.method.value,
            "engine_id": s.engine_id,
            "engine_version": s.engine_version,
            "country": s.country,
            "purpose": s.purpose,
            "tenant_id": uuid.UUID(s.tenant_id) if s.tenant_id is not None else None,
        }

    # ------------------------------------------------------------------------------------------------------------------ reads

    async def history(
        self, conn: AsyncConnection, scope_id: str, *, lock: bool = False
    ) -> list[bm.Record]:
        sql = (
            f"SELECT {_STATE_COLUMNS} FROM {self.state_table} WHERE scope_id = :i ORDER BY version"
        )
        rows = (
            await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"i": scope_id})
        ).all()
        return [self._record(r) for r in rows]

    async def scope_id_of(self, conn: AsyncConnection, scope: bm.Scope) -> str | None:
        """The id already used for this exact scope, by a state or an open proposal."""
        p = self._scope_params(scope)
        where = (
            "method = :method AND engine_id = :engine_id AND engine_version = :engine_version AND country = :country "
            "AND purpose = :purpose AND tenant_id IS NOT DISTINCT FROM :tenant_id"
        )
        for table in (self.state_table, self.proposal_table):
            row = (
                await conn.execute(text(f"SELECT scope_id FROM {table} WHERE {where} LIMIT 1"), p)
            ).first()
            if row is not None:
                return str(row.scope_id)
        return None

    async def for_call(self, conn: AsyncConnection, call: bm.Call) -> list[list[bm.Record]]:
        """Every version of every scope a live call could match (its own tenant's and the all-tenant one), grouped by scope id."""
        if not isinstance(call.method, self.method_enum):
            raise bm.MethodError("METHOD_FAMILY_MISMATCH", str(call.method))
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_STATE_COLUMNS} FROM {self.state_table} "
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
            grouped.setdefault(r.scope_id, []).append(self._record(r))
        return list(grouped.values())

    async def all_states(self, conn: AsyncConnection, *, limit: int = 2000) -> list[bm.Record]:
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_STATE_COLUMNS} FROM {self.state_table} ORDER BY scope_id, version LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
        return [self._record(r) for r in rows]

    async def proposals(
        self, conn: AsyncConnection, *, open_only: bool, limit: int = 500
    ) -> list[bm.Proposal]:
        where = "WHERE status = 'OPEN' " if open_only else ""
        rows = (
            await conn.execute(
                text(
                    f"SELECT {_PROPOSAL_COLUMNS} FROM {self.proposal_table} {where}ORDER BY proposed_at DESC LIMIT :n"
                ),
                {"n": limit},
            )
        ).all()
        return [self._proposal(r) for r in rows]

    async def proposal(
        self, conn: AsyncConnection, proposal_id: str, *, lock: bool = False
    ) -> bm.Proposal | None:
        try:
            pid = uuid.UUID(proposal_id)
        except ValueError:
            return None
        sql = f"SELECT {_PROPOSAL_COLUMNS} FROM {self.proposal_table} WHERE proposal_id = :p"
        row = (await conn.execute(text(sql + (" FOR UPDATE" if lock else "")), {"p": pid})).first()
        return self._proposal(row) if row is not None else None

    # ------------------------------------------------------------------------------------------------------------------ writes

    async def insert_proposal(self, conn: AsyncConnection, p: bm.Proposal) -> None:
        await conn.execute(
            text(
                f"INSERT INTO {self.proposal_table} ({_PROPOSAL_COLUMNS}) VALUES (:id, :sid, :method, :engine_id, "
                ":engine_version, :country, :purpose, :tenant_id, :fv, :target, :dpia, :ev, :r, :e, :by, :at, 'OPEN', NULL, NULL, NULL, :xp)"
            ),
            {
                "id": uuid.UUID(p.proposal_id),
                "sid": p.scope_id,
                **self._scope_params(p.scope),
                "fv": p.from_version,
                "target": p.target.value,
                "dpia": p.dpia_ref,
                "ev": p.evaluation_ref,
                "r": p.reason,
                "e": p.evidence_ref,
                "by": p.proposed_by,
                "at": p.proposed_at,
                "xp": p.exposure_percent,
            },
        )

    async def decide_proposal(self, conn: AsyncConnection, p: bm.Proposal) -> None:
        await conn.execute(
            text(
                f"UPDATE {self.proposal_table} SET status = :s, decided_by = :by, decided_at = :at, decision_reason = :r "
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

    async def insert_state(self, conn: AsyncConnection, r: bm.Record) -> None:
        await conn.execute(
            text(
                f"INSERT INTO {self.state_table} ({_STATE_COLUMNS}) VALUES (:sid, :v, :method, :engine_id, :engine_version, "
                ":country, :purpose, :tenant_id, :state, :dpia, :ev, :r, :e, :by, :pby, :pid, :ea, :at, :xp, :dh)"
            ),
            {
                "sid": r.scope_id,
                "v": r.version,
                **self._scope_params(r.scope),
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
                "xp": r.exposure_percent,
                "dh": r.decision_hash,
            },
        )


__all__ = ["MethodTables"]
