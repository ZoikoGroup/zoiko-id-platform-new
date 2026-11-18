"""EDD information requests: a catalogue, a due window, partial answers, a different reviewer, expiry that clears nothing, bounded reminders, and wording that never reveals the check (CR-118)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.screening.domain import edd_request as e

T0 = datetime(2026, 11, 18, tzinfo=UTC)


def opened(**kw):
    base = {
        "items": ["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"],
        "requested_by": "analyst-1",
        "now": T0,
        "due_in_days": 20,
    }
    base.update(kw)
    return e.open_request("r1", "case1", **base)


@pytest.mark.parametrize(
    "kw", [{"items": []}, {"items": ["BANK_PASSWORD"]}, {"due_in_days": 1}, {"due_in_days": 90}]
)
def test_a_request_needs_catalogue_items_and_a_sensible_due_window(kw) -> None:
    with pytest.raises(e.EddError):
        opened(**kw)


def test_a_partial_answer_stays_open_and_says_what_is_missing() -> None:
    r = e.receive(opened(), ["SOURCE_OF_FUNDS", "NOT_ASKED"], now=T0 + timedelta(days=2))
    assert (
        r.state is e.State.REQUESTED
        and r.missing == ("SUPPORTING_DOCUMENT",)
        and "NOT_ASKED" not in r.provided
    )
    done = e.receive(r, ["SUPPORTING_DOCUMENT"], now=T0 + timedelta(days=3))
    assert done.state is e.State.RECEIVED and done.missing == ()


def test_the_person_who_asked_does_not_review_the_answer_and_an_insufficient_answer_needs_a_code() -> (
    None
):
    r = e.receive(opened(), ["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"], now=T0)
    with pytest.raises(e.EddError) as err:
        e.review(r, reviewer="analyst-1", accepted=True)
    assert err.value.code == "REVIEWER_ASKED_FOR_IT"
    with pytest.raises(e.EddError):
        e.review(r, reviewer="senior-1", accepted=False)
    assert (
        e.review(r, reviewer="senior-1", accepted=False, code="UNREADABLE").state
        is e.State.INSUFFICIENT
    )
    assert e.review(r, reviewer="senior-1", accepted=True).state is e.State.ACCEPTED
    with pytest.raises(e.EddError):
        e.review(opened(), reviewer="senior-1", accepted=True)  # nothing came back yet


def test_a_late_answer_is_refused_and_expiry_is_reported_and_clears_nothing() -> None:
    r = opened()
    with pytest.raises(e.EddError) as err:
        e.receive(r, ["SOURCE_OF_FUNDS"], now=T0 + timedelta(days=21))
    assert err.value.code == "REQUEST_EXPIRED"
    expired, events = e.tick(r, now=T0 + timedelta(days=21))
    assert expired.state is e.State.EXPIRED and events == ("EXPIRED",)
    assert e.tick(expired, now=T0 + timedelta(days=40)) == (
        expired,
        (),
    )  # nothing more happens, and nothing is accepted by silence


def test_reminders_come_at_half_and_nine_tenths_at_most_twice_and_stop_once_answered() -> None:
    r = opened()
    assert e.tick(r, now=T0 + timedelta(days=5))[1] == ()
    r, ev = e.tick(r, now=T0 + timedelta(days=10))
    assert ev == ("REMINDER",)
    assert e.tick(r, now=T0 + timedelta(days=11))[1] == ()  # the same mark does not remind twice
    r, ev = e.tick(r, now=T0 + timedelta(days=18))
    assert ev == ("REMINDER",) and len(r.reminders) == e.MAX_REMINDERS
    assert e.tick(r, now=T0 + timedelta(days=19))[1] == ()
    answered = e.receive(opened(), ["SOURCE_OF_FUNDS", "SUPPORTING_DOCUMENT"], now=T0)
    assert e.tick(answered, now=T0 + timedelta(days=18))[1] == ()


def test_the_customer_wording_never_reveals_what_is_behind_the_request() -> None:
    text = e.customer_text(opened()).lower()
    for word in (
        "sanction",
        "pep",
        "politically",
        "media",
        "screening",
        "watchlist",
        "adverse",
        "risk",
    ):
        assert word not in text, word
    assert "where the money" in text and "2026-12-08" in text
