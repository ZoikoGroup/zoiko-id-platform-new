"""Automated-decision guards around counsel's rules and the routing of provider outcomes (ZID-PRI-004 sections 10, 14, 22).

These tests do NOT assert what any law requires. They assert how the platform behaves around rules counsel supplies: no rules means blocked and routed
to a person; rules in force mean every safeguard counsel names is checked. The rule sets below are test fixtures, not legal content.
"""

from __future__ import annotations

from datetime import date

import pytest
import yaml
from zid_platform.modules.privacy.domain import adm
from zid_platform.modules.privacy.domain import outcome_routing as orr
from zid_platform.modules.privacy.infrastructure import packs as pk

TODAY = date(2026, 11, 12)
FIXTURE = {
    "required_safeguards": ["SAFEGUARD_A", "SAFEGUARD_B"],
    "approval_ref": "COUNSEL-FIXTURE-1",
    "effective_from": "2026-01-01",
}


def ev(rules, **over):
    args = {
        "pack_id": "uk-gdpr", "significant": True, "solely_automated": True, "special_category": False,
        "safeguards_offered": [], "special_condition_ref": None, "on": TODAY,
    }  # fmt: skip
    return adm.evaluate(rules, **{**args, **over})


def test_the_shipped_adm_rules_are_all_counsel_pending_so_every_significant_solely_automated_decision_is_blocked() -> (
    None
):
    from zid_platform.modules.privacy.application import control

    doc = yaml.safe_load((control.CONFIG / "adm-rules.yaml").read_text(encoding="utf-8"))
    assert (
        {"uk-gdpr", "eu-gdpr", "us-ca-ccpa"}
        <= set(doc["packs"])
        <= set(pk.packs()) | {"us-ca-ccpa"}
    )
    for pack_id, rules in doc["packs"].items():
        out = ev(rules, pack_id=pack_id, safeguards_offered=["SAFEGUARD_A", "SAFEGUARD_B"])
        assert out.allowed is False and out.route_to_person is True
        assert out.reasons == ("ADM_RULES_NOT_SET_BY_COUNSEL",), (
            "an engineer's guess is never a rule"
        )


def test_at032_at033_rules_without_approval_or_a_date_that_has_arrived_do_not_count() -> None:
    assert ev(None).reasons == ("ADM_RULES_NOT_SET_BY_COUNSEL",)
    assert ev({**FIXTURE, "approval_ref": None}).reasons == ("ADM_RULES_NOT_APPROVED",)
    assert ev({**FIXTURE, "effective_from": "COUNSEL"}).reasons == (
        "ADM_RULES_HAVE_NO_EFFECTIVE_DATE",
    )
    assert ev({**FIXTURE, "effective_from": "2027-01-01"}).reasons == (
        "ADM_RULES_NOT_YET_IN_FORCE",
    ), "staged provisions: no premature assumption (AT-036 mechanism)"
    assert ev({**FIXTURE, "effective_until": "2026-06-30"}).reasons == (
        "ADM_RULES_NO_LONGER_IN_FORCE",
    )
    for out in (
        ev({**FIXTURE, "effective_from": "2027-01-01"}),
        ev({**FIXTURE, "approval_ref": None}),
    ):
        assert out.allowed is False and out.route_to_person is True


def test_at032_at033_with_rules_in_force_every_safeguard_counsel_names_must_be_offered() -> None:
    none = ev(FIXTURE)
    assert none.allowed is False and none.missing_safeguards == ("SAFEGUARD_A", "SAFEGUARD_B")
    half = ev(FIXTURE, safeguards_offered=["SAFEGUARD_A"])
    assert half.allowed is False and half.missing_safeguards == ("SAFEGUARD_B",)
    full = ev(FIXTURE, safeguards_offered=["SAFEGUARD_A", "SAFEGUARD_B", "EXTRA"])
    assert (
        full.allowed is True
        and full.outcome == "PERMITTED_WITH_SAFEGUARDS"
        and full.rules_ref == "uk-gdpr@COUNSEL-FIXTURE-1"
    )


