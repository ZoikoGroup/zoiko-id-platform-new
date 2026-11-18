"""Screening case outcomes reach a customer as a thin webhook (WF06 section 12; CR-120): subscribable, versioned, declared, and carrying nothing a check found."""

from __future__ import annotations

import re
from pathlib import Path

from zid_platform.modules.screening.application.screening_requests import OUTCOME
from zid_platform.modules.webhook.domain.webhooks import SUBSCRIBABLE, check_event_types

EVENT = "com.zoiko.id.screening.case.outcome_changed.v1"
EVENTS = Path(__file__).resolve().parents[4] / "contracts" / "asyncapi" / "proposed-events.yaml"


def test_the_event_is_subscribable_and_versioned() -> None:
    assert EVENT in SUBSCRIBABLE and check_event_types([EVENT]) == [EVENT]
    assert re.fullmatch(r"com\.zoiko\.id\.[a-z_.]+\.v\d+", EVENT)


def test_every_case_state_maps_to_in_review_or_complete_and_nothing_else() -> None:
    assert set(OUTCOME.values()) == {"IN_REVIEW", "COMPLETE"}
    assert OUTCOME["REVIEW_REQUIRED"] == OUTCOME["SOURCE_UNAVAILABLE"] == "IN_REVIEW"
    # the three ways a case can end read the same from outside: which one it was is for the authenticated view
    assert {
        OUTCOME[s]
        for s in (
            "CLOSED_CLEAR_FOR_SCOPE",
            "CLOSED_RESTRICTED",
            "CLOSED_UNRESOLVED",
            "NO_CANDIDATE",
        )
    } == {"COMPLETE"}


def test_the_event_is_declared_and_says_it_carries_no_name_list_candidate_or_reason() -> None:
    text = EVENTS.read_text(encoding="utf-8")
    block = text[text.index("  screening.case.outcome_changed:") :][:900]
    assert EVENT in block and "no name, list, candidate, disposition or reason" in block


def test_the_subscribable_description_never_promises_a_finding() -> None:
    description = SUBSCRIBABLE[EVENT].lower()
    for word in ("sanction", "pep", "match", "hit", "adverse"):
        assert word not in description.replace("no name, list, candidate or reason", ""), word
