"""A mandate written down exactly, and who may confirm it (ZID-CP-WF07 v2.0 .04; CR-169)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from zid_platform.modules.mandate.domain import canonical as c
from zid_platform.modules.mandate.domain.ledger import LedgerError

NOW = datetime(2026, 11, 25, 10, 0, tzinfo=UTC)
G, E, R, X = (str(uuid.UUID(int=i)) for i in (1, 2, 3, 4))  # grantor, executor (the agent), represented, someone else


def row(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "mandate_id": uuid.UUID(int=10), "tenant_id": uuid.UUID(int=9), "authority_grant_id": uuid.UUID(int=11), "represented_principal_id": R, "executor_principal_id": E, "purpose_id": uuid.UUID(int=12),
        "resource_scope_id": uuid.UUID(int=13), "allowed_actions": ["b.act", "a.act"], "resource_scopes": ["invoice:*"], "tools": ["erp.pay"], "jurisdictions": ["GB"], "currency": "GBP",
        "value_limit": Decimal("100.50"), "per_transaction_value_limit": Decimal("20"), "count_limit": 5, "data_limit_bytes": None, "valid_from": NOW, "valid_to": NOW + timedelta(days=7),
        "delegation_allowed": False, "max_delegation_depth": 0, "budget_mode": None, "parent_mandate_id": None, "created_by_principal_id": G,
    }  # fmt: skip
    return {**base, **over}


def h(**over: Any) -> str:
    return c.canonical_hash(c.canonical_terms(row(**over)))


def code(fn: Any, *a: Any, **k: Any) -> str:
    with pytest.raises(LedgerError) as e:
        fn(*a, **k)
    return e.value.code


def test_the_same_terms_have_one_hash_in_whatever_order_and_any_changed_term_is_a_different_mandate() -> None:
    assert h() == h() and h().startswith("sha256:")
    assert h(allowed_actions=["a.act", "b.act"]) == h()  # a set is a set
    assert h(value_limit=Decimal("100.5")) == h(value_limit=Decimal("100.50")) == h(value_limit=Decimal("100.500000000"))  # one way to write a number
    assert h(valid_from=NOW.astimezone(timezone_plus_one())) == h()  # one way to write a time
    for change in (
        {"value_limit": Decimal("100.51")}, {"currency": "EUR"}, {"tools": ["erp.refund"]}, {"allowed_actions": ["a.act"]}, {"jurisdictions": ["GB", "IE"]}, {"count_limit": 6}, {"valid_to": NOW + timedelta(days=8)},
        {"executor_principal_id": X}, {"represented_principal_id": X}, {"delegation_allowed": True}, {"resource_scopes": ["invoice:1"]}, {"per_transaction_value_limit": None}, {"created_by_principal_id": X},
    ):  # fmt: skip
        assert h(**change) != h(), change


def timezone_plus_one() -> Any:
    from datetime import timezone

    return timezone(timedelta(hours=1))


def test_a_mandate_that_can_spend_needs_a_second_confirmation_and_one_that_cannot_does_not() -> None:
    assert c.required_roles(c.canonical_terms(row())) == (c.Role.GRANTOR, c.Role.APPROVER)
    quiet = c.canonical_terms(row(value_limit=None, per_transaction_value_limit=None, count_limit=None, currency=None))
    assert c.can_spend(quiet) is False and c.required_roles(quiet) == (c.Role.GRANTOR,)
    assert c.can_spend(c.canonical_terms(row(value_limit=None, per_transaction_value_limit=None))) is True  # a count limit is still a spend


def test_only_the_grantor_confirms_as_grantor_and_the_second_confirmation_is_someone_independent() -> None:
    c.check_signer(c.Role.GRANTOR, signer=G, creator=G, executor=E, represented=R, prior=[])
    assert code(c.check_signer, c.Role.GRANTOR, signer=X, creator=G, executor=E, represented=R, prior=[]) == "MANDATE_SIGNER_NOT_GRANTOR"
    c.check_signer(c.Role.APPROVER, signer=X, creator=G, executor=E, represented=R, prior=[{"signer_principal_id": G}])
    for who in (G, E, R):  # the grantor, the agent itself, and the person it acts for
        assert code(c.check_signer, c.Role.APPROVER, signer=who, creator=G, executor=E, represented=R, prior=[]) == "MANDATE_SIGNER_NOT_INDEPENDENT"
    assert code(c.check_signer, c.Role.APPROVER, signer=X, creator=G, executor=E, represented=R, prior=[{"signer_principal_id": X}]) == "MANDATE_SIGNER_NOT_INDEPENDENT"  # no one confirms twice


def test_a_confirmation_counts_only_for_the_exact_terms_it_names() -> None:
    now, other = h(), h(value_limit=Decimal(1))
    req = c.required_roles(c.canonical_terms(row()))
    sigs = [{"role": "GRANTOR", "canonical_hash": now}, {"role": "APPROVER", "canonical_hash": other}]
    s = c.signed_state(req, sigs, now)
    assert s["have"] == ["GRANTOR"] and s["missing"] == ["APPROVER"] and s["complete"] is False and s["stale"] == [other]
    full = c.signed_state(req, [{"role": "GRANTOR", "canonical_hash": now}, {"role": "APPROVER", "canonical_hash": now}], now)
    assert full["complete"] is True and full["missing"] == [] and full["stale"] == []
    assert c.signed_state(req, [], now)["missing"] == ["GRANTOR", "APPROVER"]
    c.check_hash(now, now)
    assert code(c.check_hash, other, now) == "MANDATE_HASH_MISMATCH" and code(c.check_hash, None, now) == "MANDATE_HASH_MISMATCH"
    assert "not a qualified" in c.__doc__.replace("\n", " ") or "qualified electronic signature" in c.__doc__
