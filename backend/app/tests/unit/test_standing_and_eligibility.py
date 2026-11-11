"""GID-040/041/042/043/044/046/047/048/049: standing rules, eligibility, trust tiers, engagement.

GLB-01  US state license active; another state requested: not eligible
GLB-04  suspended license: eligibility blocked
GLB-09  unrecognized overseas award: REVIEW
GLB-11  firm holds cover, the individual is not named: personal insurance not satisfied
GLB-12  CPD credits from an unapproved program do not satisfy the rule
GLB-13  former employer gone: not false (see test_credential_claims); disclosure is limited to permitted fields
GLB-17  engagement in another jurisdiction: permission re-run, contract held
GLB-19  Tier A profile with expired insurance is downgraded / held
GLB-22  same-name adverse result is not an adverse decision
GLB-31  unlicensed specialty service for an otherwise licensed professional: refused
GLB-32  insurance excludes the requested work: fails
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import standing as st
from zid_platform.modules.credential.domain.claims import (
    CredentialStatus,
    Eligibility,
    EvidenceTier,
    SubjectMatch,
)

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
D = NOW.date()
PRIMARY = EvidenceTier.PRIMARY_SOURCE_CONFIRMED

# --- GID-040 license standing ------------------------------------------------------------------------------------------


def test_a_good_license_in_period_is_active():
    s = st.license_standing(
        {"good_standing": "GOOD", "effective_from": "2020-01-01", "effective_until": "2030-01-01"},
        on=D,
    )
    assert s.status is CredentialStatus.ACTIVE and s.reasons == ()


@pytest.mark.parametrize(
    ("fields", "status", "reason"),
    [
        (
            {"good_standing": "GOOD", "effective_until": "2026-11-10"},
            CredentialStatus.EXPIRED,
            "LICENSE_PERIOD_ENDED",
        ),
        (
            {"good_standing": "GOOD", "effective_from": "2026-11-12"},
            CredentialStatus.UNKNOWN,
            "LICENSE_NOT_YET_EFFECTIVE",
        ),
        ({"good_standing": "NOT_GOOD"}, CredentialStatus.SUSPENDED, "NOT_IN_GOOD_STANDING"),
        ({"good_standing": "UNKNOWN"}, CredentialStatus.UNKNOWN, "STANDING_NOT_REPORTED"),
        ({}, CredentialStatus.UNKNOWN, "STANDING_NOT_REPORTED"),
        ({"good_standing": "CONDITIONS"}, CredentialStatus.UNKNOWN, "CONDITIONS_UNSPECIFIED"),
    ],
)
def test_an_expired_suspended_or_unreported_license_never_becomes_active(fields, status, reason):
    s = st.license_standing(fields, on=D)
    assert s.status is status and reason in s.reasons


def test_conditions_are_carried_as_restrictions_not_dropped():
    s = st.license_standing(
        {"good_standing": "CONDITIONS", "conditions": ["no solo practice"]}, on=D
    )
    assert s.status is CredentialStatus.ACTIVE and s.restrictions == ("no solo practice",)


def test_the_last_day_of_a_license_is_still_valid_and_the_next_is_not():
    assert (
        st.license_standing({"good_standing": "GOOD", "effective_until": "2026-11-11"}, on=D).status
        is CredentialStatus.ACTIVE
    )
    assert (
        st.license_standing({"good_standing": "GOOD", "effective_until": "2026-11-10"}, on=D).status
        is CredentialStatus.EXPIRED
    )


# --- GID-042 certificate -----------------------------------------------------------------------------------------------


def test_a_certificate_past_renewal_is_expired_and_a_retired_program_is_noted():
    assert (
        st.certificate_status({"renewal_due": "2026-01-01"}, on=D).status
        is CredentialStatus.EXPIRED
    )
    ok = st.certificate_status({"renewal_due": "2027-01-01"}, on=D, program_retired=True)
    assert ok.status is CredentialStatus.ACTIVE and ok.reasons == ("PROGRAM_RETIRED_NO_RENEWAL",)
    assert (
        st.certificate_status({}, on=D).status is CredentialStatus.ACTIVE
    )  # no renewal date: not expiring


# --- GID-043 CPD -------------------------------------------------------------------------------------------------------


def test_cpd_is_satisfied_only_with_source_evidence_an_approved_provider_and_enough_credits():
    f = {"credits": 20, "evidence_source": "board-a"}
    assert st.cpd_standing(
        f, required_credits=20, approved_providers={"board-a"}, evidence=PRIMARY
    ) == (st.CpdResult.SATISFIED, ())


def test_glb_12_unapproved_or_unverified_or_short_cpd_is_indeterminate_with_every_reason():
    r, why = st.cpd_standing(
        {"credits": 5, "evidence_source": "diploma-mill"},
        required_credits=20,
        approved_providers={"board-a"},
        evidence=EvidenceTier.DOCUMENT_INTEGRITY,
    )
    assert r is st.CpdResult.INDETERMINATE
    assert set(why) == {
        "EVIDENCE_NOT_SOURCE_CONFIRMED",
        "PROVIDER_NOT_APPROVED",
        "CREDITS_BELOW_REQUIREMENT",
    }
    bad = st.cpd_standing(
        {"credits": "lots", "evidence_source": "board-a"},
        required_credits=1,
        approved_providers={"board-a"},
        evidence=PRIMARY,
    )
    assert bad[0] is st.CpdResult.INDETERMINATE and "CREDITS_UNREADABLE" in bad[1]


# --- GID-044 employment ------------------------------------------------------------------------------------------------


def test_employment_returns_only_permitted_fields_and_flags_impossible_dates():
    src = {
        "employer_id": "e1",
        "start_date": "2015-01-01",
        "end_date": "2012-01-01",
        "salary": 90000,
        "title": "Eng",
    }
    out = st.employment_disclosure(
        src, permitted_fields={"employer_id", "start_date", "end_date", "title"}
    )
    assert (
        "salary" not in out
        and out["end_date"] is None
        and out["date_fault"] is True
        and out["title"] == "Eng"
    )


# --- GID-046 disciplinary ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "expect"),
    [
        (
            {
                "subject": SubjectMatch.AMBIGUOUS,
                "record_found": True,
                "final": True,
                "appeal_pending": False,
            },
            st.Disciplinary.REVIEW,
        ),  # GLB-22
        (
            {
                "subject": SubjectMatch.NOT_MATCHED,
                "record_found": True,
                "final": True,
                "appeal_pending": False,
            },
            st.Disciplinary.NO_MATCH,
        ),
        (
            {
                "subject": SubjectMatch.MATCHED,
                "record_found": False,
                "final": False,
                "appeal_pending": False,
            },
            st.Disciplinary.NO_ADVERSE_FINDING,
        ),
        (
            {
                "subject": SubjectMatch.MATCHED,
                "record_found": True,
                "final": True,
                "appeal_pending": True,
            },
            st.Disciplinary.ADVERSE_UNDER_APPEAL,
        ),
        (
            {
                "subject": SubjectMatch.MATCHED,
                "record_found": True,
                "final": False,
                "appeal_pending": False,
            },
            st.Disciplinary.REVIEW,
        ),
        (
            {
                "subject": SubjectMatch.MATCHED,
                "record_found": True,
                "final": True,
                "appeal_pending": False,
            },
            st.Disciplinary.ADVERSE_FINDING,
        ),
    ],
)
def test_an_adverse_finding_needs_a_matched_confirmed_final_record(kw, expect):
    assert st.disciplinary_result(evidence=PRIMARY, **kw)[0] is expect


def test_an_unconfirmed_record_is_never_adverse():
    r, why = st.disciplinary_result(
        subject=SubjectMatch.MATCHED,
        evidence=EvidenceTier.ISSUER_AUTHENTICATED,
        record_found=True,
        final=True,
        appeal_pending=False,
    )
    assert r is st.Disciplinary.REVIEW and why == ("FINDING_NOT_SOURCE_CONFIRMED",)


# --- GID-047 insurance -------------------------------------------------------------------------------------------------

NEED = st.InsuranceNeed("Dana Lee", "US", "contract review", date(2026, 11, 11), date(2027, 11, 11))
COVER = {
    "insured_entity": "Dana  Lee",
    "region": ["US", "CA"],
    "expires_on": "2028-01-01",
    "activities": ["contract review"],
    "excluded_work": [],
}


def test_cover_naming_the_person_territory_dates_and_work_is_satisfied():
    assert st.insurance_result(COVER, NEED) == (True, ())


@pytest.mark.parametrize(
    ("over", "reason"),
    [
        ({"insured_entity": "Lee & Partners LLP"}, "INSURED_ENTITY_MISMATCH"),  # GLB-11
        ({"region": ["GB"]}, "TERRITORY_NOT_COVERED"),
        ({"expires_on": "2027-06-01"}, "COVER_ENDS_BEFORE_PERIOD"),
        ({"expires_on": None}, "COVER_ENDS_BEFORE_PERIOD"),
        ({"activities": ["tax advice"]}, "WORK_NOT_COVERED"),
        ({"excluded_work": ["Contract Review"]}, "WORK_EXCLUDED"),  # GLB-32
    ],
)
def test_each_insurance_failure_is_reported(over, reason):
    ok, why = st.insurance_result({**COVER, **over}, NEED)
    assert not ok and reason in why


def test_every_insurance_failure_is_listed_at_once():
    ok, why = st.insurance_result({"insured_entity": "Firm", "region": ["GB"]}, NEED)
    assert not ok and {
        "INSURED_ENTITY_MISMATCH",
        "TERRITORY_NOT_COVERED",
        "COVER_ENDS_BEFORE_PERIOD",
    } <= set(why)


# --- GID-041 eligibility -----------------------------------------------------------------------------------------------

POLICY = el.EligibilityPolicy(
    "us-ca-law", 3, "US", "CA", "LAW", "legal.advice", recognition_required_for=("ACADEMIC_AWARD",)
)


def _lic(**over) -> el.AssertionFact:
    base = {
        "claim_type": "PROFESSIONAL_LICENSE",
        "decision": "VERIFIED",
        "credential_status": "ACTIVE",
        "jurisdiction": {"country": "US", "subdivision": "CA"},
        "permitted_services": ("legal.advice",),
        "source_checked_at": NOW - timedelta(days=10),
    }
    base.update(over)
    return el.AssertionFact(**base)


def test_a_live_in_place_license_listing_the_service_is_eligible_with_its_restrictions():
    out = el.evaluate(POLICY, [_lic(restrictions=("no litigation",))], now=NOW)
    assert out.eligibility is Eligibility.ELIGIBLE and out.restrictions == ("no litigation",)
    assert out.policy_ref == {"id": "us-ca-law", "version": 3}


@pytest.mark.parametrize(
    ("assertion", "reason"),
    [
        (
            _lic(jurisdiction={"country": "US", "subdivision": "NY"}),
            "PROFESSIONAL_LICENSE_WRONG_JURISDICTION",
        ),  # GLB-01
        (
            _lic(jurisdiction={"country": "GB"}),
            "PROFESSIONAL_LICENSE_WRONG_JURISDICTION",
        ),  # UK never implies a US state
        (
            _lic(permitted_services=("tax.advice",)),
            "PROFESSIONAL_LICENSE_SERVICE_NOT_PERMITTED",
        ),  # GLB-31
        (_lic(credential_status="SUSPENDED"), "PROFESSIONAL_LICENSE_SUSPENDED"),  # GLB-04
        (_lic(decision="NOT_VERIFIED"), "PROFESSIONAL_LICENSE_NOT_VERIFIED"),
        (_lic(expires_at=NOW - timedelta(seconds=1)), "PROFESSIONAL_LICENSE_EXPIRED"),
        (_lic(source_checked_at=NOW - timedelta(days=400)), "PROFESSIONAL_LICENSE_STALE"),
    ],
)
def test_a_license_that_does_not_count_blocks_with_its_reason(assertion, reason):
    out = el.evaluate(POLICY, [assertion], now=NOW)
    assert out.eligibility is Eligibility.BLOCKED and reason in out.reasons


def test_no_license_at_all_is_blocked_missing():
    out = el.evaluate(POLICY, [], now=NOW)
    assert out.eligibility is Eligibility.BLOCKED and out.reasons == (
        "PROFESSIONAL_LICENSE_MISSING",
    )


def test_one_good_assertion_among_bad_ones_counts():
    out = el.evaluate(POLICY, [_lic(credential_status="REVOKED"), _lic()], now=NOW)
    assert out.eligibility is Eligibility.ELIGIBLE


def test_glb_09_a_foreign_award_needing_recognition_is_review_never_equivalent():
    pol = el.EligibilityPolicy(
        "p",
        1,
        "US",
        "CA",
        "ENG",
        "eng.sign",
        required_claim_types=("ACADEMIC_AWARD",),
        recognition_required_for=("ACADEMIC_AWARD",),
    )
    award = el.AssertionFact(
        "ACADEMIC_AWARD", "VERIFIED", "ACTIVE", {"country": "GB"}, recognition="REVIEW"
    )
    assert el.evaluate(pol, [award], now=NOW).eligibility is Eligibility.REVIEW
    unrec = el.AssertionFact(
        "ACADEMIC_AWARD", "VERIFIED", "ACTIVE", {"country": "IN"}, recognition="UNRECOGNIZED"
    )
    assert el.evaluate(pol, [unrec], now=NOW).eligibility is Eligibility.REVIEW
    home = el.AssertionFact(
        "ACADEMIC_AWARD", "VERIFIED", "ACTIVE", {"country": "US"}, recognition="NOT_REQUIRED"
    )
    assert el.evaluate(pol, [home], now=NOW).eligibility is Eligibility.ELIGIBLE


# --- GID-048 trust tiers -----------------------------------------------------------------------------------------------

D_ = el.Dimension
S_ = el.DimStatus
RULE = el.TierRule(
    "zoikorum",
    1,
    tier_a=(D_.IDENTITY, D_.CREDENTIAL, D_.ELIGIBILITY, D_.INSURANCE),
    tier_b=(D_.IDENTITY, D_.CREDENTIAL),
    mandatory=(D_.IDENTITY,),
)
ALL = {
    D_.IDENTITY: S_.VERIFIED,
    D_.CREDENTIAL: S_.VERIFIED,
    D_.ELIGIBILITY: S_.VERIFIED,
    D_.INSURANCE: S_.VERIFIED,
}


def test_tier_a_needs_every_dimension_and_nothing_is_published_unverified():
    assert el.tier_of(RULE, ALL) == (el.Tier.A, ())
    assert el.tier_of(RULE, {**ALL, D_.INSURANCE: S_.NOT_VERIFIED}) == (
        el.Tier.B,
        ("INSURANCE_NOT_VERIFIED",),
    )


def test_glb_19_expired_insurance_downgrades_tier_a():
    tier, gaps = el.tier_of(RULE, {**ALL, D_.INSURANCE: S_.EXPIRED})
    assert tier is el.Tier.B and gaps == ("INSURANCE_EXPIRED",)


def test_a_mandatory_dimension_cannot_be_bypassed_by_anything_else():
    tier, gaps = el.tier_of(RULE, {**ALL, D_.IDENTITY: S_.NOT_VERIFIED})
    assert tier is el.Tier.NONE and gaps[0] == "IDENTITY_NOT_VERIFIED"
    # even NOT_REQUIRED does not satisfy a mandatory dimension
    assert el.tier_of(RULE, {**ALL, D_.IDENTITY: S_.NOT_REQUIRED})[0] is el.Tier.NONE


def test_a_dimension_that_does_not_apply_may_be_marked_not_required_for_a_non_mandatory_one():
    assert el.tier_of(RULE, {**ALL, D_.INSURANCE: S_.NOT_REQUIRED})[0] is el.Tier.A
    assert el.tier_of(RULE, {})[0] is el.Tier.NONE


def test_tier_c_is_identity_only():
    rule = el.TierRule(
        "r",
        1,
        tier_a=(D_.IDENTITY, D_.CREDENTIAL),
        tier_b=(D_.IDENTITY, D_.CREDENTIAL),
        tier_c=(D_.IDENTITY,),
    )
    assert el.tier_of(rule, {D_.IDENTITY: S_.VERIFIED})[0] is el.Tier.C


# --- GID-049 engagement ------------------------------------------------------------------------------------------------

REQ = el.EngagementRequest("US", "CA", "LAW", "legal.advice")


def _eng(**over):
    kw = {"identity_verified": True, "insurance_satisfied": True, "restricted": False, "now": NOW}
    kw.update(over)
    return el.engagement_check(REQ, kw.pop("policy", POLICY), kw.pop("assertions", [_lic()]), **kw)


def test_a_complete_engagement_is_ready():
    assert _eng().engagement is el.Engagement.READY


def test_glb_17_an_engagement_in_a_different_place_holds_and_reruns_the_permission():
    other = el.EngagementRequest("US", "NY", "LAW", "legal.advice")
    out = el.engagement_check(
        other,
        POLICY,
        [_lic()],
        identity_verified=True,
        insurance_satisfied=True,
        restricted=False,
        now=NOW,
    )
    assert out.engagement is el.Engagement.HOLD and out.reasons == (
        "POLICY_DOES_NOT_MATCH_ENGAGEMENT",
    )


def test_glb_31_32_blocked_when_the_service_is_unlicensed_or_the_work_is_excluded():
    assert (
        _eng(assertions=[_lic(permitted_services=("tax.advice",))]).engagement
        is el.Engagement.BLOCKED
    )
    assert _eng(insurance_satisfied=False).engagement is el.Engagement.BLOCKED


def test_missing_checks_hold_rather_than_pass_or_deny_for_good():
    out = _eng(insurance_satisfied=None, identity_verified=False)
    assert out.engagement is el.Engagement.HOLD
    assert set(out.reasons) == {"INSURANCE_NOT_CHECKED", "IDENTITY_NOT_VERIFIED"}


def test_a_restriction_on_record_blocks():
    out = _eng(restricted=True)
    assert out.engagement is el.Engagement.BLOCKED and "RESTRICTION_ON_RECORD" in out.reasons


def test_insurance_is_not_demanded_when_the_engagement_does_not_need_it():
    req = el.EngagementRequest("US", "CA", "LAW", "legal.advice", insurance_required=False)
    out = el.engagement_check(
        req,
        POLICY,
        [_lic()],
        identity_verified=True,
        insurance_satisfied=None,
        restricted=False,
        now=NOW,
    )
    assert out.engagement is el.Engagement.READY
