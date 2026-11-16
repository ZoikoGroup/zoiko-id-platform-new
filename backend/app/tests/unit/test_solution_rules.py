"""The solution builder's rules (ZID-COM-WF-2026-11 WF-C01, WF-C02): volumes, the canonical scope hash and the offer-card states."""

from __future__ import annotations

import pytest
from zid_platform.modules.commercial.domain import market_availability as av
from zid_platform.modules.commercial.domain import solution as sol
from zid_platform.modules.commercial.infrastructure.family_registry import family_registry

REG = family_registry()
API = REG.families["API"]


def verdict(*causes: av.Cause) -> av.Verdict:
    return av.Verdict(
        "API", not causes, tuple(av.Refusal(f"X_{c.value}", c, "subject") for c in causes)
    )


def test_volumes_are_whole_non_negative_numbers_for_selected_families_only() -> None:
    ok = sol.normalise_volumes(
        {"IDV": {"rechecks_per_year": 2, "expected_units": 500}}, ["IDV", "API"]
    )
    assert ok == {"IDV": {"expected_units": 500, "rechecks_per_year": 2}}
    for bad, code in (
        ({"DOC": {"expected_units": 1}}, "VOLUME_FAMILY_NOT_SELECTED"),
        ({"IDV": {"price": 1}}, "VOLUME_FIELD_UNKNOWN"),
        ({"IDV": {"expected_units": -1}}, "VOLUME_INVALID"),
        ({"IDV": {"expected_units": 1.5}}, "VOLUME_INVALID"),
        ({"IDV": {"expected_units": True}}, "VOLUME_INVALID"),
        ({"IDV": {"expected_units": 10**10}}, "VOLUME_INVALID"),
    ):
        with pytest.raises(sol.SolutionError) as e:
            sol.normalise_volumes(bad, ["IDV", "API"])
        assert e.value.code == code


def test_the_scope_hash_is_stable_order_independent_and_sensitive_to_every_field() -> None:
    def h(**over: object) -> str:
        base: dict[str, object] = {
            "purpose": "ONBOARDING", "families": ["IDV", "API"], "subject_country": "DE", "payer_country": "DE",
            "operating_region": "eu-central", "volumes": {"IDV": {"expected_units": 5}},
        }  # fmt: skip
        base.update(over)
        return sol.scope_hash(sol.scope(**base))  # type: ignore[arg-type]

    assert h() == h(families=["API", "IDV", "IDV"]), "the same configuration, however it was listed"
    assert len(h()) == 64
    for change in (
        {"purpose": "COMPLIANCE"}, {"families": ["IDV"]}, {"subject_country": "FR"}, {"payer_country": "US"},
        {"operating_region": "us-east"}, {"volumes": {"IDV": {"expected_units": 6}}}, {"payer_country": None},
    ):  # fmt: skip
        assert h(**change) != h(), change


def test_an_unknown_purpose_is_refused() -> None:
    with pytest.raises(sol.SolutionError) as e:
        sol.scope("FUN", ["IDV"], None, None, None, {})
    assert e.value.code == "PURPOSE_UNKNOWN"


def test_a_card_says_available_only_when_every_fact_holds() -> None:
    assert sol.card_state(API, verdict(), market_named=True, row_recorded=True) == ("AVAILABLE", [])


def test_with_no_market_named_the_card_asks_for_one() -> None:
    state, reasons = sol.card_state(
        API, verdict(av.Cause.COUNTRY), market_named=False, row_recorded=False
    )
    assert state == "MARKET_REQUIRED" and reasons[0]["cause"] == "COUNTRY"


def test_the_reasons_are_told_apart_and_nobody_is_called_ineligible_without_a_recorded_no() -> None:
    # nothing recorded for the family at all: nobody has said no, so ask
    assert (
        sol.card_state(
            API,
            verdict(av.Cause.CATALOGUE, av.Cause.PROVIDER),
            market_named=True,
            row_recorded=False,
        )[0]
        == "CONTACT_SALES"
    )
    # a row exists and a right or the legal basis is what is missing: not eligible, with the cause named
    state, reasons = sol.card_state(
        API, verdict(av.Cause.PROVIDER), market_named=True, row_recorded=True
    )
    assert state == "NOT_ELIGIBLE" and [r["cause"] for r in reasons] == ["PROVIDER"]
    assert (
        sol.card_state(API, verdict(av.Cause.LEGAL), market_named=True, row_recorded=True)[0]
        == "NOT_ELIGIBLE"
    )
    # only commercial set-up missing: a conversation, not a refusal
    assert (
        sol.card_state(
            API,
            verdict(av.Cause.CONTRACT, av.Cause.TAX_PAYMENT),
            market_named=True,
            row_recorded=True,
        )[0]
        == "CONTACT_SALES"
    )
    # only the release evidence missing: designed, not released
    assert (
        sol.card_state(API, verdict(av.Cause.PRODUCTION), market_named=True, row_recorded=True)[0]
        == "RESEARCH_ONLY"
    )


def test_every_cause_has_plain_text_and_no_price_wording() -> None:
    assert set(sol.CAUSE_TEXT) == {c.value for c in av.Cause}
    assert all("$" not in t and "price of" not in t.lower() for t in sol.CAUSE_TEXT.values())


def test_a_quote_binds_the_legal_buyer_the_payer_the_matrix_version_and_every_released_term() -> (
    None
):
    terms = [
        {
            "family": "IDV",
            "seller_entity": "Zoiko Tech GmbH",
            "merchant_route": "stripe-eu",
            "currency": "EUR",
            "tax_class": "B2B",
            "data_region": "eu-central",
        },
        {
            "family": "API",
            "seller_entity": "Zoiko Tech GmbH",
            "merchant_route": "stripe-eu",
            "currency": "EUR",
            "tax_class": "B2B",
            "data_region": "eu-central",
        },
    ]

    def bind(**over: object) -> dict[str, object]:
        base: dict[str, object] = {
            "legal_buyer": "t-1",
            "scope_digest": "s" * 64,
            "payer_country": "DE",
            "matrix_version": 4,
            "terms": terms,
        }
        return sol.commercial_binding(**{**base, **over})  # type: ignore[arg-type]

    base = bind()
    assert (
        len(str(base["binding_hash"])) == 64
        and bind(terms=list(reversed(terms)))["binding_hash"] == base["binding_hash"]
    ), "order does not matter"
    changed = {
        "buyer": bind(legal_buyer="t-2"),
        "payer": bind(payer_country="FR"),
        "version": bind(matrix_version=5),
        "scope": bind(scope_digest="x" * 64),
        "seller": bind(terms=[{**terms[0], "seller_entity": "Other Ltd"}, terms[1]]),
        "route": bind(terms=[{**terms[0], "merchant_route": "adyen"}, terms[1]]),
        "tax": bind(terms=[{**terms[0], "tax_class": "B2C"}, terms[1]]),
        "currency": bind(terms=[{**terms[0], "currency": "GBP"}, terms[1]]),
    }
    for name, other in changed.items():
        assert other["binding_hash"] != base["binding_hash"], (
            f"a change of {name} must be a different quote"
        )
