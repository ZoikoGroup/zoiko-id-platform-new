"""ZID-INT-002 sections 10 and 11: authority and property from supplied evidence. One separation at a time.

T24  official property title without a mandate does not let an agent list
T25  UK Right to Rent is not applied outside England by default, and England's route needs a registered provider
T30  a legal entity identifier exists but no mandate: authority is INDETERMINATE
"""

from __future__ import annotations

import pytest
from zid_platform.modules.credential.domain import authority_check as ac


def doc(
    ref: str = "ev_1", *, usable=True, status="VERIFIED_LIMITED", unchanged=True, reasons=()
) -> dict:
    return {
        "evidence_ref": ref,
        "usable": usable,
        "signature_status": status,
        "signature_reasons": list(reasons),
        "signed_and_unchanged": unchanged,
    }


SIGNED = doc()
UNSIGNED = doc("ev_u", status="INDETERMINATE", unchanged=False, reasons=("NO_SIGNATURE_FOUND",))
BROKEN = doc(
    "ev_b", status="NOT_VERIFIED", unchanged=False, reasons=("DOCUMENT_ALTERED_AFTER_SIGNING",)
)
UNUSABLE = doc("ev_x", usable=False, status="NOT_APPLICABLE", unchanged=False)


def lei(base: str = "5493001KJTIIGC8Y1R") -> str:
    """A well-formed LEI built from 18 characters, so no real entity's identifier is assumed."""
    n = int("".join(str(int(c, 36)) for c in base) + "00")
    return base + f"{98 - n % 97:02d}"


# ----------------------------------------------------------------------------------------------------------------- dimensions


def test_a_signed_unchanged_document_is_verified_limited_and_names_what_it_does_not_show() -> None:
    d = ac.from_evidence("mandate", [SIGNED], absent_reason="MANDATE_EVIDENCE_ABSENT")
    assert d.outcome == "VERIFIED_LIMITED" and d.outcome != "VERIFIED"
    assert "signatory_not_matched_to_an_officer_or_title_register" in d.limitations
    assert "scope_of_authority_for_this_act_not_confirmed" in d.limitations


def test_no_evidence_unsigned_evidence_and_unusable_evidence_prove_nothing() -> None:
    assert ac.from_evidence("mandate", [], absent_reason="MANDATE_EVIDENCE_ABSENT").reasons == (
        "MANDATE_EVIDENCE_ABSENT",
    )
    assert ac.from_evidence("mandate", [UNSIGNED], absent_reason="x").reasons == (
        "EVIDENCE_NOT_SIGNED_OR_NOT_TRUSTED",
    )
    assert ac.from_evidence("mandate", [UNUSABLE], absent_reason="x").reasons == (
        "EVIDENCE_NOT_USABLE",
    )
    for d in (
        ac.from_evidence("mandate", [], absent_reason="x"),
        ac.from_evidence("mandate", [UNSIGNED], absent_reason="x"),
        ac.from_evidence("mandate", [UNUSABLE], absent_reason="x"),
    ):
        assert d.outcome == "INDETERMINATE"


def test_one_broken_document_poisons_the_dimension_even_beside_a_good_one() -> None:
    d = ac.from_evidence("mandate", [SIGNED, BROKEN], absent_reason="x")
    assert d.outcome == "NOT_VERIFIED" and "DOCUMENT_ALTERED_AFTER_SIGNING" in d.reasons


def test_a_good_document_beside_an_unsigned_one_is_not_enough() -> None:
    assert (
        ac.from_evidence("mandate", [SIGNED, UNSIGNED], absent_reason="x").outcome
        == "INDETERMINATE"
    )


def test_a_valid_signature_that_was_followed_by_a_change_is_not_signed_and_unchanged() -> None:
    changed = doc(status="VERIFIED_LIMITED", unchanged=False)
    assert ac.from_evidence("mandate", [changed], absent_reason="x").outcome == "INDETERMINATE"


# ------------------------------------------------------------------------------------------------------------------ LEI


def test_a_well_formed_lei_is_not_an_entity_and_a_bad_one_is_named() -> None:
    ok = ac.lei_status(lei())
    assert ok.outcome == "INDETERMINATE" and ok.reasons == (
        "LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE",
    )
    bad = list(lei())
    bad[3] = "2" if bad[3] != "2" else "3"
    assert ac.lei_status("".join(bad)).reasons == ("LEI_CHECK_DIGITS_INVALID",)
    assert ac.lei_status("SHORT").reasons == ("LEI_MALFORMED",)
    assert ac.lei_status("5493001KJTIIGC8Y1R1!").reasons == ("LEI_MALFORMED",)
    assert ac.lei_status(None).reasons == ("NO_LEGAL_ENTITY_IDENTIFIER_GIVEN",)
    assert ac.lei_status(lei().lower()).reasons == ("LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE",)


# ------------------------------------------------------------------------------------------------------------------ T30


