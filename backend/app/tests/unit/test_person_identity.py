"""PERSON_IDENTITY from a wallet or eID credential (Document 03 section 06): who a person is, from facts the credential verifier established. Pure."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest
from zid_platform.modules.credential.api.v2_identity import Expected, check_expected
from zid_platform.modules.credential.domain import person_identity as pi
from zid_platform.modules.credential.domain.claims import CredentialStatus

TODAY = date(2026, 11, 13)
POLICY = pi.IdentityPolicy(frozenset({"PersonIdentificationData"}))
GOOD = pi.PersonCredentialFacts(
    integrity=True,
    issuer_trusted_for_type=True,
    holder_bound=True,
    status=CredentialStatus.ACTIVE,
    credential_types=("PersonIdentificationData",),
    claims={
        "family_name": "Eriksson",
        "given_name": "Anna Maria",
        "birth_date": "1974-08-12",
        "document_number": "L898902C3",
        "address": "1 Utopia Road",
    },
)


def decide(
    facts: pi.PersonCredentialFacts = GOOD, policy: pi.IdentityPolicy = POLICY
) -> pi.PersonIdentityOutcome:
    return pi.decide(facts, policy, today=TODAY)


def test_a_holder_bound_credential_from_a_trusted_issuer_establishes_identity_but_never_more_than_limited() -> (
    None
):
    o = decide()
    assert o.status == "VERIFIED_LIMITED" and o.person_identity_established
    assert o.identity == {
        "family_name": "Eriksson",
        "given_name": "Anna Maria",
        "birth_date": "1974-08-12",
    }
    assert {"LIVE_PERSON_NOT_ASSESSED", "ISSUER_AUTHENTICATED_NOT_PRIMARY_SOURCE"} <= set(o.reasons)
    pub = o.public()
    assert pub["dimension"] == "PERSON_IDENTITY" and pub["primary_source_verified"] is False
    assert pub["assurance_ceiling"] == "ISSUER_AUTHENTICATED" and pub["limitations"]


def test_only_the_name_and_birth_date_are_returned_never_the_rest_of_the_credential() -> None:
    flat = repr(decide().public())
    assert "L898902C3" not in flat and "Utopia Road" not in flat


def test_a_copied_credential_does_not_identify_anyone() -> None:
    o = decide(replace(GOOD, holder_bound=False))
    assert (
        o.status == "INDETERMINATE"
        and o.reasons == ("HOLDER_NOT_BOUND",)
        and not o.person_identity_established
    )
    assert o.public()["holder_bound"] is False


def test_an_unknown_issuer_means_the_signature_could_not_be_tried_not_that_it_was_forged() -> None:
    o = decide(replace(GOOD, integrity=False, issuer_known=False))
    assert o.status == "INDETERMINATE" and o.reasons == ("ISSUER_NOT_TRUSTED_FOR_IDENTITY",)


def test_a_credential_whose_signature_fails_is_not_verified() -> None:
    o = decide(replace(GOOD, integrity=False))
    assert o.status == "NOT_VERIFIED" and o.reasons == ("CREDENTIAL_SIGNATURE_NOT_VALID",)


@pytest.mark.parametrize("status", [CredentialStatus.REVOKED, CredentialStatus.SUSPENDED])
def test_a_revoked_or_suspended_credential_is_not_verified(status) -> None:
    o = decide(replace(GOOD, status=status))
    assert o.status == "NOT_VERIFIED" and o.reasons == (f"CREDENTIAL_{status.value}",)


@pytest.mark.parametrize("status", [CredentialStatus.UNKNOWN, CredentialStatus.EXPIRED])
def test_a_status_that_cannot_be_confirmed_is_not_acceptance(status) -> None:
    o = decide(replace(GOOD, status=status))
    assert o.status == "INDETERMINATE" and o.reasons == ("CREDENTIAL_STATUS_NOT_CONFIRMED",)


def test_an_issuer_not_trusted_for_this_type_is_not_enough_however_good_the_signature() -> None:
    o = decide(replace(GOOD, issuer_trusted_for_type=False))
    assert o.status == "INDETERMINATE" and o.reasons == ("ISSUER_NOT_TRUSTED_FOR_IDENTITY",)


def test_a_credential_type_the_owners_never_accepted_as_identity_is_refused() -> None:
    o = decide(replace(GOOD, credential_types=("UniversityDegree",)))
    assert o.status == "INDETERMINATE"
    assert o.reasons == ("CREDENTIAL_TYPE_NOT_ACCEPTED_AS_EVIDENCE_OF_IDENTITY",)


def test_with_no_accepted_types_configured_nothing_passes() -> None:
    assert decide(policy=pi.IdentityPolicy()).status == "INDETERMINATE"


@pytest.mark.parametrize("missing", ["family_name", "given_name", "birth_date"])
def test_each_required_claim_must_be_present(missing) -> None:
    claims = {k: v for k, v in dict(GOOD.claims or {}).items() if k != missing}
    o = decide(replace(GOOD, claims=claims))
    assert o.status == "INDETERMINATE" and o.reasons == (
        f"IDENTITY_CLAIM_MISSING_{missing.upper()}",
    )


def test_claims_written_the_schema_org_way_are_read() -> None:
    o = decide(
        replace(
            GOOD, claims={"familyName": "Eriksson", "givenName": "Anna", "birthDate": "1974-08-12"}
        )
    )
    assert o.status == "VERIFIED_LIMITED" and o.identity["family_name"] == "Eriksson"


@pytest.mark.parametrize(
    "bad", ["12/08/1974", "1974-13-01", "1974-02-30", "2999-01-01", "1800-01-01", "", "  "]
)
def test_a_birth_date_that_is_not_a_real_past_date_is_not_accepted(bad) -> None:
    claims = {**dict(GOOD.claims or {}), "birth_date": bad}
    assert decide(replace(GOOD, claims=claims)).status == "INDETERMINATE"


def test_names_that_are_not_text_are_treated_as_missing() -> None:
    o = decide(
        replace(
            GOOD,
            claims={
                "family_name": {"en": "Eriksson"},
                "given_name": "Anna",
                "birth_date": "1974-08-12",
            },
        )
    )
    assert o.reasons == ("IDENTITY_CLAIM_MISSING_FAMILY_NAME",)


def test_the_status_checks_run_before_anything_is_read_from_the_claims() -> None:
    o = decide(replace(GOOD, status=CredentialStatus.REVOKED, claims=None))
    assert o.status == "NOT_VERIFIED" and o.identity == {}


# --------------------------------------------------------------------------------------------------- the expected person


def test_the_expected_person_matches_across_accents_and_word_order() -> None:
    ident = {"family_name": "Łukasiewicz", "given_name": "José", "birth_date": "1990-01-02"}
    assert (
        check_expected(ident, Expected(family_name="Lukasiewicz", given_name="Jose"))[0]
        == "MATCHED"
    )
    assert (
        check_expected(ident, Expected(family_name="Jose", given_name="Lukasiewicz"))[0]
        == "MATCHED"
    )


def test_a_clearly_different_person_is_not_matched() -> None:
    ident = {"family_name": "Eriksson", "given_name": "Anna", "birth_date": "1974-08-12"}
    verdict, _ = check_expected(ident, Expected(family_name="Tanaka", given_name="Kenji"))
    assert verdict == "NOT_MATCHED"


def test_a_different_birth_date_is_never_a_match() -> None:
    ident = {"family_name": "Eriksson", "given_name": "Anna", "birth_date": "1974-08-12"}
    got = check_expected(
        ident, Expected(family_name="Eriksson", given_name="Anna", birth_date="1975-08-12")
    )
    assert got == ("NOT_MATCHED", "BIRTH_DATE_DIFFERS")
    assert (
        check_expected(
            ident, Expected(family_name="Eriksson", given_name="Anna", birth_date="1974-08-12")
        )[0]
        == "MATCHED"
    )


def test_a_name_in_a_script_the_scorer_cannot_read_goes_to_a_person_and_is_never_a_different_person() -> (
    None
):
    ident = {"family_name": "山田", "given_name": "太郎", "birth_date": "1980-05-05"}
    verdict, why = check_expected(ident, Expected(family_name="Yamada", given_name="Taro"))
    assert verdict == "AMBIGUOUS" and why == "SCRIPT_NOT_COMPARABLE"


def test_a_register_refusal_stops_an_otherwise_good_credential() -> None:
    o = decide(replace(GOOD, register_refusal=("PID_PROVIDER_NOT_LISTED",)))
    assert o.status == "INDETERMINATE" and o.reasons == ("PID_PROVIDER_NOT_LISTED",)
    assert not o.person_identity_established and o.identity == {}


def test_a_register_refusal_never_hides_a_revoked_or_forged_credential() -> None:
    refusal = ("PID_PROVIDER_NOT_LISTED",)
    assert (
        decide(replace(GOOD, status=CredentialStatus.REVOKED, register_refusal=refusal)).status
        == "NOT_VERIFIED"
    )
    assert decide(replace(GOOD, integrity=False, register_refusal=refusal)).status == "NOT_VERIFIED"
