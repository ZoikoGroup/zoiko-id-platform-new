"""T20: contradictory shareholder papers and registry evidence demand an exception, with each source's word kept and named (CR-115)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from zid_platform.modules.organization.domain import ownership as ow
from zid_platform.modules.organization.domain import ownership_sources as src

ENTS = [
    {"id": "T", "kind": "COMPANY"},
    {"id": "ann", "kind": "PERSON"},
    {"id": "bob", "kind": "PERSON"},
]


def st(owner: str, owned: str, pct: str, kind: str, ref: str) -> dict:
    return {"owner": owner, "owned": owned, "percent": pct, "source_kind": kind, "source_ref": ref}


def run(items: list[dict], covering: dict | None = None):
    return src.reconcile(src.parse(items), covering=covering)


def test_two_sources_that_agree_make_an_undisputed_evidenced_link() -> None:
    items, conflicts = run(
        [
            st("ann", "T", "60", "REGISTER", "extract-1"),
            st("ann", "T", "60", "SIGNED_PAPER", "paper-7"),
        ]
    )
    assert conflicts == [] and items[0]["disputed"] is False and items[0]["evidenced"] is True
    assert [s["ref"] for s in items[0]["sources"]] == ["extract-1", "paper-7"]


def test_a_rounding_difference_inside_the_tolerance_is_not_a_conflict() -> None:
    _, conflicts = run(
        [st("ann", "T", "33.33", "REGISTER", "e"), st("ann", "T", "33.335", "SIGNED_PAPER", "p")]
    )
    assert conflicts == []


def test_papers_that_contradict_the_register_are_disputed_and_every_source_is_named() -> None:
    items, conflicts = run(
        [
            st("ann", "T", "25", "REGISTER", "extract-1"),
            st("ann", "T", "60", "SIGNED_PAPER", "paper-7"),
            st("ann", "T", "75", "DECLARATION", "form-2"),
        ]
    )
    [c] = conflicts
    assert c.reason == "SHARE_DIFFERS" and [
        (s.source_kind, s.source_ref, s.percent) for s in c.statements
    ] == [
        ("REGISTER", "extract-1", Decimal(25)),
        ("SIGNED_PAPER", "paper-7", Decimal(60)),
        ("DECLARATION", "form-2", Decimal(75)),
    ]
    assert items[0]["disputed"] is True and items[0]["evidenced"] is False
    assert (
        items[0]["percent"] == "25"
    )  # the stronger kind is only the arithmetic default, not a ruling
    assert src.needs_exception(conflicts) is True


def test_a_complete_register_that_omits_a_holder_a_paper_states_is_a_conflict() -> None:
    items, conflicts = run(
        [
            st("ann", "T", "60", "REGISTER", "extract-1"),
            st("bob", "T", "40", "SIGNED_PAPER", "paper-7"),
        ],
        covering={"extract-1": frozenset({"T"})},
    )
    assert [(c.owner, c.reason) for c in conflicts] == [("bob", "NOT_IN_COVERING_SOURCE")]
    by_owner = {i["owner"]: i for i in items}
    assert by_owner["bob"]["disputed"] is True and by_owner["ann"]["disputed"] is False


def test_a_single_paper_is_not_a_covering_source_so_silence_from_it_is_not_a_conflict() -> None:
    _, conflicts = run(
        [
            st("ann", "T", "60", "REGISTER", "extract-1"),
            st("bob", "T", "40", "REGISTER", "extract-1"),
        ]
    )
    assert conflicts == [] and src.needs_exception(conflicts) is False


def test_the_analysis_turns_a_contradiction_into_a_disputed_link_that_needs_evidence() -> None:
    items, conflicts = run(
        [st("ann", "T", "60", "REGISTER", "e"), st("ann", "T", "30", "SIGNED_PAPER", "p")]
    )
    graph = ow.build(ENTS, items)
    facts = ow.analyze(graph, "T")
    assert any(g.code == "DISPUTED_LINK" for g in facts.gaps) and facts.complete is False
    assert all(c.status != "CONFIRMED" for c in facts.candidates)
    assert conflicts


@pytest.mark.parametrize(
    "bad",
    [
        {"owner": "a", "owned": "T", "percent": "10", "source_kind": "REGISTER"},
        {"owner": "a", "owned": "T", "percent": "10", "source_kind": "HEARSAY", "source_ref": "x"},
        {
            "owner": "a",
            "owned": "T",
            "percent": "101",
            "source_kind": "REGISTER",
            "source_ref": "x",
        },
        {
            "owner": "a",
            "owned": "T",
            "percent": "ten",
            "source_kind": "REGISTER",
            "source_ref": "x",
        },
        {"owner": "a", "owned": "T", "source_kind": "REGISTER", "source_ref": "x"},
    ],
)
def test_a_statement_without_a_named_source_or_a_sensible_share_is_refused(bad: dict) -> None:
    with pytest.raises(src.SourceError):
        src.parse([bad])
