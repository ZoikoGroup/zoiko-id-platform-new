"""Finance authority rules and the policy file (ZID-COM-CL-004; slice L-14b, CR-054). Pure: no database."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from zid_platform.modules.commercial.domain import finance as f
from zid_platform.modules.commercial.infrastructure.finance_config import (
    CONFIG,
    finance_policy,
    parse,
)

D = Decimal
NOW = datetime(2026, 11, 3, tzinfo=UTC)


def code(call, *a, **kw) -> str:  # type: ignore[no-untyped-def]
    with pytest.raises(f.FinanceError) as e:
        call(*a, **kw)
    return e.value.code


def test_money_is_whole_minor_units_and_never_rounded_down_into_a_lower_tier() -> None:
    assert f.to_minor(D("500.00")) == 50_000 and f.to_minor(D("0.01")) == 1
    assert code(f.to_minor, D("10.005")) == "AMOUNT_INVALID"
    assert (
        code(f.to_minor, D("0")) == "AMOUNT_INVALID"
        and code(f.to_minor, D("-1")) == "AMOUNT_INVALID"
    )
    assert f.usd_minor(1001, "EUR", {"EUR": D("1.0001")}) == 1002  # 1001.1001 rounds UP
    assert f.usd_minor(500, "USD", {"USD": D("1")}) == 500
    assert code(f.usd_minor, 500, "JPY", {"USD": D("1")}) == "FX_UNPINNED"


def test_the_shipped_policy_loads_and_places_every_boundary() -> None:
    p = finance_policy()
    assert len(p.content_hash) == 64 and p.fx_usd == {"USD": D("1")}
    notes = p.credit_note
    for usd, roles in (
        (50_000, ("FINANCE_OPS_LEAD",)), (50_001, ("CONTROLLER",)), (500_000, ("CONTROLLER",)), (500_001, ("CFO",)),
        (2_500_000, ("CFO",)), (2_500_001, ("CFO", "FOUNDER")), (10**12, ("CFO", "FOUNDER")),
    ):  # fmt: skip
        assert f.tier_for(notes, usd).roles == roles, usd
    assert f.tier_for(p.pilot_credit, 100_000).roles == ("CCO", "FINANCE_OPS_LEAD")
    assert f.tier_for(p.pilot_credit, 100_001).roles == ("CCO", "CONTROLLER")
    assert f.tier_for(p.pilot_credit, 1_000_001).roles == ("CFO", "FOUNDER")
    assert p.pilot_credit.max_days == 90 and not set(p.request_roles) & set(p.execute_roles)


def test_a_policy_that_could_widen_authority_by_a_typo_does_not_load() -> None:
    raw = (CONFIG / "commercial" / "finance-policy.yaml").read_bytes().decode()

    def broken(old: str, new: str) -> bytes:
        assert old in raw, old
        return raw.replace(old, new, 1).encode()

    for old, new in (
        (
            "{max_usd: null, roles: [CFO, FOUNDER]}\n\npilot_credit",
            "{max_usd: 99999, roles: [CFO, FOUNDER]}\n\npilot_credit",
        ),  # last tier has a limit
        (
            "{max_usd: 5000, roles: [CONTROLLER]}",
            "{max_usd: 400, roles: [CONTROLLER]}",
        ),  # limits fall
        (
            "roles: [CFO, FOUNDER]}\n\npilot_credit",
            "roles: [CFO, CFO]}\n\npilot_credit",
        ),  # one role twice: one person in two slots
        (
            "execute_roles: [FINANCE_EXECUTOR]",
            "execute_roles: [REVOPS]",
        ),  # opening and running are one permission
        ("unit: USD", "unit: EUR"),
    ):
        with pytest.raises(f.FinanceError):
            parse(broken(old, new))
    assert (
        parse(raw.encode()).content_hash != parse(raw.replace("14", "15", 1).encode()).content_hash
    )  # any edit is another hash to sign


def test_a_decision_needs_the_exact_request_a_required_role_and_a_new_person() -> None:
    kw: dict[str, Any] = {
        "required": ("CCO", "CONTROLLER"),
        "role": "CCO",
        "approver": "b",
        "requester": "a",
        "decided": {},
        "presented_hash": "h",
        "request_hash": "h",
    }
    f.check_decision(**kw)  # type: ignore[arg-type]
    assert code(f.check_decision, **{**kw, "presented_hash": "other"}) == "SNAPSHOT_CHANGED"
    assert code(f.check_decision, **{**kw, "role": "CFO"}) == "ROLE_NOT_REQUIRED"
    assert code(f.check_decision, **{**kw, "decided": {"CCO": "c"}}) == "ROLE_ALREADY_DECIDED"
    assert code(f.check_decision, **{**kw, "approver": "a"}) == "SELF_APPROVAL"
    assert (
        code(f.check_decision, **{**kw, "role": "CONTROLLER", "decided": {"CCO": "b"}})
        == "ONE_PERSON_TWO_SLOTS"
    )
    assert not f.is_complete(("CCO", "CONTROLLER"), {"CCO": "b"}) and f.is_complete(
        ("CCO", "CONTROLLER"), {"CCO": "b", "CONTROLLER": "c"}
    )


def test_running_needs_an_approved_unexpired_request_and_an_independent_executor() -> None:
    ok: dict[str, Any] = {
        "state": f.State.APPROVED,
        "approval_expires_at": NOW + timedelta(days=1),
        "now": NOW,
        "executor": "x",
        "requester": "a",
        "approvers": ("b", "c"),
    }
    f.check_execution(**ok)  # type: ignore[arg-type]
    assert code(f.check_execution, **{**ok, "state": f.State.PENDING_APPROVAL}) == "NOT_APPROVED"
    assert code(f.check_execution, **{**ok, "state": f.State.APPLIED}) == "NOT_APPROVED"
    assert code(f.check_execution, **{**ok, "approval_expires_at": NOW}) == "APPROVAL_EXPIRED"
    assert code(f.check_execution, **{**ok, "approval_expires_at": None}) == "APPROVAL_EXPIRED"
    assert code(f.check_execution, **{**ok, "executor": "a"}) == "EXECUTOR_NOT_INDEPENDENT"
    assert code(f.check_execution, **{**ok, "executor": "c"}) == "EXECUTOR_NOT_INDEPENDENT"


def test_the_snapshot_hash_moves_with_every_fact_and_ignores_key_order() -> None:
    base = {"amount_minor": 1000, "tenant_id": "t", "required_roles": ["CCO"]}
    assert f.snapshot_hash(base) == f.snapshot_hash(dict(reversed(list(base.items()))))
    for key, value in (("amount_minor", 1001), ("tenant_id", "u"), ("required_roles", ["CFO"])):
        assert f.snapshot_hash({**base, key: value}) != f.snapshot_hash(base)


def test_a_tier_that_names_no_roles_or_a_policy_that_names_nobody_is_refused() -> None:
    p = finance_policy()
    assert code(f.validate_policy, replace(p, request_roles=())) == "POLICY_INVALID"
    bad = replace(p, credit_note=f.Instrument(90, (f.Tier(None, ()),)))
    assert code(f.validate_policy, bad) == "POLICY_INVALID"
