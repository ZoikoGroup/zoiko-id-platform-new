"""DTV-24 ownership chains and beneficial owner candidates: exact arithmetic checked against hand-worked answers, and the failure cases. Synthetic only."""

from __future__ import annotations

import random
from decimal import Decimal

import pytest
from zid_platform.modules.organization.domain import ownership as ow


def ents(**kinds):
    return [{"id": k, "kind": v} for k, v in kinds.items()]


def link(owner, owned, pct=None, *, control=False, evidenced=True):
    d = {"owner": owner, "owned": owned, "evidenced": evidenced}
    if control:
        d["control"] = True
    else:
        d["percent"] = pct
    return d


def run(entities, links, target="T", **kw):
    return ow.analyze(ow.build(entities, links), target, **kw)


def pct(f: ow.Findings, who: str) -> Decimal:
    return next(c.effective_percent for c in f.candidates if c.entity_id == who)


# ------------------------------------------------------------------------------------------------------------------- the arithmetic


def test_a_direct_owner_at_the_threshold_is_a_candidate_and_one_below_is_not():
    f = run(
        ents(T="COMPANY", a="PERSON", b="PERSON"), [link("a", "T", "25"), link("b", "T", "24.99")]
    )
    assert [c.entity_id for c in f.candidates] == ["a"] and pct(f, "a") == Decimal(25)


def test_a_chain_multiplies_the_shares_down_the_path():
    # 60 percent of a company that holds 50 percent of the target is 30 percent of the target
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON", q="PERSON"),
        [link("H", "T", "50"), link("p", "H", "60"), link("q", "H", "40")],
    )
    assert pct(f, "p") == Decimal(30)
    assert "q" not in {c.entity_id for c in f.candidates}  # 20 percent: below the threshold


def test_several_paths_to_one_person_add_up():
    # p holds 20 directly and 50 percent of H, which holds 40 percent: 20 + 20 = 40
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON"),
        [link("p", "T", "20"), link("H", "T", "40"), link("p", "H", "50")],
    )
    assert (
        pct(f, "p") == Decimal(40)
        and next(c for c in f.candidates if c.entity_id == "p").paths == 2
    )


def test_a_person_just_under_the_threshold_directly_but_over_by_a_second_path_is_found():
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON"),
        [link("p", "T", "15"), link("H", "T", "30"), link("p", "H", "40")],
    )
    assert pct(f, "p") == Decimal(27)  # 15 + 12: neither path alone reaches 25


def test_the_arithmetic_is_exact_with_awkward_fractions():
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON"),
        [link("H", "T", "33.3333"), link("p", "H", "75")],
        threshold=Decimal(10),
    )
    assert pct(f, "p") == Decimal("24.999975")  # never a float: 0.333333 * 0.75 exactly


def test_a_different_threshold_changes_who_is_a_candidate():
    e, ls = ents(T="COMPANY", a="PERSON"), [link("a", "T", "10")]
    assert run(e, ls).candidates == ()
    assert [c.entity_id for c in run(e, ls, threshold=Decimal(10)).candidates] == ["a"]


# ------------------------------------------------------------------------------------------------------------------- control by other means


def test_control_by_other_means_makes_a_candidate_whatever_the_share():
    f = run(
        ents(T="COMPANY", a="PERSON", b="PERSON"),
        [link("a", "T", "100"), link("b", "T", control=True)],
    )
    by = {c.entity_id: c for c in f.candidates}
    assert by["b"].reasons == ("CONTROL_BY_OTHER_MEANS",) and by["b"].effective_percent == 0
    assert by["a"].reasons == ("OWNERSHIP_AT_OR_ABOVE_THRESHOLD",)


def test_a_person_who_owns_and_controls_has_both_reasons():
    f = run(ents(T="COMPANY", a="PERSON"), [link("a", "T", "30"), link("a", "T", control=True)])
    assert f.candidates[0].reasons == ("OWNERSHIP_AT_OR_ABOVE_THRESHOLD", "CONTROL_BY_OTHER_MEANS")


# ------------------------------------------------------------------------------------------------------------------- gaps


