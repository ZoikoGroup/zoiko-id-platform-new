"""Sub-agent mandates and parent-budget preservation (ZID-MANDATE-001 §17-18; MAND-18..20).

A child mandate is one more attenuation step: depth decrements, scopes are subsets, purpose stays
the same or narrower, the child can't outlive the parent, and the parent's budget is never
multiplied. Two budget models (§17.1):

- ALLOCATED_SUB_BUDGET: the parent ledger holds a reservation for the child's allocation; the child
  spends only that allocation; when the child ends, the parent reservation commits what the child
  actually committed and releases the rest.
- SHARED_PARENT_LEDGER: the child has no budget of its own and reserves straight against the parent
  ledger (short delegated tool chains).

Action/resource/tool scopes on mandates are normalized IDs (ZID-DATA-001 §8.2), so containment is
a plain subset check here; grant-level pattern algebra lives in the authority module. Pure domain.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from zid_platform.modules.mandate.domain.ledger import (
    TERMINAL_MANDATE_STATES,
    Amounts,
    Balance,
    Budget,
    LedgerError,
    MandateStatus,
    Reservation,
    ReservationStatus,
)


class BudgetMode(StrEnum):
    ALLOCATED_SUB_BUDGET = "ALLOCATED_SUB_BUDGET"
    SHARED_PARENT_LEDGER = "SHARED_PARENT_LEDGER"


@dataclass(frozen=True, slots=True)
class MandateTerms:
    mandate_id: str
    executor_principal_id: str
    represented_principal_id: str
    purpose_id: str
    actions: frozenset[str]
    resource_scope: frozenset[str]
    tools: frozenset[str]
    jurisdictions: frozenset[str]
    valid_from: datetime
    valid_to: datetime
    delegation_allowed: bool
    max_delegation_depth: int
    status: MandateStatus
    parent_mandate_id: str | None = None
    budget_mode: BudgetMode | None = None  # set on children only
    executor_is_registered_agent: bool = True  # AI_AGENT/workload principal with sponsor (Doc 09)


def validate_sub_mandate(parent: MandateTerms, child: MandateTerms) -> list[str]:
    """Delegation gate for a sub-agent mandate. Empty list = may be created."""
    problems: list[str] = []
    if parent.status is not MandateStatus.ACTIVE:
        problems.append("MANDATE_NOT_ACTIVE")
    if not parent.delegation_allowed:
        problems.append("GRANT_DELEGATION_NOT_ALLOWED")
    if child.max_delegation_depth >= parent.max_delegation_depth or child.max_delegation_depth < 0:
        problems.append("GRANT_DEPTH_EXCEEDED")
    if not child.executor_is_registered_agent:
        problems.append(
            "MANDATE_EXECUTOR_NOT_REGISTERED"
        )  # no delegation by agent-to-agent messaging alone
    if child.represented_principal_id != parent.represented_principal_id:
        problems.append("MANDATE_SCOPE_EXCEEDED")  # the represented party never changes silently
    if child.purpose_id != parent.purpose_id:
        problems.append("PURPOSE_NOT_ALLOWED")
    for name in ("actions", "resource_scope", "tools", "jurisdictions"):
        if not getattr(child, name) <= getattr(parent, name):
            problems.append("MANDATE_SCOPE_EXCEEDED")
    if child.valid_from < parent.valid_from or child.valid_to > parent.valid_to:
        problems.append("MANDATE_SCOPE_EXCEEDED")
    if child.parent_mandate_id != parent.mandate_id or child.budget_mode is None:
        problems.append("MANDATE_SOURCE_INVALID")
    return list(dict.fromkeys(problems))


def child_budget(allocation: Amounts, parent: Budget) -> Budget:
    """An allocated child's own budget equals its allocation, in the parent's currency."""
    return Budget(
        currency=parent.currency,
        value_limit=allocation.value if parent.value_limit is not None else None,
        count_limit=allocation.count if parent.count_limit is not None else None,
        data_limit_bytes=allocation.data_bytes if parent.data_limit_bytes is not None else None,
        per_transaction_value_limit=parent.per_transaction_value_limit,
    )


def settle_allocation(
    parent_balance: Balance, allocation: Reservation, child_committed: Amounts
) -> tuple[Balance, Reservation]:
    """Child finished: commit what it spent out of the allocation, release the rest (§17.1)."""
    if allocation.status is not ReservationStatus.RESERVED:
        raise LedgerError("RESERVATION_ALREADY_TERMINAL", str(allocation.status))
    unused = allocation.amounts - child_committed
    if not child_committed.non_negative() or not unused.non_negative():
        raise LedgerError("LEDGER_CORRUPTION", "child committed more than its allocation")
    reserved = parent_balance.reserved - allocation.amounts
    if not reserved.non_negative():
        raise LedgerError("LEDGER_CORRUPTION", "reserved would go negative")
    new = Balance(
        committed=parent_balance.committed + child_committed,
        reserved=reserved,
        ledger_version=parent_balance.ledger_version + 1,
    )
    return new, replace(allocation, status=ReservationStatus.COMMITTED, amounts=child_committed)


class CascadeEffect(StrEnum):
    PAUSE = "PAUSE"
    REVOKE = "REVOKE"


def cascade(
    mandate_id: str, effect: CascadeEffect, mandates: Mapping[str, MandateTerms]
) -> dict[str, MandateStatus]:
    """Parent pause/revoke reaches every live descendant sub-agent mandate (MAND-20)."""
    children: dict[str, list[str]] = {}
    for m in mandates.values():
        if m.parent_mandate_id is not None:
            children.setdefault(m.parent_mandate_id, []).append(m.mandate_id)
    target = MandateStatus.PAUSED if effect is CascadeEffect.PAUSE else MandateStatus.REVOKED
    out: dict[str, MandateStatus] = {}
    stack = list(children.get(mandate_id, []))
    while stack:
        mid = stack.pop()
        if mid in out:
            continue
        m = mandates[mid]
        if m.status not in TERMINAL_MANDATE_STATES:
            out[mid] = target
        stack.extend(children.get(mid, []))
    return out
