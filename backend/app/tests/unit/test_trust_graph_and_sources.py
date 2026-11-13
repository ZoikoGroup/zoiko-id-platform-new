"""GID-038 / 045 issuer trust graph and recognition; GID-055 / 056 / 057 / 065 source contracts and gates.

GLB-03  a real issuer whose program was not accredited at the award date gives no accredited claim
GLB-09  an unrecognized overseas award goes to review, never to automatic equivalence
GLB-15  a provider without the permitted purpose is not queried
GLB-16  a region that violates the data's residency is blocked
GLB-18  a sensitive (criminal) check without entitlement is forbidden
GLB-34  a coverage claim the registry cannot back is refused
GID-038 negative proof: lookalike school, expired accreditation, renamed issuer
"""

from __future__ import annotations

from datetime import date

import pytest
from zid_platform.modules.credential.domain import sources as src
from zid_platform.modules.credential.domain import trust_graph as tg

D = date(2026, 11, 11)


def _registry() -> tg.Registry:
    return tg.parse(
        {
            "version": "1",
            "issuers": [
                {
                    "issuer_id": "uni-x",
                    "kind": "UNIVERSITY",
                    "authority": "OFFICIAL",
                    "countries": ["US"],
                    "claim_types": ["ACADEMIC_AWARD"],
                    "names": [
                        {"name": "State Technical College", "until": "2015-06-30"},
                        {"name": "University of Exampleton", "from": "2015-07-01"},
                    ],
                    "accreditations": [
                        {
                            "body": "acc-1",
                            "program": "BSc Computing",
                            "from": "2010-01-01",
                            "until": "2019-12-31",
                        },
                        {"body": "acc-1", "program": "BA History", "from": "2000-01-01"},
                    ],
                },
                {
                    "issuer_id": "uni-y",
                    "kind": "UNIVERSITY",
                    "countries": ["GB"],
                    "claim_types": ["ACADEMIC_AWARD"],
                    "names": [{"name": "University of Exampleton"}],
                    "authority": "OFFICIAL",
                },
                {
                    "issuer_id": "reg-1",
                    "kind": "REGULATOR",
                    "authority": "OFFICIAL",
                    "countries": ["US"],
                    "claim_types": ["PROFESSIONAL_LICENSE"],
                    "names": [{"name": "State Bar"}],
                    "revoked_on": "2030-01-01",
                },
                {
                    "issuer_id": "shady",
                    "kind": "UNIVERSITY",
                    "countries": ["US"],
                    "claim_types": ["ACADEMIC_AWARD"],
                    "names": [{"name": "Shady Online University"}],
                },
            ],
            "recognition": [
                {
                    "rule_id": "rec-gb-us-eng",
                    "award_country": "GB",
                    "target_country": "US",
                    "profession_code": "ENGINEER",
                    "from": "2020-01-01",
                }
            ],
        }
    )


# --- GID-038 ----------------------------------------------------------------------------------------------------------


def test_an_exact_name_resolves_after_case_accent_and_punctuation_folding():
    r = tg.resolve(_registry(), "  UNIVERSITY  of EXAMPLETON. ", country="US")
    assert (r.match, r.issuer_id) == (tg.Match.RESOLVED, "uni-x")


def test_a_renamed_issuer_still_resolves_to_the_issuer_that_held_the_name():
    r = tg.resolve(_registry(), "State Technical College", country="US", on=date(2012, 1, 1))
    assert (r.match, r.issuer_id, r.via_former_name) == (tg.Match.RESOLVED, "uni-x", True)


def test_a_lookalike_school_is_never_resolved():
    r = tg.resolve(_registry(), "Universty of Exampleton Online", country="US")
    assert r.match is tg.Match.LOOKALIKE and r.issuer_id is None
    assert "uni-x" in r.candidates
    assert (
        tg.resolve(_registry(), "Totally Different Academy", country="US").match is tg.Match.UNKNOWN
    )


