"""Screening across scripts and list families (ZID-ED-04 section 05): other scripts meet their Latin spellings, the OFAC
consolidated files and a country's own list load, and a pack can require that list before anyone is screened."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Policy,
    Role,
    ScreeningInput,
    ScreeningStatus,
    Subject,
    decide,
)
from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot
from zid_platform.modules.screening.domain.matching import Party, Strength, name_score, screen_party
from zid_platform.modules.screening.domain.ownership import (
    CountryPack,
    Edge,
    Graph,
    Holder,
    HolderKind,
)
from zid_platform.modules.screening.infrastructure.parsers import (
    PARSERS,
    ListFormatError,
    parse_local_csv,
    parse_ofac,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
RU = "Иван Петров"  # Ivan Petrov
GR = "Μιχάλης Παπαδόπουλος"  # Michalis Papadopoulos
AR = "محمد"  # Muhammad
HAN = "株式会社アクメ"


def entry(list_id: ListId = ListId.UN_SC, **kw: object) -> Entry:
    base: dict[str, object] = {
        "list_id": list_id,
        "source_ref": "1",
        "kind": PartyKind.PERSON,
        "primary_name": "Ivan Petrov",
    }
    return Entry(**{**base, **kw})  # type: ignore[arg-type]


def snap(list_id: ListId, *entries: Entry) -> Snapshot:
    return Snapshot(list_id, "v1", "h" * 64, NOW - timedelta(hours=1), tuple(entries))


def person(name: str, **kw: object) -> Party:
    return Party("p1", PartyKind.PERSON, name, **kw)  # type: ignore[arg-type]


def test_cyrillic_greek_and_arabic_names_meet_their_latin_spellings() -> None:
    assert name_score(RU, "Ivan Petrov") == 1.0
    assert name_score(RU, "Ivan Petroff") == 1.0  # -ov, -off and -of are one surname
    assert name_score(GR, "Michalis Papadopoulos") >= 0.9
    assert name_score(AR, "Muhammad") >= 0.9 and name_score(AR, "Mohammed") >= 0.9
    assert (
        name_score(RU, "Peter Smith") < 0.5 and name_score(AR, "Jane Doe") < 0.5
    )  # a script change is no free match


def test_a_listed_name_in_another_script_raises_an_alert_for_its_latin_spelling() -> None:
    s = snap(ListId.UN_SC, entry(primary_name=RU, birth=("1970",)))
    (m,) = screen_party(person("Ivan Petrov", birth="1970"), [s])
    assert m.strength is Strength.STRONG and "NAME_EXACT" in m.reasons
    s2 = snap(ListId.UN_SC, entry(primary_name="Someone Else", aliases=(AR,)))
    assert screen_party(
        person("Muhammad"), [s2]
    )  # the original-script alias is matched through its transliteration


def test_the_consonant_comparison_is_only_for_names_that_changed_script() -> None:
    assert name_score("Mhmd", "Muhammad") < 0.88  # two Latin names are compared as they are written
    assert name_score(AR, "Muhammad") >= 0.88


def test_other_scripts_are_compared_as_written() -> None:
    assert name_score(HAN, HAN) == 1.0
    assert (
        name_score(HAN, "Acme Corporation") < 0.5
    )  # no transliteration is attempted: the list's own aliases cover this


OFAC_SDN = (
    b'36,"PETROV, Ivan","individual","RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 12 Mar 1970"\n'
    b'42,"ACME TRADING LLC",-0- ,"SDGT",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- \n'
)
OFAC_ALT = b'36,1,"aka","IVAN PETROFF",-0- \n'
OFAC_ADD = b'36,1,"Tverskaya 1","Moscow 125009","Russia",-0- \n'


def test_the_ofac_consolidated_files_load_as_their_own_list_with_the_sdn_layout() -> None:
    entries = parse_ofac(OFAC_SDN, OFAC_ALT, OFAC_ADD, list_id=ListId.OFAC_NON_SDN)
    assert {e.list_id for e in entries} == {ListId.OFAC_NON_SDN} and len(entries) == 2
    assert (
        PARSERS[ListId.OFAC_NON_SDN](OFAC_SDN, OFAC_ALT, OFAC_ADD)[0].list_id is ListId.OFAC_NON_SDN
    )
    assert {e.list_id for e in parse_ofac(OFAC_SDN)} == {ListId.OFAC_SDN}


HEADER = b"list_key,source_ref,kind,primary_name,aliases,birth,countries,identifiers,programs\n"
LOCAL_CSV = (
    HEADER
    + b"AE-LTL,7,PERSON,Khalid Example,Khaled Eksample|K. Example,1975,AE|Iran,P1234567|X9876543,Terrorism\n"
    + b"AE-LTL,8,ENTITY,Example Trading FZE,,,,,\n"
)


def test_a_countrys_own_list_loads_from_the_documented_local_format() -> None:
    entries = {e.source_ref: e for e in parse_local_csv(LOCAL_CSV)}
    assert set(entries) == {"AE-LTL:7", "AE-LTL:8"} and {e.list_id for e in entries.values()} == {
        ListId.LOCAL
    }
    p = entries["AE-LTL:7"]
    assert (
        p.aliases == ("Khaled Eksample", "K. Example")
        and p.birth == ("1975",)
        and p.countries == ("ae", "iran")
    )
    assert p.identifiers == ("P1234567", "X9876543") and p.programs == ("Terrorism",)
    assert entries["AE-LTL:8"].kind is PartyKind.ENTITY and entries["AE-LTL:8"].programs == (
        "AE-LTL",
    )


@pytest.mark.parametrize(
    "raw",
    [
        b"name,ref\nx,1\n",
        HEADER + b"AE,1,PERSONN,Name,,,,,\n",  # not a kind
        HEADER + b"AE,1,PERSON,,,,,,\n",  # no name
        HEADER + b"AE,1,PERSON,Name\n",  # short row
        HEADER,  # nothing in it
    ],
)
def test_a_local_list_that_does_not_follow_the_format_is_refused_whole(raw: bytes) -> None:
    with pytest.raises(ListFormatError):
        parse_local_csv(raw)


POLICY = Policy(
    "p1",
    (
        ListId.OFAC_SDN,
        ListId.OFAC_NON_SDN,
        ListId.UK_SANCTIONS,
        ListId.EU_CONSOLIDATED,
        ListId.UN_SC,
    ),
    timedelta(hours=48),
    timedelta(days=365),
    timedelta(days=90),
)


def _inputs(pack: CountryPack, snapshots: dict[ListId, Snapshot]) -> ScreeningInput:
    graph = Graph(
        "co",
        {"co": Holder("co", HolderKind.ENTITY), "o": Holder("o", HolderKind.PERSON)},
        (Edge("o", "co", Decimal(60)),),
    )
    return ScreeningInput(
        country=pack.country,
        subjects=(
            Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
            Subject(Role.REPRESENTATIVE, Party("rep", PartyKind.PERSON, "Alice Smith")),
            Subject(Role.OWNER, Party("o", PartyKind.PERSON, "Olivia Owner")),
        ),
        graph=graph,
        authority=AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-1"),
        elevated_risk=False,
        snapshots=snapshots,
        pack=pack,
        policy=POLICY,
        now=NOW,
    )


def test_a_pack_can_require_the_countrys_own_list_before_anyone_is_screened() -> None:
    lists = {
        lid: snap(lid, entry(lid, primary_name="Nobody Atall")) for lid in POLICY.required_lists
    }
    ae = CountryPack("AE", "a1", None, None, ("ofac_50",), local_list_required=True)
    d = decide(_inputs(ae, lists))
    assert d.status is ScreeningStatus.UNAVAILABLE and "LIST_UNAVAILABLE:LOCAL" in d.reasons
    lists[ListId.LOCAL] = snap(
        ListId.LOCAL, entry(ListId.LOCAL, source_ref="AE:1", primary_name="Someone Unrelated")
    )
    ok = decide(_inputs(ae, lists))
    assert ok.status is ScreeningStatus.VERIFIED and len(ok.lists) == 6
    # and a country whose pack does not ask for it is not held up by it
    gb = CountryPack("GB", "g1", None, None, ("ofac_50",))
    lists.pop(ListId.LOCAL)
    assert decide(_inputs(gb, lists)).status is ScreeningStatus.VERIFIED
