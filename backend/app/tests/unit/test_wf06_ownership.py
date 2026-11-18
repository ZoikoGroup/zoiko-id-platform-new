"""WF06 ownership and control rule packs (CR-113): a listed owner reaches an unlisted entity; unknown never clears; the pack that ran is on the result (QA-029 to QA-033)."""

from __future__ import annotations

from decimal import Decimal

from zid_platform.modules.screening.domain import ownership as ow
from zid_platform.modules.screening.domain.ownership import (
    Control,
    CountryPack,
    Edge,
    Graph,
    Holder,
    HolderKind,
)

LISTED = frozenset({"listed"})


def graph(*edges: Edge, extra: dict | None = None) -> Graph:
    holders = {
        "co": Holder("co", HolderKind.ENTITY),
        "mid": Holder("mid", HolderKind.ENTITY),
        "listed": Holder("listed", HolderKind.PERSON),
        "other": Holder("other", HolderKind.PERSON),
    }
    holders.update(extra or {})
    return Graph("co", holders, tuple(edges))


def pack(*evaluators: str, version="gb-1") -> CountryPack:
    return CountryPack("GB", version, None, None, tuple(evaluators))


def test_a_listed_owner_of_an_intermediary_reaches_the_unlisted_company_under_each_rule() -> None:
    g = graph(Edge("listed", "mid", Decimal(60)), Edge("mid", "co", Decimal(70)))
    for rule, flag in (
        ("ofac_50", "OFAC_50_PERCENT_AGGREGATE"),
        ("ofsi", "OFSI_OWNED_OR_CONTROLLED"),
        ("eu_control", "EU_OWNED_OR_CONTROLLED"),
    ):
        result = ow.evaluate(g, pack(rule), designated=LISTED)
        assert result.flags == (flag,), (
            rule
        )  # QA-029, QA-033: the unlisted company is flagged for a person
    both = ow.evaluate(g, pack("ofac_50", "ofsi", "eu_control"), designated=LISTED)
    assert set(both.flags) == {
        "OFAC_50_PERCENT_AGGREGATE",
        "OFSI_OWNED_OR_CONTROLLED",
        "EU_OWNED_OR_CONTROLLED",
    }


def test_two_listed_owners_add_up_under_the_us_rule_only() -> None:
    g = graph(Edge("listed", "co", Decimal(30)), Edge("other", "co", Decimal(30)))
    both = frozenset({"listed", "other"})
    assert ow.evaluate(g, pack("ofac_50"), designated=both).flags == ("OFAC_50_PERCENT_AGGREGATE",)
    assert (
        ow.evaluate(g, pack("ofsi", "eu_control"), designated=both).flags == ()
    )  # holders are never added together there


def test_control_without_a_majority_of_the_capital_is_caught_by_the_eu_and_uk_tests() -> None:
    g = graph(
        Edge("listed", "co", Decimal(10), controls=frozenset({Control.APPOINT_BOARD_MAJORITY}))
    )
    assert ow.evaluate(g, pack("eu_control"), designated=LISTED).flags == (
        "EU_OWNED_OR_CONTROLLED",
    )
    assert (
        ow.evaluate(g, pack("ofac_50"), designated=LISTED).flags == ()
    )  # the US test is about ownership, so the pack that asks for it alone misses this


def test_a_listed_minority_owner_is_not_flagged() -> None:
    g = graph(Edge("listed", "co", Decimal(20)), Edge("other", "co", Decimal(80)))
    assert ow.evaluate(g, pack("ofac_50", "ofsi", "eu_control"), designated=LISTED).flags == ()


def test_unknown_percentages_and_missing_owners_are_reasons_the_structure_cannot_be_relied_on() -> (
    None
):
    g = graph(Edge("other", "co", None))
    result = ow.evaluate(g, pack("ofac_50", "eu_control"), designated=LISTED)
    assert (
        "HOLDING_UNQUANTIFIED" in result.unresolved
    )  # QA-030: an unknown figure never clears the company
    unknown = graph(Edge("x", "co", Decimal(100)), extra={"x": Holder("x", HolderKind.UNKNOWN)})
    assert "OWNER_UNKNOWN" in ow.evaluate(unknown, pack("ofac_50"), designated=LISTED).unresolved
    assert (
        "NO_OWNERS_DECLARED" in ow.evaluate(graph(), pack("ofac_50"), designated=LISTED).unresolved
    )
    gap = graph(Edge("mid", "co", Decimal(100)))
    assert (
        "MISSING_INTERMEDIARY_OWNER"
        in ow.evaluate(gap, pack("ofac_50"), designated=LISTED).unresolved
    )


def test_the_pack_version_is_on_the_result_so_a_decision_can_be_replayed() -> None:
    g = graph(Edge("listed", "co", Decimal(60)))
    one = ow.evaluate(g, pack("eu_control", version="eu-2026-11"), designated=LISTED)
    two = ow.evaluate(g, pack("eu_control", version="eu-2026-12"), designated=LISTED)
    assert one.pack_version == "eu-2026-11" and two.pack_version == "eu-2026-12"  # QA-031
    assert one.flags == two.flags
    assert ow.evaluate(g, pack("eu_control"), designated=LISTED) == ow.evaluate(
        g, pack("eu_control"), designated=LISTED
    )


def test_a_circular_holding_does_not_loop_or_inflate() -> None:
    g = graph(
        Edge("listed", "mid", Decimal(100)),
        Edge("mid", "co", Decimal(100)),
        Edge("co", "mid", Decimal(100)),
    )
    result = ow.evaluate(g, pack("ofac_50", "eu_control"), designated=LISTED)
    assert "OFAC_50_PERCENT_AGGREGATE" in result.flags


def test_a_change_in_shareholding_changes_the_finding_so_a_rescreen_is_needed() -> None:
    before = graph(Edge("other", "co", Decimal(100)))
    after = graph(Edge("other", "co", Decimal(40)), Edge("listed", "co", Decimal(60)))
    assert ow.evaluate(before, pack("ofac_50", "eu_control"), designated=LISTED).flags == ()
    assert set(ow.evaluate(after, pack("ofac_50", "eu_control"), designated=LISTED).flags) == {
        "OFAC_50_PERCENT_AGGREGATE",
        "EU_OWNED_OR_CONTROLLED",
    }  # QA-032
