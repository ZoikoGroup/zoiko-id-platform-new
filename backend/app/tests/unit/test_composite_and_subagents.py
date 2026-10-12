"""Composite authority, approvals and sub-agent mandates (AUTHZ-15/16, MAND-16..20)."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from zid_platform.modules.authority.domain.composite import (
    ApprovalDecision,
    ApprovalPolicy,
    ApprovalRequest,
    ApprovalState,
    CompositeMode,
    Verdict,
    evaluate_approval,
)
from zid_platform.modules.mandate.domain.ledger import (
    Amounts,
    Balance,
    Budget,
    LedgerError,
    MandateStatus,
    ReserveRequest,
    invariant_holds,
    reserve,
)
from zid_platform.modules.mandate.domain.subagent import (
    BudgetMode,
    CascadeEffect,
    MandateTerms,
    cascade,
    child_budget,
    settle_allocation,
    validate_sub_mandate,
)

NOW = datetime(2026, 10, 12, tzinfo=UTC)
TX = "sha256:tx-1"


def decision(who: str, roles: set[str], grant: str | None = None, **kw: object) -> ApprovalDecision:
    base: dict[str, object] = {
        "approver_principal_id": who,
        "approver_roles": frozenset(roles),
        "authority_grant_id": grant or f"g-{who}",
        "authority_valid": True,
        "verdict": Verdict.APPROVE,
        "transaction_context_hash": TX,
        "decided_at": NOW,
        "session_id": "s1",
    } | kw
    return ApprovalDecision(**base)  # type: ignore[arg-type]


def request(
    mode: CompositeMode, roles: tuple[str, ...], k: int = 1, **kw: object
) -> ApprovalRequest:
    return ApprovalRequest(
        "apr_1",
        "prn_requester",
        TX,
        ApprovalPolicy(mode, roles, k, **kw),  # type: ignore[arg-type]
        expires_at=NOW + timedelta(hours=1),
    )


def outcome(
    req: ApprovalRequest, *ds: ApprovalDecision, tx: str = TX, now: datetime = NOW
) -> ApprovalState:
    return evaluate_approval(req, ds, current_transaction_context_hash=tx, now=now).state


def test_two_of_n_needs_two_distinct_principals() -> None:
    """AUTHZ-15 / MAND-17: the same principal twice (two sessions) never makes two-person control."""
    req = request(CompositeMode.K_OF_N, ("finance_approver",), k=2)
    a = decision("prn_a", {"finance_approver"})
    a_again = decision(
        "prn_a", {"finance_approver"}, session_id="s2", decided_at=NOW + timedelta(minutes=1)
    )
    assert outcome(req, a, a_again) is ApprovalState.PENDING
    assert outcome(req, a, decision("prn_b", {"finance_approver"})) is ApprovalState.APPROVED


def test_one_person_counts_once_even_through_two_grants() -> None:
    """Two approvals by one principal via different grants are still one approver."""
    req = request(CompositeMode.K_OF_N, ("finance_approver",), k=2)
    a1 = decision("prn_a", {"finance_approver"}, grant="g_1")
    a2 = decision("prn_a", {"finance_approver"}, grant="g_2", decided_at=NOW + timedelta(minutes=1))
    assert outcome(req, a1, a2) is ApprovalState.PENDING


def test_requester_cannot_approve_their_own_request() -> None:
    req = request(CompositeMode.SINGLE, ("manager",))
    assert outcome(req, decision("prn_requester", {"manager"})) is ApprovalState.PENDING
    relaxed = request(CompositeMode.SINGLE, ("manager",), self_approval_forbidden=False)
    assert outcome(relaxed, decision("prn_requester", {"manager"})) is ApprovalState.APPROVED


def test_role_pair_needs_two_people_even_if_one_holds_both_roles() -> None:
    req = request(CompositeMode.ROLE_PAIR, ("officer", "finance_control"))
    both = decision("prn_a", {"officer", "finance_control"})
    assert outcome(req, both) is ApprovalState.PENDING
    assert outcome(req, both, decision("prn_b", {"finance_control"})) is ApprovalState.APPROVED


def test_one_upstream_grant_cannot_satisfy_two_branches() -> None:
    """AUTHZ §9.1: unless the policy explicitly allows it."""
    req = request(CompositeMode.ALL_OF, ("officer", "finance_control"))
    a = decision("prn_a", {"officer"}, grant="g_shared")
    b = decision("prn_b", {"finance_control"}, grant="g_shared")
    assert outcome(req, a, b) is ApprovalState.PENDING
    allowed = request(CompositeMode.ALL_OF, ("officer", "finance_control"), allow_shared_grant=True)
    assert outcome(allowed, a, b) is ApprovalState.APPROVED


def test_changed_transaction_makes_approvals_stale() -> None:
    """AUTHZ-16 / MAND-16: value, resource or tool change invalidates prior approval."""
    req = request(CompositeMode.SINGLE, ("manager",))
    assert (
        outcome(req, decision("prn_a", {"manager"}), tx="sha256:tx-1-with-new-amount")
        is ApprovalState.STALE
    )
    old = decision("prn_a", {"manager"}, transaction_context_hash="sha256:older")
    assert (
        outcome(req, old) is ApprovalState.PENDING
    )  # decisions bound to another transaction never count


def test_expired_approval_fails_commit_recheck() -> None:
    req = request(CompositeMode.SINGLE, ("manager",))
    assert (
        outcome(req, decision("prn_a", {"manager"}), now=NOW + timedelta(hours=2))
        is ApprovalState.EXPIRED
    )


def test_unqualified_or_invalid_approvers_do_not_count_and_qualified_deny_wins() -> None:
    req = request(CompositeMode.SINGLE, ("manager",))
    assert outcome(req, decision("prn_a", {"intern"})) is ApprovalState.PENDING
    assert (
        outcome(req, decision("prn_a", {"manager"}, authority_valid=False)) is ApprovalState.PENDING
    )
    deny = decision("prn_b", {"manager"}, verdict=Verdict.DENY)
    assert outcome(req, decision("prn_a", {"manager"}), deny) is ApprovalState.DENIED


# ---------------------------------------------------------------- sub-agents

PARENT = MandateTerms(
    mandate_id="m_parent",
    executor_principal_id="agent_parent",
    represented_principal_id="org_1",
    purpose_id="procurement",
    actions=frozenset({"purchase.create", "tool.execute"}),
    resource_scope=frozenset({"rs_suppliers"}),
    tools=frozenset({"tool_search", "tool_order"}),
    jurisdictions=frozenset({"GB"}),
    valid_from=NOW,
    valid_to=NOW + timedelta(days=7),
    delegation_allowed=True,
    max_delegation_depth=2,
    status=MandateStatus.ACTIVE,
)
CHILD = replace(
    PARENT,
    mandate_id="m_child",
    executor_principal_id="agent_child",
    actions=frozenset({"tool.execute"}),
    tools=frozenset({"tool_search"}),
    max_delegation_depth=1,
    delegation_allowed=False,
    parent_mandate_id="m_parent",
    budget_mode=BudgetMode.ALLOCATED_SUB_BUDGET,
    valid_to=NOW + timedelta(days=1),
)


def test_valid_sub_mandate() -> None:
    assert validate_sub_mandate(PARENT, CHILD) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        ({"tools": frozenset({"tool_wire_transfer"})}, "MANDATE_SCOPE_EXCEEDED"),
        ({"purpose_id": "marketing"}, "PURPOSE_NOT_ALLOWED"),
        ({"represented_principal_id": "org_2"}, "MANDATE_SCOPE_EXCEEDED"),
        ({"valid_to": NOW + timedelta(days=30)}, "MANDATE_SCOPE_EXCEEDED"),
        ({"max_delegation_depth": 2}, "GRANT_DEPTH_EXCEEDED"),
        ({"executor_is_registered_agent": False}, "MANDATE_EXECUTOR_NOT_REGISTERED"),
    ],
)
def test_sub_mandate_cannot_widen(change: dict[str, object], code: str) -> None:
    assert code in validate_sub_mandate(PARENT, replace(CHILD, **change))  # type: ignore[arg-type]


def test_delegation_stops_when_depth_is_exhausted() -> None:
    """MAND-18."""
    leaf = replace(PARENT, max_delegation_depth=0)
    assert "GRANT_DEPTH_EXCEEDED" in validate_sub_mandate(
        leaf, replace(CHILD, max_delegation_depth=0)
    )


def test_parent_revoke_cascades_to_all_descendants() -> None:
    """MAND-20."""
    grand = replace(CHILD, mandate_id="m_grand", parent_mandate_id="m_child")
    done = replace(CHILD, mandate_id="m_done", status=MandateStatus.COMPLETED)
    ms = {m.mandate_id: m for m in (PARENT, CHILD, grand, done)}
    assert cascade("m_parent", CascadeEffect.REVOKE, ms) == {
        "m_child": MandateStatus.REVOKED,
        "m_grand": MandateStatus.REVOKED,
    }


PARENT_BUDGET = Budget(currency="GBP", value_limit=Decimal(1000), count_limit=50)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(st.integers(0, 400), st.integers(0, 400)), max_size=12))
def test_allocated_children_never_exceed_the_parent_budget(children: list[tuple[int, int]]) -> None:
    """MAND-19: allocations are parent reservations, so children together can't overspend the parent."""
    bal = Balance()
    for i, (alloc, spend) in enumerate(children):
        amounts = Amounts(Decimal(alloc), 1, 0)
        try:
            bal, res = reserve(
                PARENT_BUDGET,
                bal,
                ReserveRequest(f"alloc-{i}", f"child-{i}", amounts, "GBP"),
                existing=None,
                new_reservation_id=f"r{i}",
                mandate_status=MandateStatus.ACTIVE,
                now=NOW,
                valid_to=None,
                expires_at=NOW + timedelta(days=1),
            )
        except LedgerError:
            continue  # parent capacity exhausted: the child is simply not created
        cb = child_budget(amounts, PARENT_BUDGET)
        used = Amounts(Decimal(min(spend, alloc)), 1, 0)
        assert (
            cb.value_limit == Decimal(alloc) and used.value <= cb.value_limit
        )  # child bounded by allocation
        bal, _ = settle_allocation(bal, res, used)
        assert invariant_holds(PARENT_BUDGET, bal)
    assert bal.reserved == Amounts()


def test_child_cannot_settle_more_than_its_allocation() -> None:
    bal, res = reserve(
        PARENT_BUDGET,
        Balance(),
        ReserveRequest("a", "c", Amounts(Decimal(100), 1, 0), "GBP"),
        existing=None,
        new_reservation_id="r",
        mandate_status=MandateStatus.ACTIVE,
        now=NOW,
        valid_to=None,
        expires_at=NOW,
    )
    with pytest.raises(LedgerError):
        settle_allocation(bal, res, Amounts(Decimal(101), 1, 0))
