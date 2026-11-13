"""Zoikorum deny/review fixtures through the real engagement path (ZID-RCG-003 RCG-G05; CR-076): licence conditions and cover adequacy read against a
published practice rule, qualification and practice kept apart, and no change at all while no rule is published."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from conftest import ctx, engine
from test_credential_trust_pg import RULE as TIER_RULE
from test_credential_trust_pg import check, license_claim
from zid_db import tenant_transaction
from zid_platform.modules.credential.application import claim_checks as checks
from zid_platform.modules.credential.application import trust_service as svc
from zid_platform.modules.credential.domain import eligibility as el
from zid_platform.modules.credential.domain import practice as pr
from zid_platform.modules.credential.infrastructure.registries import issuer_registry, policies

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)
POLICY = policies()[0][0]
REQUEST = el.EngagementRequest("US", "CA", "LAW", "legal.advice")
RULE = pr.PracticeRule(
    rule_id="sample-practice",
    version=1,
    policy_id=POLICY.policy_id,
    permitted=frozenset({"CONTINUING_EDUCATION_DUE"}),
    review=frozenset({"PROBATION"}),
    blocking=frozenset({"SUPERVISED_PRACTICE_ONLY"}),
    insurance=pr.InsuranceRequirement(Decimal(1_000_000), "USD", frozenset({"LEGAL"})),
)
HOLDER = checks.Holder("Dana Lee")


def uid() -> str:
    return str(uuid.uuid4())


def cover(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "insured_entity": "Dana Lee",
        "insurer": "Sample Indemnity Insurance",
        "coverage_type": "PI",
        "region": "US",
        "expires_on": "2028-01-01",
        "activities": "legal.advice",
        "coverage_limit": "2000000",
        "currency": "USD",
        "practice_class": "legal",
        "cover_basis": "OCCURRENCE",
    }
    base.update(over)
    return base


def licence(**over: Any) -> dict[str, Any]:
    return {**license_claim(), **over}


async def engage(
    c: Any,
    tenant: str,
    subject: str,
    *,
    practice: pr.PracticeRule | None = RULE,
    policy: el.EligibilityPolicy = POLICY,
    request: el.EngagementRequest = REQUEST,
) -> svc.EngagementResult:
    return await svc.evaluate_engagement(
        c,
        tenant_id=tenant,
        subject=subject,
        request=request,
        policy=policy,
        rule=TIER_RULE,
        registry=issuer_registry(),
        now=NOW,
        consumer="zoikorum",
        practice=practice,
    )


def scenario(rt_url: str, work: Any) -> Any:
    async def go() -> Any:
        async with engine(rt_url) as eng, tenant_transaction(eng, ctx(tenant := uid())) as c:
            return await work(c, tenant)

    return asyncio.run(go())


def test_a_licence_condition_that_closes_the_service_blocks_it_and_says_the_qualification_is_fine(
    rt_url: str,
) -> None:
    async def work(c: Any, tenant: str) -> tuple[Any, Any]:
        subject = uid()
        await check(
            c,
            tenant,
            subject,
            claimed=licence(good_standing="CONDITIONS", conditions=["Supervised Practice Only"]),
        )
        with_rule = await engage(c, tenant, subject)
        without = await engage(c, tenant, subject, practice=None)
        return with_rule, without

    with_rule, without = scenario(rt_url, work)
    assert with_rule.outcome.engagement is el.Engagement.BLOCKED
    assert {
        "QUALIFICATION_VERIFIED",
        "CONDITION_BLOCKS_SERVICE",
        "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED",
    } <= set(with_rule.outcome.reasons)
    # with no rule published the engagement is decided exactly as before: only identity and insurance hold it
    assert without.outcome.engagement is el.Engagement.HOLD
    assert not {"CONDITION_BLOCKS_SERVICE", "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED"} & set(
        without.outcome.reasons
    )


def test_a_condition_for_review_or_one_nobody_classified_holds_for_a_person(rt_url: str) -> None:
    async def work(c: Any, tenant: str) -> list[Any]:
        out = []
        for conditions in (["Probation"], ["A Mystery Clause"]):
            subject = uid()
            await check(
                c,
                tenant,
                subject,
                claimed=licence(good_standing="CONDITIONS", conditions=conditions),
            )
            out.append(await engage(c, tenant, subject))
        return out

    review, unknown = scenario(rt_url, work)
    assert (
        review.outcome.engagement is el.Engagement.HOLD
        and "CONDITION_NEEDS_REVIEW" in review.outcome.reasons
    )
    assert (
        unknown.outcome.engagement is el.Engagement.HOLD
        and "CONDITION_NOT_CLASSIFIED" in unknown.outcome.reasons
    )


def test_a_condition_the_rule_permits_changes_nothing(rt_url: str) -> None:
    async def work(c: Any, tenant: str) -> Any:
        subject = uid()
        await check(
            c,
            tenant,
            subject,
            claimed=licence(good_standing="CONDITIONS", conditions=["Continuing Education Due"]),
        )
        return await engage(c, tenant, subject)

    r = scenario(rt_url, work)
    assert not {
        "CONDITION_BLOCKS_SERVICE",
        "CONDITION_NEEDS_REVIEW",
        "CONDITION_NOT_CLASSIFIED",
    } & set(r.outcome.reasons)


def test_t16_a_licence_from_another_state_is_not_practice_here_but_the_qualification_stays_verified(
    rt_url: str,
) -> None:
    ny_policy = replace(POLICY, policy_id="sample-ny", subdivision="NY")
    ny_request = el.EngagementRequest("US", "NY", "LAW", "legal.advice")
    ny_rule = replace(RULE, policy_id="sample-ny")

    async def work(c: Any, tenant: str) -> Any:
        subject = uid()
        await check(c, tenant, subject)  # a verified California licence
        return await engage(
            c, tenant, subject, practice=ny_rule, policy=ny_policy, request=ny_request
        )

    r = scenario(rt_url, work)
    assert r.outcome.engagement is el.Engagement.BLOCKED
    assert "PROFESSIONAL_LICENSE_WRONG_JURISDICTION" in r.outcome.reasons
    assert (
        "QUALIFICATION_VERIFIED" in r.outcome.reasons
        and "PRACTICE_ELIGIBILITY_NOT_ESTABLISHED" in r.outcome.reasons
    )


def test_t17_cover_that_is_too_low_in_the_wrong_currency_or_for_the_wrong_practice_is_insufficient_with_reasons(
    rt_url: str,
) -> None:
    cases = {
        "COVERAGE_LIMIT_BELOW_MINIMUM": {"coverage_limit": "500000"},
        "COVERAGE_CURRENCY_MISMATCH": {"currency": "GBP"},
        "PRACTICE_CLASS_NOT_COVERED": {"practice_class": "medical"},
        "RETROACTIVE_DATE_MISSING": {"cover_basis": "CLAIMS_MADE"},
        "RETROACTIVE_DATE_AFTER_WORK_START": {
            "cover_basis": "CLAIMS_MADE",
            "retroactive_date": "2099-01-01",
        },
    }

    async def work(c: Any, tenant: str) -> dict[str, Any]:
        out = {}
        for reason, over in cases.items():
            subject = uid()
            await check(c, tenant, subject)
            await check(c, tenant, subject, "INSURANCE_ASSERTION", cover(**over), holder=HOLDER)
            out[reason] = await engage(c, tenant, subject)
        good = uid()
        await check(c, tenant, good)
        await check(c, tenant, good, "INSURANCE_ASSERTION", cover(), holder=HOLDER)
        out["GOOD"] = await engage(c, tenant, good)
        out["NO_RULE"] = await engage(c, tenant, good, practice=None)
        return out

    out = scenario(rt_url, work)
    for reason in cases:
        r = out[reason]
        assert r.outcome.engagement is el.Engagement.BLOCKED, reason
        assert {"INSURANCE_NOT_SATISFIED", reason} <= set(r.outcome.reasons), (
            reason,
            r.outcome.reasons,
        )
    good = out["GOOD"].outcome.reasons
    assert (
        "INSURANCE_NOT_SATISFIED" not in good and "INSURANCE_NOT_CHECKED" not in good
    )  # adequate cover passes
    assert (
        out["NO_RULE"].outcome.engagement is out["GOOD"].outcome.engagement
    )  # and a rule never loosens or tightens a good case