def test_one_name_in_two_countries_resolves_only_within_the_given_country():
    assert tg.resolve(_registry(), "University of Exampleton", country="GB").issuer_id == "uni-y"
    assert tg.resolve(_registry(), "University of Exampleton", country="US").issuer_id == "uni-x"
    both = tg.resolve(
        _registry(), "University of Exampleton"
    )  # no country: ambiguous, never picked
    assert both.match is tg.Match.LOOKALIKE and both.candidates == ("uni-x", "uni-y")


def test_an_empty_name_is_unknown():
    assert tg.resolve(_registry(), "  ...  ").match is tg.Match.UNKNOWN


def test_trust_needs_registry_facts_not_just_a_signature():
    reg = _registry()
    tg.trusted_for(reg, "uni-x", claim_type="ACADEMIC_AWARD", country="US", on=D)
    for issuer, ctype, country, why in (
        ("nobody", "ACADEMIC_AWARD", "US", "unknown issuer"),
        ("shady", "ACADEMIC_AWARD", "US", "authority not recorded"),
        ("uni-x", "PROFESSIONAL_LICENSE", "US", "not trusted for this claim type"),
        ("uni-x", "ACADEMIC_AWARD", "GB", "not trusted for this country"),
        (None, "ACADEMIC_AWARD", "US", "unknown issuer"),
    ):
        with pytest.raises(tg.TrustGraphError) as e:
            tg.trusted_for(reg, issuer, claim_type=ctype, country=country, on=D)
        assert e.value.code == "ISSUER_NOT_TRUSTED" and why in str(e.value)


def test_a_revoked_issuer_is_trusted_until_the_day_it_is_revoked():
    reg = _registry()
    tg.trusted_for(
        reg, "reg-1", claim_type="PROFESSIONAL_LICENSE", country="US", on=date(2029, 12, 31)
    )
    with pytest.raises(tg.TrustGraphError):
        tg.trusted_for(
            reg, "reg-1", claim_type="PROFESSIONAL_LICENSE", country="US", on=date(2030, 1, 1)
        )


# --- GID-045 ----------------------------------------------------------------------------------------------------------


def test_glb_03_accreditation_is_judged_at_the_award_date_not_today():
    uni = _registry().issuers["uni-x"]
    assert (
        tg.accreditation_at_award(uni, "BSc Computing", date(2018, 5, 1))[0]
        is tg.Accredited.ACCREDITED
    )
    state, why = tg.accreditation_at_award(
        uni, "BSc Computing", date(2022, 5, 1)
    )  # lapsed in 2019: expired accreditation
    assert state is tg.Accredited.NOT_ACCREDITED and why == ("NOT_ACCREDITED_AT_AWARD_DATE",)
    assert (
        tg.accreditation_at_award(uni, "BSc Physics", date(2018, 5, 1))[0]
        is tg.Accredited.NOT_ACCREDITED
    )
    assert (
        tg.accreditation_at_award(uni, "ba history", date(2024, 1, 1))[0]
        is tg.Accredited.ACCREDITED
    )


def test_no_accreditation_record_is_unknown_not_a_failure():
    state, why = tg.accreditation_at_award(_registry().issuers["uni-y"], "BSc", D)
    assert state is tg.Accredited.UNKNOWN and why == ("NO_ACCREDITATION_RECORD",)


def test_glb_09_recognition_is_never_automatic_equivalence():
    reg = _registry()
    assert (
        tg.recognition(reg, award_country="US", target_country="us", profession="ENGINEER", on=D)[0]
        is tg.Recognition.NOT_REQUIRED
    )
    got, rule = tg.recognition(
        reg, award_country="GB", target_country="US", profession="ENGINEER", on=D
    )
    assert (got, rule) == (
        tg.Recognition.REVIEW,
        "rec-gb-us-eng",
    )  # best case is REVIEW, a body still decides
    assert (
        tg.recognition(reg, award_country="GB", target_country="US", profession="LAW", on=D)[0]
        is tg.Recognition.UNRECOGNIZED
    )
    assert (
        tg.recognition(reg, award_country="IN", target_country="US", profession="ENGINEER", on=D)[0]
        is tg.Recognition.UNRECOGNIZED
    )
    assert (
        tg.recognition(
            reg, award_country="GB", target_country="US", profession="ENGINEER", on=date(2019, 1, 1)
        )[0]
        is tg.Recognition.UNRECOGNIZED
    )


