"""Former PEP detail, same-name article linking, and retraction or correction reasons (QA-023, QA-025, QA-026; CR-118)."""

from __future__ import annotations

from datetime import date

import pytest
from zid_platform.modules.screening.domain import candidate_detail as cd

TODAY = date(2026, 11, 18)


def test_a_former_pep_is_shown_as_former_with_dates_and_is_not_lowered_without_a_policy() -> None:
    v = cd.pep_view({"office": "Minister of Finance", "ended": "2021-03-01"}, today=TODAY)
    assert (
        v.status is cd.PepStatus.FORMER
        and v.ended == date(2021, 3, 1)
        and v.office == "Minister of Finance"
    )
    assert v.note == "FORMER_RISK_NOT_REDUCED" and v.needs_person is True


@pytest.mark.parametrize(
    ("months", "note"), [(12, "FORMER_OUTSIDE_LOOKBACK_REVIEW"), (120, "FORMER_WITHIN_LOOKBACK")]
)
def test_a_lookback_changes_only_the_note_never_the_need_for_a_person(
    months: int, note: str
) -> None:
    v = cd.pep_view(
        {"office": "Judge", "ended": "2021-03-01"}, today=TODAY, policy=cd.PepPolicy(months)
    )
    assert v.note == note and v.needs_person is True


def test_current_unknown_and_future_dated_offices_are_never_called_former() -> None:
    assert (
        cd.pep_view({"office": "Mayor", "current": True}, today=TODAY).status
        is cd.PepStatus.CURRENT
    )
    unknown = cd.pep_view({"office": "Mayor"}, today=TODAY)
    assert unknown.status is cd.PepStatus.UNKNOWN and unknown.note == "DATES_MISSING"
    assert (
        cd.pep_view({"office": "Mayor", "ended": "2027-01-01"}, today=TODAY).status
        is cd.PepStatus.CURRENT
    )


def test_a_relative_or_associate_needs_the_named_pep_and_the_relationship() -> None:
    ok = cd.pep_view(
        {
            "office": "Senator",
            "ended": "2020-01-01",
            "relationship": "family_member",
            "related_to": "PEP-77",
        },
        today=TODAY,
    )
    assert ok.relationship == "FAMILY_MEMBER" and ok.related_to == "PEP-77"
    missing = cd.pep_view(
        {"office": "Senator", "ended": "2020-01-01", "relationship": "FAMILY_MEMBER"}, today=TODAY
    )
    assert missing.status is cd.PepStatus.UNKNOWN and missing.note == "RELATIONSHIP_MISSING"
    assert (
        cd.pep_view(
            {"relationship": "FRIEND", "related_to": "x", "ended": "2020-01-01"}, today=TODAY
        ).note
        == "RELATIONSHIP_MISSING"
    )


SUBJECT = {"birth": "1970-05-04", "country": ["GB", "IE"], "employer": "Acme Ltd"}


def test_an_article_with_only_the_same_name_is_not_linked() -> None:
    link = cd.link_article(SUBJECT, {})
    assert link.result is cd.Link.NAME_ONLY and link.attach is False
    one = cd.link_article(SUBJECT, {"country": "gb"})
    assert (
        one.result is cd.Link.NAME_ONLY and one.agreeing == ("country",) and one.attach is False
    )  # one agreement is not enough


def test_two_independent_agreements_link_the_article() -> None:
    link = cd.link_article(SUBJECT, {"birth": "1970-05-04", "employer": "  ACME   ltd "})
    assert (
        link.result is cd.Link.LINKED
        and link.attach is True
        and set(link.agreeing) == {"birth", "employer"}
    )


def test_a_contradiction_beats_any_number_of_agreements() -> None:
    link = cd.link_article(
        SUBJECT, {"birth": "1983-01-01", "country": "GB", "employer": "Acme Ltd"}
    )
    assert (
        link.result is cd.Link.CONTRADICTED
        and link.contradicting == ("birth",)
        and link.attach is False
    )


def test_a_retraction_or_correction_reopens_and_an_unknown_kind_does_nothing() -> None:
    assert cd.update_reason("ARTICLE_RETRACTED") == "ARTICLE_RETRACTED"
    assert cd.update_reason("ARTICLE_CORRECTED") == "ARTICLE_CORRECTED"
    assert cd.update_reason("ENTRY_REMOVED") == "ENTRY_DELISTED_OR_CHANGED"
    assert cd.update_reason("SOMETHING_NEW") is None
