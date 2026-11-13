"""ZID-COM-WF-2026-11 v3.0: the service families, the "sellable in this market?" decision and the chargeability table (CW-02, CW-03, CW-04, CW-08).

CAT-01 different families have different meters and units    CAT-03 domestic issuer allowed, foreign payer restricted
CAT-04 subject, payer and region are judged separately       CAT-06 no signed price: request a quote, never an invented price
CAT-12 a retry carries one operation id and yields one event CAT-14 abandoned versus completed     CAT-15 manual review only if authorized
CAT-16 a re-screen is a new operation                        INV-03 one operation, at most one canonical event      INV-12 sandbox is never billable
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest
import yaml
from zid_platform.modules.commercial.domain import chargeability as ch
from zid_platform.modules.commercial.domain import market_availability as av
from zid_platform.modules.commercial.domain import service_families as sf
from zid_platform.modules.commercial.infrastructure.bands import meter_register
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

FILE = Path(__file__).resolve().parents[3] / "config" / "commercial" / "service-families.yaml"


def doc() -> dict:
    return yaml.safe_load(FILE.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------------------------------------------- the sixteen families


def test_the_shipped_catalogue_has_exactly_the_sixteen_families_each_with_its_own_unit() -> None:
    reg = family_registry()
    assert set(reg.families) == sf.EXPECTED_CODES and len(reg.families) == 16
    assert all(f.unit for f in reg.families.values())
    assert reg.status == "PROPOSED", "a family is a proposal, never a price or a launch SKU"
    assert len({f.unit for f in reg.families.values()}) == 16, (
        "no two families share a unit (CAT-01)"
    )


def test_every_meter_a_family_names_is_in_the_ratified_meter_register() -> None:
    names = set(meter_register())
    assert all(f.meter in names for f in family_registry().families.values() if f.meter)


def test_a_family_with_no_meter_is_simply_not_billed_and_no_meter_is_invented() -> None:
    unmetered = {c for c, f in family_registry().families.items() if f.meter is None}
    assert {"DOC", "BIO", "SCR", "SIGN", "SOV", "REV"} <= unmetered


def test_the_catalogue_carries_no_price_anywhere() -> None:
    flat = FILE.read_text(encoding="utf-8").lower()
    assert "price:" not in flat and "rate:" not in flat and "$" not in flat.replace("# ", "")


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (lambda d: d["families"].pop(), "FAMILY_SET_CHANGED"),
        (lambda d: d["families"].append({**d["families"][0], "code": "NEW"}), "FAMILY_SET_CHANGED"),
        (lambda d: d["families"][1].update(code="IDV"), "FAMILY_CODE_INVALID_OR_DUPLICATE"),
        (lambda d: d["families"][0].update(unit=""), "FAMILY_UNIT_MISSING"),
        (
            lambda d: d["families"][0].update(meter="not.a.meter"),
            "FAMILY_METER_NOT_IN_THE_REGISTER",
        ),
        (lambda d: d["families"][0].update(depends_on=["GHOST"]), "FAMILY_DEPENDENCY_UNKNOWN"),
        (lambda d: d["families"][0].update(depends_on=["IDV"]), "FAMILY_DEPENDS_ON_ITSELF"),
        (lambda d: d["families"][0].update(regulated="yes"), "FAMILY_FLAG_NOT_A_BOOLEAN"),
        (lambda d: d.pop("families"), "FAMILIES_MISSING"),
    ],
)
def test_a_catalogue_that_loses_or_adds_a_family_or_breaks_a_rule_does_not_load(
    change, code
) -> None:
    d = copy.deepcopy(doc())
    change(d)
    with pytest.raises(sf.FamilyError) as e:
        sf.parse(d, known_meters=set(meter_register()))
    assert e.value.code == code


def test_a_dependency_cycle_is_refused() -> None:
    d = copy.deepcopy(doc())
    by = {f["code"]: f for f in d["families"]}
    by["IDV"]["depends_on"] = ["SIGN"]  # SIGN already depends on IDV
    with pytest.raises(sf.FamilyError) as e:
        sf.parse(d)
    assert e.value.code == "FAMILY_DEPENDENCY_CYCLE"


def test_a_signature_without_a_verified_identity_cannot_be_configured() -> None:
    reg = family_registry()
    (problem,) = sf.check_selection(reg, ["SIGN"])
    assert (problem.code, problem.family, problem.needs) == ("MISSING_DEPENDENCY", "SIGN", "IDV")
    assert sf.check_selection(reg, ["SIGN", "IDV"]) == ()


def test_unknown_and_empty_selections_are_named() -> None:
    reg = family_registry()
    assert [p.code for p in sf.check_selection(reg, [])] == ["NOTHING_SELECTED"]
    assert [(p.code, p.family) for p in sf.check_selection(reg, ["IDV", "GHOST"])] == [
        ("UNKNOWN_FAMILY", "GHOST")
    ]


# ---------------------------------------------------------------------------------------------------------------- sellable in a market


ALL_TRUE = av.Facts(True, True, True, True, True, True, True, True, True)
SIGN = family_registry().families["SIGN"]
API = family_registry().families["API"]


def everywhere(facts: av.Facts) -> dict[str, av.Facts]:
    return {"subject": facts, "payer": facts, "operating": facts}


def test_with_no_recorded_fact_nothing_is_sellable_and_the_answer_is_request_a_quote() -> None:
    v = av.decide(API, {})
    assert v.sellable is False and v.next_action == "REQUEST_QUOTE"
    assert {r.code for r in v.refusals} == {
        "SUBJECT_NOT_EVALUATED",
        "PAYER_NOT_EVALUATED",
        "OPERATING_NOT_EVALUATED",
    }


def test_an_unknown_fact_is_a_refusal_never_a_pass() -> None:
    v = av.decide(API, everywhere(av.Facts()))
    assert v.sellable is False
    assert {
        "OFFER_EFFECTIVE_UNKNOWN",
        "SELLER_ENTITY_UNKNOWN",
        "TAX_UNKNOWN",
        "PAYMENT_UNKNOWN",
    } <= {r.code for r in v.refusals}


def test_every_recorded_fact_in_every_dimension_makes_a_family_sellable() -> None:
    v = av.decide(API, everywhere(ALL_TRUE))
    assert v.sellable is True and v.next_action == "SELECT_PLAN" and v.refusals == ()


def test_cat03_an_allowed_domestic_issuer_does_not_make_a_restricted_foreign_payer_sellable() -> (
    None
):
    restricted = av.Facts(
        offer_effective=True, seller_entity_approved=False, merchant_route_supported=True, provider_entitled=True,
        legal_basis_documented=True, regulated_status_proven=True, payment_configured=True, tax_configured=True,
        release_evidence_executed=True,
    )  # fmt: skip
    v = av.decide(API, {"subject": ALL_TRUE, "payer": restricted, "operating": ALL_TRUE})
    assert v.sellable is False
    assert [(r.code, r.dimension) for r in v.refusals] == [("SELLER_ENTITY_NOT_SATISFIED", "payer")]


def test_cat04_subject_payer_and_region_are_judged_separately_and_a_missing_one_blocks_the_sale() -> (
    None
):
    v = av.decide(API, {"subject": ALL_TRUE, "payer": ALL_TRUE})
    assert v.sellable is False and [r.code for r in v.refusals] == ["OPERATING_NOT_EVALUATED"]


def test_a_refusal_names_its_cause_so_the_screen_can_say_why() -> None:
    facts = av.Facts(offer_effective=True, seller_entity_approved=True, merchant_route_supported=True,
                     provider_entitled=False, legal_basis_documented=None, regulated_status_proven=False,
                     payment_configured=True, tax_configured=False, release_evidence_executed=False)  # fmt: skip
    v = av.decide(SIGN, everywhere(facts))
    assert set(v.causes) == {"PROVIDER", "LEGAL", "TAX_PAYMENT", "PRODUCTION"}


def test_inv11_a_regulated_family_needs_its_regulated_status_proven_but_an_ordinary_one_does_not() -> (
    None
):
    no_status = av.Facts(
        **{f: True for f in ALL_TRUE.__slots__ if f != "regulated_status_proven"},
        regulated_status_proven=None,
    )
    assert av.decide(SIGN, everywhere(no_status)).sellable is False
    assert av.decide(API, everywhere(no_status)).sellable is True


def test_a_family_that_needs_no_provider_does_not_wait_for_provider_rights() -> None:
    no_provider = av.Facts(
        **{f: True for f in ALL_TRUE.__slots__ if f != "provider_entitled"}, provider_entitled=None
    )
    assert av.decide(API, everywhere(no_provider)).sellable is True
    assert av.decide(family_registry().families["IDV"], everywhere(no_provider)).sellable is False


# ---------------------------------------------------------------------------------------------------------------------- chargeability

LIVE = "LIVE"
FULL = ch.SkuPolicy(
    unit_disclosed=True, manual_review_priced=True, pass_through_contracted=True,
    monitoring_schedule="monthly", verification_meter=True, issuance_meter=True,
)  # fmt: skip


@pytest.mark.parametrize("env", ["TEST", "SANDBOX", "", "live"])
def test_inv12_nothing_outside_live_is_ever_billable(env) -> None:
    for scenario in ch.Scenario:
        assert (
            ch.decide(scenario, environment=env, policy=FULL, entitlement_in_force=True).billable
            is False
        )


def test_cat14_an_abandoned_session_is_not_an_attempt_and_a_completed_check_is_charged_whatever_the_decision() -> (
    None
):
    assert (
        ch.decide(
            ch.Scenario.ABANDONED_BEFORE_PROVIDER_REQUEST, environment=LIVE, policy=FULL
        ).billable
        is False
    )
    done = ch.decide(ch.Scenario.IDENTITY_COMPLETED, environment=LIVE, policy=FULL)
    assert done.billable is True and done.treatment == "CHARGE"
    assert (
        ch.decide(ch.Scenario.IDENTITY_COMPLETED, environment=LIVE, policy=ch.SkuPolicy()).billable
        is False
    ), "an undisclosed policy charges nothing"


def test_a_provider_failure_or_internal_retry_is_never_charged() -> None:
    assert (
        ch.decide(
            ch.Scenario.PROVIDER_FAILURE_OR_INTERNAL_RETRY, environment=LIVE, policy=FULL
        ).billable
        is False
    )


def test_cat12_and_inv03_any_number_of_retries_of_one_operation_is_one_chargeable_event() -> None:
    attempts = [
        ("op-1", False),
        ("op-1", False),
        ("op-1", True),
        ("op-1", True),
        ("op-2", True),
        ("op-3", False),
    ]
    assert ch.chargeable_events(attempts) == 2
    assert ch.chargeable_events([("op-1", True)] * 50) == 1
    assert ch.chargeable_events([]) == 0


def test_cat15_manual_review_is_charged_only_when_priced_and_instructed() -> None:
    assert (
        ch.decide(ch.Scenario.MANUAL_ADJUDICATION, environment=LIVE, policy=ch.SkuPolicy()).billable
        is False
    )
    assert (
        ch.decide(ch.Scenario.MANUAL_ADJUDICATION, environment=LIVE, policy=FULL).billable is True
    )


def test_a_pass_through_lookup_needs_its_contract_terms() -> None:
    assert (
        ch.decide(
            ch.Scenario.PASS_THROUGH_LOOKUP,
            environment=LIVE,
            policy=ch.SkuPolicy(unit_disclosed=True),
        ).billable
        is False
    )
    assert (
        ch.decide(ch.Scenario.PASS_THROUGH_LOOKUP, environment=LIVE, policy=FULL).billable is True
    )


def test_cat16_a_rescreen_is_a_new_operation_with_its_own_schedule_and_entitlement() -> None:
    same_op = ch.decide(
        ch.Scenario.RESCREEN_OR_RENEWAL,
        environment=LIVE,
        policy=FULL,
        new_operation=False,
        entitlement_in_force=True,
    )
    assert same_op.billable is False and same_op.treatment == "NEW_OPERATION_REQUIRED"
    no_entitlement = ch.decide(
        ch.Scenario.ONGOING_MONITORING, environment=LIVE, policy=FULL, entitlement_in_force=False
    )
    assert no_entitlement.billable is False
    no_schedule = ch.decide(
        ch.Scenario.ONGOING_MONITORING,
        environment=LIVE,
        policy=ch.SkuPolicy(unit_disclosed=True),
        entitlement_in_force=True,
    )
    assert no_schedule.billable is False
    assert (
        ch.decide(
            ch.Scenario.ONGOING_MONITORING, environment=LIVE, policy=FULL, entitlement_in_force=True
        ).billable
        is True
    )


def test_verification_and_issuance_are_metered_separately_and_only_when_ratified() -> None:
    assert (
        ch.decide(
            ch.Scenario.CREDENTIAL_VERIFICATION, environment=LIVE, policy=ch.SkuPolicy()
        ).billable
        is False
    )
    assert (
        ch.decide(
            ch.Scenario.CREDENTIAL_ISSUANCE,
            environment=LIVE,
            policy=ch.SkuPolicy(verification_meter=True),
        ).billable
        is False
    )
    assert (
        ch.decide(
            ch.Scenario.CREDENTIAL_VERIFICATION,
            environment=LIVE,
            policy=ch.SkuPolicy(verification_meter=True),
        ).billable
        is True
    )


def test_a_cancelled_or_disputed_operation_is_corrected_by_a_linked_adjustment_never_rewritten() -> (
    None
):
    d = ch.decide(ch.Scenario.CANCELLED_REVERSED_OR_DISPUTED, environment=LIVE, policy=FULL)
    assert d.billable is False and d.treatment == "ADJUSTMENT"


def test_the_default_policy_charges_nothing_anywhere() -> None:
    for scenario in ch.Scenario:
        assert ch.decide(scenario, environment=LIVE, policy=ch.SkuPolicy()).billable is False
