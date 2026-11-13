"""DTV-26..28, DTV-32..34: the six regulated product lines exist as a gate and none can run without its approvals (CR-073 section D)."""

from __future__ import annotations

import pytest
from zid_platform.modules.verification.domain import product_lines as pl
from zid_platform.modules.verification.infrastructure.product_line_registry import (
    product_line_registry,
)


def _doc(**lines):
    return {"version": "t", "lines": lines}


FULL = {
    "state": "LIVE",
    "countries": ["GB"],
    "legal_basis_ref": "LEG-1",
    "partner_contract_ref": "PC-1",
    "approved_by": "CTO",
}


def test_the_shipped_file_has_all_six_lines_and_every_one_is_design() -> None:
    reg = product_line_registry()
    assert set(reg.lines) == set(pl.KNOWN_LINES) and len(reg.lines) == 6
    assert all(
        line.state is pl.LineState.DESIGN and not line.available for line in reg.lines.values()
    )
    assert {line.dtv for line in reg.lines.values()} == {
        "DTV-26",
        "DTV-27",
        "DTV-28",
        "DTV-32",
        "DTV-33",
        "DTV-34",
    }


def test_a_line_left_out_of_the_file_is_design_not_missing() -> None:
    reg = pl.parse({"version": "t", "lines": {}})
    assert all(line.state is pl.LineState.DESIGN for line in reg.lines.values())


def test_asking_for_a_line_that_is_off_is_an_error_that_names_the_state() -> None:
    with pytest.raises(pl.ProductLineError) as e:
        pl.require_available(product_line_registry(), "travel_rule", "GB")
    assert e.value.code == "SERVICE_UNAVAILABLE" and "DESIGN" in str(e.value)


@pytest.mark.parametrize(
    ("missing", "code"),
    [
        ("legal_basis_ref", "LEGAL_BASIS_MISSING"),
        ("partner_contract_ref", "PARTNER_CONTRACT_MISSING"),
        ("approved_by", "APPROVER_MISSING"),
        ("countries", "NO_COUNTRIES_LISTED"),
    ],
)
def test_switching_a_line_on_without_its_approvals_does_not_load(missing: str, code: str) -> None:
    entry = {k: v for k, v in FULL.items() if k != missing}
    with pytest.raises(pl.ProductLineError) as e:
        pl.parse(_doc(travel_rule=entry))
    assert e.value.code == code


def test_source_of_funds_needs_no_partner_but_still_needs_a_legal_basis_and_approver() -> None:
    ok = {k: v for k, v in FULL.items() if k != "partner_contract_ref"}
    assert pl.parse(_doc(source_of_funds=ok)).get("source_of_funds").available
    no_legal = {k: v for k, v in ok.items() if k != "legal_basis_ref"}
    with pytest.raises(pl.ProductLineError):
        pl.parse(_doc(source_of_funds=no_legal))


def test_staging_needs_no_approver_but_live_does() -> None:
    staging = {**FULL, "state": "STAGING"}
    staging.pop("approved_by")
    assert pl.parse(_doc(travel_rule=staging)).get("travel_rule").available


def test_a_live_line_works_only_in_the_countries_it_lists() -> None:
    reg = pl.parse(_doc(travel_rule=FULL))
    assert pl.require_available(reg, "travel_rule", "gb").code == "travel_rule"
    with pytest.raises(pl.ProductLineError) as e:
        pl.require_available(reg, "travel_rule", "US")
    assert e.value.code == "SERVICE_UNAVAILABLE_IN_MARKET"


def test_one_line_going_live_does_not_enable_the_others() -> None:
    reg = pl.parse(_doc(travel_rule=FULL))
    with pytest.raises(pl.ProductLineError):
        pl.require_available(reg, "onchain_screening", "GB")


def test_an_unknown_line_or_state_is_refused_not_ignored() -> None:
    with pytest.raises(pl.ProductLineError) as e:
        pl.parse(_doc(made_up_line={"state": "LIVE"}))
    assert e.value.code == "PRODUCT_LINE_UNKNOWN"
    with pytest.raises(pl.ProductLineError) as e2:
        pl.parse(_doc(travel_rule={"state": "ENABLED"}))
    assert e2.value.code == "PRODUCT_LINE_STATE_UNKNOWN"