def test_the_graph_refuses_malformed_rows():
    for bad in (
        {"issuers": [{"issuer_id": "a", "kind": "WIZARD", "names": [{"name": "x"}]}]},
        {"issuers": [{"issuer_id": "a", "kind": "UNIVERSITY", "names": []}]},
        {"issuers": [{"kind": "UNIVERSITY", "names": [{"name": "x"}]}]},
        {"issuers": [{"issuer_id": "a", "kind": "UNIVERSITY", "names": [{"name": "x"}]}] * 2},
    ):
        with pytest.raises(tg.TrustGraphError) as e:
            tg.parse(bad)
        assert e.value.code == "TRUST_GRAPH_INVALID"


# --- GID-055 / 056 / 057 ----------------------------------------------------------------------------------------------


def _contract(**over) -> src.SourceContract:
    base = {
        "source_id": "registrar-us",
        "claim_types": frozenset({"ACADEMIC_AWARD"}),
        "countries": frozenset({"US"}),
        "purposes": frozenset({"PROFESSIONAL_ENGAGEMENT"}),
        "processing_regions": frozenset({"us"}),
        "valid_from": date(2026, 1, 1),
    }
    base.update(over)
    return src.SourceContract(**base)


def _req(**over) -> src.Request:
    base = {
        "claim_type": "ACADEMIC_AWARD",
        "country": "US",
        "purpose": "PROFESSIONAL_ENGAGEMENT",
        "processing_region": "us",
        "on": D,
    }
    base.update(over)
    return src.Request(**base)


def test_a_covered_permitted_request_may_query():
    src.may_query(_contract(), _req())


@pytest.mark.parametrize(
    ("contract", "req", "code"),
    [
        (None, {}, "SOURCE_NO_CONTRACT"),  # a provider with a product but no contract
        ({"valid_from": date(2027, 1, 1)}, {}, "SOURCE_CONTRACT_NOT_IN_FORCE"),
        ({"valid_until": date(2026, 6, 1)}, {}, "SOURCE_CONTRACT_NOT_IN_FORCE"),
        ({}, {"claim_type": "PROFESSIONAL_LICENSE"}, "SOURCE_NOT_COVERED"),
        ({}, {"country": "GB"}, "SOURCE_NOT_COVERED"),
        ({}, {"purpose": "MARKETING"}, "SOURCE_PURPOSE_NOT_PERMITTED"),  # GLB-15
        ({}, {"processing_region": "eu"}, "SOURCE_REGION_BLOCKED"),  # GLB-16
    ],
)
def test_each_refusal_has_its_own_code(contract, req, code):
    with pytest.raises(src.SourceError) as e:
        src.may_query(None if contract is None else _contract(**contract), _req(**req))
    assert e.value.code == code


def test_glb_16_restricted_data_leaves_its_region_only_by_a_lawful_transfer():
    c = _contract(processing_regions=frozenset({"us", "eu"}), lawful_transfer=frozenset({"us"}))
    src.may_query(c, _req(subject_region="eu", processing_region="eu"))  # stays home
    with pytest.raises(src.SourceError) as e:
        src.may_query(c, _req(subject_region="us", processing_region="eu"))
    assert e.value.code == "SOURCE_TRANSFER_NOT_LAWFUL"
    src.may_query(c, _req(subject_region="eu", processing_region="us"))  # lawful to move to the US


def test_glb_18_a_sensitive_source_needs_a_basis_an_authorized_user_and_a_reviewer():
    c = _contract(sensitivity=src.Sensitivity.CRIMINAL, claim_types=frozenset({"ACADEMIC_AWARD"}))
    for kwargs, code in (
        ({}, "SENSITIVE_NO_BASIS"),
        ({"consent_ref": "c1"}, "SENSITIVE_USER_NOT_AUTHORIZED"),
        ({"consent_ref": "c1", "authorized_user": True}, "SENSITIVE_NO_REVIEWER"),
    ):
        with pytest.raises(src.SourceError) as e:
            src.may_query(c, _req(**kwargs))
        assert e.value.code == code
    src.may_query(
        c, _req(legal_basis_ref="statute-9", authorized_user=True, reviewer_assigned=True)
    )


