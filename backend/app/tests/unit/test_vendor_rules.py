"""Vendor costs, retry billing, spikes and reconciliation (ZID-COM-001 §7, §8, §31; CR-029). Pure rules."""

from __future__ import annotations

from decimal import Decimal

import pytest
from zid_platform.modules.commercial.domain import vendor as v

D = Decimal
RATE = v.Rate(D("1.20"), D("0.40"), "USD", "SIGNED_TARIFF")


def test_a_provider_or_platform_error_is_costed_but_never_charged() -> None:
    for outcome in ("PROVIDER_ERROR", "PLATFORM_ERROR"):
        a = v.price_attempt(RATE, outcome, 1)
        assert not a.chargeable and a.charge_basis == "NONE" and a.cost == D("1.20")


def test_first_completed_check_is_base_and_retries_are_retry_priced() -> None:
    assert v.price_attempt(RATE, "COMPLETED", 1) == v.Attempt(
        "COMPLETED", 1, D("1.20"), True, "BASE"
    )
    assert v.price_attempt(RATE, "COMPLETED", 2).charge_basis == "RETRY"
    assert v.price_attempt(RATE, "CUSTOMER_RETRY", 1).cost == D("0.40")
    assert v.price_attempt(RATE, "INSUFFICIENT_DATA", 1) == v.Attempt(
        "INSUFFICIENT_DATA", 1, D("0.40"), True, "RETRY"
    )
    with pytest.raises(v.VendorError):
        v.price_attempt(RATE, "MADE_UP", 1)
    with pytest.raises(v.VendorError):
        v.price_attempt(RATE, "COMPLETED", 0)


def test_classification_puts_fault_where_it_belongs() -> None:
    assert v.classify("FAILED", ["PROVIDER_UNAVAILABLE"]) == "PROVIDER_ERROR"
    assert v.classify("FAILED", ["INSUFFICIENT_DATA"]) == "INSUFFICIENT_DATA"
    assert v.classify("VERIFIED", []) == "COMPLETED"
    assert (
        v.classify("FAILED", ["DOCUMENT_FORGED"]) == "COMPLETED"
    )  # a definite answer was delivered


def test_spikes_need_a_configured_threshold() -> None:
    assert v.is_spike(D(1), D("1.25"), D(20))
    assert not v.is_spike(D(1), D("1.20"), D(20))  # exactly the threshold is not over it
    assert not v.is_spike(D(1), D(5), None)  # no threshold, no automatic rule
    assert not v.is_spike(None, D(5), D(20))


def test_rates_are_never_negative_and_confidence_is_closed() -> None:
    v.check_rate(D(0), D(0), "ESTIMATE")
    with pytest.raises(v.VendorError):
        v.check_rate(D(-1), D(0), "SIGNED_TARIFF")
    with pytest.raises(v.VendorError):
        v.check_rate(D(1), D(1), "GUESS")


def test_reconciliation_finds_matches_mismatches_gaps_unknowns_and_duplicates() -> None:
    ours = {"r1": D("1.20"), "r2": D("0.40"), "r3": D("1.20")}
    theirs = [("r1", D("1.20")), ("r2", D("0.45")), ("r4", D("1.20")), ("r1", D("1.20"))]
    r = v.reconcile(ours, theirs)
    assert r.matched == ("r1",)
    assert r.amount_mismatch == (("r2", D("0.40"), D("0.45")),)
    assert r.missing_on_invoice == ("r3",)
    assert r.unknown_on_invoice == ("r4",)
    assert r.duplicate_on_invoice == ("r1",)


def test_margin_matches_the_proposal_formula() -> None:
    assert v.margin(D(100), D(60), D(5), D(10)) == D("0.25")
    assert v.margin(D(0), D(5)) is None
    assert v.margin(D(10), D(12)) == D("-0.2")
