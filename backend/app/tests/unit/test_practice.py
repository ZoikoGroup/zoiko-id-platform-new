"""Practice restrictions and insurance adequacy (ZID-RCG-003 RCG-G05; CR-076): Zoikorum deny/review fixtures T13 to T17 and the mandatory
counterexample (qualification verified, practice eligibility not established)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

import pytest
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import practice as pr
from zid_platform.modules.credential.domain import standing as st
from zid_platform.modules.credential.domain.claims import Eligibility

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
TODAY = NOW.date()
UNTIL = date(2027, 11, 12)

RULE = pr.PracticeRule(
    rule_id="r1",
    version=1,
    policy_id="p1",
    permitted=frozenset({"CONTINUING_EDUCATION_DUE"}),
    review=frozenset({"PROBATION"}),
    blocking=frozenset({"SUPERVISED_PRACTICE_ONLY", "NO_INDEPENDENT_PRACTICE"}),
    insurance=pr.InsuranceRequirement(
        Decimal(1_000_000), "USD", frozenset({"LEGAL"}), claims_made_needs_retroactive=True
    ),
)
NEED = st.InsuranceNeed("the-subject", "US", "legal.advice", TODAY, UNTIL)


def cover(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "insured_entity": "the-subject",
        "region": ["US"],
        "expires_on": "2028-01-01",
        "activities": ["legal.advice"],
        "excluded_work": [],
        "coverage_limit": "2000000",
        "currency": "USD",
        "practice_class": ["legal"],
        "cover_basis": "OCCURRENCE",
        "retroactive_date": None,
    }
    base.update(over)
    return base


def adequate(**over: Any) -> tuple[bool, tuple[str, ...]]:
    return pr.insurance_adequacy(RULE.insurance, cover(**over), NEED)  # type: ignore[arg-type]


# ---- licence conditions


def test_no_conditions_leave_the_service_open() -> None:
    assert pr.restriction_result(RULE, [])[0] is pr.Practice.ESTABLISHED


def test_a_permitted_condition_does_not_limit_the_service_and_matching_ignores_case_and_punctuation() -> (
    None
):
    assert pr.restriction_result(RULE, ["continuing education due"])[0] is pr.Practice.ESTABLISHED
    assert pr.restriction_result(RULE, ["Continuing-Education_Due"])[0] is pr.Practice.ESTABLISHED


@pytest.mark.parametrize("condition", ["Supervised Practice Only", "no-independent-practice"])
def test_a_blocking_condition_closes_the_service(condition: str) -> None:
    practice, reasons, detail = pr.restriction_result(RULE, [condition])
    assert practice is pr.Practice.NOT_ESTABLISHED and reasons == ("CONDITION_BLOCKS_SERVICE",)
    assert detail["CONDITION_BLOCKS_SERVICE"] == (pr.fold(condition),)


def test_a_review_condition_needs_a_person_and_an_unknown_condition_is_never_permitted() -> None:
    assert pr.restriction_result(RULE, ["probation"])[:2] == (
        pr.Practice.REVIEW,
        ("CONDITION_NEEDS_REVIEW",),
    )
    practice, reasons, detail = pr.restriction_result(RULE, ["Mystery Clause"])
    assert (practice, reasons) == (pr.Practice.REVIEW, ("CONDITION_NOT_CLASSIFIED",))
    assert detail["CONDITION_NOT_CLASSIFIED"] == ("MYSTERY_CLAUSE",)


def test_the_worst_condition_wins_and_every_kind_is_reported() -> None:
    practice, reasons, _ = pr.restriction_result(
        RULE, ["probation", "Supervised Practice Only", "Mystery"]
    )
    assert practice is pr.Practice.NOT_ESTABLISHED
    assert reasons == (
        "CONDITION_BLOCKS_SERVICE",
        "CONDITION_NEEDS_REVIEW",
        "CONDITION_NOT_CLASSIFIED",
    )


# ---- insurance adequacy (T17)


def test_adequate_cover_passes() -> None:
    assert adequate() == (True, ())


def test_t17_cover_for_the_wrong_region_or_the_wrong_practice_is_insufficient_with_reasons() -> (
    None
):
    ok, reasons = adequate(region=["GB"])
    assert (
        not ok and "TERRITORY_NOT_COVERED" in reasons
    )  # the existing territory rule still applies
    ok, reasons = adequate(practice_class=["medical"])
    assert not ok and "PRACTICE_CLASS_NOT_COVERED" in reasons
    ok, reasons = adequate(practice_class=[])
    assert not ok and "PRACTICE_CLASS_NOT_COVERED" in reasons  # not reported is not covered


def test_the_limit_must_be_reported_in_the_required_currency_and_be_enough() -> None:
    assert adequate(coverage_limit=None)[1] == ("COVERAGE_LIMIT_NOT_REPORTED",)
    assert adequate(coverage_limit="not a number")[1] == ("COVERAGE_LIMIT_NOT_REPORTED",)
    assert adequate(coverage_limit="-5")[1] == ("COVERAGE_LIMIT_NOT_REPORTED",)
    assert adequate(currency="GBP", coverage_limit="9999999")[1] == (
        "COVERAGE_CURRENCY_MISMATCH",
    )  # never converted
    assert adequate(coverage_limit="999999")[1] == ("COVERAGE_LIMIT_BELOW_MINIMUM",)
    assert adequate(coverage_limit="1000000")[0] is True  # exactly the minimum is enough


def test_claims_made_cover_needs_a_retroactive_date_no_later_than_the_start_of_the_work() -> None:
    assert adequate(cover_basis="CLAIMS_MADE", retroactive_date="2020-01-01")[0] is True
    assert adequate(cover_basis="CLAIMS_MADE", retroactive_date=TODAY.isoformat())[0] is True
    assert adequate(cover_basis="CLAIMS_MADE", retroactive_date=None)[1] == (
        "RETROACTIVE_DATE_MISSING",
    )
    assert adequate(cover_basis="claims made", retroactive_date="2099-01-01")[1] == (
        "RETROACTIVE_DATE_AFTER_WORK_START",
    )


def test_a_missing_or_unknown_basis_is_not_reported_never_a_pass() -> None:
    assert adequate(cover_basis=None)[1] == ("COVER_BASIS_NOT_REPORTED",)
    assert adequate(cover_basis="WHATEVER")[1] == ("COVER_BASIS_NOT_REPORTED",)


def test_every_failure_is_reported_at_once() -> None:
    ok, reasons = adequate(
        coverage_limit="1",
        practice_class=["medical"],
        cover_basis="CLAIMS_MADE",
        retroactive_date="2099-01-01",
    )
    assert not ok and set(reasons) == {
        "COVERAGE_LIMIT_BELOW_MINIMUM",
        "PRACTICE_CLASS_NOT_COVERED",
        "RETROACTIVE_DATE_AFTER_WORK_START",
    }


# ---- qualification and practice stay separate (document 10.1)


def test_the_mandatory_counterexample_qualification_verified_practice_not_established() -> None:
    d = pr.decide(RULE, qualification_verified=True, eligible=False, conditions=[])
    assert (d.qualification, d.practice) == (pr.Qualification.VERIFIED, pr.Practice.NOT_ESTABLISHED)
    assert d.reasons == ("QUALIFICATION_VERIFIED", "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED")


def test_nothing_verified_and_not_eligible_does_not_claim_a_qualification() -> None:
    d = pr.decide(RULE, qualification_verified=False, eligible=False, conditions=[])
    assert (
        d.qualification is pr.Qualification.NOT_VERIFIED
        and "QUALIFICATION_VERIFIED" not in d.reasons
    )


def test_eligible_with_open_conditions_is_established_and_carries_no_reasons() -> None:
    d = pr.decide(
        RULE, qualification_verified=True, eligible=True, conditions=["continuing education due"]
    )
    assert (d.practice, d.reasons) == (pr.Practice.ESTABLISHED, ())


def test_eligible_but_a_blocking_condition_is_not_established_and_still_says_the_qualification_is_fine() -> (
    None
):
    d = pr.decide(
        RULE, qualification_verified=True, eligible=True, conditions=["Supervised Practice Only"]
    )
    assert d.practice is pr.Practice.NOT_ESTABLISHED
    assert d.reasons == (
        "QUALIFICATION_VERIFIED",
        "CONDITION_BLOCKS_SERVICE",
        "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED",
    )


# ---- applying a decision to an engagement result: never upgrade


def ready() -> el.EngagementOutcome:
    return el.EngagementOutcome(el.Engagement.READY, (), Eligibility.ELIGIBLE)


def test_not_established_blocks_a_ready_engagement_and_review_holds_it() -> None:
    blocked = pr.apply_to_engagement(
        ready(),
        pr.decide(
            RULE,
            qualification_verified=True,
            eligible=True,
            conditions=["supervised practice only"],
        ),
    )
    assert (
        blocked.engagement is el.Engagement.BLOCKED
        and "CONDITION_BLOCKS_SERVICE" in blocked.reasons
    )
    held = pr.apply_to_engagement(
        ready(),
        pr.decide(RULE, qualification_verified=True, eligible=True, conditions=["probation"]),
    )
    assert held.engagement is el.Engagement.HOLD and held.reasons == (
        "QUALIFICATION_VERIFIED",
        "CONDITION_NEEDS_REVIEW",
    )


def test_a_rule_never_upgrades_a_stricter_result() -> None:
    blocked = el.EngagementOutcome(
        el.Engagement.BLOCKED, ("RESTRICTION_ON_RECORD",), Eligibility.BLOCKED
    )
    review = pr.decide(RULE, qualification_verified=True, eligible=True, conditions=["probation"])
    assert pr.apply_to_engagement(blocked, review).engagement is el.Engagement.BLOCKED
    held = el.EngagementOutcome(
        el.Engagement.HOLD, ("IDENTITY_NOT_VERIFIED",), Eligibility.ELIGIBLE
    )
    out = pr.apply_to_engagement(
        held, pr.decide(RULE, qualification_verified=True, eligible=True, conditions=[])
    )
    assert out == held  # an established practice changes nothing


def test_reasons_are_not_repeated() -> None:
    held = el.EngagementOutcome(
        el.Engagement.HOLD, ("CONDITION_NEEDS_REVIEW",), Eligibility.ELIGIBLE
    )
    out = pr.apply_to_engagement(
        held, pr.decide(RULE, qualification_verified=True, eligible=True, conditions=["probation"])
    )
    assert out.reasons.count("CONDITION_NEEDS_REVIEW") == 1


# ---- configuration


DOC = {
    "rules": [
        {
            "rule_id": "r1",
            "version": 1,
            "policy_id": "p1",
            "conditions": {"permitted": ["a b"], "review": ["c"], "blocking": ["d"]},
            "insurance": {"min_limit": 500000, "currency": "usd", "practice_classes": ["legal"]},
        }
    ]
}


def test_a_rule_parses_with_folded_conditions_and_an_upper_case_currency() -> None:
    (r,) = pr.parse(DOC)
    assert (
        r.permitted == frozenset({"A_B"})
        and r.insurance is not None
        and r.insurance.currency == "USD"
    )
    assert (
        r.insurance.min_limit == Decimal(500000)
        and r.insurance.claims_made_needs_retroactive is True
    )


def test_no_rules_configured_is_an_empty_list() -> None:
    assert pr.parse({"rules": []}) == [] and pr.parse({}) == []


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d["rules"][0]["conditions"].update(
            blocking=["a b"]
        ),  # a condition in two classes
        lambda d: d["rules"][0]["insurance"].update(min_limit=0),
        lambda d: d["rules"][0]["insurance"].update(min_limit="lots"),
        lambda d: d["rules"][0].pop("policy_id"),
        lambda d: d["rules"].append(dict(d["rules"][0])),  # a duplicate version
    ],
)
def test_a_malformed_rule_is_refused_whole(mutate: Any) -> None:
    import copy

    doc = copy.deepcopy(DOC)
    mutate(doc)
    with pytest.raises(pr.PracticeError) as e:
        pr.parse(doc)
    assert e.value.code == "PRACTICE_RULE_INVALID"


def test_the_shipped_config_lists_no_rule_so_nothing_changes_until_the_owners_publish_one() -> None:
    from zid_platform.modules.credential.infrastructure import registries

    assert registries.practice_rules() == ()
    assert registries.find_practice_rule("sample-us-ca-law") is None