def test_an_employment_check_needs_a_consent_or_legal_basis_even_from_a_standard_source():
    c = _contract(claim_types=frozenset({"EMPLOYMENT_ASSERTION"}))
    with pytest.raises(src.SourceError) as e:
        src.may_query(c, _req(claim_type="EMPLOYMENT_ASSERTION"))
    assert e.value.code == "EMPLOYMENT_NO_BASIS"
    src.may_query(c, _req(claim_type="EMPLOYMENT_ASSERTION", consent_ref="c1"))
    src.may_query(c, _req(claim_type="EMPLOYMENT_ASSERTION", legal_basis_ref="statute-9"))
    # the rule is about employment only: a degree from a standard source still needs neither
    src.may_query(_contract(), _req())


def test_choose_prefers_a_primary_source_and_never_falls_back_to_a_refused_one():
    agg = _contract(source_id="aggregator", primary=False)
    prim = _contract(source_id="registrar-us")
    got, refused = src.choose([agg, prim], _req())
    assert got is prim and refused == {}
    got, refused = src.choose([agg, prim], _req(purpose="MARKETING"))
    assert got is None and refused == {
        "registrar-us": "SOURCE_PURPOSE_NOT_PERMITTED",
        "aggregator": "SOURCE_PURPOSE_NOT_PERMITTED",
    }


def test_coverage_is_primary_partner_or_none():
    prim, agg = _contract(), _contract(source_id="agg", primary=False, countries=frozenset({"FR"}))
    assert (
        src.coverage([prim, agg], country="us", claim_type="ACADEMIC_AWARD", on=D)
        is src.Coverage.PRIMARY_SOURCE
    )
    assert (
        src.coverage([prim, agg], country="FR", claim_type="ACADEMIC_AWARD", on=D)
        is src.Coverage.PARTNER_ONLY
    )
    assert (
        src.coverage([prim, agg], country="DE", claim_type="ACADEMIC_AWARD", on=D)
        is src.Coverage.NO_COVERAGE
    )
    assert (
        src.coverage([prim], country="US", claim_type="ACADEMIC_AWARD", on=date(2025, 1, 1))
        is src.Coverage.NO_COVERAGE
    )
    sens = _contract(sensitivity=src.Sensitivity.CRIMINAL)
    assert (
        src.coverage([sens], country="US", claim_type="ACADEMIC_AWARD", on=D)
        is src.Coverage.NO_COVERAGE
    )


def test_glb_34_a_200_country_claim_is_blocked_by_the_coverage_gate():
    prim = _contract()
    src.refuse_unbacked_claim(src.CoverageClaim("ACADEMIC_AWARD", frozenset({"US"})), [prim], on=D)
    countries = frozenset({"US", "FR", "DE", "JP"})
    with pytest.raises(src.SourceError) as e:
        src.refuse_unbacked_claim(src.CoverageClaim("ACADEMIC_AWARD", countries), [prim], on=D)
    assert (
        e.value.code == "COVERAGE_CLAIM_UNBACKED"
        and "FR" in str(e.value)
        and "US" not in str(e.value).split(": ")[1]
    )


def test_the_registry_loader_refuses_bad_rows():
    ok = {
        "source_id": "s",
        "claim_types": ["A"],
        "countries": ["us"],
        "purposes": ["P"],
        "processing_regions": ["us"],
        "valid_from": "2026-01-01",
    }
    assert src.parse({"sources": [ok]})[0].countries == frozenset({"US"})
    for bad in (
        {"sources": [{**ok, "sensitivity": "NUCLEAR"}]},
        {"sources": [{"source_id": "s"}]},
        {"sources": [ok, ok]},
    ):
        with pytest.raises(src.SourceError) as e:
            src.parse(bad)
        assert e.value.code == "SOURCE_REGISTRY_INVALID"
