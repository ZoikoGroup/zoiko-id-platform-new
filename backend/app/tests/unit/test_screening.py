"""Director, beneficial-owner and sanctions screening (ZID-ED-04 acceptance tests): alerts are for people, a missing or stale
list is never a clear, a registered company is not a person's authority, and UK and U.S. ownership rules differ."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from zid_platform.modules.screening.domain.decision import (
    AuthorityEvidence,
    AuthorityKind,
    Policy,
    Role,
    Ruling,
    ScreeningError,
    ScreeningInput,
    ScreeningStatus,
    Subject,
    check_ruling,
    decide,
    effective_status,
    evidence_object,
    safe_view,
)
from zid_platform.modules.screening.domain.lists import Entry, ListId, PartyKind, Snapshot
from zid_platform.modules.screening.domain.matching import (
    MatchPolicy,
    Party,
    Strength,
    name_score,
    screen_party,
)
from zid_platform.modules.screening.domain.ownership import (
    Control,
    CountryPack,
    Edge,
    Graph,
    Holder,
    HolderKind,
    evaluate,
    ofac_50,
    ofsi_owned_or_controlled,
    uk_psc,
)
from zid_platform.modules.screening.infrastructure.config import config, parse
from zid_platform.modules.screening.infrastructure.parsers import (
    ListFormatError,
    parse_eu_xml,
    parse_ofac,
    parse_uk_csv,
    parse_un_xml,
)

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
D = Decimal
POLICY = Policy(
    "p1",
    (ListId.OFAC_SDN, ListId.UK_SANCTIONS, ListId.EU_CONSOLIDATED, ListId.UN_SC),
    timedelta(hours=48),
    timedelta(days=365),
    timedelta(days=90),
)


def entry(list_id: ListId = ListId.OFAC_SDN, **kw: object) -> Entry:
    base: dict[str, object] = {
        "list_id": list_id,
        "source_ref": "100",
        "kind": PartyKind.PERSON,
        "primary_name": "Ivan Petrov",
    }
    return Entry(**{**base, **kw})  # type: ignore[arg-type]


def snap(
    list_id: ListId, *entries: Entry, age: timedelta = timedelta(hours=1), version: str = "v1"
) -> Snapshot:
    return Snapshot(
        list_id,
        version,
        "h" * 64,
        NOW - age,
        tuple(entries) or (entry(list_id, source_ref="0", primary_name="Nobody Atall"),),
    )


def all_lists(**per_list: Entry) -> dict[ListId, Snapshot]:
    return {
        lid: snap(lid, *([per_list[lid.value]] if lid.value in per_list else []))
        for lid in POLICY.required_lists
    }


def person(ref: str = "p1", name: str = "Jane Doe", **kw: object) -> Party:
    return Party(ref, PartyKind.PERSON, name, **kw)  # type: ignore[arg-type]


# ---- matching: alerts, never judgements --------------------------------------------------------------------------------


def test_a_close_name_alone_is_a_possible_match_never_a_strong_one() -> None:
    s = snap(ListId.OFAC_SDN, entry(primary_name="Ivan Petrov"))
    (m,) = screen_party(person(name="Ivan Petrov"), [s])
    assert m.strength is Strength.POSSIBLE and "NAME_EXACT" in m.reasons and m.score == 1.0


def test_an_identifier_or_a_matching_name_and_birth_year_is_strong() -> None:
    s = snap(
        ListId.OFAC_SDN,
        entry(primary_name="Ivan Petrov", birth=("1970",), identifiers=("A1234567",)),
    )
    by_id = screen_party(person(name="Completely Different", identifiers=("a-1234567",)), [s])
    assert by_id[0].strength is Strength.STRONG and "IDENTIFIER_MATCH" in by_id[0].reasons
    by_birth = screen_party(person(name="Ivan Petrov", birth="1970-05-01"), [s])
    assert by_birth[0].strength is Strength.STRONG and "BIRTH_YEAR_MATCH" in by_birth[0].reasons


def test_a_namesake_with_another_birth_year_is_still_an_alert_with_the_conflict_stated() -> None:
    s = snap(ListId.OFAC_SDN, entry(primary_name="Ivan Petrov", birth=("1970",)))
    (m,) = screen_party(person(name="Ivan Petrov", birth="1985"), [s])
    assert (
        m.strength is Strength.POSSIBLE and "BIRTH_YEAR_CONFLICT" in m.reasons
    )  # a reviewer reads it; nobody is rejected


def test_names_match_without_case_accents_punctuation_or_word_order() -> None:
    assert name_score("Müller, Hans", "HANS MULLER") == 1.0
    assert name_score("Petrov Ivan", "Ivan Petrov") == 1.0
    assert name_score("Ivan Petrov", "Totally Unrelated") < 0.5
    s = snap(
        ListId.UN_SC, entry(ListId.UN_SC, primary_name="Someone Else", aliases=("Hans Muller",))
    )
    assert screen_party(person(name="Müller Hans"), [s])  # an alias is as good as the primary name


def test_unrelated_names_and_the_wrong_kind_of_party_raise_no_alert() -> None:
    s = snap(ListId.OFAC_SDN, entry(primary_name="Ivan Petrov"))
    assert screen_party(person(name="Jane Doe"), [s]) == []
    company = Party("c1", PartyKind.ENTITY, "Ivan Petrov")
    assert screen_party(company, [s]) == []  # an entity is not matched to a person by name alone
    ship = snap(ListId.OFAC_SDN, entry(kind=PartyKind.OTHER, primary_name="Jane Doe"))
    assert screen_party(person(name="Jane Doe"), [ship]) == []


def test_the_threshold_is_the_policys_and_short_identifiers_never_collide() -> None:
    s = snap(ListId.OFAC_SDN, entry(primary_name="Ivan Petrovich", identifiers=("1234",)))
    assert screen_party(person(name="Ivan Petrov"), [s], MatchPolicy(threshold=0.99)) == []
    assert screen_party(person(name="Ivan Petrov"), [s], MatchPolicy(threshold=0.80))
    assert (
        screen_party(person(name="Zed Quux", identifiers=("1234",)), [s]) == []
    )  # under five characters: not an identifier


# ---- ownership: UK and U.S. rules give different answers --------------------------------------------------------------


def graph(
    *edges: Edge, holders: dict[str, HolderKind] | None = None, terminal: set[str] | None = None
) -> Graph:
    kinds = {"co": HolderKind.ENTITY, **(holders or {})}
    for e in edges:
        kinds.setdefault(e.owner, HolderKind.PERSON)
    return Graph(
        "co", {r: Holder(r, k, r in (terminal or set())) for r, k in kinds.items()}, tuple(edges)
    )


def own(
    owner: str, owned: str = "co", shares: str | None = None, votes: str | None = None, **kw: object
) -> Edge:
    return Edge(owner, owned, D(shares) if shares else None, D(votes) if votes else None, **kw)  # type: ignore[arg-type]


def test_the_uk_psc_test_is_over_twenty_five_percent_or_control_and_only_for_people() -> None:
    g = graph(
        own("a", shares="26"),
        own("b", shares="25"),
        own("c", shares="10", controls=frozenset({Control.APPOINT_BOARD_MAJORITY})),
    )
    assert uk_psc(g, D(25)) == ["a", "c"]  # exactly 25 is not over 25
    chain = graph(
        own("holdco", shares="60"), own("p", "holdco", shares="50"),
        holders={"holdco": HolderKind.ENTITY},
    )  # fmt: skip
    assert uk_psc(chain, D(25)) == [
        "p"
    ]  # 50% of 60% is 30%: indirect, and the intermediary is not a person
    votes = graph(own("v", shares="5", votes="40"))
    assert uk_psc(votes, D(25)) == ["v"]  # votes count as well as shares


def test_the_us_rule_adds_designated_holdings_together_and_the_uk_rule_does_not() -> None:
    two = graph(own("d1", shares="30"), own("d2", shares="30"))
    designated = frozenset({"d1", "d2"})
    assert ofac_50(two, designated) is True  # 30 + 30 reaches 50 in aggregate
    assert (
        ofsi_owned_or_controlled(two, designated) is False
    )  # unrelated designated holders are not added up
    just_under = graph(own("d1", shares="30"), own("d2", shares="19"))
    assert ofac_50(just_under, frozenset({"d1", "d2"})) is False
    single = graph(own("d1", shares="51"))
    assert (
        ofsi_owned_or_controlled(single, frozenset({"d1"})) is True
        and ofac_50(single, frozenset({"d1"})) is True
    )
    exactly = graph(own("d1", shares="50"))
    assert (
        ofac_50(exactly, frozenset({"d1"})) is True
        and ofsi_owned_or_controlled(exactly, frozenset({"d1"})) is False
    )


def test_an_entity_blocked_by_ownership_blocks_what_it_owns_in_turn() -> None:
    g = Graph(
        "co",
        {"d1": Holder("d1", HolderKind.PERSON), "d2": Holder("d2", HolderKind.PERSON), "mid": Holder("mid", HolderKind.ENTITY), "co": Holder("co", HolderKind.ENTITY)},
        (own("d1", "mid", shares="30"), own("d2", "mid", shares="30"), own("mid", "co", shares="60")),
    )  # fmt: skip
    assert (
        ofac_50(g, frozenset({"d1", "d2"})) is True
    )  # mid is blocked (30 + 30), and it holds 60% of the company


def test_control_without_ownership_counts_for_the_uk_test_only() -> None:
    g = graph(own("d1", shares="10", controls=frozenset({Control.OTHER_CONTROL})))
    assert ofsi_owned_or_controlled(g, frozenset({"d1"})) is True
    assert (
        ofac_50(g, frozenset({"d1"})) is False
    )  # OFAC's 50 percent rule is about ownership, assessed apart from control


@pytest.mark.parametrize(
    ("build", "reason"),
    [
        (
            lambda: (
                graph(
                    own("a", shares="40"),
                    holders={"u": HolderKind.UNKNOWN},
                )
                if False
                else Graph(
                    "co",
                    {"co": Holder("co", HolderKind.ENTITY), "u": Holder("u", HolderKind.UNKNOWN)},
                    (own("u", shares="60"),),
                )
            ),
            "OWNER_UNKNOWN",
        ),
        (
            lambda: graph(own("holdco", shares="60"), holders={"holdco": HolderKind.ENTITY}),
            "MISSING_INTERMEDIARY_OWNER",
        ),
        (lambda: graph(Edge("a", "co")), "HOLDING_UNQUANTIFIED"),
        (lambda: graph(own("a", shares="60", verified=False)), "HOLDING_UNVERIFIED"),
        (lambda: graph(holders={}), "NO_OWNERS_DECLARED"),
    ],
)
def test_a_chain_with_a_gap_is_unresolved_never_clear(build: object, reason: str) -> None:
    from zid_platform.modules.screening.domain.ownership import unresolved_reasons

    assert reason in unresolved_reasons(build())  # type: ignore[operator]


def test_a_listed_company_or_state_body_needs_no_owner_behind_it() -> None:
    from zid_platform.modules.screening.domain.ownership import unresolved_reasons

    g = graph(own("plc", shares="60"), holders={"plc": HolderKind.ENTITY}, terminal={"plc"})
    assert "MISSING_INTERMEDIARY_OWNER" not in unresolved_reasons(g)


# ---- the decision ------------------------------------------------------------------------------------------------------

GB = CountryPack("GB", "g1", "uk_psc", D(25), ("ofsi", "ofac_50"))
OFFICER = AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, "officer-entry-1")


def clean_inputs(**over: object) -> ScreeningInput:
    g = graph(own("p-owner", shares="60"))
    subjects = (
        Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
        Subject(Role.REPRESENTATIVE, person("rep", "Alice Smith")),
        Subject(Role.OWNER, person("p-owner", "Olivia Owner")),
    )
    base: dict[str, object] = {
        "country": "GB", "subjects": subjects, "graph": g, "authority": OFFICER, "elevated_risk": False,
        "snapshots": all_lists(), "pack": GB, "policy": POLICY, "now": NOW,
    }  # fmt: skip
    return ScreeningInput(**{**base, **over})  # type: ignore[arg-type]


def test_a_clean_screening_with_proven_authority_is_verified_with_the_policys_expiry() -> None:
    d = decide(clean_inputs())
    assert d.status is ScreeningStatus.VERIFIED and d.authority_verified and d.matches == ()
    assert d.expiry_at == NOW + timedelta(days=365)
    assert len(d.lists) == 4 and all(
        len(h) == 64 for _, _, h in d.lists
    )  # the exact lists that cleared it
    assert decide(clean_inputs(elevated_risk=True)).expiry_at == NOW + timedelta(days=90)


def test_a_missing_or_stale_list_means_not_screened_never_clear() -> None:
    missing = all_lists()
    del missing[ListId.EU_CONSOLIDATED]
    d = decide(clean_inputs(snapshots=missing))
    assert (
        d.status is ScreeningStatus.UNAVAILABLE and "LIST_UNAVAILABLE:EU_CONSOLIDATED" in d.reasons
    )
    stale = all_lists()
    stale[ListId.UN_SC] = snap(ListId.UN_SC, age=timedelta(hours=49))
    d = decide(clean_inputs(snapshots=stale))
    assert d.status is ScreeningStatus.UNAVAILABLE and "LIST_STALE:UN_SC" in d.reasons
    assert d.expiry_at is None


def test_an_alert_waits_for_a_person_and_is_never_rejected_by_a_rule() -> None:
    lists = all_lists(UN_SC=entry(ListId.UN_SC, primary_name="Alice Smith", birth=("1980",)))
    inp = clean_inputs(
        snapshots=lists,
        subjects=(
            Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme Widgets Limited")),
            Subject(Role.REPRESENTATIVE, person("rep", "Alice Smith", birth="1980")),
            Subject(Role.OWNER, person("p-owner", "Olivia Owner")),
        ),
    )
    d = decide(inp)
    assert d.status is ScreeningStatus.REVIEW_REQUIRED and d.status is not ScreeningStatus.REJECTED
    assert "SCREENING_ALERT" in d.reasons and "ALERT_STRONG" in d.reasons and d.expiry_at is None


def test_a_company_that_exists_is_not_a_persons_authority_to_act_for_it() -> None:
    for authority in (
        AuthorityEvidence(AuthorityKind.NONE),
        AuthorityEvidence(AuthorityKind.REGISTER_OFFICER, None),  # nothing to point at
        AuthorityEvidence(
            AuthorityKind.DELEGATED_MANDATE, "mandate-1", NOW - timedelta(days=1)
        ),  # expired
    ):
        d = decide(clean_inputs(authority=authority))
        assert d.status is ScreeningStatus.REVIEW_REQUIRED and not d.authority_verified
        assert "AUTHORITY_UNPROVEN" in d.reasons
    # an expired mandate is unproven even when a reviewer once confirmed it
    lapsed = AuthorityEvidence(
        AuthorityKind.DELEGATED_MANDATE, "m-1", NOW - timedelta(days=1), "DELEGATED_EMPLOYEE", True
    )
    assert "AUTHORITY_UNPROVEN" in decide(clean_inputs(authority=lapsed)).reasons


def test_a_delegated_mandate_proves_nothing_until_a_person_confirms_it_and_never_outlives_its_end_date() -> (
    None
):
    """The applicant's own document is evidence for a reviewer, not proof: it waits for a person, a confirmed one proves authority, and the
    clear outcome ends no later than the mandate does."""
    ends = NOW + timedelta(days=10)
    declared = AuthorityEvidence(
        AuthorityKind.DELEGATED_MANDATE, "m-1", ends, "EXTERNAL_REPRESENTATIVE"
    )
    waiting = decide(clean_inputs(authority=declared))
    assert waiting.status is ScreeningStatus.REVIEW_REQUIRED and not waiting.authority_verified
    assert (
        "AUTHORITY_MANDATE_REVIEW" in waiting.reasons
        and "AUTHORITY_UNPROVEN" not in waiting.reasons
    )
    assert not declared.valid(NOW)

    confirmed = AuthorityEvidence(
        AuthorityKind.DELEGATED_MANDATE, "m-1", ends, "EXTERNAL_REPRESENTATIVE", True
    )
    ok = decide(clean_inputs(authority=confirmed))
    assert ok.status is ScreeningStatus.VERIFIED and ok.authority_verified
    assert (
        ok.expiry_at is not None and ok.expiry_at <= ends
    )  # the policy's validity is longer; the mandate is what bounds it
    assert not confirmed.valid(ends)  # and on its end date it proves nothing

    # a register officer is unaffected: the register is the proof, and nothing about a mandate applies
    officer = AuthorityEvidence(
        AuthorityKind.REGISTER_OFFICER, "root-1", None, "DIRECTOR_OR_OFFICER"
    )
    assert decide(clean_inputs(authority=officer)).status is ScreeningStatus.VERIFIED


def test_entity_and_representative_are_always_required() -> None:
    only_entity = clean_inputs(
        subjects=(Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme")),)
    )
    d = decide(only_entity)
    assert d.status is ScreeningStatus.GATHERING and "MISSING_PARTY:REPRESENTATIVE" in d.reasons


def test_ownership_is_needed_where_the_countrys_rules_ask_for_it() -> None:
    d = decide(clean_inputs(graph=None))
    assert d.status is ScreeningStatus.GATHERING and d.reasons == ("MISSING_OWNERSHIP",)


def test_a_country_without_a_pack_is_a_review_never_a_pass_and_no_universal_threshold_is_used() -> (
    None
):
    d = decide(clean_inputs(pack=None, graph=None, country="DE"))
    assert d.status is ScreeningStatus.REVIEW_REQUIRED and "COUNTRY_PACK_MISSING" in d.reasons


def test_an_owner_the_rule_says_to_identify_but_nobody_screened_is_flagged() -> None:
    inp = clean_inputs(
        subjects=(
            Subject(Role.ENTITY, Party("co", PartyKind.ENTITY, "Acme")),
            Subject(Role.REPRESENTATIVE, person("rep", "Alice Smith")),
        )
    )  # the 60% owner p-owner is a PSC but was never collected or screened
    d = decide(inp)
    assert d.status is ScreeningStatus.REVIEW_REQUIRED and "OWNER_NOT_SCREENED:p-owner" in d.reasons


def test_a_designated_owner_with_half_the_company_is_a_sanctions_ownership_finding() -> None:
    lists = all_lists(OFAC_SDN=entry(primary_name="Olivia Owner"))
    d = decide(clean_inputs(snapshots=lists, graph=graph(own("p-owner", shares="55"))))
    assert d.status is ScreeningStatus.REVIEW_REQUIRED
    assert {"OFAC_50_PERCENT_AGGREGATE", "OFSI_OWNED_OR_CONTROLLED"} <= set(d.reasons)


def test_a_chain_with_a_missing_intermediary_cannot_silently_pass() -> None:
    gap = graph(own("holdco", shares="60"), holders={"holdco": HolderKind.ENTITY})
    d = decide(clean_inputs(graph=gap))
    assert (
        d.status is ScreeningStatus.REVIEW_REQUIRED
        and "OWNERSHIP_MISSING_INTERMEDIARY_OWNER" in d.reasons
    )


def test_a_clear_outcome_expires_and_reads_as_expired() -> None:
    d = decide(clean_inputs())
    assert effective_status(d.status, d.expiry_at, NOW) is ScreeningStatus.VERIFIED
    assert effective_status(d.status, d.expiry_at, d.expiry_at) is ScreeningStatus.EXPIRED  # type: ignore[arg-type]


def test_evidence_names_lists_and_policy_and_holds_no_name_or_birth_date() -> None:
    lists = all_lists(UN_SC=entry(ListId.UN_SC, primary_name="Alice Smith"))
    inp = clean_inputs(snapshots=lists)
    d = decide(inp)
    body = evidence_object(tenant_id="t1", case_id="c1", inp=inp, decision=d, version=1)
    text = repr(body)
    assert (
        body["policy_version"] == "p1"
        and body["country_pack_version"] == "g1"
        and len(body["lists"]) == 4
    )
    for private in ("Alice", "Smith", "Olivia", "Owner", "Acme"):
        assert private not in text
    assert body["matches"][0]["source_ref"] == "100" and "matched_name" not in body["matches"][0]
    assert (
        body["evidence_hash"]
        == evidence_object(tenant_id="t1", case_id="c1", inp=inp, decision=d, version=1)[
            "evidence_hash"
        ]
    )


def test_a_customer_sees_an_outcome_and_a_next_step_only() -> None:
    for status in ScreeningStatus:
        view = safe_view(status, ["OFAC_50_PERCENT_AGGREGATE", "ALERT_STRONG"])
        assert set(view) == {"status", "next_action", "tasks"}
        assert (
            view["tasks"] == []
        )  # nothing from a list or an ownership finding becomes a task the customer can read
        assert "OFAC" not in view["next_action"] and "alert" not in view["next_action"].lower()


def test_a_ruling_needs_a_case_in_review_a_rationale_and_someone_independent() -> None:
    ok = Ruling(
        "rev-1",
        ScreeningStatus.VERIFIED,
        "Namesake confirmed: different date and place of birth on the passport.",
    )
    check_ruling(ScreeningStatus.REVIEW_REQUIRED, ok, requester_id="req", subject_refs=["p1"])
    with pytest.raises(ScreeningError, match="CASE_NOT_IN_REVIEW"):
        check_ruling(ScreeningStatus.VERIFIED, ok, requester_id="req", subject_refs=[])
    with pytest.raises(ScreeningError, match="RATIONALE_REQUIRED"):
        check_ruling(
            ScreeningStatus.REVIEW_REQUIRED,
            Ruling("rev-1", ScreeningStatus.VERIFIED, "fine"),
            requester_id="req",
            subject_refs=[],
        )
    for conflicted in ("req", "p1"):
        with pytest.raises(ScreeningError, match="REVIEWER_CONFLICT"):
            check_ruling(
                ScreeningStatus.REVIEW_REQUIRED,
                Ruling(conflicted, ScreeningStatus.VERIFIED, "x" * 30),
                requester_id="req",
                subject_refs=["p1"],
            )
    with pytest.raises(ScreeningError, match="RULING_OUTCOME_INVALID"):
        check_ruling(
            ScreeningStatus.REVIEW_REQUIRED,
            Ruling("rev-1", ScreeningStatus.EXPIRED, "x" * 30),
            requester_id="req",
            subject_refs=[],
        )


def test_the_two_countries_packs_evaluate_differently() -> None:
    g = graph(own("a", shares="30"), own("b", shares="30"))
    us = CountryPack("US", "u1", None, None, ("ofac_50", "ofsi"))
    assert evaluate(g, us, designated=frozenset({"a", "b"})).flags == ("OFAC_50_PERCENT_AGGREGATE",)
    only_uk = CountryPack("XX", "x1", None, None, ("ofsi",))
    assert evaluate(g, only_uk, designated=frozenset({"a", "b"})).flags == ()
    assert evaluate(g, GB, designated=frozenset()).identify == ("a", "b")


# ---- the shipped configuration -------------------------------------------------------------------------------------------


def test_the_shipped_policy_requires_the_five_families_and_proposes_only_gb_and_us_packs() -> None:
    c = config()
    assert set(c.policy.required_lists) == set(ListId) - {
        ListId.LOCAL,
        ListId.PEP,
    }  # the country's own list is asked for by a pack
    assert c.pack("GB") is not None and c.pack("GB").beneficial_owner_threshold == D(25)  # type: ignore[union-attr]
    assert c.pack("US") is not None and c.pack("US").beneficial_owner_rule is None  # type: ignore[union-attr]
    assert (
        c.pack("DE") is None and c.pack("FR") is None
    )  # no pack, so a review: no universal 25 percent
    assert c.sources[ListId.UN_SC].configured and c.sources[ListId.UN_SC].redirect_hosts
    assert (
        not c.sources[ListId.EU_CONSOLIDATED].configured
        and not c.sources[ListId.OFAC_SDN].configured
    )
    assert c.policy.list_max_age == timedelta(hours=48)


def test_a_bad_policy_is_refused() -> None:
    for doc in (
        {"policy": {"required_lists": []}},
        {"policy": {"required_lists": ["NOPE"]}},
        {
            "policy": {"required_lists": ["UN_SC"]},
            "packs": {"GB": {"beneficial_owner_rule": "uk_psc"}},
        },
        {
            "policy": {"required_lists": ["UN_SC"]},
            "packs": {"GB": {"sanctions_evaluators": ["magic"]}},
        },
    ):
        with pytest.raises(ScreeningError, match="CONFIG_INVALID"):
            parse(doc)


# ---- the list parsers ----------------------------------------------------------------------------------------------------

OFAC_SDN = (
    b'36,"PETROV, Ivan","individual","RUSSIA-EO14024",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"DOB 12 Mar 1970; nationality Russia; Passport 71 1234567 (Russia); alt. DOB 1971"\n'
    b'42,"ACME TRADING LLC",-0- ,"SDGT] [IRGC",-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,-0- ,"Registration ID 1234567890 (Iran); Business Registration Document # AB-998877"\n'
    b'77,"SEA STAR","vessel","IRAN",-0- ,-0- ,"Tanker",-0- ,-0- ,"Panama",-0- ,-0- \n'
)
OFAC_ALT = b'36,1,"aka","IVAN PETROFF",-0- \n36,2,"fka","Ivan P. Petrov",-0- \n'
OFAC_ADD = (
    b'36,1,"Tverskaya 1","Moscow 125009","Russia",-0- \n42,1,"Street 5","Tehran","Iran",-0- \n'
)


def test_ofac_csv_files_are_read_with_aliases_birth_years_documents_and_countries() -> None:
    entries = {e.source_ref: e for e in parse_ofac(OFAC_SDN, OFAC_ALT, OFAC_ADD)}
    assert entries["36"].kind is PartyKind.PERSON and entries["36"].birth == ("1970", "1971")
    assert (
        entries["36"].aliases == ("IVAN PETROFF", "Ivan P. Petrov")
        and "russia" in entries["36"].countries
    )
    assert "711234567" in entries["36"].identifiers or "711234567" in "".join(
        entries["36"].identifiers
    )
    assert entries["42"].kind is PartyKind.ENTITY and "1234567890" in entries["42"].identifiers
    assert entries["42"].programs == ("SDGT", "IRGC") and entries["77"].kind is PartyKind.OTHER


UK_CSV = (
    b"Report Date,2026-10-30\n"
    b"Unique ID,Name 1,Name 2,Name 6,Name type,D.O.B,Nationality(/ies),Passport number,Business registration number (s),Regime Name,Type of ship\n"
    b"RUS0001,Ivan,,Petrov,Primary Name,12/03/1970,Russia,71 1234567,,Russia,\n"
    b"RUS0001,Ivan,,Petroff,Alias,,,,,Russia,\n"
    b"RUS0002,,,Acme Trading Limited,Primary Name,,,,00123456,Russia,\n"
    b"RUS0003,,,Sea Star,Primary Name,,,,,Iran,Tanker\n"
)


def test_the_uk_csv_is_read_by_its_headings_and_grouped_by_unique_id() -> None:
    entries = {e.source_ref: e for e in parse_uk_csv(UK_CSV)}
    assert set(entries) == {"RUS0001", "RUS0002", "RUS0003"}
    p = entries["RUS0001"]
    assert (
        p.kind is PartyKind.PERSON
        and p.primary_name == "Ivan Petrov"
        and p.aliases == ("Ivan Petroff",)
    )
    assert p.birth == ("1970",) and p.countries == ("russia",) and p.programs == ("Russia",)
    assert entries["RUS0002"].kind is PartyKind.ENTITY and entries["RUS0002"].identifiers == (
        "00123456",
    )
    assert entries["RUS0003"].kind is PartyKind.OTHER
    reordered = b"Name 6,Unique ID,Name type\nPetrov,X1,Primary Name\n"
    assert parse_uk_csv(reordered)[0].source_ref == "X1"


UN_XML = b"""<?xml version="1.0"?>
<CONSOLIDATED_LIST dateGenerated="2026-10-29T23:00:03.345Z">
 <INDIVIDUALS>
  <INDIVIDUAL><DATAID>6907993</DATAID><VERSIONNUM>1</VERSIONNUM><FIRST_NAME>ERIC</FIRST_NAME><SECOND_NAME>BADEGE</SECOND_NAME>
   <UN_LIST_TYPE>DRC</UN_LIST_TYPE><REFERENCE_NUMBER>CDi.001</REFERENCE_NUMBER>
   <NATIONALITY><VALUE>Democratic Republic of the Congo</VALUE></NATIONALITY>
   <INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>Eric Badege Mapendo</ALIAS_NAME></INDIVIDUAL_ALIAS>
   <INDIVIDUAL_ALIAS><QUALITY></QUALITY><ALIAS_NAME></ALIAS_NAME></INDIVIDUAL_ALIAS>
   <INDIVIDUAL_ADDRESS><COUNTRY>Rwanda</COUNTRY></INDIVIDUAL_ADDRESS>
   <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE><YEAR>1971</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
   <INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT><NUMBER>OB123456</NUMBER></INDIVIDUAL_DOCUMENT></INDIVIDUAL>
 </INDIVIDUALS>
 <ENTITIES>
  <ENTITY><DATAID>6908001</DATAID><FIRST_NAME>ACME MINING SARL</FIRST_NAME><UN_LIST_TYPE>DRC</UN_LIST_TYPE>
   <ENTITY_ALIAS><ALIAS_NAME>ACME MINES</ALIAS_NAME></ENTITY_ALIAS><ENTITY_ADDRESS><COUNTRY>Uganda</COUNTRY></ENTITY_ADDRESS></ENTITY>
 </ENTITIES>
