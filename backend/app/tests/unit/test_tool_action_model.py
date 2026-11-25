"""The pure rules of a tool action and its receipt (ZID-CP-WF07 v2.0 .07, .11; CR-169): states, who may report what, what a receipt may claim, and how a changed receipt is noticed."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from zid_platform.modules.machine.domain import tool_action as ta
from zid_platform.modules.machine.domain.agents import MachineError

S = ta.ActionState
NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)


def code(fn: Any, *a: Any) -> str:
    with pytest.raises(MachineError) as e:
        fn(*a)
    return e.value.code


def action(state: S = S.EXECUTED, **over: Any) -> dict[str, Any]:
    base = {
        "action_id": uuid.UUID(int=7), "tenant_id": uuid.UUID(int=1), "agent_chain": ["child", "root"], "chain_commitment": "sha256:" + "c" * 64, "tool_id": "erp.pay", "resource": "invoice:1",
        "args_sha256": "sha256:" + "a" * 64, "decision_id": "dec_1", "mandate_id": uuid.UUID(int=3), "reservation_id": uuid.UUID(int=4), "state": state.value, "downstream_ref": "erp-77",
    }  # fmt: skip
    return {**base, **over}


def test_an_action_moves_only_along_the_allowed_paths_and_a_grant_nobody_redeemed_has_nothing_to_report() -> None:
    assert ta.check_transition(S.AUTHORIZED, S.DISPATCHED) is S.DISPATCHED
    assert ta.check_transition(S.DISPATCHED, S.UNKNOWN) is S.UNKNOWN
    assert ta.check_transition(S.UNKNOWN, S.EXECUTED) is S.EXECUTED and ta.check_transition(S.UNKNOWN, S.FAILED) is S.FAILED
    assert ta.check_transition(S.EXECUTED, S.COMPENSATED) is S.COMPENSATED
    assert code(ta.check_transition, S.AUTHORIZED, S.EXECUTED) == "TOOL_ACTION_NOT_DISPATCHED"
    for src in (S.FAILED, S.COMPENSATED):
        for dst in S:
            assert code(ta.check_transition, src, dst) == "TOOL_ACTION_STATE_INVALID"
    assert code(ta.check_transition, S.EXECUTED, S.FAILED) == "TOOL_ACTION_STATE_INVALID"  # a final outcome is not rewritten
    assert code(ta.check_transition, S.UNKNOWN, S.COMPENSATED) == "TOOL_ACTION_STATE_INVALID"  # nothing is undone that is not known to have run
    assert set(ta.TRANSITIONS) == set(S)


def test_a_server_reports_an_outcome_never_a_state_of_its_own_choosing() -> None:
    for ok in ("EXECUTED", "FAILED", "UNKNOWN", "COMPENSATED"):
        assert ta.check_outcome(ok).value == ok
    for bad in ("AUTHORIZED", "DISPATCHED", "SUCCEEDED", "executed", ""):
        assert code(ta.check_outcome, bad) == "TOOL_ACTION_INVALID"


def test_the_capacity_follows_the_report_and_an_unknown_outcome_frees_nothing() -> None:
    assert ta.ledger_effect(S.EXECUTED) == "COMMIT" and ta.ledger_effect(S.FAILED) == "RELEASE"
    assert ta.ledger_effect(S.UNKNOWN) == "HOLD"  # a timeout is not proof it did not happen
    assert ta.ledger_effect(S.COMPENSATED) == "NONE"  # whether an undone action restores a cap is a decision for people


def test_a_receipt_keeps_authorized_executed_committed_failed_unknown_and_compensated_apart() -> None:
    f = ta.flags(S.AUTHORIZED, None)
    assert f == {"authorized": True, "executed": False, "committed": False, "failed": False, "unknown": False, "compensated": False}
    assert ta.flags(S.EXECUTED, "COMMITTED")["executed"] and ta.flags(S.EXECUTED, "COMMITTED")["committed"]
    assert ta.flags(S.EXECUTED, "RECONCILIATION_REQUIRED")["committed"] is False  # it ran, but the capacity was not committed
    u = ta.flags(S.UNKNOWN, "RESERVED")
    assert u["unknown"] and not u["executed"] and not u["failed"] and not u["committed"]
    assert ta.conclusion(S.UNKNOWN) == "OUTCOME_UNKNOWN" and ta.conclusion(S.EXECUTED) == "EXECUTED_AS_REPORTED"
    assert ta.conclusion(S.AUTHORIZED) == "AUTHORIZED_NOT_YET_RUN" and ta.conclusion(S.FAILED) == "NOT_EXECUTED"
    assert {ta.conclusion(s) for s in S} == {"AUTHORIZED_NOT_YET_RUN", "IN_PROGRESS", "EXECUTED_AS_REPORTED", "NOT_EXECUTED", "OUTCOME_UNKNOWN", "UNDONE_AS_REPORTED"}
    assert "EXECUTED" not in ta.conclusion(S.UNKNOWN)  # never a success while nobody knows


def test_a_receipt_names_identifiers_digests_and_states_and_nothing_personal() -> None:
    p = ta.receipt_payload(action(), chain=["child", "root"], reservation_state="COMMITTED", revision=3, issued_at=NOW, issuer="https://id.zoiko.test")
    assert p["typ"] == ta.RECEIPT_TYP and p["receipt_for"] == str(uuid.UUID(int=7)) and p["revision"] == 3 and p["agent_chain"] == ["child", "root"]
    assert p["arguments_sha256"] == "sha256:" + "a" * 64 and p["decision_id"] == "dec_1" and p["state"] == "EXECUTED" and p["limits"] == ta.LIMITS
    assert "does not prove the real-world effect" in p["limits"]
    assert set(p) >= {"tenant_id", "chain_commitment", "tool_id", "resource", "mandate_id", "reservation_id", "reservation_state", "outcomes", "downstream_ref", "issued_at"}
    assert ta.payload_digest(p) == ta.payload_digest(dict(reversed(list(p.items()))))  # the digest does not depend on key order
    assert ta.payload_digest(p).startswith("sha256:")
    none = ta.receipt_payload(action(S.AUTHORIZED, mandate_id=None, reservation_id=None, downstream_ref=None), chain=["a"], reservation_state=None, revision=1, issued_at=NOW, issuer="i")
    assert none["mandate_id"] is None and none["reservation_id"] is None and none["conclusion"] == "AUTHORIZED_NOT_YET_RUN"


def test_a_changed_receipt_is_noticed_from_its_value_its_action_and_its_revision() -> None:
    p = ta.receipt_payload(action(), chain=["child", "root"], reservation_state="COMMITTED", revision=3, issued_at=NOW, issuer="i")
    stored = {"digest": ta.payload_digest(p), "action_id": uuid.UUID(int=7), "revision": 3}
    assert ta.receipt_problems(p, stored) == []
    changed = {**p, "state": "FAILED"}
    assert ta.receipt_problems(changed, stored) == ["RECEIPT_ALTERED"]
    assert "RECEIPT_ALTERED" in ta.receipt_problems({**p, "issued_at": "2026-01-01T00:00:00+00:00"}, stored)  # a changed time is a changed receipt
    other = {**stored, "action_id": uuid.UUID(int=9)}
    assert "RECEIPT_FOR_ANOTHER_ACTION" in ta.receipt_problems(p, other)
    assert "RECEIPT_REVISION_MISMATCH" in ta.receipt_problems(p, {**stored, "revision": 2})


def request(**over: Any) -> str:
    base: dict[str, Any] = {
        "tool_id": "erp.pay", "resource": "invoice:1", "arguments_digest": "sha256:" + "a" * 64, "represented_principal_id": "p", "mandate_id": "m", "reservation_id": None, "purpose": "renewal", "value": ("85.00", "USD"),
    }  # fmt: skip
    return ta.canonical_request_hash(**{**base, **over})


def test_one_exact_request_has_one_name_and_any_change_is_a_different_request() -> None:
    assert request() == request() and request().startswith("sha256:")
    for change in ({"arguments_digest": "sha256:" + "b" * 64}, {"resource": "invoice:2"}, {"value": ("85.01", "USD")}, {"value": ("85.00", "EUR")}, {"mandate_id": "n"}, {"tool_id": "erp.refund"}, {"purpose": None}, {"represented_principal_id": None}, {"value": None}):
        assert request(**change) != request(), change  # a changed payee is a changed argument digest, so it is a changed request


def test_the_rehearsal_answers_allow_deny_approval_required_or_unavailable_and_never_guesses() -> None:
    d = ta.preflight_decision
    assert d(allowed=True, codes=[], capacity=None, unavailable=False) == ("ALLOW", [])
    assert d(allowed=True, codes=[], capacity={"available": True}, unavailable=False) == ("ALLOW", [])
    assert d(allowed=True, codes=[], capacity={"available": False, "reason": "MANDATE_VALUE_LIMIT"}, unavailable=False) == ("DENY", ["MANDATE_VALUE_LIMIT"])  # the policy said yes, the budget says no
    assert d(allowed=False, codes=["AGENT_NOT_ACTIVE"], capacity=None, unavailable=False) == ("DENY", ["AGENT_NOT_ACTIVE"])
    assert d(allowed=False, codes=["HUMAN_APPROVAL_REQUIRED"], capacity=None, unavailable=False) == ("APPROVAL_REQUIRED", ["HUMAN_APPROVAL_REQUIRED"])
    assert d(allowed=True, codes=[], capacity=None, unavailable=True) == ("UNAVAILABLE", ["POLICY_UNAVAILABLE"])  # an unanswerable question is never an allow
    assert d(allowed=True, codes=["X"], capacity={"available": True}, unavailable=True) == ("UNAVAILABLE", ["X"])
