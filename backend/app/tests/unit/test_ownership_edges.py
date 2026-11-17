"""Ownership: voting, nominee, trust and dated or disputed links (WF02 K04; acceptance T17, T18, T19, T20)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from zid_platform.modules.organization.domain import ownership as ow


def ents(**kinds):
    return [{"id": k, "kind": v} for k, v in kinds.items()]


def own(owner, owned, pct, **kw):
    return {
        "owner": owner,
        "owned": owned,
        "percent": pct,
        "evidenced": kw.pop("evidenced", True),
        **kw,
    }


def run(entities, links, target="T", **kw):
    return ow.analyze(ow.build(entities, links), target, **kw)


def codes(f):
    return {g.code for g in f.gaps}


def test_votes_make_a_candidate_whatever_the_share_of_the_capital() -> None:
    f = run(
        ents(T="COMPANY", p="PERSON", q="PERSON"),
        [
            own("p", "T", "10"),
            own("q", "T", "90"),
            own("p", "T", "60", type="VOTING"),
            own("q", "T", "40", type="VOTING"),
        ],
    )
    by = {c.entity_id: c for c in f.candidates}
    assert (
        "VOTING_AT_OR_ABOVE_THRESHOLD" in by["p"].reasons
        and "OWNERSHIP_AT_OR_ABOVE_THRESHOLD" not in by["p"].reasons
    )  # 10% of the capital, 60% of the votes
    assert set(by["q"].reasons) == {
        "OWNERSHIP_AT_OR_ABOVE_THRESHOLD",
        "VOTING_AT_OR_ABOVE_THRESHOLD",
    }


def test_votes_pass_up_through_voting_links_only() -> None:
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON"),
        [
            own("H", "T", "100"),
            own("p", "H", "100"),
            own("H", "T", "60", type="VOTING"),
            own("p", "H", "50", type="VOTING"),
        ],
    )
    p = next(c for c in f.candidates if c.entity_id == "p")
    assert (
        "VOTING_AT_OR_ABOVE_THRESHOLD" in p.reasons
    )  # 60% of the votes in T held by H, 50% of H's votes: 30%
    assert ow.percent_text(Decimal(30)) == "30"


def test_economic_and_voting_totals_are_counted_apart_and_each_may_not_pass_the_whole() -> None:
    ow.build(
        ents(T="COMPANY", p="PERSON", q="PERSON"),
        [
            own("p", "T", "100"),
            own("p", "T", "70", type="VOTING"),
            own("q", "T", "30", type="VOTING"),
        ],
    )
    with pytest.raises(ow.OwnershipError, match="OWNED_OVER_100_PERCENT"):
        ow.build(
            ents(T="COMPANY", p="PERSON", q="PERSON"),
            [own("p", "T", "70", type="VOTING"), own("q", "T", "40", type="VOTING")],
        )


def test_a_nominee_with_a_named_beneficiary_attributes_the_share_to_the_beneficiary() -> None:
    f = run(
        ents(T="COMPANY", n="PERSON", b="PERSON"),
        [own("n", "T", "100", type="NOMINEE", beneficiary="b")],
    )
    assert [c.entity_id for c in f.candidates] == ["b"]
    assert (
        f.candidates[0].status == "NEEDS_EVIDENCE"
    )  # attributed, never taken as evidenced until a person confirms it
    assert "NOMINEE_BENEFICIARY_UNDECLARED" not in codes(f)


def test_an_undeclared_nominee_is_a_material_gap_and_triggers_review() -> None:  # T19
    f = run(ents(T="COMPANY", n="PERSON"), [own("n", "T", "60", type="NOMINEE")])
    gap = next(g for g in f.gaps if g.code == "NOMINEE_BENEFICIARY_UNDECLARED")
    assert gap.entity_id == "n" and gap.effective_percent == Decimal(60) and f.complete is False


def test_a_trust_with_no_person_recorded_as_controlling_it_is_a_gap() -> None:
    f = run(ents(T="COMPANY", X="TRUST", p="PERSON"), [own("X", "T", "100"), own("p", "X", "100")])
    assert "TRUST_PARTIES_UNDECLARED" in codes(f) and f.complete is False
    ok = run(
        ents(T="COMPANY", X="TRUST", p="PERSON", s="PERSON"),
        [
            own("X", "T", "100"),
            own("p", "X", "100"),
            {"owner": "s", "owned": "X", "control": True, "evidenced": True},
        ],
    )
    assert "TRUST_PARTIES_UNDECLARED" not in codes(ok)
    arrangement = run(
        ents(T="COMPANY", F="ARRANGEMENT", p="PERSON"), [own("F", "T", "100"), own("p", "F", "100")]
    )
    assert "TRUST_PARTIES_UNDECLARED" in codes(arrangement)


def test_a_disputed_link_makes_whoever_depends_on_it_need_evidence_and_is_a_gap() -> None:  # T20
    f = run(ents(T="COMPANY", p="PERSON"), [own("p", "T", "80", disputed=True)])
    assert f.candidates[0].status == "NEEDS_EVIDENCE"
    assert (
        any(g.code == "DISPUTED_LINK" and g.effective_percent == Decimal(80) for g in f.gaps)
        and f.complete is False
    )


def test_links_out_of_force_are_left_out_for_the_date_asked() -> None:
    links = [
        own("old", "T", "100", valid_from="2020-01-01", valid_to="2024-01-01"),
        own("new", "T", "100", valid_from="2024-01-01"),
    ]
    e = ents(T="COMPANY", old="PERSON", new="PERSON")
    assert [c.entity_id for c in run(e, links, as_of=date(2022, 6, 1)).candidates] == ["old"]
    assert [c.entity_id for c in run(e, links, as_of=date(2025, 6, 1)).candidates] == ["new"]
    assert {c.entity_id for c in run(e, links[:1]).candidates} == {
        "old"
    }  # no date asked: every recorded link counts, as before


def test_a_change_of_owner_does_not_count_as_owning_more_than_the_whole() -> None:
    ow.build(
        ents(T="COMPANY", a="PERSON", b="PERSON"),
        [
            own("a", "T", "100", valid_to="2024-01-01"),
            own("b", "T", "100", valid_from="2024-01-01"),
        ],
    )
    with pytest.raises(ow.OwnershipError, match="OWNED_OVER_100_PERCENT"):
        ow.build(
            ents(T="COMPANY", a="PERSON", b="PERSON"),
            [own("a", "T", "100"), own("b", "T", "1", valid_from="2024-01-01")],
        )


def test_a_cycle_through_voting_or_nominee_links_is_refused_whole() -> None:  # T18
    with pytest.raises(ow.OwnershipError, match="OWNERSHIP_CYCLE"):
        run(
            ents(T="COMPANY", A="COMPANY", B="COMPANY"),
            [
                own("A", "T", "50", type="VOTING"),
                own("B", "A", "50", type="VOTING"),
                own("A", "B", "50", type="VOTING"),
            ],
        )


def test_the_new_fields_are_checked() -> None:
    e = ents(T="COMPANY", a="PERSON", b="PERSON")
    for bad, code in (
        ([own("a", "T", "10", type="GOLDEN_SHARE")], "LINK_TYPE_INVALID"),
        ([own("a", "T", "10", beneficiary="b")], "BENEFICIARY_ONLY_FOR_NOMINEE"),
        ([own("a", "T", "10", type="NOMINEE", beneficiary="ghost")], "BENEFICIARY_INVALID"),
        ([own("a", "T", "10", type="NOMINEE", beneficiary="a")], "BENEFICIARY_INVALID"),
        (
            [own("a", "T", "10", valid_from="2025-01-01", valid_to="2024-01-01")],
            "LINK_DATES_INVALID",
        ),
        ([own("a", "T", "10", valid_from="not a date")], "LINK_DATES_INVALID"),
        (
            [{"owner": "a", "owned": "T", "control": True, "type": "VOTING", "evidenced": True}],
            "LINK_TYPE_INVALID",
        ),
    ):
        with pytest.raises(ow.OwnershipError, match=code):
            ow.build(e, bad)


def test_the_same_pair_may_carry_one_link_of_each_type_but_not_two_of_one() -> None:
    ow.build(
        ents(T="COMPANY", p="PERSON"), [own("p", "T", "10"), own("p", "T", "10", type="VOTING")]
    )
    with pytest.raises(ow.OwnershipError, match="LINK_REPEATED"):
        ow.build(
            ents(T="COMPANY", p="PERSON"),
            [own("p", "T", "10", type="VOTING"), own("p", "T", "20", type="VOTING")],
        )
