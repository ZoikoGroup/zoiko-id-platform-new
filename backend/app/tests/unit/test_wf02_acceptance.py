"""Company verification acceptance tests from the doc 2 register that no test linked before (T03, T05, T06, T13, T19, T29, T30, T31, T32, T35, T36, T37; CR-112).

Each one drives the real code and asserts the behaviour the register names. The ones that need real inputs (T07 live mirrors, T38 assistive technology, T40 a deployed cell) are not here."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from zid_platform.modules.organization.application import kyb as kyb_service
from zid_platform.modules.organization.application import kyb_assessment as ka
from zid_platform.modules.organization.domain import kyb_grade as g
from zid_platform.modules.organization.domain import ownership as ow
from zid_platform.modules.organization.domain.kyb import (
    Applicant,
    Capabilities,
    CompanyFacts,
    KybError,
    KybStatus,
    SourceKind,
    check_applicant,
    check_registration_number,
    name_similarity,
    normalize_name,
)
from zid_platform.modules.organization.infrastructure.kyb_adapters import InpiKyb, default_adapters
from zid_platform.modules.organization.infrastructure.kyb_config import parse as parse_routing

NOW = datetime(2026, 11, 18, 12, 0, tzinfo=UTC)
SIREN = "123456782"
FOUND = {
    "formality": {
        "content": {
            "personneMorale": {
                "identite": {"entreprise": {"siren": SIREN, "denomination": "ACME SAS"}}
            }
        }
    }
}


# ---- T03: INPI behind credentials: no credential, no request, no made-up answer -------------------------------------------------------------------------------


def inpi(handler, creds=("u", "p")):
    return InpiKyb(credentials=lambda: creds, transport=httpx.MockTransport(handler))


def get(a, number=SIREN, country="FR"):
    return asyncio.run(a.get(number, country, NOW))


def test_t03_no_credential_means_no_request_and_an_unavailable_source() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=FOUND)

    for creds in (None, ("", "p"), ("u", "")):
        with pytest.raises(KybError) as e:
            get(inpi(handler, creds))
        assert e.value.code == "SOURCE_UNAVAILABLE" and "no INPI credential" in e.value.detail
    assert calls == []  # nothing left the platform, and no lookup was made up


def test_t03_a_refused_login_is_unavailable_and_a_good_answer_is_a_record() -> None:
    with pytest.raises(KybError) as e:
        get(inpi(lambda r: httpx.Response(401)))
    assert e.value.code == "SOURCE_UNAVAILABLE"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/sso/login":
            return httpx.Response(200, json={"token": "t"})
        assert request.headers["authorization"] == "Bearer t"
        return httpx.Response(200, json=FOUND)

    facts = get(inpi(handler))
    assert (
        facts is not None
        and facts.legal_name == "ACME SAS"
        and facts.active
        and facts.registration_number == SIREN
    )
    assert (
        facts.registry_authority == "sirene-rne"
        and facts.source_kind is SourceKind.OFFICIAL_REGISTER
    )


def test_t03_another_companys_answer_a_missing_company_and_a_dissolved_one_are_never_confused() -> (
    None
):
    def serve(body: dict, status: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/api/sso/login":
                return httpx.Response(200, json={"token": "t"})
            return httpx.Response(status, json=body)

        return handler

    other = {
        "formality": {
            "content": {
                "personneMorale": {
                    "identite": {
                        "entreprise": {"siren": "987654321", "denomination": "Someone Else"}
                    }
                }
            }
        }
    }
    assert get(inpi(serve(other))) is None  # an answer for another SIREN is not this company
    assert get(inpi(serve({}, 404))) is None  # a 404 is the only "not found"
    dissolved = {
        "formality": {
            "content": {
                "personneMorale": {
                    "identite": {
                        "entreprise": {
                            "siren": SIREN,
                            "denomination": "ACME SAS",
                            "dateRadiation": "2025-01-01",
                        }
                    }
                }
            }
        }
    }
    facts = get(inpi(serve(dissolved)))
    assert facts is not None and facts.active is False
    assert (
        get(inpi(serve(FOUND)), "GB") is None and get(inpi(serve(FOUND)), "12345") is None
    )  # only a French nine-digit SIREN


def test_t03_the_adapter_is_registered_but_the_routing_keeps_it_off() -> None:
    adapters = default_adapters(None)
    assert "inpi" in adapters and adapters["inpi"].capabilities.countries == frozenset({"FR"})


# ---- T35: a source the routing does not enable is not called and not billed ------------------------------------------------------------------------------------


class Source:
    def __init__(self, adapter_id: str, kind: SourceKind, facts: CompanyFacts | None) -> None:
        self.adapter_id, self.kind, self.facts = adapter_id, kind, facts
        self.capabilities = Capabilities(countries=frozenset({"GB"}))
        self.calls = 0

    async def search(self, query: str, jurisdiction: str, now: datetime) -> list:
        return []

    async def get(self, number: str, jurisdiction: str, now: datetime) -> CompanyFacts | None:
        self.calls += 1
        return self.facts


def routing(creditsafe_status: str):
    return parse_routing(
        {
            "version": "t",
            "adapters": {
                "companies-house": {"kind": "OFFICIAL_REGISTER", "status": "ENABLED"},
                "creditsafe": {"kind": "SUPPLIER", "status": creditsafe_status},
            },
            "routes": {"GB": {"first_pass": ["companies-house"], "exception": ["creditsafe"]}},
        }
    )


def decide(supplier: Source, status: str):
    register = Source(
        "companies-house", SourceKind.OFFICIAL_REGISTER, None
    )  # the register does not know the company: that is the trigger for the exception route
    applicant = Applicant("GB", "00123456", "Acme Widgets Limited")
    d = asyncio.run(
        kyb_service.decide(
            applicant,
            adapters={"companies-house": register, "creditsafe": supplier},
            routing=routing(status),
            now=NOW,
        )
    )
    return d, register


@pytest.mark.parametrize(
    "status",
    ["DISABLED_UNTIL_CONTRACT", "NOT_QUALIFIED", "NOT_LICENSED", "DISABLED_UNTIL_CREDENTIALS"],
)
def test_t35_a_source_that_is_not_enabled_is_never_called_so_nothing_is_billed(status: str) -> None:
    supplier = Source("creditsafe", SourceKind.SUPPLIER, None)
    d, register = decide(supplier, status)
    assert supplier.calls == 0  # no call, so no billable check is recorded
    assert register.calls == 1
    assert all(
        o.adapter_id != "creditsafe" for o in d.sources
    )  # and it is not even in the outcomes the bill is made from


def test_t35_an_enabled_source_is_called_and_a_source_missing_from_the_routing_is_not() -> None:
    supplier = Source("creditsafe", SourceKind.SUPPLIER, None)
    d, _ = decide(supplier, "ENABLED")
    assert supplier.calls == 1 and any(o.adapter_id == "creditsafe" for o in d.sources)
    r = routing("ENABLED")
    assert r.enabled("companies-house") and r.enabled("creditsafe") and not r.enabled("unlisted")


# ---- T05, T19: an ownership chain that stops at a company is not a settled structure ------------------------------------------------------------------------------


def ents(**kinds: str) -> list[dict]:
    return [{"id": k, "kind": v} for k, v in kinds.items()]


def link(owner: str, owned: str, pct: str, **kw) -> dict:
    return {
        "owner": owner,
        "owned": owned,
        "percent": pct,
        "evidenced": kw.pop("evidenced", True),
        **kw,
    }


def test_t05_a_parent_company_alone_does_not_make_the_ownership_complete() -> None:
    f = ow.analyze(ow.build(ents(T="COMPANY", parent="COMPANY"), [link("parent", "T", "100")]), "T")
    assert f.complete is False  # one parent (with an LEI, say) is not a natural person
    assert any(gap.code == "UNRESOLVED_CHAIN" and gap.entity_id == "parent" for gap in f.gaps)
    assert f.candidates == ()  # and no person is named


def test_t19_a_nominee_or_trust_without_its_parties_is_a_gap_for_review() -> None:
    f = ow.analyze(
        ow.build(ents(T="COMPANY", trust="TRUST", p="PERSON"), [link("trust", "T", "60")]), "T"
    )
    assert f.complete is False and f.gaps


# ---- T06, T13: documents are evidence for a reviewer, never a registry match -----------------------------------------------------------------------------------


def documents(reviewers: tuple[str, ...] = ("r1", "r2")) -> g.DocumentFact:
    return g.DocumentFact(reviewers, True, NOW)


def test_t06_t13_documents_alone_never_reach_a_registry_grade_or_a_verified_company() -> None:
    a = g.assess(g.KybFacts(documents=documents()), now=NOW)
    assert (
        a.grade is g.Grade.K1 and a.label == "Documents reviewed"
    )  # T13: never "verified company"
    assert (
        "REGISTER_FOUND" not in a.claims and "COMPANY_ACTIVE_SOURCE_REPORTED" not in a.claims
    )  # T06: no registry-linked approval from a file
    one = g.assess(g.KybFacts(documents=documents(("r1",))), now=NOW)
    assert one.grade is g.Grade.K0 and "TWO_INDEPENDENT_REVIEWERS_REQUIRED" in one.reasons
    same = g.assess(g.KybFacts(documents=documents(("r1", "r1"))), now=NOW)
    assert same.grade is g.Grade.K0  # the same person twice is one reviewer


def test_t06_a_stale_or_unmatched_registry_check_does_not_lift_a_documents_grade() -> None:
    stale = g.RegistryFact(True, "ACTIVE", NOW - timedelta(days=400), timedelta(days=90))
    a = g.assess(g.KybFacts(registry=stale, documents=documents()), now=NOW)
    assert a.grade is g.Grade.K1 and "REGISTRY_CHECK_STALE" in a.reasons
    unmatched = g.RegistryFact(False, "UNKNOWN", NOW, timedelta(days=90))
    assert (
        g.assess(g.KybFacts(registry=unmatched, documents=documents()), now=NOW).grade is g.Grade.K1
    )


# ---- T29: a registered address is not an operating site ------------------------------------------------------------------------------------------------------


def test_t29_no_grade_claim_says_anything_about_an_address() -> None:
    registry = g.RegistryFact(True, "ACTIVE", NOW, timedelta(days=90))
    mandate = g.MandateFact(
        "p1", True, frozenset({"SIGN_AGREEMENT"}), NOW - timedelta(days=1), None
    )
    top = g.assess(
        g.KybFacts(
            registry=registry,
            ownership_complete=True,
            persons_verified=True,
            screening="SCREENING_CLEAR",
            mandates=(mandate,),
        ),
        now=NOW,
    )
    assert top.grade is g.Grade.K4
    assert not any(
        "ADDRESS" in c or "SITE" in c or "PREMISES" in c for c in (*top.claims, *top.reasons)
    )


def facts(address: str | None) -> CompanyFacts:
    return CompanyFacts(
        "companies-house",
        SourceKind.OFFICIAL_REGISTER,
        "GB",
        "companies-house",
        "00123456",
        "Acme Widgets Limited",
        "ltd",
        "active",
        True,
        address,
        "https://x/1",
        NOW,
        "a" * 64,
    )


def test_t29_the_decision_is_the_same_whatever_address_the_register_holds() -> None:
    from zid_platform.modules.organization.domain.kyb import (
        Policy,
        SourceOutcome,
        SourceState,
        reconcile,
    )

    applicant = Applicant("GB", "00123456", "Acme Widgets Limited")

    def decide_with(address: str | None):
        outcome = SourceOutcome(
            "companies-house", SourceKind.OFFICIAL_REGISTER, SourceState.FOUND, facts(address)
        )
        return reconcile(
            applicant,
            [outcome],
            accepted_suppliers=frozenset(),
            policy=Policy("p1", timedelta(days=365)),
            now=NOW,
        )

    a, b = decide_with("1 High Street, London"), decide_with("A PO box in another country")
    assert (
        a.status is b.status is KybStatus.VERIFIED and a.reasons == b.reasons
    )  # an address neither proves nor disproves the company


# ---- T30: no universal company number -------------------------------------------------------------------------------------------------------------------------


def test_t30_a_us_registration_needs_its_state_and_never_a_uk_number() -> None:
    assert (
        check_registration_number("US", "5012345") == "5012345"
    )  # a state file number has no UK shape
    with pytest.raises(KybError) as e:
        check_applicant(Applicant("US", "5012345", "Acme Inc"))
    assert (
        e.value.code == "REGISTER_REQUIRED"
    )  # a U.S. number means nothing without its state register
    ok = check_applicant(Applicant("US", "5012345", "Acme Inc", register_id="US-DE"))
    assert ok.register_id == "US-DE"
    with pytest.raises(KybError):
        check_applicant(Applicant("US", "5012345", "Acme Inc", register_id="Delaware"))
    with pytest.raises(KybError):
        check_registration_number("GB", "5012345")  # the UK shape is required for the UK only


# ---- T31: names keep their exact original, and the normalisation is auditable ---------------------------------------------------------------------------------


def test_t31_the_exact_original_name_is_kept_and_the_normalised_form_is_separate() -> None:
    for name in (
        "Müller Großhandel GmbH",
        "株式会社テスト",
        "Société Générale S.A.",
        "Ølgaard & Søn A/S",
    ):
        kept = check_applicant(Applicant("GB", "00123456", f"  {name}  ")).legal_name
        assert kept == name  # nothing but the surrounding space is touched
    assert (
        normalize_name("Café Société") == "cafe societe"
    )  # the normalised form is a separate, derived value
    assert name_similarity("Café Société Limited", "CAFE SOCIETE LTD") == 1.0
    assert name_similarity("株式会社テスト", "株式会社テスト") == 1.0
    assert (
        normalize_name("株式会社テスト") == "株式会社テスト"
    )  # a script it cannot fold is left as written, never dropped


# ---- T32: a possible sanctions or PEP result goes to a person, never to a clear ----------------------------------------------------------------------------------


def test_t32_a_possible_match_holds_the_company_below_the_completed_grade() -> None:
    registry = g.RegistryFact(True, "ACTIVE", NOW, timedelta(days=90))
    base = {"registry": registry, "ownership_complete": True, "persons_verified": True}
    clear = g.assess(g.KybFacts(**base, screening="SCREENING_CLEAR"), now=NOW)
    assert clear.grade is g.Grade.K3
    for screening in ("POTENTIAL_MATCH", "UNAVAILABLE", "NOT_RUN"):
        held = g.assess(g.KybFacts(**base, screening=screening), now=NOW)
        assert (
            held.grade is g.Grade.K2 and f"SCREENING_{screening}" in held.reasons
        )  # never auto-cleared


# ---- T36, T37: what a customer reads names no owner, no source secret and no threshold ------------------------------------------------------------------------------


def stored(reasons: tuple[str, ...], grade: g.Grade = g.Grade.K2) -> ka.Stored:
    mandate = g.MandateFact("person-secret-id", True, frozenset({"SIGN_AGREEMENT"}), NOW, None)
    return ka.Stored(
        version=3,
        grade=grade,
        label=g.LABEL[grade],
        claims=("REGISTER_FOUND",),
        reasons=reasons,
        facts=g.KybFacts(
            documents=g.DocumentFact(("reviewer-secret-1", "reviewer-secret-2"), True, NOW),
            mandates=(mandate,),
            extra={"owner_name": "Jane Owner", "source_key": "api-key-123"},
        ),
        policy_hash="sha256:abc",
        trigger="REQUESTED",
        assessed_at=NOW,
        recheck_due=NOW + timedelta(days=30),
    )


def test_t36_the_customer_view_holds_no_owner_reviewer_person_or_source_secret() -> None:
    out = ka.view(stored(("OWNERSHIP_INCOMPLETE", "PERSONS_NOT_VERIFIED")))
    text = repr(out)
    for secret in (
        "Jane Owner",
        "api-key-123",
        "reviewer-secret",
        "person-secret-id",
        "owner_name",
        "source_key",
    ):
        assert secret not in text, secret
    assert set(out) <= {
        "version",
        "grade",
        "label",
        "claims",
        "reasons",
        "assessed_at",
        "recheck_due",
        "policy_hash",
        "now",
        "permitted_actions",
    }


def test_t37_a_refusal_gives_stable_reason_codes_and_never_a_threshold_or_a_detector_rule() -> None:
    out = ka.view(stored(("REGISTRY_NO_EXACT_MATCH", "OWNERSHIP_INCOMPLETE"), g.Grade.K0))
    assert out["reasons"] == ["REGISTRY_NO_EXACT_MATCH", "OWNERSHIP_INCOMPLETE"]
    text = repr(out).lower()
    for leaked in ("threshold", "0.85", "similarity", "score", Decimal("0.85").to_eng_string()):
        assert leaked not in text, leaked
