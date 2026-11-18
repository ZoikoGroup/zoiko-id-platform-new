"""Market policy packs: activation and publishing (WF02 K09; acceptance T12, T23, T39)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.organization.domain import company_documents as cd
from zid_platform.modules.organization.domain import market_policy as m

T0 = datetime(2026, 11, 1, tzinfo=UTC)
GOOD = m.MarketPolicy(
    jurisdiction="GB",
    region=None,
    legal_form="LTD",
    purpose="ORG_ONBOARDING",
    risk_tier="LOW",
    version="gb-ltd-v1",
    sources=(m.Source("companies_house", 1, "LB-1", "kms://ch", True, "terms-2026-01"),),
    documents=(cd.DocumentRule("REGISTRY_EXTRACT", cd.Claim.CURRENT_STATUS, timedelta(days=90)),),
    ubo=m.UboPolicy(Decimal(25), True, "compliance-gb"),
    reviewers_for_document_first=2,
    recheck_days={"K1": 180, "K2": 365},
    retention=m.Retention({c: 365 for c in m.RECORD_CLASSES}, "LB-RET-1", True, "job-1"),
)


def approvals(policy, *people):
    digest = m.policy_hash(policy)
    return [m.Approval(p, r, digest) for p, r in people]


FOUR = (("lena", "LEGAL"), ("pete", "PRIVACY"), ("sam", "SECURITY"), ("cora", "COMPLIANCE"))


def test_a_complete_pack_has_no_activation_problems() -> None:
    assert m.activation_problems(GOOD) == []


def test_an_empty_pack_is_not_enabled_and_every_gap_is_named() -> None:
    empty = m.MarketPolicy("GB", None, "LTD", "ORG", "LOW", "v0")
    p = m.activation_problems(empty)
    assert "NO_EVIDENCE_ROUTE" in p and "UBO_THRESHOLD_MISSING" in p
    assert all(f"RETENTION_{c}_MISSING" in p for c in m.RECORD_CLASSES)
    assert {
        "RETENTION_LEGAL_BASIS_MISSING",
        "LEGAL_HOLD_NOT_SUPPORTED",
        "DELETION_JOB_MISSING",
        "RECHECK_K1_MISSING",
        "RECHECK_K2_MISSING",
    } <= set(p)


def test_a_missing_retention_schedule_fails_activation() -> None:  # T23
    for cls in m.RECORD_CLASSES:
        days = {**GOOD.retention.days, cls: None}
        bad = replace(GOOD, retention=replace(GOOD.retention, days=days))
        assert f"RETENTION_{cls}_MISSING" in m.activation_problems(bad), cls
    no_basis = replace(GOOD, retention=replace(GOOD.retention, legal_basis=None))
    assert "RETENTION_LEGAL_BASIS_MISSING" in m.activation_problems(no_basis)


def test_a_source_without_lawful_basis_credential_or_terms_keeps_the_market_off() -> None:
    bad = replace(GOOD, sources=(m.Source("inpi_rne", 1, None, None, True, None),))
    p = m.activation_problems(bad)
    assert {
        "SOURCE_INPI_RNE_LEGAL_BASIS_MISSING",
        "SOURCE_INPI_RNE_CREDENTIAL_MISSING",
        "SOURCE_INPI_RNE_TERMS_NOT_RECORDED",
    } <= set(p)
    open_data = replace(GOOD, sources=(m.Source("gleif", 3, "LB-2", None, False, "t"),))
    assert (
        m.activation_problems(open_data) == []
    )  # an open source with no key is fine when none is required


def test_document_first_approval_needs_two_reviewers_in_the_pack() -> None:
    assert "TWO_REVIEWERS_REQUIRED_FOR_DOCUMENT_FIRST" in m.activation_problems(
        replace(GOOD, reviewers_for_document_first=1)
    )
    registry_only = replace(GOOD, documents=(), reviewers_for_document_first=0)
    assert m.activation_problems(registry_only) == []


def test_there_is_no_universal_ownership_percentage_it_is_signed_per_market() -> None:
    assert "UBO_THRESHOLD_NOT_SIGNED" in m.activation_problems(
        replace(GOOD, ubo=m.UboPolicy(Decimal(25), True, None))
    )
    assert "CONTROL_BY_OTHER_MEANS_NOT_ASSESSED" in m.activation_problems(
        replace(GOOD, ubo=m.UboPolicy(Decimal(25), False, "c"))
    )
    assert "UBO_THRESHOLD_MISSING" in m.activation_problems(
        replace(GOOD, ubo=m.UboPolicy(None, True, "c"))
    )


def test_recheck_periods_beyond_the_provisional_ceilings_need_ratification() -> None:
    long = replace(GOOD, recheck_days={"K1": 400, "K2": 700})
    assert {
        "RECHECK_K1_ABOVE_CEILING_NOT_RATIFIED",
        "RECHECK_K2_ABOVE_CEILING_NOT_RATIFIED",
    } <= set(m.activation_problems(long))
    assert m.activation_problems(replace(long, recheck_ratified=True)) == []
    assert (
        m.activation_problems(replace(GOOD, recheck_days={"K1": 30, "K2": 90})) == []
    )  # shorter is always allowed


def test_the_policy_digest_is_stable_and_changes_with_any_field() -> None:
    assert m.policy_hash(GOOD) == m.policy_hash(replace(GOOD))
    assert m.policy_hash(GOOD).startswith("sha256:") and len(m.policy_hash(GOOD)) == 71
    assert m.policy_hash(replace(GOOD, reviewers_for_document_first=3)) != m.policy_hash(GOOD)
    assert m.policy_hash(
        replace(GOOD, ubo=m.UboPolicy(Decimal(10), True, "compliance-gb"))
    ) != m.policy_hash(GOOD)


def test_publishing_needs_tests_the_four_sign_offs_and_not_the_author() -> None:  # T12, T39
    ok = approvals(GOOD, *FOUR)
    assert m.publish_problems(GOOD, GOOD, author="ann", approvals=ok, tests_passed=True) == []
    assert "PUBLISH_TESTS_NOT_PASSED" in m.publish_problems(
        GOOD, GOOD, author="ann", approvals=ok, tests_passed=False
    )
    some = approvals(GOOD, ("lena", "LEGAL"), ("pete", "PRIVACY"))
    p = m.publish_problems(GOOD, GOOD, author="ann", approvals=some, tests_passed=True)
    assert {"SIGN_OFF_SECURITY_MISSING", "SIGN_OFF_COMPLIANCE_MISSING"} <= set(
        p
    ) and "SIGN_OFF_LEGAL_MISSING" not in p


def test_the_author_cannot_approve_their_own_policy() -> None:
    mine = approvals(
        GOOD, ("ann", "LEGAL"), ("pete", "PRIVACY"), ("sam", "SECURITY"), ("cora", "COMPLIANCE")
    )
    p = m.publish_problems(GOOD, GOOD, author="ann", approvals=mine, tests_passed=True)
    assert "AUTHOR_CANNOT_APPROVE_OWN_POLICY" in p and "SIGN_OFF_LEGAL_MISSING" in p


def test_an_approval_of_a_different_version_counts_for_nothing() -> None:
    other = replace(GOOD, reviewers_for_document_first=3)
    stale = approvals(other, *FOUR)
    p = m.publish_problems(GOOD, GOOD, author="ann", approvals=stale, tests_passed=True)
    assert {
        "SIGN_OFF_LEGAL_MISSING",
        "SIGN_OFF_PRIVACY_MISSING",
        "SIGN_OFF_SECURITY_MISSING",
        "SIGN_OFF_COMPLIANCE_MISSING",
    } <= set(p)


def test_a_high_impact_change_needs_two_different_people_and_a_small_one_does_not() -> None:
    changed = replace(GOOD, ubo=m.UboPolicy(Decimal(10), True, "compliance-gb"))
    assert m.high_impact_changes(GOOD, changed) == ["ubo"]
    one_person_four_roles = [
        m.Approval("zed", r, m.policy_hash(changed)) for r in sorted(m.SIGN_OFF_ROLES)
    ]
    p = m.publish_problems(
        changed, GOOD, author="ann", approvals=one_person_four_roles, tests_passed=True
    )
    assert p == ["TWO_PERSON_PUBLISH_REQUIRED"]
    assert (
        m.publish_problems(
            changed, GOOD, author="ann", approvals=approvals(changed, *FOUR), tests_passed=True
        )
        == []
    )
    cosmetic = replace(GOOD, version="gb-ltd-v1b")
    assert m.high_impact_changes(GOOD, cosmetic) == []
    assert m.high_impact_changes(None, GOOD) == list(m.HIGH_IMPACT_FIELDS)


def test_activation_adds_an_interval_and_never_rewrites_the_past() -> None:
    v1 = m.activate([], GOOD, at=T0)
    v2pol = replace(GOOD, version="gb-ltd-v2", reviewers_for_document_first=3)
    v2 = m.activate(v1, v2pol, at=T0 + timedelta(days=30))
    assert (
        m.effective_at(v2, T0 + timedelta(days=5)).version == "gb-ltd-v1"
    )  # last month's decision replays against last month's pack
    assert m.effective_at(v2, T0 + timedelta(days=31)).version == "gb-ltd-v2"
    assert m.effective_at(v2, T0 - timedelta(days=1)) is None
    assert v2[0].until == T0 + timedelta(days=30) and v2[0].policy_hash == m.policy_hash(GOOD)


def test_a_rollback_is_a_new_interval_for_the_earlier_version() -> None:
    v1 = m.activate([], GOOD, at=T0)
    v2 = m.activate(v1, replace(GOOD, version="gb-ltd-v2"), at=T0 + timedelta(days=30))
    back = m.rollback(v2, m.policy_hash(GOOD), at=T0 + timedelta(days=40))
    assert (
        m.effective_at(back, T0 + timedelta(days=35)).version == "gb-ltd-v2"
    )  # what happened in between stays as it was
    assert m.effective_at(back, T0 + timedelta(days=41)).version == "gb-ltd-v1"
    with pytest.raises(ValueError):
        m.rollback(v2, "sha256:unknown", at=T0)


def test_a_pack_round_trips_through_plain_json_with_the_same_digest() -> None:
    import json

    again = m.policy_from_dict(json.loads(json.dumps(m.policy_to_dict(GOOD))))
    assert again == GOOD and m.policy_hash(again) == m.policy_hash(GOOD)
    empty = m.MarketPolicy("GB", None, "LTD", "ORG", "LOW", "v0")
    assert m.policy_from_dict(m.policy_to_dict(empty)) == empty


def test_an_unreadable_stored_pack_is_refused_not_guessed() -> None:
    for bad in (
        {},
        {"jurisdiction": "GB"},
        {**m.policy_to_dict(GOOD), "retention": None},
        {**m.policy_to_dict(GOOD), "documents": [{"doc_class": "X", "claim": "NOT_A_CLAIM"}]},
    ):
        with pytest.raises(ValueError, match="KYB_POLICY_UNREADABLE"):
            m.policy_from_dict(bad)
