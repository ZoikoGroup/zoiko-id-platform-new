"""Mandate use cases that depend on authority (ZID-MANDATE-001 §9-10; MAND-06).

A mandate activates (or resumes from PAUSED) only if its source AuthorityGrant still resolves to a
valid chain at that moment; the source is read through the authority module's port.

A mandate is created only by the grant's grantee and only inside the chain's effective authority:
actions, resource scopes, purpose, tools, jurisdictions, value/count/data limits, the validity
window and sub-agent delegation may narrow, never widen (MANDATE §9-10, §17; CR-010). The
represented principal is the grant's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.authority.ports import Authority, ValueLimit, Window, load_grant, resolve
from zid_platform.modules.mandate.domain.ledger import Budget, LedgerError, MandateStatus
from zid_platform.modules.mandate.domain.subagent import MandateTerms
from zid_platform.modules.mandate.infrastructure.ledger_store import (
    MandateRecord,
    check_writer,
    create_mandate,
)
from zid_platform.modules.mandate.infrastructure.mandate_store import load_terms, set_mandate_status
from zid_platform.modules.principal.ports import load_principal


async def activate_mandate(
    conn: AsyncConnection, *, tenant_id: str, mandate_id: str, risk_level: int, now: datetime
) -> MandateStatus:
    _, _, grant_id = await load_terms(conn, mandate_id)
    res = await resolve(
        conn, tenant_id=tenant_id, grant_id=grant_id, risk_level=risk_level, now=now
    )
    if not res.valid:
        raise LedgerError("MANDATE_SOURCE_INVALID", ", ".join(res.codes))
    return await set_mandate_status(conn, mandate_id, MandateStatus.ACTIVE, source_valid=True)


@dataclass(frozen=True, slots=True)
class MandateRequest:
    """What the grantee asks for; everything else (represented principal, creator) is derived."""

    authority_grant_id: str
    executor_principal_id: str
    purpose_id: str
    actions: frozenset[str]
    resource_scopes: frozenset[str]
    tools: frozenset[str]
    jurisdictions: frozenset[str]
    budget: Budget
    valid_from: datetime
    valid_to: datetime
    delegation_allowed: bool
    max_delegation_depth: int


def _widenings(effective: Authority, req: MandateRequest) -> list[str]:
    b = req.budget
    child = Authority(
        actions=req.actions,
        resources=req.resource_scopes,
        purposes=frozenset({req.purpose_id}),
        data=effective.data,  # a mandate carries no data-category scope of its own
        jurisdictions=req.jurisdictions,
        tools=req.tools,
        value=ValueLimit(b.value_limit, b.currency or "") if b.value_limit is not None else None,
        count=b.count_limit,
        data_bytes=b.data_limit_bytes,
        min_assurance=effective.min_assurance,
        window=Window(req.valid_from, req.valid_to),
    )
    out: list[str] = [d.value for d in effective.widenings(child)]
    per_tx, cap = b.per_transaction_value_limit, effective.value
    if (
        per_tx is not None
        and cap is not None
        and per_tx > 0
        and (b.currency != cap.currency or per_tx > cap.amount)
        and "value" not in out
    ):
        out.append("value")
    return out


async def create_from_grant(
    conn: AsyncConnection,
    req: MandateRequest,
    *,
    tenant_id: str,
    creator_principal_id: str,
    cell_id: uuid.UUID,
    writer_epoch: int,
    risk_level: int,
    now: datetime,
    mandate_id: str | None = None,
) -> str:
    """Attenuate the caller's grant into a PENDING_APPROVAL mandate. Returns the mandate id."""
    grant = await load_grant(conn, req.authority_grant_id)
    if grant is None:
        raise LedgerError("AUTHORITY_GRANT_NOT_FOUND")
    if grant.grantee_principal_id != creator_principal_id:
        raise LedgerError(
            "GRANTOR_AUTHORITY_MISSING", "only the grant's grantee can mandate its authority"
        )
    res = await resolve(
        conn,
        tenant_id=tenant_id,
        grant_id=req.authority_grant_id,
        risk_level=risk_level,
        now=now,
    )
    if not res.valid or res.effective is None:
        raise LedgerError("MANDATE_SOURCE_INVALID", ", ".join(res.codes))
    if req.delegation_allowed and not grant.delegation_allowed:
        raise LedgerError("GRANT_DELEGATION_NOT_ALLOWED", "the source grant cannot be delegated")
    if req.max_delegation_depth > grant.max_delegation_depth:
        raise LedgerError("GRANT_DEPTH_EXCEEDED", "deeper than the source grant allows")
    widened = _widenings(res.effective, req)
    if widened:
        raise LedgerError(
            "GRANT_SCOPE_WIDENING", "mandate exceeds its source grant on: " + ", ".join(widened)
        )
    try:
        await load_principal(conn, req.executor_principal_id)
    except ValueError as e:
        raise LedgerError("PRINCIPAL_NOT_FOUND", "the executor is not a principal") from e
    await check_writer(conn, uuid.UUID(tenant_id), cell_id, writer_epoch)
    mid = mandate_id or str(uuid.uuid4())
    await create_mandate(
        conn,
        MandateRecord(
            mandate_id=uuid.UUID(mid),
            tenant_id=uuid.UUID(tenant_id),
            authority_grant_id=uuid.UUID(req.authority_grant_id),
            represented_principal_id=uuid.UUID(grant.represented_principal_id),
            executor_principal_id=uuid.UUID(req.executor_principal_id),
            purpose_id=uuid.UUID(req.purpose_id),
            resource_scope_id=uuid.uuid4(),
            allowed_actions=tuple(sorted(req.actions)),
            budget=req.budget,
            home_cell_id=cell_id,
            status=MandateStatus.PENDING_APPROVAL,
            valid_from=req.valid_from,
            valid_to=req.valid_to,
            created_by_principal_id=uuid.UUID(creator_principal_id),
        ),
        terms=MandateTerms(
            mandate_id=mid,
            executor_principal_id=req.executor_principal_id,
            represented_principal_id=grant.represented_principal_id,
            purpose_id=req.purpose_id,
            actions=req.actions,
            resource_scope=req.resource_scopes,
            tools=req.tools,
            jurisdictions=req.jurisdictions,
            valid_from=req.valid_from,
            valid_to=req.valid_to,
            delegation_allowed=req.delegation_allowed,
            max_delegation_depth=req.max_delegation_depth,
            status=MandateStatus.PENDING_APPROVAL,
        ),
    )
    return mid


__all__ = ["MandateRequest", "MandateStatus", "activate_mandate", "create_from_grant"]
