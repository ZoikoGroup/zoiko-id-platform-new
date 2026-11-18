"""WF06 match quality (CR-113): transliteration with a trace, original spellings kept, expired identifiers, missing identifiers (QA-012 to QA-018)."""

from __future__ import annotations

from datetime import UTC, datetime

from zid_platform.modules.screening.domain import matching as mt
from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot

POLICY = mt.MatchPolicy()


def snap(*entries: Entry) -> Snapshot:
    return Snapshot(
        ListId.OFAC_SDN, "v1", "h" * 64, datetime(2026, 11, 18, tzinfo=UTC), tuple(entries)
    )


def entry(name="Sergei Ivanov", kind=PartyKind.PERSON, **kw) -> Entry:
    return Entry(ListId.OFAC_SDN, kw.pop("ref", "100"), kind, name, **kw)


def screen(party: mt.Party, *entries: Entry):
    return mt.screen_party(party, [snap(*entries)], POLICY)


def person(name, **kw) -> mt.Party:
    return mt.Party("p1", PartyKind.PERSON, name, **kw)


def test_a_cyrillic_name_matches_its_latin_spelling_and_the_trace_says_how() -> None:
    hits = screen(
        person("Сергей Иванов", birth="1970", countries=("ru",)),
        entry(birth=("1970",), countries=("ru",)),
    )
    assert hits and hits[0].list_id is ListId.OFAC_SDN  # QA-014
    t = mt.trace("Сергей Иванов")
    assert t == [
        {
            "script": "CYRILLIC",
            "letters": "".join(sorted(set("сергйиванов"))),
            "handling": "TRANSLITERATED",
        }
    ]


def test_an_arabic_name_matches_and_is_traced() -> None:
    hits = screen(person("محمد حسن", birth="1980"), entry("Muhammad Hasan", birth=("1980",)))
    assert hits
    assert [x["script"] for x in mt.trace("محمد حسن")] == ["ARABIC"]


def test_a_devanagari_name_is_written_in_latin_for_sanctions_screening() -> None:
    assert mt.transliterate("राम", indic=True) == ("rama", True)
    assert mt.transliterate("कृष्ण", indic=True)[0].startswith("krish")
    assert (
        mt.transliterate("राजेश", indic=True)[0] == "rajesha"
    )  # lossy by design: the schwa is not dropped, so a reviewer reads the alert
    hits = screen(person("राजेश कुमार", birth="1975"), entry("Rajesh Kumar", birth=("1975",)))
    assert hits  # QA-014: Devanagari
    assert [x["script"] for x in mt.trace("राजेश")] == ["DEVANAGARI"]


def test_identity_matching_is_unchanged_because_devanagari_is_opt_in() -> None:
    assert mt.transliterate("राम") == (
        "राम",
        False,
    )  # the fairness rule stays: Indic names go to a person in identity matching
    assert mt.name_score("राम", "Rama") < 0.5


def test_chinese_is_compared_as_written_and_the_trace_says_so() -> None:
    t = mt.trace("王小明")
    assert t[0]["script"] == "AS_WRITTEN:CJK" and t[0]["handling"] == "AS_WRITTEN"
    hits = screen(person("王小明"), entry("Wang Xiaoming", aliases=("王小明",)))
    assert (
        hits and hits[0].matched_name == "王小明"
    )  # only the identical original-script alias matches
    assert (
        screen(person("王小明"), entry("Wang Xiaoming")) == []
    )  # no transliteration table: a reviewer's own checks stay mandatory


def test_accented_hyphenated_and_multi_part_names_match_and_the_original_is_what_is_recorded() -> (
    None
):
    party = person("José María García-López de la Cruz", birth="1962")
    hits = screen(party, entry("Jose Maria Garcia Lopez de la Cruz", birth=("1962",)))
    assert hits and hits[0].score >= POLICY.threshold  # QA-015
    assert (
        party.name == "José María García-López de la Cruz"
    )  # the party keeps the exact original; folding never overwrites it


def test_a_common_name_with_a_conflicting_birth_year_is_a_candidate_never_strong() -> None:
    hits = screen(person("John Smith", birth="1990"), entry("John Smith", birth=("1950",)))
    assert (
        hits
        and hits[0].strength is mt.Strength.POSSIBLE
        and "BIRTH_YEAR_CONFLICT" in hits[0].reasons
    )  # QA-012, QA-013


def test_an_entity_never_matches_an_individual_only_entry_by_name() -> None:
    company = mt.Party("c1", PartyKind.ENTITY, "Sergei Ivanov")
    assert (
        screen(company, entry("Sergei Ivanov")) == []
    )  # QA-016: the kinds are compared, never mixed
    vessel = mt.Party("v1", PartyKind.OTHER, "Sergei Ivanov")
    assert screen(vessel, entry("Sergei Ivanov", kind=PartyKind.OTHER)) == []


def test_an_expired_identifier_is_a_candidate_for_a_person_and_never_a_strong_match() -> None:
    e = entry("Completely Different Name", identifiers=("AB1234567",))
    live = screen(person("Someone Else", identifiers=("AB1234567",)), e)
    assert live and live[0].strength is mt.Strength.STRONG and "IDENTIFIER_MATCH" in live[0].reasons
    lapsed = screen(person("Someone Else", expired_identifiers=("AB1234567",)), e)
    assert lapsed and lapsed[0].strength is mt.Strength.POSSIBLE  # QA-017
    assert (
        "IDENTIFIER_MATCH_EXPIRED_DOCUMENT" in lapsed[0].reasons
        and "IDENTIFIER_MATCH" not in lapsed[0].reasons
    )


def test_an_expired_identifier_is_not_counted_as_current_when_the_same_number_is_listed_both_ways() -> (
    None
):
    e = entry("Completely Different Name", identifiers=("AB1234567",))
    both = screen(
        person("Someone Else", identifiers=("AB1234567",), expired_identifiers=("AB1234567",)), e
    )
    assert (
        both
        and both[0].strength is mt.Strength.POSSIBLE
        and "IDENTIFIER_MATCH" not in both[0].reasons
    )


def test_missing_secondary_identifiers_are_flagged_and_never_read_as_negative_evidence() -> None:
    bare = screen(person("Sergei Ivanov"), entry(birth=("1970",)))
    assert bare and "NO_SECONDARY_IDENTIFIERS" in bare[0].reasons  # QA-018
    rich = screen(person("Sergei Ivanov", birth="1970"), entry(birth=("1970",)))
    assert "NO_SECONDARY_IDENTIFIERS" not in rich[0].reasons
    assert bare[0].score >= POLICY.threshold  # a missing date does not remove the alert


def test_a_name_with_no_foreign_script_has_no_trace() -> None:
    assert mt.trace("Sergei Ivanov") == []
