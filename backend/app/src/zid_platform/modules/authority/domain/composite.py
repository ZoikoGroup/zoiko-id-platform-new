"""Composite authority and transaction-bound approvals (ZID-AUTHZ-001 §9; ZID-MANDATE-001 §16).

Composite authority is explicit policy, never an accidental union of partial grants. An approval
binds to one transaction commitment; it is not a reusable entitlement.

- Distinct *principals* count, not sessions: two approvals by one person never make two-person control.
- The requester can't approve where self-approval is forbidden.
- Branches of ALL_OF / ROLE_PAIR need different principals, and one upstream grant can't satisfy
  two branches unless the policy explicitly allows it.
- Any change to the committed transaction (value, resource, tool, purpose...) makes every
  decision stale; expiry is checked at commit recheck.
- Only approvers whose own authority resolved valid count; a DENY from a qualified approver denies.
Pure domain, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class CompositeMode(StrEnum):
    SINGLE = "SINGLE"
    ALL_OF = "ALL_OF"  # every named predicate satisfied, each by a different principal
    K_OF_N = "K_OF_N"  # at least k distinct qualified approvers
    ROLE_PAIR = "ROLE_PAIR"  # separation of duties: the roles held by distinct principals
    EXTERNAL_CONFIRMATION = "EXTERNAL_CONFIRMATION"  # a current external trust assertion


@dataclass(frozen=True, slots=True)
class ApprovalPolicy:
    mode: CompositeMode
    # Qualifying role/predicate per branch. SINGLE/K_OF_N: any approver holding one of these roles
    # qualifies. ALL_OF/ROLE_PAIR: one branch per role, each satisfied by a different principal.
    roles: tuple[str, ...]
    k: int = 1
    self_approval_forbidden: bool = True
    allow_shared_grant: bool = False

    def __post_init__(self) -> None:
        if not self.roles:
            raise ValueError("an approval policy needs at least one qualifying role")
        if self.mode is CompositeMode.K_OF_N and not 1 <= self.k:
            raise ValueError("K_OF_N needs k >= 1")
        if self.mode is CompositeMode.ROLE_PAIR and len(self.roles) != 2:
            raise ValueError("ROLE_PAIR names exactly two roles")


class Verdict(StrEnum):
    APPROVE = "APPROVE"
    DENY = "DENY"


@dataclass(frozen=True, slots=True)
class ApprovalDecision:
    approver_principal_id: str
    approver_roles: frozenset[str]
    authority_grant_id: str  # the grant the approver's authority resolved through
    authority_valid: bool  # resolved valid at decision time (and re-checked at commit)
    verdict: Verdict
    transaction_context_hash: str
    decided_at: datetime
    session_id: str = ""  # recorded for evidence; never used to count approvers


@dataclass(frozen=True, slots=True)
class ApprovalRequest:
    approval_request_id: str
    requester_principal_id: str
    transaction_context_hash: str
    policy: ApprovalPolicy
    expires_at: datetime
    external_confirmation_valid: bool = False


class ApprovalState(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPIRED = "EXPIRED"
    STALE = "STALE"  # transaction changed: APPROVAL_STALE


@dataclass(frozen=True, slots=True)
class ApprovalOutcome:
    state: ApprovalState
    codes: tuple[str, ...] = ()
    counted: tuple[str, ...] = field(default_factory=tuple)  # principals whose approval counted


def _qualified(d: ApprovalDecision, req: ApprovalRequest) -> bool:
    if not d.authority_valid:
        return False
    if req.policy.self_approval_forbidden and d.approver_principal_id == req.requester_principal_id:
        return False
    return bool(d.approver_roles & set(req.policy.roles))


def _assign_branches(
    roles: Sequence[str], approvals: list[ApprovalDecision], shared_grant_ok: bool
) -> list[str] | None:
    """Give each branch (role) its own principal; backtracking is fine for the handful of branches."""

    def go(
        i: int, used_people: frozenset[str], used_grants: frozenset[str], picked: list[str]
    ) -> list[str] | None:
        if i == len(roles):
            return picked
        for d in approvals:
            if roles[i] not in d.approver_roles or d.approver_principal_id in used_people:
                continue
            if not shared_grant_ok and d.authority_grant_id in used_grants:
                continue
            found = go(
                i + 1,
                used_people | {d.approver_principal_id},
                used_grants | {d.authority_grant_id},
                [*picked, d.approver_principal_id],
            )
            if found is not None:
                return found
        return None

    return go(0, frozenset(), frozenset(), [])


def evaluate_approval(
    req: ApprovalRequest,
    decisions: Iterable[ApprovalDecision],
    *,
    current_transaction_context_hash: str,
    now: datetime,
) -> ApprovalOutcome:
    """Is the approval satisfied for *this* transaction right now? Evaluated again at commit."""
    if now >= req.expires_at:
        return ApprovalOutcome(ApprovalState.EXPIRED, ("APPROVAL_STALE",))
    if current_transaction_context_hash != req.transaction_context_hash:
        return ApprovalOutcome(ApprovalState.STALE, ("APPROVAL_STALE",))
    bound = [d for d in decisions if d.transaction_context_hash == req.transaction_context_hash]
    qualified = [d for d in bound if _qualified(d, req)]
    if any(d.verdict is Verdict.DENY for d in qualified):
        return ApprovalOutcome(ApprovalState.DENIED, ("APPROVAL_DENIED",))
    approvals: list[ApprovalDecision] = []
    seen: set[str] = set()
    for d in sorted(
        qualified, key=lambda x: x.decided_at
    ):  # one vote per principal, first one wins
        if d.verdict is Verdict.APPROVE and d.approver_principal_id not in seen:
            seen.add(d.approver_principal_id)
            approvals.append(d)

    policy = req.policy
    if policy.mode is CompositeMode.EXTERNAL_CONFIRMATION:
        ok = req.external_confirmation_valid
        return ApprovalOutcome(
            ApprovalState.APPROVED if ok else ApprovalState.PENDING,
            () if ok else ("APPROVAL_REQUIRED",),
        )
    if policy.mode in (CompositeMode.SINGLE, CompositeMode.K_OF_N):
        need = 1 if policy.mode is CompositeMode.SINGLE else policy.k
        if not policy.allow_shared_grant:  # one upstream grant counts once
            by_grant: dict[str, ApprovalDecision] = {}
            for d in approvals:
                by_grant.setdefault(d.authority_grant_id, d)
            approvals = list(by_grant.values())
        if len(approvals) >= need:
            return ApprovalOutcome(
                ApprovalState.APPROVED, (), tuple(d.approver_principal_id for d in approvals[:need])
            )
        return ApprovalOutcome(ApprovalState.PENDING, ("APPROVAL_REQUIRED",))
    picked = _assign_branches(policy.roles, approvals, policy.allow_shared_grant)
    if picked is not None:
        return ApprovalOutcome(ApprovalState.APPROVED, (), tuple(picked))
    return ApprovalOutcome(ApprovalState.PENDING, ("COMPOSITE_AUTHORITY_INCOMPLETE",))
