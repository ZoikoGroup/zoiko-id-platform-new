"""The staged rollout of a market (ZID-COM-WF-2026-11 WF-I05): one stage at a time, a different person each step, stopping is never held up."""

from __future__ import annotations

import pytest
from zid_platform.modules.commercial.domain import market_availability as av
from zid_platform.modules.commercial.domain import market_matrix as mm
from zid_platform.modules.commercial.domain import rollout as ro
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

REG = family_registry()


def refused(code: str, *args: object) -> None:
    with pytest.raises(ro.RolloutError) as e:
        ro.check_move(*args)  # type: ignore[arg-type]
    assert e.value.code == code, e.value


def test_a_market_starts_in_the_sandbox_and_goes_one_stage_at_a_time() -> None:
    ro.check_move(None, None, "SANDBOX", "a", None)
    refused("ROLLOUT_STAGE_SKIP", None, None, "PRODUCTION", "a", None)
    last = None
    for current, target, who in (
        ("SANDBOX", "STAGING", "b"),
        ("STAGING", "PILOT", "a"),
        ("PILOT", "CANARY", "b"),
        ("CANARY", "PRODUCTION", "a"),
    ):
        ro.check_move(current, None, target, who, last)
        last = who
    refused("ROLLOUT_STAGE_SKIP", "SANDBOX", None, "PILOT", "b", "a")
    refused("ROLLOUT_STAGE_SKIP", "PILOT", None, "STAGING", "b", "a")
    refused("ROLLOUT_AT_END", "PRODUCTION", None, "PRODUCTION", "b", "a")


def test_the_person_who_made_the_last_move_cannot_make_the_next() -> None:
    refused("ROLLOUT_SAME_PERSON", "STAGING", None, "PILOT", "a", "a")
    ro.check_move("STAGING", None, "PILOT", "b", "a")


def test_pausing_and_rolling_back_need_no_second_person_but_need_something_running() -> None:
    ro.check_move("CANARY", None, "PAUSED", "a", "a")
    ro.check_move("PRODUCTION", None, "ROLLED_BACK", "a", "a")
    refused("ROLLOUT_NOT_STARTED", None, None, "PAUSED", "a", None)
    refused("ROLLOUT_ALREADY_IN_STAGE", "PAUSED", "CANARY", "PAUSED", "a", "b")
    refused("ROLLOUT_NOT_LIVE", "ROLLED_BACK", None, "PAUSED", "a", "b")
    ro.check_move("PAUSED", "CANARY", "ROLLED_BACK", "a", "a")


def test_a_paused_market_resumes_only_where_it_was_and_a_rolled_back_one_restarts() -> None:
    ro.check_move("PAUSED", "CANARY", "CANARY", "b", "a")
    refused("ROLLOUT_STAGE_SKIP", "PAUSED", "CANARY", "PRODUCTION", "b", "a")
    refused("ROLLOUT_SAME_PERSON", "PAUSED", "CANARY", "CANARY", "a", "a")
    ro.check_move("ROLLED_BACK", None, "SANDBOX", "b", "a")
    refused("ROLLOUT_STAGE_SKIP", "ROLLED_BACK", None, "STAGING", "b", "a")
    refused("ROLLOUT_STAGE_UNKNOWN", "SANDBOX", None, "LAUNCH", "b", "a")


def test_only_canary_and_production_count_as_released() -> None:
    assert [s for s in (*ro.STAGES, *ro.HOLDS, None) if ro.is_live(s)] == ["CANARY", "PRODUCTION"]


def sold(stage: str | None) -> bool:
    row = mm.Row(
        family="API", country="DE", seller_entity="Zoiko Tech GmbH", merchant_route="stripe-eu", currency="EUR", tax_class="B2B", data_region="eu-central",
        offer_ref="o", legal_basis_ref="l", release_evidence_ref="r", payment_configured=True, tax_configured=True, stage=stage,
    )  # fmt: skip
    from datetime import date

    facts = mm.facts_by_dimension(
        {("API", "DE"): row},
        REG,
        "API",
        subject_country="DE",
        payer_country="DE",
        operating_region="eu-central",
        on=date(2026, 11, 13),
    )
    verdict = av.decide(REG.families["API"], facts)
    return verdict.sellable


def test_a_market_is_sellable_only_in_canary_or_production_and_stopping_it_stops_the_sale() -> None:
    assert [
        s
        for s in (
            None,
            "SANDBOX",
            "STAGING",
            "PILOT",
            "CANARY",
            "PRODUCTION",
            "PAUSED",
            "ROLLED_BACK",
        )
        if sold(s)
    ] == ["CANARY", "PRODUCTION"]
