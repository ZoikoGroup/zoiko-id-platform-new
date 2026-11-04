"""Customer commitment approval rules (ZID-COM-CL-004 section 04; slice L-14c, CR-054). Pure: no database."""

from __future__ import annotations

from typing import Any

import pytest
from zid_platform.modules.commercial.domain import commitment as c
from zid_platform.modules.commercial.infrastructure.finance_config import finance_policy

POLICY = finance_policy().commitment
USD = 100  # minor units per dollar


def code(call: Any, *a: Any, **kw: Any) -> str:
    with pytest.raises(c.CommitmentError) as e:
        call(*a, **kw)
    return e.value.code


def test_exposure_is_the_higher_of_the_first_year_and_the_whole_term_less_what_was_already_committed() -> (
    None
):
    assert (
        c.exposure_minor(400 * USD, 12) == 4800 * USD
    )  # +$400 a month for 12 months is one $4,800 approval
    assert (
        c.exposure_minor(400 * USD, 36) == 14400 * USD
    )  # a long term is its whole non-cancelable value
    assert c.exposure_minor(400 * USD, 6) == 2400 * USD
    assert (
        c.exposure_minor(500 * USD, 12, amended_monthly_minor=300 * USD) == 2400 * USD
    )  # only the increase is new
    assert (
        c.exposure_minor(100 * USD, 12, amended_monthly_minor=300 * USD) == 0
    )  # a smaller commitment adds nothing
    assert code(c.exposure_minor, -1, 12) == "EXPOSURE_INVALID"
    assert code(c.exposure_minor, 1, 0) == "EXPOSURE_INVALID"


def test_the_tiers_change_exactly_at_the_documented_boundaries() -> None:
    assert c.tier_for(POLICY, 250_000) is c.Tier.T1  # $2,500.00
    assert c.tier_for(POLICY, 250_001) is c.Tier.T2  # $2,500.01
    assert c.tier_for(POLICY, 2_500_000) is c.Tier.T2  # $25,000.00
    assert c.tier_for(POLICY, 2_500_001) is c.Tier.T3  # $25,000.01


def test_nonstandard_terms_need_review_whatever_the_amount() -> None:
    standard = {"liability_capped": True, "residency_commitment": None, "term_months": 12}
    assert not c.needs_review(POLICY, **standard)
    assert c.needs_review(POLICY, **{**standard, "liability_capped": False})
    assert c.needs_review(POLICY, **{**standard, "residency_commitment": "EU-only"})
    assert c.needs_review(POLICY, **{**standard, "term_months": POLICY.overlay_term_months + 1})
    assert not c.needs_review(POLICY, **{**standard, "term_months": POLICY.overlay_term_months})


def decision(tier: c.Tier, **kw: Any) -> c.Authority:
    base: dict[str, Any] = {
        "approver": "b", "requester": "a", "is_owner": False, "delegate": None, "exposure_usd_minor": 100_000, "decided": {},
        "presented_hash": "h", "request_hash": "h", "review_required": False, "reviewed": False,
    }  # fmt: skip
    return c.check_decision(tier, **{**base, **kw})


def test_only_the_owner_or_a_delegate_with_enough_limit_signs_and_never_twice() -> None:
    assert decision(c.Tier.T1, is_owner=True) is c.Authority.OWNER
    assert (
        decision(c.Tier.T1, delegate=c.Delegate("BILLING_SPEND", 100_000))
        is c.Authority.BILLING_SPEND
    )
    assert (
        code(decision, c.Tier.T1, delegate=c.Delegate("BILLING_SPEND", 99_999))
        == "OVER_SPEND_LIMIT"
    )
    assert (
        code(decision, c.Tier.T1) == "NOT_AN_APPROVER"
    )  # an Admin, a Member, platform support: no authority
    assert (
        code(decision, c.Tier.T1, delegate=c.Delegate("PROCUREMENT_APPROVER", 10**9))
        == "NOT_AN_APPROVER"
    )  # the wrong kind for this tier
    assert (
        code(decision, c.Tier.T2, delegate=c.Delegate("BILLING_SPEND", 10**9)) == "NOT_AN_APPROVER"
    )  # a Billing Admin limit never reaches tier 2
    assert (
        decision(c.Tier.T2, delegate=c.Delegate("PROCUREMENT_APPROVER", 100_000))
        is c.Authority.PROCUREMENT_APPROVER
    )
    assert (
        code(decision, c.Tier.T2, approver="a", delegate=c.Delegate("PROCUREMENT_APPROVER", 10**9))
        == "SELF_APPROVAL"
    )
    assert (
        decision(c.Tier.T2, approver="a", is_owner=True) is c.Authority.OWNER
    )  # the Owner may approve what they asked for
    assert code(decision, c.Tier.T1, is_owner=True, decided={"b": "OWNER"}) == "ALREADY_SIGNED"
    assert code(decision, c.Tier.T1, is_owner=True, presented_hash="x") == "SNAPSHOT_CHANGED"


def test_the_top_tier_takes_two_different_people_and_a_review_blocks_everything() -> None:
    assert not c.is_complete(c.Tier.T3, {"o": "OWNER"})
    assert not c.is_complete(c.Tier.T3, {"p": "PROCUREMENT_APPROVER"})
    assert c.is_complete(c.Tier.T3, {"o": "OWNER", "p": "PROCUREMENT_APPROVER"})
    assert c.is_complete(c.Tier.T2, {"o": "OWNER"}) and not c.is_complete(c.Tier.T2, {})
    # a second Owner is not the independent approver: the OWNER signature is given
    assert (
        code(decision, c.Tier.T3, approver="o2", is_owner=True, decided={"o": "OWNER"})
        == "NOT_AN_APPROVER"
    )
    assert (
        decision(
            c.Tier.T3,
            approver="p",
            delegate=c.Delegate("PROCUREMENT_APPROVER", 10**9),
            decided={"o": "OWNER"},
        )
        is c.Authority.PROCUREMENT_APPROVER
    )
    assert code(decision, c.Tier.T1, is_owner=True, review_required=True) == "REVIEW_REQUIRED"
    assert (
        decision(c.Tier.T1, is_owner=True, review_required=True, reviewed=True) is c.Authority.OWNER
    )


def test_only_the_owner_delegates_and_never_to_themselves() -> None:
    c.check_delegation("BILLING_SPEND", 1, grantor="o", grantee="x", is_owner=True)
    assert (
        code(c.check_delegation, "BILLING_SPEND", 1, grantor="a", grantee="x", is_owner=False)
        == "OWNER_ONLY"
    )
    assert (
        code(c.check_delegation, "BILLING_SPEND", 1, grantor="o", grantee="o", is_owner=True)
        == "SELF_DELEGATION"
    )
    assert (
        code(c.check_delegation, "ADMIN", 1, grantor="o", grantee="x", is_owner=True)
        == "KIND_UNKNOWN"
    )
    assert (
        code(c.check_delegation, "BILLING_SPEND", 0, grantor="o", grantee="x", is_owner=True)
        == "CAP_INVALID"
    )