</CONSOLIDATED_LIST>"""


def test_the_un_xml_is_read_for_individuals_and_entities() -> None:
    entries = {e.source_ref: e for e in parse_un_xml(UN_XML)}
    p, e = entries["6907993"], entries["6908001"]
    assert (
        p.primary_name == "ERIC BADEGE"
        and p.aliases == ("Eric Badege Mapendo",)
        and p.birth == ("1971",)
    )
    assert set(p.countries) == {"democratic republic of the congo", "rwanda"} and p.identifiers == (
        "OB123456",
    )
    assert (
        e.kind is PartyKind.ENTITY and e.aliases == ("ACME MINES",) and e.countries == ("uganda",)
    )


EU_XML = b"""<?xml version="1.0"?>
<export xmlns="http://eu.europa.ec/fpi/fsd/export">
 <sanctionEntity logicalId="13" euReferenceNumber="EU.1.23">
  <subjectType code="person"/>
  <nameAlias wholeName="Ivan Petrov"/><nameAlias wholeName="Ivan Petroff"/>
  <birthdate birthdate="1970-03-12" year="1970"/><citizenship countryIso2Code="RU"/>
  <identification number="71 1234567"/><regulation programme="UKR"/>
 </sanctionEntity>
 <sanctionEntity logicalId="14"><subjectType code="enterprise"/><nameAlias wholeName="Acme Trading"/></sanctionEntity>
