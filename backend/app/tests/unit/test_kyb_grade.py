"""Company verification grades K0 to K4 (WF02 section 4; acceptance T13, T14, T15, T21, T22, T33)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from zid_platform.modules.organization.domain import kyb_grade as g

NOW = datetime(2026, 11, 17, tzinfo=UTC)
FRESH = NOW - timedelta(days=10)
REG = g.RegistryFact(True, "ACTIVE", FRESH, timedelta(days=90))
DOCS = g.DocumentFact(("r1", "r2"), True, NOW - timedelta(days=5))
MANDATE = g.MandateFact(
    "p1",
    True,
    frozenset({"SIGN_AGREEMENT", "BIND_ENTITY"}),
    NOW - timedelta(days=30),
    NOW + timedelta(days=300),
)
FULL = {
    "registry": REG,
    "ownership_complete": True,
    "persons_verified": True,
    "screening": "SCREENING_CLEAR",
    "mandates": (MANDATE,),
}


def grade(**kw) -> g.Assessment:
    return g.assess(g.KybFacts(**kw), now=NOW)


def test_nothing_independent_is_k0() -> None:
    a = grade()
    assert a.grade == g.Grade.K0 and a.label == "Unverified"
    assert "NO_REGISTRY_CHECK" in a.reasons and "NO_DOCUMENT_REVIEW" in a.reasons


def test_documents_reviewed_by_two_people_are_k1_and_never_a_verified_company() -> None:
    a = grade(documents=DOCS)
    assert (
        a.grade == g.Grade.K1
        and a.label == "Documents reviewed"
        and a.claims == ("DOCUMENTS_REVIEWED",)
    )
    assert "verified" not in a.label.lower()  # T13: never the unqualified "Verified company"
    assert all("VERIFIED_COMPANY" not in c for c in a.claims)


def test_one_reviewer_or_the_same_reviewer_twice_is_not_k1() -> None:
    for reviewers in ((), ("r1",), ("r1", "r1")):
        a = grade(documents=g.DocumentFact(reviewers, True, NOW))
        assert a.grade == g.Grade.K0 and "TWO_INDEPENDENT_REVIEWERS_REQUIRED" in a.reasons, (
            reviewers
        )


def test_documents_that_are_not_current_do_not_count() -> None:
    a = grade(documents=g.DocumentFact(("r1", "r2"), False, NOW))
    assert a.grade == g.Grade.K0 and "DOCUMENTS_NOT_CURRENT" in a.reasons


def test_an_exact_active_fresh_registry_match_is_k2() -> None:
    a = grade(registry=REG)
    assert a.grade == g.Grade.K2 and a.label == "Registry matched"
    assert a.claims == ("REGISTER_FOUND", "COMPANY_ACTIVE_SOURCE_REPORTED")
    assert {"OWNERSHIP_INCOMPLETE", "PERSONS_NOT_VERIFIED"} <= set(a.reasons)


def test_a_registry_that_did_not_match_or_could_not_answer_is_not_k2_and_is_not_a_finding_against_the_company() -> (
    None
):
    for reg, why in (
        (g.RegistryFact(False, "UNKNOWN", FRESH, timedelta(days=90)), "REGISTRY_NO_EXACT_MATCH"),
        (
            g.RegistryFact(True, "SOURCE_UNAVAILABLE", FRESH, timedelta(days=90)),
            "REGISTRY_STATUS_NOT_ESTABLISHED",
        ),
        (
            g.RegistryFact(True, "ACTIVE", NOW - timedelta(days=200), timedelta(days=90)),
            "REGISTRY_CHECK_STALE",
        ),  # T09, T34
    ):
        a = grade(registry=reg)
        assert a.grade == g.Grade.K0 and why in a.reasons, why


def test_a_stale_registry_with_good_documents_falls_back_to_k1_not_k2() -> None:
    a = grade(
        registry=g.RegistryFact(True, "ACTIVE", NOW - timedelta(days=200), timedelta(days=90)),
        documents=DOCS,
    )
    assert a.grade == g.Grade.K1 and "REGISTRY_CHECK_STALE" in a.reasons


def test_k3_needs_ownership_people_and_clear_screening() -> None:
    assert grade(**{**FULL, "mandates": ()}).grade == g.Grade.K3
    for key, value, code in (
        ("ownership_complete", False, "OWNERSHIP_INCOMPLETE"),
        ("persons_verified", False, "PERSONS_NOT_VERIFIED"),
        ("screening", "POTENTIAL_MATCH", "SCREENING_POTENTIAL_MATCH"),
        ("screening", "UNAVAILABLE", "SCREENING_UNAVAILABLE"),
        ("screening", "NOT_RUN", "SCREENING_NOT_RUN"),
    ):
        a = grade(**{**FULL, "mandates": (), key: value})
        assert a.grade == g.Grade.K2 and code in a.reasons, code


def test_screening_that_is_not_required_does_not_block_k3() -> None:
    a = grade(**{**FULL, "mandates": (), "screening": "NOT_RUN", "screening_required": False})
    assert a.grade == g.Grade.K3 and "SCREENING_CLEAR" not in a.claims


def test_k4_needs_a_verified_person_with_a_mandate_in_force() -> None:
    assert grade(**FULL).grade == g.Grade.K4
    expired = g.MandateFact(
        "p1", True, MANDATE.powers, NOW - timedelta(days=400), NOW - timedelta(days=1)
    )
    revoked = g.MandateFact(
        "p1", True, MANDATE.powers, NOW - timedelta(days=30), None, revoked=True
    )  # T22
    unverified = g.MandateFact("p1", False, MANDATE.powers, NOW - timedelta(days=30), None)
    future = g.MandateFact("p1", True, MANDATE.powers, NOW + timedelta(days=1), None)
    for m in (expired, revoked, unverified, future):
        a = grade(**{**FULL, "mandates": (m,)})
        assert a.grade == g.Grade.K3 and "NO_MANDATE_IN_FORCE" in a.reasons


def test_a_company_the_source_reports_dissolved_is_k0_whatever_else_is_true() -> None:  # T21
    a = grade(
        **{
            **FULL,
            "registry": g.RegistryFact(True, "DISSOLVED", FRESH, timedelta(days=90)),
            "documents": DOCS,
        }
    )
    assert a.grade == g.Grade.K0 and a.reasons == ("COMPANY_DISSOLVED_SOURCE_REPORTED",)
    assert g.permitted_actions(g.KybFacts(**FULL), a, person="p1", now=NOW) == frozenset()


def test_k1_cannot_do_anything_that_binds_the_company() -> None:  # T14
    facts = g.KybFacts(documents=DOCS, mandates=(MANDATE,))
    a = g.assess(facts, now=NOW)
    allowed = g.permitted_actions(facts, a, person="p1", now=NOW)
    assert allowed == {"ORG_PROFILE_DRAFT", "ORG_PROFILE_MAINTENANCE"} and not (
        allowed & g.BINDING_ACTIONS
    )


def test_a_registry_matched_company_without_a_verified_representative_cannot_sign() -> None:  # T15
    facts = g.KybFacts(registry=REG)
    a = g.assess(facts, now=NOW)
    assert a.grade == g.Grade.K2
    assert not (g.permitted_actions(facts, a, person="p1", now=NOW) & g.BINDING_ACTIONS)


def test_a_binding_act_needs_the_person_the_power_and_the_second_signatory() -> None:
    facts = g.KybFacts(**FULL)
    a = g.assess(facts, now=NOW)
    assert g.permitted_actions(facts, a, person="p1", now=NOW) >= {"SIGN_AGREEMENT", "BIND_ENTITY"}
    assert not (
        g.permitted_actions(facts, a, person="someone-else", now=NOW) & g.BINDING_ACTIONS
    )  # a different person gets nothing
    assert not (g.permitted_actions(facts, a, now=NOW) & g.BINDING_ACTIONS)  # no person named
    assert "AUTHORIZE_PAYMENT" not in g.permitted_actions(
        facts, a, person="p1", now=NOW
    )  # the mandate does not list it
    dual = g.MandateFact(
        "p1", True, MANDATE.powers, NOW - timedelta(days=30), None, second_signatory_required=True
    )
    f2 = g.KybFacts(**{**FULL, "mandates": (dual,)})
    assert not (
        g.permitted_actions(f2, g.assess(f2, now=NOW), person="p1", now=NOW) & g.BINDING_ACTIONS
    )
    done = g.MandateFact(
        "p1",
        True,
        MANDATE.powers,
        NOW - timedelta(days=30),
        None,
        second_signatory_required=True,
        second_signatory_present=True,
    )
    f3 = g.KybFacts(**{**FULL, "mandates": (done,)})
    assert g.permitted_actions(f3, g.assess(f3, now=NOW), person="p1", now=NOW) >= {
        "SIGN_AGREEMENT"
    }


def test_a_grade_is_never_unconditional_privilege_the_actions_are_recomputed_when_the_mandate_lapses() -> (
    None
):
    facts = g.KybFacts(**FULL)
    later = NOW + timedelta(days=400)  # the mandate has ended and the registry check is stale
    a = g.assess(facts, now=later)
    assert a.grade == g.Grade.K0
    assert g.permitted_actions(facts, a, person="p1", now=later) == frozenset()


def test_recheck_due_follows_the_provisional_ceilings_and_events_override_them() -> None:
    k1 = grade(documents=DOCS)
    assert k1.recheck_due == DOCS.reviewed_at + timedelta(days=180)
    k2 = grade(registry=REG)
    assert k2.recheck_due == FRESH + timedelta(days=365)
    short = g.assess(g.KybFacts(documents=DOCS), now=NOW, recheck_days={g.Grade.K1: 30})
    assert short.recheck_due == DOCS.reviewed_at + timedelta(
        days=30
    )  # policy may shorten, the default never lengthens past it
    for event in (
        "MANDATE_REVOKED",
        "COMPANY_DISSOLVED",
        "OWNERSHIP_CHANGED",
        "SANCTIONS_ALERT",
        "REGISTRY_STATUS_CHANGED",
    ):
        assert g.event_invalidates(event), event  # T33
    assert not g.event_invalidates("PAGE_VIEWED")


def test_the_same_facts_always_give_the_same_answer_and_ranks_are_ordered() -> None:
    assert grade(**FULL) == grade(**FULL)
    assert [g.rank(x) for x in g.Grade] == [0, 1, 2, 3, 4]
    assert set(g.LABEL) == set(g.Grade) and all(
        "verified company" not in v.lower() for v in g.LABEL.values()
    )


def test_facts_round_trip_through_plain_json_and_give_the_same_grade() -> None:
    import json

    facts = g.KybFacts(**FULL, documents=DOCS)
    again = g.facts_from_dict(json.loads(json.dumps(g.facts_to_dict(facts))))
    assert again == facts
    assert g.assess(again, now=NOW) == g.assess(facts, now=NOW)
    assert g.facts_from_dict(g.facts_to_dict(g.KybFacts())) == g.KybFacts()


def test_the_stored_form_holds_ids_and_codes_only() -> None:
    text = str(g.facts_to_dict(g.KybFacts(**FULL, documents=DOCS)))
    assert "p1" in text and "r1" in text  # person and reviewer ids
    assert all(word not in text.lower() for word in ("name", "address", "passport", "birth"))


def test_an_unreadable_stored_record_is_refused_not_guessed() -> None:
    import pytest

    for bad in (
        {"registry": {"matched": True}},
        {"mandates": [{"person": "p"}]},
        {
            "registry": {
                "matched": True,
                "status_reported": "ACTIVE",
                "checked_at": "not a date",
                "max_age_s": 1,
            }
        },
    ):
        with pytest.raises(ValueError, match="KYB_FACTS_UNREADABLE"):
            g.facts_from_dict(bad)


def test_a_company_check_that_rested_on_an_lei_record_is_k2_but_never_claims_a_register_finding() -> (
    None
):
    reference = g.RegistryFact(True, "ACTIVE", NOW, timedelta(days=30), reference_only=True)
    a = grade(registry=reference)
    assert a.grade is g.Grade.K2
    assert "LEI_RECORD_FOUND" in a.claims and "REGISTER_FOUND" not in a.claims
    assert "REGISTER_NOT_CHECKED_LEI_REFERENCE_ONLY" in a.reasons