def test_a_chain_that_ends_at_a_company_with_no_recorded_owners_is_a_gap_not_a_clean_answer():
    f = run(ents(T="COMPANY", H="COMPANY"), [link("H", "T", "100")])
    assert not f.complete and [(g.code, g.entity_id, g.effective_percent) for g in f.gaps] == [
        ("UNRESOLVED_CHAIN", "H", Decimal(100))
    ]


def test_a_small_unresolved_holding_is_a_gap_but_not_a_material_one():
    f = run(
        ents(T="COMPANY", H="COMPANY", a="PERSON"), [link("a", "T", "90"), link("H", "T", "10")]
    )
    assert f.gaps and all(g.effective_percent < f.threshold for g in f.gaps) and f.complete
    assert [c.entity_id for c in f.candidates] == ["a"]


def test_the_part_of_a_company_nobody_explains_is_reported_with_its_share_of_the_target():
    # T is 60 percent explained; H holds half of T and is only 50 percent explained: 50 * 50 / 100 = 25 percent of T is unknown through H
    f = run(
        ents(T="COMPANY", H="COMPANY", a="PERSON", b="PERSON"),
        [link("a", "T", "50"), link("H", "T", "50"), link("b", "H", "50")],
    )
    gap = next(g for g in f.gaps if g.entity_id == "H")
    assert (
        gap.code == "UNEXPLAINED_REMAINDER"
        and gap.effective_percent == Decimal(25)
        and not f.complete
    )


def test_a_target_with_no_owners_at_all_is_unresolved():
    f = run(ents(T="COMPANY"), [])
    assert not f.complete and f.gaps[0].code == "UNRESOLVED_CHAIN" and f.gaps[0].entity_id == "T"


def test_a_listed_company_or_a_government_is_a_recognised_end_not_a_gap():
    f = run(
        ents(T="COMPANY", L="LISTED_COMPANY", G="GOVERNMENT"),
        [link("L", "T", "60"), link("G", "T", "40")],
    )
    assert f.complete and f.gaps == () and f.recognised_ends == ("G", "L") and f.candidates == ()
    assert (
        f.fallback and "senior managing official" in f.fallback
    )  # no candidate, nothing unknown: a person identifies someone


def test_a_trust_with_no_recorded_owners_is_a_gap_like_a_company():
    f = run(ents(T="COMPANY", X="TRUST"), [link("X", "T", "70")])
    assert not f.complete and ("UNRESOLVED_CHAIN", "X") in {(g.code, g.entity_id) for g in f.gaps}


# ------------------------------------------------------------------------------------------------------------------- evidence


def test_a_candidate_that_depends_on_an_unevidenced_link_needs_evidence_and_one_that_does_not_needs_confirmation():
    f = run(
        ents(T="COMPANY", H="COMPANY", p="PERSON", q="PERSON"),
        [link("H", "T", "100"), link("p", "H", "60", evidenced=False), link("q", "H", "40")],
        threshold=Decimal(10),
    )
    status = {c.entity_id: c.status for c in f.candidates}
    assert status == {"p": "NEEDS_EVIDENCE", "q": "NEEDS_CONFIRMATION"}


# ------------------------------------------------------------------------------------------------------------------- refusals


def test_a_circular_chain_is_refused_whole_naming_the_entities():
    with pytest.raises(ow.OwnershipError, match="OWNERSHIP_CYCLE") as e:
        run(
            ents(T="COMPANY", A="COMPANY", B="COMPANY"),
            [link("A", "T", "50"), link("B", "A", "50"), link("A", "B", "50")],
        )
    assert "A" in str(e.value) and "B" in str(e.value)