</export>"""


def test_the_eu_xml_is_read_as_described_but_that_shape_is_unconfirmed() -> None:
    entries = {e.source_ref: e for e in parse_eu_xml(EU_XML)}
    p = entries["13"]
    assert p.kind is PartyKind.PERSON and p.aliases == ("Ivan Petroff",) and p.birth == ("1970",)
    assert p.countries == ("ru",) and p.identifiers == ("711234567",) and p.programs == ("UKR",)
    assert entries["14"].kind is PartyKind.ENTITY


@pytest.mark.parametrize(
    ("parser", "raw"),
    [
        (parse_un_xml, b"<html><body>Service unavailable</body></html>"),
        (parse_un_xml, b"<CONSOLIDATED_LIST><INDIVIDUALS>"),  # truncated
        (parse_un_xml, b"<CONSOLIDATED_LIST/>"),  # no designations
        (parse_eu_xml, b"<export/>"),
        (parse_uk_csv, b"<html>nope</html>"),
        (parse_ofac, b"only,two\n"),
        (parse_ofac, b""),
    ],
)
def test_a_file_that_is_not_its_list_is_refused_whole(parser: object, raw: bytes) -> None:
    with pytest.raises(ListFormatError):
        parser(raw)  # type: ignore[operator]


def test_xml_with_entities_is_refused_so_a_list_cannot_attack_the_parser() -> None:
    bomb = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;&a;">]><CONSOLIDATED_LIST>&b;</CONSOLIDATED_LIST>'
    with pytest.raises(ListFormatError):
        parse_un_xml(bomb)
    external = b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><CONSOLIDATED_LIST>&e;</CONSOLIDATED_LIST>'
    with pytest.raises(ListFormatError):
        parse_un_xml(external)