def test_t30_a_legal_entity_identifier_with_no_mandate_leaves_authority_indeterminate() -> None:
    out = ac.mandate_verification([], lei())
    assert out["legal_entity"]["reasons"] == ["LEI_WELL_FORMED_EXISTENCE_NOT_LOOKED_UP_HERE"]
    assert out["authority"]["outcome"] == "INDETERMINATE" and out["authority"]["reasons"] == [
        "MANDATE_EVIDENCE_ABSENT"
    ]
    assert out["authority_assurance"] == "NONE"


def test_a_signed_mandate_is_signed_document_only_never_confirmed_authority() -> None:
    out = ac.mandate_verification([SIGNED], lei())
    assert (
        out["authority"]["outcome"] == "VERIFIED_LIMITED"
        and out["authority_assurance"] == "SIGNED_DOCUMENT_ONLY"
    )


# ------------------------------------------------------------------------------------------------------------ property


def prop(
    relationship="authorized_agent",
    requested=("ownership", "mandate"),
    country="US",
    sub="",
    **evidence,
):
    return ac.property_check(
        relationship=relationship,
        requested=list(requested),
        jurisdiction={"country": country, "subdivision": sub},
        title=evidence.get("title", []),
        mandate=evidence.get("mandate", []),
        permission=evidence.get("permission", []),
    )


def by(out: dict, name: str) -> dict:
    return next(d for d in out["dimensions"] if d["dimension"] == name)


def test_t24_official_title_without_a_mandate_does_not_let_an_agent_list() -> None:
    out = prop(title=[SIGNED])
    assert by(out, "ownership")["outcome"] == "VERIFIED_LIMITED"
    assert by(out, "mandate")["outcome"] == "INDETERMINATE"
    assert out["listing_authority"] == "UNKNOWN", (
        "title alone is not a right to list for someone else"
    )


def test_an_agent_is_judged_on_the_mandate_even_when_only_ownership_was_asked_about() -> None:
    out = prop(requested=("ownership",), title=[SIGNED])
    assert out["listing_authority"] == "UNKNOWN"


def test_an_agent_with_title_and_a_signed_mandate_is_review_required_never_eligible() -> None:
    out = prop(title=[SIGNED], mandate=[doc("ev_m")])
    assert out["listing_authority"] == "REVIEW_REQUIRED"
    assert (
        "ELIGIBLE" != out["listing_authority"]
        and "never confirms a right to let" in out["listing_authority_note"]
    )


def test_a_broken_mandate_makes_the_agent_not_eligible() -> None:
    assert prop(title=[SIGNED], mandate=[BROKEN])["listing_authority"] == "NOT_ELIGIBLE"


def test_an_owner_needs_no_mandate_and_no_sublet_permission() -> None:
    out = prop("owner", ("ownership", "mandate", "permission_to_list"), title=[SIGNED])
    assert (
        by(out, "mandate")["required"] is False
        and by(out, "permission_to_list")["required"] is False
    )
    assert out["listing_authority"] == "REVIEW_REQUIRED"


def test_a_subletting_tenant_needs_a_permission_that_stands_on_its_own() -> None:
    assert prop("tenant_sublet", ("permission_to_list",))["listing_authority"] == "UNKNOWN"
    assert (
        prop("tenant_sublet", ("permission_to_list",), permission=[SIGNED])["listing_authority"]
        == "REVIEW_REQUIRED"
    )


def test_t25_england_needs_a_registered_provider_and_no_other_place_borrows_its_rule() -> None:
    england = by(
        prop(requested=("tenant_legal_route",), country="GB", sub="GB-ENG"), "tenant_legal_route"
    )
    assert england["outcome"] == "UNAVAILABLE" and england["reasons"] == [
        "RIGHT_TO_RENT_REQUIRES_A_REGISTERED_PROVIDER"
    ]
    for country, sub in (("GB", "GB-SCT"), ("GB", "GB-WLS"), ("US", ""), ("DE", "")):
        d = by(
            prop(requested=("tenant_legal_route",), country=country, sub=sub), "tenant_legal_route"
        )
        assert d["outcome"] == "UNAVAILABLE" and d["reasons"] == [
            "NO_RULE_FOR_THIS_JURISDICTION"
        ], (country, sub)


def test_the_legal_route_never_decides_the_listing() -> None:
    out = prop(
        "owner", ("ownership", "tenant_legal_route"), country="GB", sub="GB-ENG", title=[SIGNED]
    )
    assert out["listing_authority"] == "REVIEW_REQUIRED", (
        "an unavailable legal route does not make an owner ineligible, nor eligible"
    )


@pytest.mark.parametrize(
    ("relationship", "requested", "code"),
    [
        ("landlord_friend", ("ownership",), "RELATIONSHIP_UNKNOWN"),
        ("owner", ("ownership", "vibes"), "DIMENSION_UNKNOWN"),
        ("owner", (), "DIMENSION_UNKNOWN"),
    ],
)
def test_an_unknown_relationship_or_dimension_is_refused_by_name(
    relationship, requested, code
) -> None:
    with pytest.raises(ac.AuthorityError) as e:
        prop(relationship, requested)
    assert e.value.code == code
