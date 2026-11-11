"""Section 11: name matching must not treat people differently because of how their name is written.

The cohorts are SYNTHETIC and small. This is a regression harness, not a validated fairness evaluation: it pins the properties
that must never regress, and it states the known disparity openly: names in scripts the scorer cannot read always go to a person.
"""

from __future__ import annotations

import pytest
from zid_platform.modules.credential.application.identity_match import (
    Holder,
    comparable,
    match_holder,
)
from zid_platform.modules.credential.domain.claims import SubjectMatch

# (name on the document, the same person's name as the source writes it)
TRUE_PAIRS: dict[str, list[tuple[str, str]]] = {
    "LATIN": [("Jane Mary Doe", "DOE JANE MARY"), ("Robert Smith", "Smith, Robert")],
    "DIACRITICS": [
        ("Jose Alvarez", "José Álvarez"),
        ("Lukasz Wisniewski", "Łukasz Wiśniewski"),
        ("Soren Kierkegaard", "Søren Kierkegaard"),
        ("Nguyen Van An", "Nguyễn Văn An"),
    ],
    "CYRILLIC": [("Sergey Ivanov", "Сергей Иванов"), ("Olga Petrova", "Ольга Петрова")],
    "GREEK": [("Nikos Papadopoulos", "Νίκος Παπαδόπουλος")],
    "ARABIC": [("Mohammed Ali", "محمد علي"), ("Fatima Hassan", "فاطمة حسن")],
    "CJK": [("Wang Wei", "王伟"), ("Li Na", "李娜"), ("Kim Min-jun", "김민준")],
    "INDIC": [("Priya Sharma", "प्रिया शर्मा")],
    "HEBREW": [("Yael Cohen", "יעל כהן")],
}
# cohorts the scorer reads: these must be matched outright
READABLE = ("LATIN", "DIACRITICS", "CYRILLIC", "GREEK")
UNREADABLE = ("CJK", "INDIC", "HEBREW")
DIFFERENT = [
    ("Priya Raman", "Dana Lee"),
    ("Jane Doe", "John Roe"),
    ("Sergey Ivanov", "Ольга Петрова"),
    ("Jose Alvarez", "Søren Kierkegaard"),
]


def decide(a: str, b: str) -> SubjectMatch:
    return match_holder(Holder(a), b)[0]


@pytest.mark.parametrize("cohort", sorted(TRUE_PAIRS))
def test_a_true_pair_is_never_called_a_different_person(cohort: str) -> None:
    for doc, source in TRUE_PAIRS[cohort]:
        assert decide(doc, source) is not SubjectMatch.NOT_MATCHED, (cohort, doc, source)


@pytest.mark.parametrize("cohort", READABLE)
def test_readable_cohorts_match_outright(cohort: str) -> None:
    for doc, source in TRUE_PAIRS[cohort]:
        assert decide(doc, source) is SubjectMatch.MATCHED, (cohort, doc, source)


@pytest.mark.parametrize("cohort", UNREADABLE)
def test_unreadable_scripts_always_go_to_a_person_with_a_clear_reason(cohort: str) -> None:
    for doc, source in TRUE_PAIRS[cohort]:
        decision, why = match_holder(Holder(doc), source)
        assert decision is SubjectMatch.AMBIGUOUS and why == ("SCRIPT_NOT_COMPARABLE",)


def test_the_arabic_cohort_is_never_automatically_refused() -> None:
    # known disparity: a transliteration gap can leave it AMBIGUOUS (reviewed by a person) rather than MATCHED
    for doc, source in TRUE_PAIRS["ARABIC"]:
        assert decide(doc, source) in {SubjectMatch.MATCHED, SubjectMatch.AMBIGUOUS}


@pytest.mark.parametrize(("a", "b"), DIFFERENT)
def test_different_people_are_never_matched(a: str, b: str) -> None:
    assert decide(a, b) is not SubjectMatch.MATCHED


def test_a_different_person_in_a_readable_script_is_still_refused() -> None:
    assert decide("Priya Raman", "Dana Lee") is SubjectMatch.NOT_MATCHED


def test_comparable_recognizes_the_scripts_the_scorer_reads() -> None:
    assert comparable("José Álvarez") and comparable("Сергей") and comparable("محمد")
    assert not comparable("王伟") and not comparable("김민준") and not comparable("יעל")