@pytest.mark.parametrize(
    ("entities", "links", "code"),
    [
        (
            ents(T="COMPANY", a="PERSON", b="PERSON"),
            [link("a", "T", "60"), link("b", "T", "50")],
            "OWNED_OVER_100_PERCENT",
        ),
        (ents(T="COMPANY"), [link("T", "T", "10")], "SELF_OWNERSHIP"),
        (
            ents(T="COMPANY", a="PERSON"),
            [link("a", "T", "10"), link("a", "T", "20")],
            "LINK_REPEATED",
        ),
        (
            ents(T="COMPANY", a="PERSON"),
            [link("a", "T", 10)],
            "PERCENT_INVALID",
        ),  # a number, not a decimal string
        (ents(T="COMPANY", a="PERSON"), [link("a", "T", "0")], "PERCENT_INVALID"),
        (ents(T="COMPANY", a="PERSON"), [link("a", "T", "101")], "PERCENT_INVALID"),
        (ents(T="COMPANY", a="PERSON"), [link("a", "T", "NaN")], "PERCENT_INVALID"),
        (ents(T="COMPANY", a="PERSON"), [link("ghost", "T", "10")], "LINK_ENTITY_UNKNOWN"),
        (ents(T="COMPANY", a="PERSON"), [link("T", "a", "10")], "PERSON_CANNOT_BE_OWNED"),
        ([{"id": "T", "kind": "ROBOT"}], [], "ENTITY_INVALID"),
        ([{"id": "T", "kind": "COMPANY"}, {"id": "T", "kind": "COMPANY"}], [], "ENTITY_INVALID"),
    ],
)
def test_a_malformed_graph_is_refused_with_a_code(entities, links, code):
    with pytest.raises(ow.OwnershipError, match=code):
        ow.build(entities, links)


def test_the_target_must_be_a_company_like_entity_and_the_threshold_sane():
    g = ow.build(ents(T="COMPANY", a="PERSON"), [link("a", "T", "50")])
    for bad in ("a", "missing"):
        with pytest.raises(ow.OwnershipError, match="TARGET_INVALID"):
            ow.analyze(g, bad)
    for t in (Decimal(0), Decimal(101)):
        with pytest.raises(ow.OwnershipError, match="THRESHOLD_INVALID"):
            ow.analyze(g, "T", threshold=t)


def test_a_graph_over_the_size_limit_is_refused():
    many = [{"id": f"e{i}", "kind": "COMPANY"} for i in range(ow.MAX_ENTITIES + 1)]
    with pytest.raises(ow.OwnershipError, match="GRAPH_TOO_LARGE"):
        ow.build(many, [])


# ------------------------------------------------------------------------------------------------------------------- properties


def test_the_result_does_not_depend_on_the_order_links_were_recorded_in():
    entities = ents(T="COMPANY", H="COMPANY", I="COMPANY", p="PERSON", q="PERSON", r="PERSON")
    links = [
        link("H", "T", "40"),
        link("I", "T", "30"),
        link("r", "T", "30"),
        link("p", "H", "50"),
        link("q", "H", "50"),
        link("p", "I", "100"),
    ]
    expected = run(entities, links)
    for seed in range(20):
        shuffled = links[:]
        random.Random(seed).shuffle(shuffled)
        assert run(entities, shuffled) == expected


def test_the_candidates_shares_never_add_up_to_more_than_the_target():
    entities = ents(T="COMPANY", H="COMPANY", p="PERSON", q="PERSON", r="PERSON")
    f = run(
        entities,
        [link("H", "T", "70"), link("r", "T", "30"), link("p", "H", "55"), link("q", "H", "45")],
        threshold=Decimal(1),
    )
    assert sum(c.effective_percent for c in f.candidates) == Decimal(100)


def test_a_finding_is_a_candidate_list_for_a_person_and_never_says_someone_is_the_owner():
    f = run(ents(T="COMPANY", a="PERSON"), [link("a", "T", "100")])
    assert f.recommendation == "REVIEW" and any(
        "candidates for a person to confirm" in n for n in f.notes
    )
    assert not any(hasattr(f, n) for n in ("ubo", "beneficial_owner", "verdict", "score"))
    s = ow.summary(f)
    assert s["candidates"] == 1 and s["complete"] is True
    assert set(s) == {
        "candidates",
        "needs_evidence",
        "gaps",
        "material_gaps",
        "complete",
        "recommendation",
    }
    assert "a" not in s.values() and "T" not in s.values()  # counts only, never an entity


def test_percentages_print_exactly_without_rounding():
    assert (
        ow.percent_text(Decimal("24.999975")) == "24.999975"
        and ow.percent_text(Decimal("30.000")) == "30"
        and ow.percent_text(Decimal("0.5")) == "0.5"
    )
