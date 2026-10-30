"""Company verification (ZID-ED-03 acceptance tests): Zoiko iD owns the outcome, and a name, a claim, a gap, a timeout or
an old check never makes a company verified."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from zid_platform.modules.organization.application import kyb as service
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    KybError,
    KybStatus,
    Policy,
    Review,
    Route,
    Routing,
    SourceKind,
    SourceOutcome,
    SourceState,
    canonical_authority,
    check_applicant,
    check_registration_number,
    check_review,
    effective_status,
    entity_id,
    evidence_object,
    name_similarity,
    reconcile,
)
from zid_platform.modules.organization.infrastructure.kyb_adapters import (
    CompaniesHouseKyb,
    CreditsafeKyb,
    GleifKyb,
    SireneKyb,
)
from zid_platform.modules.organization.infrastructure.kyb_config import parse, routing

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)
POLICY = Policy("p1", timedelta(days=365))


def facts(
    *,
    adapter: str = "companies-house",
    kind: SourceKind = SourceKind.OFFICIAL_REGISTER,
    country: str = "GB",
    number: str = "00123456",
    name: str = "Acme Widgets Limited",
    status: str = "active",
    active: bool = True,
    form: str | None = "ltd",
    lei: str | None = None,
) -> CompanyFacts:
    return CompanyFacts(
        adapter_id=adapter,
        source_kind=kind,
        jurisdiction=country,
        registry_authority="companies-house"
        if kind is SourceKind.OFFICIAL_REGISTER
        else f"x:{country}",
        registration_number=number,
        legal_name=name,
        legal_form=form,
        registration_status=status,
        active=active,
        registered_address="1 High St, London",
        source_uri=f"https://source.example/{number}",
        observed_at=NOW,
        response_hash="a" * 64,
        lei=lei,
    )


def found(f: CompanyFacts) -> SourceOutcome:
    return SourceOutcome(f.adapter_id, f.source_kind, SourceState.FOUND, f)


def missing(
    adapter: str = "companies-house", kind: SourceKind = SourceKind.OFFICIAL_REGISTER
) -> SourceOutcome:
    return SourceOutcome(adapter, kind, SourceState.NOT_FOUND)


def down(
    adapter: str = "companies-house", kind: SourceKind = SourceKind.OFFICIAL_REGISTER
) -> SourceOutcome:
    return SourceOutcome(adapter, kind, SourceState.UNAVAILABLE, None, "SOURCE_UNAVAILABLE")


GB = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd")


def decide(
    applicant: Applicant, outcomes: list[SourceOutcome], suppliers: frozenset[str] = frozenset()
) -> Any:
    return reconcile(applicant, outcomes, accepted_suppliers=suppliers, policy=POLICY, now=NOW)


# ---- the fixtures ED-03 §08 names ---------------------------------------------------------------------------------


def test_an_exact_register_match_is_verified_with_an_expiry_and_reasons() -> None:
    d = decide(GB, [found(facts())])
    assert d.status is KybStatus.VERIFIED and d.expiry_at == NOW + timedelta(days=365)
    assert "REGISTRY_MATCH_EXACT" in d.reasons and d.match_confidence == 1.0


@pytest.mark.parametrize(
    ("country", "number"),
    [("US", "C1234567"), ("IE", "123456"), ("DE", "HRB 12345"), ("FR", "552100554")],
)
def test_a_proven_supplier_verifies_the_other_wave_one_markets_and_an_unproven_one_never_does(
    country: str, number: str
) -> None:
    applicant = Applicant(country, number, "Acme Widgets Limited")
    supplier = facts(adapter="creditsafe", kind=SourceKind.SUPPLIER, country=country, number=number)
    assert (
        decide(applicant, [found(supplier)], frozenset({"creditsafe"})).status is KybStatus.VERIFIED
    )
    d = decide(applicant, [found(supplier)])  # coverage not proven for this country
    assert d.status is KybStatus.UNAVAILABLE and d.review_reason == "NO_COVERAGE"
    assert "SUPPLIER_COVERAGE_UNPROVEN" in d.reasons


def test_a_close_name_duplicate_is_never_picked_for_the_applicant() -> None:
    other = facts(number="00123457", name="Acme Widgets Ltd")
    # the applicant chose 00123456; the register answers for that number only
    assert decide(GB, [found(facts())]).status is KybStatus.VERIFIED
    # a name that matches a *different* company does not carry the number it was not asked about
    wrong = Applicant("GB", "00123456", other.legal_name)
    assert (
        decide(wrong, [found(facts(name="Totally Different Holdings Limited"))]).review_reason
        == "NAME_MISMATCH"
    )
    assert (
        name_similarity("Acme Widgets Limited", "Acme Widgets Ltd") == 1.0
    )  # ranking only, never proof


def test_a_changed_legal_name_goes_to_a_reviewer() -> None:
    d = decide(GB, [found(facts(name="Acme Global Industries Limited"))])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "NAME_MISMATCH"


def test_an_inactive_or_dissolved_entity_goes_to_a_reviewer() -> None:
    d = decide(GB, [found(facts(status="dissolved", active=False))])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "ENTITY_INACTIVE"


def test_an_absent_register_entry_is_not_found_for_review_never_a_denial_that_it_exists() -> None:
    d = decide(GB, [missing()])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "NOT_FOUND"


def test_an_lei_that_cannot_be_confirmed_does_not_block_and_is_not_recorded() -> None:
    applicant = Applicant(
        "GB", "00123456", "Acme Widgets Limited", "ltd", lei="5493001KJTIIGC8Y1R12"
    )
    d = decide(applicant, [found(facts()), missing("gleif", SourceKind.LEI_REFERENCE)])
    assert d.status is KybStatus.VERIFIED and "LEI_NOT_CONFIRMED" in d.reasons
    assert (
        evidence_object(
            tenant_id="t", applicant=applicant, decision=d, policy=POLICY, version=1, now=NOW
        )["lei"]
        is None
    )


def test_a_confirmed_lei_is_recorded_and_a_conflicting_one_goes_to_review() -> None:
    lei = "5493001KJTIIGC8Y1R12"
    applicant = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd", lei=lei)
    ref = facts(adapter="gleif", kind=SourceKind.LEI_REFERENCE, number=lei, lei=lei)
    d = decide(applicant, [found(facts()), found(ref)])
    body = evidence_object(
        tenant_id="t", applicant=applicant, decision=d, policy=POLICY, version=1, now=NOW
    )
    assert d.status is KybStatus.VERIFIED and body["lei"] == lei
    other = facts(
        adapter="gleif",
        kind=SourceKind.LEI_REFERENCE,
        number=lei,
        lei=lei,
        name="Another Company Plc",
    )
    assert decide(applicant, [found(facts()), found(other)]).review_reason == "LEI_CONFLICT"


def test_a_different_legal_entity_type_goes_to_review() -> None:
    d = decide(Applicant("GB", "00123456", "Acme Widgets Limited", "llc"), [found(facts())])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "ENTITY_FORM_MISMATCH"


# ---- what never makes a company verified ------------------------------------------------------------------------------


def test_a_timeout_or_outage_is_unavailable_never_verified() -> None:
    d = decide(GB, [down()])
    assert d.status is KybStatus.UNAVAILABLE and d.review_reason == "SOURCE_UNAVAILABLE"


def test_nothing_from_any_source_is_no_coverage_never_verified() -> None:
    assert decide(GB, []).status is KybStatus.UNAVAILABLE


def test_missing_data_in_a_record_is_not_verified() -> None:
    d = decide(GB, [found(facts(status=""))])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "INCOMPLETE_RECORD"


def test_an_answer_for_a_different_number_is_a_conflict_not_a_match() -> None:
    d = decide(GB, [found(facts(number="00999999"))])
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "IDENTIFIER_MISMATCH"


def test_two_sources_that_disagree_go_to_review() -> None:
    d = decide(
        GB,
        [
            found(facts()),
            found(
                facts(
                    adapter="creditsafe", kind=SourceKind.SUPPLIER, status="inactive", active=False
                )
            ),
        ],
        frozenset({"creditsafe"}),
    )
    assert d.status is KybStatus.NEEDS_REVIEW and d.review_reason == "SOURCES_CONFLICT"


def test_a_supplier_outage_beside_a_register_answer_does_not_hide_the_answer() -> None:
    d = decide(
        GB, [found(facts()), down("creditsafe", SourceKind.SUPPLIER)], frozenset({"creditsafe"})
    )
    assert d.status is KybStatus.VERIFIED and "PARTIAL_SOURCES" in d.reasons


def test_an_expired_check_reads_as_expired_never_verified() -> None:
    d = decide(GB, [found(facts())])
    assert effective_status(KybStatus.VERIFIED, d.expiry_at, NOW) is KybStatus.VERIFIED
    assert effective_status(KybStatus.VERIFIED, d.expiry_at, d.expiry_at) is KybStatus.EXPIRED
    assert (
        effective_status(KybStatus.NEEDS_REVIEW, None, NOW + timedelta(days=999))
        is KybStatus.NEEDS_REVIEW
    )


# ---- identity of an entity: jurisdiction + register + number, as written ------------------------------------------------


def test_leading_zeros_are_kept_and_a_number_is_unique_only_within_its_jurisdiction() -> None:
    assert check_registration_number("GB", "00123456") == "00123456"
    assert entity_id("GB", "companies-house", "00123456") != entity_id(
        "GB", "companies-house", "123456"
    )
    assert entity_id("GB", "r", "00123456") != entity_id("IE", "r", "00123456")
    assert entity_id("GB", "r", "00123456") == entity_id("GB", "r", "00123456")


@pytest.mark.parametrize(
    ("country", "number", "ok"),
    [
        ("GB", "00123456", True),
        ("GB", "SC123456", True),
        ("GB", "123", False),
        ("GB", "", False),
        ("IE", "123456", True),
        ("IE", "12AB", False),
        ("DE", "HRB 12345", True),
        ("DE", "12345", False),
        ("FR", "552100554", True),
        ("FR", "552100555", False),  # fails the SIREN check digit
        ("US", "C1234567", True),
        ("US", "", False),
        ("XX", "1", True),  # no per-country format yet: only safe characters are required
        ("G", "1", False),
        ("gb", "00123456", False),
    ],
)
def test_the_identifier_is_checked_against_its_jurisdictions_format(
    country: str, number: str, ok: bool
) -> None:
    if ok:
        assert check_registration_number(country, number)
    else:
        with pytest.raises(KybError):
            check_registration_number(country, number)


def test_unicode_names_compare_without_accent_or_case_tricks() -> None:
    assert name_similarity("Müller & Söhne GmbH", "MULLER SOHNE GMBH") > 0.9
    assert name_similarity("株式会社アクメ", "株式会社アクメ") == 1.0
    assert name_similarity("", "Acme") == 0.0


# ---- evidence and review -------------------------------------------------------------------------------------------------


def test_the_evidence_object_has_everything_ed03_asks_for_and_a_hash_that_covers_it() -> None:
    d = decide(GB, [found(facts())])
    body = evidence_object(
        tenant_id="t1", applicant=GB, decision=d, policy=POLICY, version=1, now=NOW
    )
    for key in (
        "tenant_id", "entity_id", "jurisdiction", "registry_authority", "registration_number", "legal_name",
        "legal_form", "registration_status", "registered_address", "lei", "duns", "supplier_entity_id",
        "source_uri", "observed_at", "policy_version", "match_confidence", "evidence_hash", "expiry_at",
        "decision_status", "review_reason", "reasons", "sources",
    ):  # fmt: skip
        assert key in body, key
    assert body["sources"][0]["response_hash"] == "a" * 64 and body["policy_version"] == "p1"
    again = evidence_object(
        tenant_id="t1", applicant=GB, decision=d, policy=POLICY, version=1, now=NOW
    )
    assert again["evidence_hash"] == body["evidence_hash"]
    changed = evidence_object(
        tenant_id="t2", applicant=GB, decision=d, policy=POLICY, version=1, now=NOW
    )
    assert changed["evidence_hash"] != body["evidence_hash"]


def test_a_review_needs_a_case_in_review_a_rationale_and_someone_other_than_the_requester() -> None:
    ok = Review(
        "rev-1",
        KybStatus.VERIFIED,
        "Registry extract checked against the certificate of incorporation.",
    )
    check_review(KybStatus.NEEDS_REVIEW, ok, requester_id="req-1")
    with pytest.raises(KybError, match="CASE_NOT_IN_REVIEW"):
        check_review(KybStatus.VERIFIED, ok, requester_id="req-1")
    with pytest.raises(KybError, match="RATIONALE_REQUIRED"):
        check_review(
            KybStatus.NEEDS_REVIEW, Review("rev-1", KybStatus.VERIFIED, "ok"), requester_id="req-1"
        )
    with pytest.raises(KybError, match="REVIEWER_IS_REQUESTER"):
        check_review(
            KybStatus.NEEDS_REVIEW,
            Review("req-1", KybStatus.VERIFIED, "x" * 20),
            requester_id="req-1",
        )
    with pytest.raises(KybError, match="REVIEW_OUTCOME_INVALID"):
        check_review(
            KybStatus.NEEDS_REVIEW,
            Review("rev-1", KybStatus.EXPIRED, "x" * 20),
            requester_id="req-1",
        )


# ---- routing and the orchestration over fake sources ---------------------------------------------------------------------


class FakeAdapter:
    def __init__(
        self,
        adapter_id: str,
        kind: SourceKind,
        countries: set[str],
        answer: CompanyFacts | None | str,
    ) -> None:
        self.adapter_id, self.kind = adapter_id, kind
        self.capabilities = Capabilities(frozenset(countries))
        self.answer, self.calls = answer, 0

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list[Any]:
        return []

    async def get(self, number: str, country: str, now: datetime) -> CompanyFacts | None:
        self.calls += 1
        if self.answer == "down":
            raise KybError("SOURCE_UNAVAILABLE")
        if self.answer == "boom":
            raise RuntimeError("socket exploded")
        assert not isinstance(self.answer, str)
        return self.answer


def _routing(coverage: dict[str, frozenset[str]] | None = None) -> Routing:
    return Routing(
        "r1",
        POLICY,
        {
            "GB": Route(("companies-house",), "gleif", ("creditsafe",)),
            "US": Route(("creditsafe",), "gleif", ()),
        },
        {"companies-house": "ENABLED", "gleif": "ENABLED", "creditsafe": "ENABLED"},
        coverage or {},
    )


def run(applicant: Applicant, adapters: dict[str, Any], routing_: Routing) -> Any:
    return asyncio.run(service.decide(applicant, adapters=adapters, routing=routing_, now=NOW))


def test_the_supplier_is_called_when_the_register_has_no_entry_but_not_when_the_register_is_down() -> (
    None
):
    cs_facts = facts(adapter="creditsafe", kind=SourceKind.SUPPLIER)
    for register_answer, expect_calls, expect in (
        (None, 1, KybStatus.VERIFIED),  # absent from the register: the supplier may confirm
        (
            "down",
            0,
            KybStatus.UNAVAILABLE,
        ),  # an outage is not a missing register route: no substitute
    ):
        register = FakeAdapter(
            "companies-house", SourceKind.OFFICIAL_REGISTER, {"GB"}, register_answer
        )
        supplier = FakeAdapter("creditsafe", SourceKind.SUPPLIER, {"GB"}, cs_facts)
        d = run(
            GB,
            {"companies-house": register, "creditsafe": supplier},
            _routing({"GB": frozenset({"creditsafe"})}),
        )
        assert supplier.calls == expect_calls and d.status is expect


def test_a_supplier_without_proven_coverage_cannot_verify_even_when_it_is_the_only_route() -> None:
    applicant = Applicant("US", "C1234567", "Acme Widgets Limited")
    supplier = FakeAdapter(
        "creditsafe",
        SourceKind.SUPPLIER,
        {"US"},
        facts(adapter="creditsafe", kind=SourceKind.SUPPLIER, country="US", number="C1234567"),
    )
    assert run(applicant, {"creditsafe": supplier}, _routing()).status is KybStatus.UNAVAILABLE
    assert (
        run(applicant, {"creditsafe": supplier}, _routing({"US": frozenset({"creditsafe"})})).status
        is KybStatus.VERIFIED
    )


def test_a_crashing_source_is_an_outage_and_a_country_without_a_route_has_no_coverage() -> None:
    register = FakeAdapter("companies-house", SourceKind.OFFICIAL_REGISTER, {"GB"}, "boom")
    assert run(GB, {"companies-house": register}, _routing()).status is KybStatus.UNAVAILABLE
    nowhere = Applicant("BR", "123", "Acme")
    d = run(nowhere, {}, _routing())
    assert d.status is KybStatus.UNAVAILABLE and d.review_reason == "NO_COVERAGE"


def test_a_source_that_does_not_cover_the_country_is_never_asked() -> None:
    register = FakeAdapter("companies-house", SourceKind.OFFICIAL_REGISTER, {"IE"}, facts())
    d = run(GB, {"companies-house": register}, _routing())
    assert register.calls == 0 and d.status is KybStatus.UNAVAILABLE


def test_the_applicants_lei_is_looked_up_only_through_the_routes_lei_source() -> None:
    lei = "5493001KJTIIGC8Y1R12"
    applicant = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd", lei=lei)
    register = FakeAdapter("companies-house", SourceKind.OFFICIAL_REGISTER, {"GB"}, facts())
    gleif = FakeAdapter(
        "gleif",
        SourceKind.LEI_REFERENCE,
        {"*"},
        facts(adapter="gleif", kind=SourceKind.LEI_REFERENCE, number=lei, lei=lei),
    )
    d = run(applicant, {"companies-house": register, "gleif": gleif}, _routing())
    assert gleif.calls == 1 and "LEI_CONFIRMED" in d.reasons


# ---- the shipped routing -------------------------------------------------------------------------------------------------


def test_the_shipped_routing_follows_ed03_and_claims_no_supplier_coverage() -> None:
    r = routing()
    assert r.route("GB") == Route(("companies-house",), "gleif", ("creditsafe",))
    for country in ("US", "IE", "DE"):
        assert r.route(country) is not None and r.route(country).first_pass == ("creditsafe",)  # type: ignore[union-attr]
    assert r.route("FR") == Route(
        ("sirene",), "gleif", ("creditsafe",)
    )  # an official register answers on its own
    assert r.supplier_coverage == {}  # nothing is proven until the owners record it
    assert r.adapter_status["creditsafe"] == "DISABLED_UNTIL_CONTRACT"
    assert r.policy.validity == timedelta(days=365)


def test_routing_that_names_an_unknown_adapter_is_refused() -> None:
    with pytest.raises(KybError, match="ROUTING_INVALID"):
        parse({"adapters": {"a": {}}, "routes": {"GB": {"first_pass": ["nope"]}}})
    with pytest.raises(KybError, match="ROUTING_INVALID"):
        parse({"adapters": {"a": {}}, "supplier_coverage_proven": {"GB": ["nope"]}})


# ---- the adapters over a fake transport ----------------------------------------------------------------------------------


def _mock(handler: Any) -> httpx.MockTransport:
    return httpx.MockTransport(handler)


def test_companies_house_search_and_get_normalize_the_source() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/search/companies":
            return httpx.Response(
                200,
                json={"items": [
                    {"company_number": "00123457", "title": "ACME WIDGETS LTD", "company_status": "active"},
                    {"company_number": "00123456", "title": "Acme Widgets Limited", "company_status": "active"},
                ]},
            )  # fmt: skip
        if request.url.path == "/company/00123456":
            return httpx.Response(
                200,
                json={"company_number": "00123456", "company_name": "ACME WIDGETS LIMITED", "company_status": "active",
                      "type": "ltd", "registered_office_address": {"address_line_1": "1 High St", "locality": "London"}},
            )  # fmt: skip
        return httpx.Response(404)

    ch = CompaniesHouseKyb(lambda: "key", _mock(handler))
    ranked = asyncio.run(ch.search("Acme Widgets Limited", "GB", NOW))
    assert [c.registration_number for c in ranked] == [
        "00123456",
        "00123457",
    ]  # ranked, neither chosen
    got = asyncio.run(ch.get("00123456", "GB", NOW))
    assert got is not None and got.registration_number == "00123456" and got.active
    assert (
        got.registered_address == "1 High St, London"
        and got.legal_form == "ltd"
        and len(got.response_hash) == 64
    )
    assert asyncio.run(ch.get("00999999", "GB", NOW)) is None
    assert asyncio.run(ch.get("00123456", "IE", NOW)) is None  # outside its market


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500),
        httpx.Response(429),
        httpx.Response(302, headers={"location": "https://evil.example/"}),
        httpx.Response(200, content=b"not json"),
        httpx.Response(200, content=b"[]"),
        httpx.Response(200, content=b"x" * 1_000_001),
    ],
)
def test_every_unusable_answer_is_an_outage_never_not_found_or_success(
    response: httpx.Response,
) -> None:
    ch = CompaniesHouseKyb(lambda: "key", _mock(lambda request: response))
    with pytest.raises(KybError) as caught:
        asyncio.run(ch.get("00123456", "GB", NOW))
    assert caught.value.code == "SOURCE_UNAVAILABLE"
    with pytest.raises(KybError, match="SOURCE_UNAVAILABLE"):
        asyncio.run(
            CompaniesHouseKyb(lambda: None, _mock(lambda request: response)).get(
                "00123456", "GB", NOW
            )
        )


def test_a_transport_failure_is_an_outage() -> None:
    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(KybError, match="SOURCE_UNAVAILABLE"):
        asyncio.run(CompaniesHouseKyb(lambda: "key", _mock(boom)).get("00123456", "GB", NOW))


def test_gleif_get_and_search() -> None:
    lei = "5493001KJTIIGC8Y1R12"
    record = {"id": lei, "attributes": {"entity": {"legalName": {"name": "Acme Widgets Limited"}, "jurisdiction": "GB",
              "status": "ACTIVE", "legalAddress": {"city": "London", "country": "GB"}}, "registration": {"status": "ISSUED"}}}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith(f"/lei-records/{lei}"):
            return httpx.Response(200, json={"data": record})
        if request.url.path.endswith("/lei-records"):
            return httpx.Response(200, json={"data": [record]})
        return httpx.Response(404)

    g = GleifKyb(_mock(handler))
    got = asyncio.run(g.get(lei, "GB", NOW))
    assert got is not None and got.lei == lei and got.active and got.jurisdiction == "GB"
    assert asyncio.run(g.get("00000000000000000000", "GB", NOW)) is None
    assert [
        c.registration_number for c in asyncio.run(g.search("Acme Widgets Limited", "GB", NOW))
    ] == [lei]
    assert (
        asyncio.run(g.search("Acme Widgets Limited", "FR", NOW)) == []
    )  # another jurisdiction's records are not offered


def test_creditsafe_authenticates_then_searches_and_never_picks_between_ambiguous_matches() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/v1/authenticate":
            body = json.loads(request.content)
            return (
                httpx.Response(200, json={"token": "T"})
                if body["username"] == "u"
                else httpx.Response(401)
            )
        assert request.headers["authorization"] == "Bearer T"  # the documented scheme
        if "regNo=AMBIG" in str(request.url):
            return httpx.Response(200, json={"companies": [
                {"id": "IE-0-1", "regNo": "AMBIG", "name": "A", "status": "Active"},
                {"id": "IE-0-2", "regNo": "AMBIG", "name": "B", "status": "Active"}]})  # fmt: skip
        return httpx.Response(200, json={"companies": [
            {"id": "IE-0-123456", "regNo": "123456", "name": "Acme Widgets Limited", "status": "Active",
             "address": {"simpleValue": "1 Main St, Dublin"}}]})  # fmt: skip

    cs = CreditsafeKyb(lambda: ("u", "p"), frozenset({"IE"}), _mock(handler))
    got = asyncio.run(cs.get("123456", "IE", NOW))
    assert (
        got is not None
        and got.supplier_entity_id == "IE-0-123456"
        and got.active
        and got.source_kind is SourceKind.SUPPLIER
    )
    assert asyncio.run(cs.get("AMBIG", "IE", NOW)) is None  # two exact matches: nothing is picked
    assert asyncio.run(cs.get("123456", "FR", NOW)) is None  # a country it does not cover
    assert [c.registration_number for c in asyncio.run(cs.search("Acme", "IE", NOW))] == ["123456"]
    assert seen[0] == "POST /v1/authenticate"
    with pytest.raises(KybError, match="SOURCE_UNAVAILABLE"):
        asyncio.run(
            CreditsafeKyb(lambda: ("bad", "p"), frozenset({"IE"}), _mock(handler)).get(
                "123456", "IE", NOW
            )
        )
    with pytest.raises(KybError, match="SOURCE_UNAVAILABLE"):
        asyncio.run(
            CreditsafeKyb(lambda: None, frozenset({"IE"}), _mock(handler)).get("123456", "IE", NOW)
        )


# ---- the French register, over a fake transport ----------------------------------------------------------------------


def _sirene_result(
    siren: str = "552100554", name: str = "ACME WIDGETS SAS", state: str = "A"
) -> dict[str, Any]:
    return {
        "siren": siren,
        "nom_complet": f"{name} (ACME)",
        "nom_raison_sociale": name,
        "etat_administratif": state,
        "nature_juridique": "5710",
        "date_creation": "2001-02-03",
        "siege": {"adresse": "1 RUE DE LA PAIX 75002 PARIS", "code_postal": "75002"},
    }


def _sirene(results: list[dict[str, Any]]) -> SireneKyb:
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["q"] = request.url.params["q"]
        assert "authorization" not in request.headers  # the API is open: no credential is ever sent
        return httpx.Response(200, json={"results": results, "total_results": len(results)})

    return SireneKyb(_mock(handler))


def test_the_french_register_answers_a_siren_lookup_from_its_open_api() -> None:
    adapter = _sirene([_sirene_result()])
    got = asyncio.run(adapter.get("552100554", "FR", NOW))
    assert got is not None and got.registration_number == "552100554" and got.active
    assert got.legal_name == "ACME WIDGETS SAS" and got.registration_status == "A"
    assert (
        got.registered_address == "1 RUE DE LA PAIX 75002 PARIS"
        and got.source_kind is SourceKind.OFFICIAL_REGISTER
    )
    assert got.legal_form is None and len(got.response_hash) == 64
    ceased = asyncio.run(_sirene([_sirene_result(state="C")]).get("552100554", "FR", NOW))
    assert ceased is not None and not ceased.active
    # only an exact SIREN is accepted: another company the API returned is never taken for the one asked about
    assert (
        asyncio.run(_sirene([_sirene_result(siren="732829320")]).get("552100554", "FR", NOW))
        is None
    )
    assert asyncio.run(_sirene([]).get("552100554", "FR", NOW)) is None
    assert asyncio.run(adapter.get("552100554", "GB", NOW)) is None  # outside its market
    ranked = asyncio.run(
        _sirene([_sirene_result("732829320", "OTHER SA"), _sirene_result()]).search(
            "ACME WIDGETS SAS", "FR", NOW
        )
    )
    assert [c.registration_number for c in ranked] == ["552100554", "732829320"]


def test_a_french_company_verifies_from_the_official_register_without_any_supplier_coverage() -> (
    None
):
    applicant = Applicant("FR", "552100554", "Acme Widgets SAS")
    d = run(applicant, {"sirene": _sirene([_sirene_result()])}, routing())
    assert d.status is KybStatus.VERIFIED and "REGISTRY_MATCH_EXACT" in d.reasons
    ceased = run(applicant, {"sirene": _sirene([_sirene_result(state="C")])}, routing())
    assert ceased.status is KybStatus.NEEDS_REVIEW and ceased.review_reason == "ENTITY_INACTIVE"


# ---- which register a number belongs to --------------------------------------------------------------------------------


def test_a_german_number_needs_its_register_court_and_a_us_number_its_state() -> None:
    de = Applicant("DE", "HRB 12345", "Acme GmbH", register_id="Amtsgericht Charlottenburg")
    assert check_applicant(de).register_id == "Amtsgericht Charlottenburg"
    for bad in (None, "", "x"):
        with pytest.raises(KybError, match="REGISTER_REQUIRED"):
            check_applicant(Applicant("DE", "HRB 12345", "Acme GmbH", register_id=bad))
    assert (
        check_applicant(Applicant("US", "C1234567", "Acme Inc", register_id="US-DE")).register_id
        == "US-DE"
    )
    for bad in (None, "DE", "US-del", "us-DE"):
        with pytest.raises(KybError, match="REGISTER_REQUIRED"):
            check_applicant(Applicant("US", "C1234567", "Acme Inc", register_id=bad))
    # the same number at two courts is two companies
    assert entity_id(
        "DE", canonical_authority("DE", "Amtsgericht Charlottenburg"), "HRB 12345"
    ) != entity_id("DE", canonical_authority("DE", "Amtsgericht München"), "HRB 12345")


def test_an_entity_keeps_one_id_whichever_source_reported_it() -> None:
    gb = Applicant("GB", "00123456", "Acme Widgets Limited", "ltd")
    from_register = evidence_object(
        tenant_id="t",
        applicant=gb,
        decision=decide(gb, [found(facts())]),
        policy=POLICY,
        version=1,
        now=NOW,
    )
    supplier = facts(adapter="creditsafe", kind=SourceKind.SUPPLIER)
    from_supplier = evidence_object(
        tenant_id="t",
        applicant=gb,
        decision=decide(gb, [missing(), found(supplier)], frozenset({"creditsafe"})),
        policy=POLICY,
        version=1,
        now=NOW,
    )
    assert from_register["entity_id"] == from_supplier["entity_id"]
    assert (
        from_register["registry_authority"]
        == "companies-house"
        == from_supplier["registry_authority"]
    )
    assert (
        from_supplier["source_authority"] != from_register["source_authority"]
    )  # what each source called itself


def test_irish_numbers_are_all_digits_of_any_length() -> None:
    for number in ("1", "123456", "1234567", "123456789"):
        assert check_registration_number("IE", number) == number
    with pytest.raises(KybError):
        check_registration_number("IE", "IE123456")


def test_creditsafe_status_enum_is_taken_as_documented_and_type_is_not_a_legal_form() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/authenticate":
            return httpx.Response(200, json={"token": "T"})
        status = "NonActive" if "regNo=222" in str(request.url) else "Active"
        return httpx.Response(200, json={"correlationId": "c", "messages": [], "totalSize": 1, "companies": [
            {"id": "IE-0-1", "regNo": "222" if status == "NonActive" else "111", "name": "A", "status": status, "type": "Ltd"}]})  # fmt: skip

    cs = CreditsafeKyb(lambda: ("u", "p"), frozenset({"IE"}), _mock(handler))
    live = asyncio.run(cs.get("111", "IE", NOW))
    gone = asyncio.run(cs.get("222", "IE", NOW))
    assert live is not None and live.active and live.legal_form is None
    assert gone is not None and not gone.active  # NonActive is not active