def test_a_special_category_decision_needs_the_recorded_condition_when_counsel_requires_one() -> (
    None
):
    offered = ["SAFEGUARD_A", "SAFEGUARD_B"]
    no = ev(FIXTURE, special_category=True, safeguards_offered=offered)
    assert no.allowed is False and "SPECIAL_CATEGORY_CONDITION_NOT_RECORDED" in no.reasons
    yes = ev(
        FIXTURE, special_category=True, safeguards_offered=offered, special_condition_ref="COND-9"
    )
    assert yes.allowed is True
    relaxed = ev(
        {**FIXTURE, "special_category_condition_required": False},
        special_category=True,
        safeguards_offered=offered,
    )
    assert relaxed.allowed is True


def test_a_decision_that_is_not_significant_or_not_solely_automated_is_not_blocked_by_this_check() -> (
    None
):
    assert (
        ev(None, significant=False).outcome == "NOT_SIGNIFICANT"
        and ev(None, significant=False).allowed
    )
    assert ev(None, solely_automated=False).outcome == "NOT_SOLELY_AUTOMATED"


@pytest.mark.parametrize("pad", [None, "UNKNOWN", "ELEVATED", "HIGH_RISK"])
def test_at010_a_doubtful_pad_band_or_an_unknown_one_goes_to_a_person_never_to_approval(
    pad,
) -> None:
    r = orr.route(
        match_band="MATCH",
        pad_band=pad,
        injection_suspected=False,
        capture_quality_ok=True,
        high_impact=False,
    )
    assert r.route == "HUMAN_REVIEW" and r.offer_alternative and r.offer_correction_route


def test_at010_an_injected_feed_goes_to_a_person_even_with_a_perfect_match() -> None:
    r = orr.route(
        match_band="MATCH",
        pad_band="LOW_RISK",
        injection_suspected=True,
        capture_quality_ok=True,
        high_impact=False,
    )
    assert r.route == "HUMAN_REVIEW" and "INJECTED_MEDIA_SUSPECTED" in r.reasons


def test_at012_a_poor_capture_is_a_retry_or_an_alternative_never_fraud_or_a_penalty() -> None:
    r = orr.route(
        match_band=None,
        pad_band="LOW_RISK",
        injection_suspected=False,
        capture_quality_ok=False,
        high_impact=False,
    )
    assert (
        r.route == "RETRY_OR_ALTERNATIVE"
        and r.offer_alternative
        and r.penalty is False
        and r.may_say_fraud is False
    )


def test_at013_a_high_impact_decision_is_held_for_a_person_whichever_way_the_match_points() -> None:
    for band in ("MATCH", "NO_MATCH", "UNCERTAIN"):
        r = orr.route(
            match_band=band,
            pad_band="LOW_RISK",
            injection_suspected=False,
            capture_quality_ok=True,
            high_impact=True,
        )
        assert r.route == "HUMAN_REVIEW" and r.offer_correction_route is True


def test_at014_a_non_match_is_a_retry_or_a_contest_never_a_refusal() -> None:
    r = orr.route(
        match_band="NO_MATCH",
        pad_band="LOW_RISK",
        injection_suspected=False,
        capture_quality_ok=True,
        high_impact=False,
    )
    assert r.route == "RETRY_OR_ALTERNATIVE" and r.offer_correction_route and r.penalty is False


def test_a_match_alone_only_continues_to_the_next_separate_check_and_nothing_routes_to_approval_or_refusal() -> (
    None
):
    r = orr.route(
        match_band="MATCH",
        pad_band="LOW_RISK",
        injection_suspected=False,
        capture_quality_ok=True,
        high_impact=False,
    )
    assert r.route == "CONTINUE_TO_NEXT_CHECK" and r.reasons == ("MATCH_IS_NOT_APPROVAL",)
    seen = {
        orr.route(match_band=m, pad_band=p, injection_suspected=i, capture_quality_ok=q, high_impact=h).route
        for m in (*orr.MATCH, None) for p in (*orr.PAD, None) for i in (False, True) for q in (False, True) for h in (False, True)
    }  # fmt: skip
    assert seen <= {"CONTINUE_TO_NEXT_CHECK", "HUMAN_REVIEW", "RETRY_OR_ALTERNATIVE"}, (
        "there is no approve or refuse route to reach"
    )
