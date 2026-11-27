"""Mandate lifecycle, terms and sub-mandates in PostgreSQL (ZID-MANDATE-001 §9-10, §17-18; MAND-06/07/18-20).

- Lifecycle changes go through the domain state machine; terminal states are final in the database too.
- A sub-mandate is created in the same transaction that reserves its allocation on the parent ledger
  (ALLOCATED_SUB_BUDGET), so children together can never spend more than the parent (MAND-19).
- `pause_or_revoke_for_grants` is the mandate side of a revocation cascade (MAND-20, AT-07): every
  live mandate built on an invalidated grant, and every descendant sub-mandate, stops.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import Row, text
from sqlalchemy.ext.asyncio import AsyncConnection
from zid_outbox import emit

from zid_platform.modules.mandate.domain.ledger import (
    TERMINAL_MANDATE_STATES,
    Amounts,
    Budget,
    LedgerError,
    MandateStatus,
    mandate_transition,
)
from zid_platform.modules.mandate.domain.subagent import (
    BudgetMode,
    CascadeEffect,
    MandateTerms,
    cascade,
    child_budget,
    validate_sub_mandate,
)
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    MandateRecord,
    create_mandate,
    reserve_capacity,
)

_TERMS = """mandate_id, executor_principal_id, represented_principal_id, purpose_id, allowed_actions, resource_scopes,
            tools, jurisdictions, valid_from, valid_to, delegation_allowed, max_delegation_depth, status,
            parent_mandate_id, budget_mode, authority_grant_id, currency, value_limit, count_limit, data_limit_bytes,
            per_transaction_value_limit"""


def _terms(row: Row[Any]) -> MandateTerms:
    m = row._mapping
    return MandateTerms(
        mandate_id=str(m["mandate_id"]),
        executor_principal_id=str(m["executor_principal_id"]),
        represented_principal_id=str(m["represented_principal_id"]),
        purpose_id=str(m["purpose_id"]),
        actions=frozenset(m["allowed_actions"]),
        resource_scope=frozenset(m["resource_scopes"]),
        tools=frozenset(m["tools"]),
        jurisdictions=frozenset(m["jurisdictions"]),
        valid_from=m["valid_from"],
        valid_to=m["valid_to"],
        delegation_allowed=m["delegation_allowed"],
        max_delegation_depth=m["max_delegation_depth"],
        status=MandateStatus(m["status"]),
        parent_mandate_id=str(m["parent_mandate_id"]) if m["parent_mandate_id"] else None,
        budget_mode=BudgetMode(m["budget_mode"]) if m["budget_mode"] else None,
    )


async def load_terms(conn: AsyncConnection, mandate_id: str) -> tuple[MandateTerms, Budget, str]:
    """Terms, budget and source grant id of one mandate."""
    row = (
        await conn.execute(
            text(f"SELECT {_TERMS} FROM agent.mandate WHERE mandate_id = :id"),
            {"id": uuid.UUID(mandate_id)},
        )
    ).first()
    if row is None:
        raise LedgerError("MANDATE_NOT_FOUND")
    return _terms(row), _budget(row), str(row._mapping["authority_grant_id"])


async def creator_of(conn: AsyncConnection, mandate_id: str) -> str:
    """The principal that created the mandate from their grant."""
    row = (
        await conn.execute(
            text("SELECT created_by_principal_id FROM agent.mandate WHERE mandate_id = :id"),
            {"id": uuid.UUID(mandate_id)},
        )
    ).first()
    if row is None:
        raise LedgerError("MANDATE_NOT_FOUND")
    return str(row.created_by_principal_id)


def _budget(row: Row[Any]) -> Budget:
    m = row._mapping
    return Budget(
        currency=m["currency"].strip() if m["currency"] else None,
        value_limit=m["value_limit"],
        count_limit=m["count_limit"],
        data_limit_bytes=m["data_limit_bytes"],
        per_transaction_value_limit=m["per_transaction_value_limit"],
    )


@dataclass(frozen=True, slots=True)
class MandateListing:
    terms: MandateTerms
    budget: Budget
    authority_grant_id: str
    created_at: datetime


async def list_mandates(
    conn: AsyncConnection,
    *,
    status: MandateStatus | None = None,
    executor_principal_id: str | None = None,
    mandate_id: str | None = None,
    involving_principal_id: str | None = None,
    limit: int = 100,
) -> list[MandateListing]:
    """The tenant's mandates, newest first (RLS scopes the read to the caller's tenant).

    `involving_principal_id` keeps only the mandates that person executes or created."""
    where: list[str] = ["TRUE"]
    params: dict[str, Any] = {"lim": limit}
    if involving_principal_id is not None:
        where.append("(executor_principal_id = :who OR created_by_principal_id = :who)")
        params["who"] = uuid.UUID(involving_principal_id)
    if status is not None:
        where.append("status = CAST(:st AS agent.mandate_status)")
        params["st"] = status.value
    if executor_principal_id is not None:
        where.append("executor_principal_id = :exe")
        params["exe"] = uuid.UUID(executor_principal_id)
    if mandate_id is not None:
        where.append("mandate_id = :mid")
        params["mid"] = uuid.UUID(mandate_id)
    rows = (
        await conn.execute(
            text(
                f"SELECT {_TERMS}, created_at FROM agent.mandate WHERE {' AND '.join(where)} "
                "ORDER BY created_at DESC, mandate_id DESC LIMIT :lim"
            ),
            params,
        )
    ).all()
    return [
        MandateListing(_terms(r), _budget(r), str(r.authority_grant_id), r.created_at) for r in rows
    ]


async def set_mandate_status(
    conn: AsyncConnection, mandate_id: str, target: MandateStatus, *, source_valid: bool = True
) -> MandateStatus:
    terms, _, _ = await load_terms(conn, mandate_id)
    mandate_transition(terms.status, target, source_valid=source_valid)
    result = await conn.execute(
        text(
            "UPDATE agent.mandate SET status = CAST(:s AS agent.mandate_status), row_version = row_version + 1 "
            "WHERE mandate_id = :id AND status = CAST(:old AS agent.mandate_status) "
            "RETURNING tenant_id, row_version"
        ),
        {"s": target.value, "old": terms.status.value, "id": uuid.UUID(mandate_id)},
    )
    row = result.first()
    if row is None:
        raise LedgerError("CONCURRENT_MODIFICATION")
    await emit(
        conn,
        name=MANDATE_EVENTS[target],
        tenant_id=str(row.tenant_id),
        aggregate_type="mandate",
        aggregate_id=mandate_id,
        aggregate_version=row.row_version,
        data={
            "mandate_id": mandate_id,
            "status": target.value,
            "previous_status": terms.status.value,
        },
    )
    return target


MANDATE_EVENTS = {
    MandateStatus.ACTIVE: "mandate.activated",
    MandateStatus.PAUSED: "mandate.paused",
    MandateStatus.REVOKED: "mandate.revoked",
    MandateStatus.EXHAUSTED: "mandate.exhausted",
    MandateStatus.COMPLETED: "mandate.completed",
    MandateStatus.EXPIRED: "mandate.revoked",
    MandateStatus.PENDING_APPROVAL: "mandate.created",
}


async def create_sub_mandate(
    conn: AsyncConnection,
    *,
    parent_id: str,
    child: MandateRecord,
    child_terms: MandateTerms,
    allocation: Amounts,
    cell_id: uuid.UUID,
    writer_epoch: int,
    now: datetime,
) -> MandateTerms:
    """Attenuate, reserve the allocation on the parent ledger, then insert the child: one transaction."""
    parent, parent_budget, _ = await load_terms(conn, parent_id)
    problems = validate_sub_mandate(parent, child_terms)
    if problems:
        raise LedgerError(problems[0], ", ".join(problems))
    reservation_id = None
    if child_terms.budget_mode is BudgetMode.ALLOCATED_SUB_BUDGET:
        lease = child_terms.valid_to - now + timedelta(minutes=1)
        res = await reserve_capacity(
            conn,
            tenant_id=child.tenant_id,
            mandate_id=uuid.UUID(parent_id),
            idempotency_key=f"allocation:{child.mandate_id}",
            transaction_context=f"sub-mandate:{child.mandate_id}",
            amounts=allocation,
            currency=parent_budget.currency,
            cell_id=cell_id,
            writer_epoch=writer_epoch,
            now=now,
            lease=lease,
        )
        reservation_id = res.reservation_id
        child = MandateRecord(
            **{
                **{f: getattr(child, f) for f in MandateRecord.__dataclass_fields__},
                "budget": child_budget(allocation, parent_budget),
            }
        )
    await create_mandate(conn, child, terms=child_terms, allocation_reservation_id=reservation_id)
    return child_terms


async def _all_terms(conn: AsyncConnection) -> dict[str, MandateTerms]:
    rows = (await conn.execute(text(f"SELECT {_TERMS} FROM agent.mandate"))).all()
    return {str(r.mandate_id): _terms(r) for r in rows}


async def pause_or_revoke_for_grants(
    conn: AsyncConnection, grant_ids: Iterable[str], effect: CascadeEffect
) -> dict[str, MandateStatus]:
    """Stop every live mandate built on these grants, and all their sub-mandates (AT-07, MAND-20)."""
    ids = [uuid.UUID(g) for g in grant_ids]
    if not ids:
        return {}
    rows = (
        await conn.execute(
            text("SELECT mandate_id FROM agent.mandate WHERE authority_grant_id = ANY(:g)"),
            {"g": ids},
        )
    ).all()
    everything = await _all_terms(conn)
    target = MandateStatus.PAUSED if effect is CascadeEffect.PAUSE else MandateStatus.REVOKED
    changes: dict[str, MandateStatus] = {}
    for r in rows:
        mid = str(r.mandate_id)
        if everything[mid].status not in TERMINAL_MANDATE_STATES:
            changes[mid] = target
        changes |= cascade(mid, effect, everything)
    for mid, status in changes.items():
        current = everything[mid].status
        if current is status or current in TERMINAL_MANDATE_STATES:
            continue
        if status is MandateStatus.PAUSED and current is not MandateStatus.ACTIVE:
            continue  # only ACTIVE mandates pause; PENDING ones simply never activate
        await set_mandate_status(conn, mid, status)
    return changes


@dataclass(frozen=True, slots=True)
class MandateMetadata:
    """The metadata projection an access review may read (CR-091): ids, state, dates and scope sets; no budget, no amounts."""

    mandate_id: str
    status: str
    valid_to: datetime
    executor_principal_id: str
    authority_grant_id: str
    allowed_actions: frozenset[str]
    tools: frozenset[str]


async def mandate_metadata_page(
    conn: AsyncConnection, *, home_cell: str, statuses: Iterable[str], limit: int
) -> list[MandateMetadata]:
    """Live mandates homed in this cell, in a stable order (RLS scopes the read to the caller's tenant). A mandate whose home is
    another cell is not read from here: raw metadata stays in its region."""
    rows = (
        await conn.execute(
            text(
                "SELECT mandate_id, status, valid_to, executor_principal_id, authority_grant_id, allowed_actions, tools "
                "FROM agent.mandate WHERE home_cell_id = :cell AND status::text = ANY(:st) "
                "ORDER BY mandate_id LIMIT :lim"
            ),
            {"cell": uuid.UUID(home_cell), "st": list(statuses), "lim": limit},
        )
    ).all()
    return [
        MandateMetadata(
            str(r.mandate_id),
            str(r.status),
            r.valid_to,
            str(r.executor_principal_id),
            str(r.authority_grant_id),
            frozenset(r.allowed_actions or ()),
            frozenset(r.tools or ()),
        )
        for r in rows
    ]


# ------------------------------------------------------------------------------- the exact text of a mandate, and who confirmed it (CR-169; mandate_0003)

_CANONICAL = (
    "mandate_id, tenant_id, authority_grant_id, represented_principal_id, executor_principal_id, purpose_id, resource_scope_id, allowed_actions, resource_scopes, tools, jurisdictions, currency, "
    "value_limit, per_transaction_value_limit, count_limit, data_limit_bytes, valid_from, valid_to, delegation_allowed, max_delegation_depth, budget_mode, parent_mandate_id, created_by_principal_id"
)


async def canonical_row(conn: AsyncConnection, mandate_id: str) -> dict[str, Any] | None:
    row = (
        await conn.execute(
            text(f"SELECT {_CANONICAL} FROM agent.mandate WHERE mandate_id = :id"),
            {"id": uuid.UUID(mandate_id)},
        )
    ).first()
    return dict(row._mapping) if row else None


async def add_signature(
    conn: AsyncConnection, tenant_id: str, *, mandate_id: str, role: str, canonical_hash: str, signer: str, assurance_level: int, auth_time: Any, signature: str, key_id: str | None, signed_at: Any
) -> bool:  # fmt: skip
    """False when this person already confirmed this hash in this role (a repeat is not a second confirmation)."""
    res = await conn.execute(
        text(
            "INSERT INTO agent.mandate_signature (signature_id, tenant_id, mandate_id, role, canonical_hash, signer_principal_id, assurance_level, auth_time, signature, key_id, signed_at) "
            "VALUES (:s, :t, :m, :r, :h, :p, :a, :at, :sig, :k, :n) ON CONFLICT DO NOTHING"
        ),
        {
            "s": uuid.uuid4(),
            "t": uuid.UUID(tenant_id),
            "m": uuid.UUID(mandate_id),
            "r": role,
            "h": canonical_hash,
            "p": uuid.UUID(signer),
            "a": assurance_level,
            "at": auth_time,
            "sig": signature,
            "k": key_id,
            "n": signed_at,
        },
    )
    return bool(res.rowcount)


async def signatures_of(conn: AsyncConnection, mandate_id: str) -> list[dict[str, Any]]:
    rows = await conn.execute(
        text(
            "SELECT signature_id, role, canonical_hash, signer_principal_id, assurance_level, auth_time, signature, key_id, signed_at FROM agent.mandate_signature WHERE mandate_id = :m ORDER BY signed_at, role"
        ),
        {"m": uuid.UUID(mandate_id)},
    )
    return [dict(x._mapping) for x in rows]
