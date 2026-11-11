"""GID-037 credential claim taxonomy and the five-field assertion (ZID-GAP-001 v2.0, sections 01.1, 04.1, 08.1).

Pure rules, no database, no network. Rows from the global acceptance suite that this slice can prove:
GLB-02 / GLB-21  a source that says ATTENDED (or never conferred) never gives a verified degree
GLB-04           a suspended license is not verified and can never be eligible
GLB-05           a source that cannot be reached is SOURCE_UNAVAILABLE, never FAILED and never VERIFIED
GLB-07           a valid signature from an issuer that is not authenticated does not verify the claim
GLB-13           a source that no longer exists leaves the claim UNVERIFIABLE (not false)
GLB-22           a same-name, different-person adverse match is not an adverse decision
GID-037          an unrecognized claim class fails closed
"""

from __future__ import annotations

import pytest
from zid_platform.modules.credential.domain.claims import (
    SPECS,
    Assertion,
    Authenticity,
    ClaimError,
    ClaimType,
    CredentialStatus,
    Decision,
    Eligibility,
    EvidenceTier,
    SourceResult,
    SubjectMatch,
    assertion_from_facts,
    claim_type,
    tier,
    validate_claim,
)

PRIMARY = EvidenceTier.PRIMARY_SOURCE_CONFIRMED


def _facts(claim: ClaimType = ClaimType.ACADEMIC_AWARD, **over):
    base = {
        "source_fields": {"award_status": "CONFERRED"},
        "evidence": PRIMARY,
        "source": SourceResult.RESPONDED,
        "authenticity": Authenticity.CONFIRMED,
        "status": CredentialStatus.ACTIVE,
        "subject": SubjectMatch.MATCHED,
    }
    base.update(over)
    return assertion_from_facts(claim, **base)


# --- taxonomy ---------------------------------------------------------------------------------------------------------


def test_every_object_in_section_04_1_has_a_spec():
    assert set(SPECS) == set(ClaimType)
    assert len(ClaimType) == 7


def test_an_unrecognized_claim_class_fails_closed():
    with pytest.raises(ClaimError) as e:
        claim_type("MASTERS_OF_UNIVERSE")
    assert e.value.code == "CLAIM_CLASS_UNRECOGNIZED"
    with pytest.raises(ClaimError):
        validate_claim("", {})


def test_a_valid_claim_passes_and_a_missing_field_is_refused():
    fields = {"institution": "Uni of X", "program": "BSc", "award_status": "CONFERRED"}
    assert validate_claim("ACADEMIC_AWARD", fields) is ClaimType.ACADEMIC_AWARD
    with pytest.raises(ClaimError) as e:
        validate_claim("ACADEMIC_AWARD", {"institution": "Uni of X", "program": "BSc"})
    assert e.value.code == "CLAIM_FIELD_MISSING"


def test_an_unknown_field_is_refused_not_ignored():
    fields = {
        "institution": "U",
        "program": "BSc",
        "award_status": "CONFERRED",
        "grade_inflation": "A+",
    }
    with pytest.raises(ClaimError) as e:
        validate_claim("ACADEMIC_AWARD", fields)
    assert e.value.code == "CLAIM_FIELD_UNKNOWN"


def test_an_enumerated_field_with_an_unknown_value_is_refused():
    fields = {"institution": "U", "program": "BSc", "award_status": "GRADUATED"}
    with pytest.raises(ClaimError) as e:
        validate_claim("ACADEMIC_AWARD", fields)
    assert e.value.code == "CLAIM_VALUE_UNRECOGNIZED"


def test_every_spec_states_the_inference_it_refuses():
    assert all(spec.disallowed_inference for spec in SPECS.values())


def test_eligibility_and_portability_are_not_evidence_tiers():
    assert tier("PRIMARY_SOURCE_CONFIRMED") is PRIMARY
    for name in ("CURRENT_ELIGIBILITY", "CONSENTED_PORTABLE_ASSERTION", "VERIFIED", ""):
        with pytest.raises(ClaimError) as e:
            tier(name)
        assert e.value.code == "EVIDENCE_TIER_UNRECOGNIZED"


def test_tiers_are_ordered_by_what_they_prove():
    assert (
        EvidenceTier.SELF_DECLARED
        < EvidenceTier.DOCUMENT_INTEGRITY
        < EvidenceTier.ISSUER_AUTHENTICATED
        < EvidenceTier.PRIMARY_SOURCE_CONFIRMED
    )


# --- decisions --------------------------------------------------------------------------------------------------------


def test_a_confirmed_active_matched_award_verifies():
    a = _facts()
    assert a.decision is Decision.VERIFIED
    assert a.primary_source_verified is True
    assert a.reason_codes == ()


@pytest.mark.parametrize(
    "evidence",
    [
        EvidenceTier.SELF_DECLARED,
        EvidenceTier.DOCUMENT_INTEGRITY,
        EvidenceTier.ISSUER_AUTHENTICATED,
    ],
)
def test_nothing_below_a_primary_source_confirmation_ever_verifies(evidence):
    # a genuine-looking scan, an OCR read, or a good signature is not a source-confirmed pass
    a = _facts(evidence=evidence)
    assert a.decision is Decision.NOT_VERIFIED
    assert a.reason_codes == ("PRIMARY_SOURCE_NOT_CONFIRMED",)
    assert a.primary_source_verified is False


def test_glb_02_a_diploma_the_source_never_conferred_is_not_verified():
    a = _facts(source_fields={"award_status": "UNKNOWN"})
    assert a.decision is Decision.NOT_VERIFIED


def test_glb_21_attended_is_not_graduated():
    a = _facts(source_fields={"award_status": "ATTENDED"})
    assert a.decision is Decision.NOT_VERIFIED
    assert a.reason_codes == ("AWARD_STATUS_NOT_CONFERRED",)


def test_glb_07_a_valid_signature_from_an_unauthenticated_issuer_does_not_verify():
    a = _facts(authenticity=Authenticity.NOT_CONFIRMED)
    assert a.decision is Decision.NOT_VERIFIED
    assert a.reason_codes == ("ISSUER_NOT_AUTHENTICATED",)
    assert _facts(authenticity=Authenticity.UNKNOWN).decision is Decision.NOT_VERIFIED


def test_glb_05_an_unreachable_source_is_neither_a_failure_nor_a_pass():
    a = _facts(source=SourceResult.UNAVAILABLE)
    assert a.decision is Decision.SOURCE_UNAVAILABLE
    assert a.primary_source_verified is False
    assert a.decision not in (Decision.VERIFIED, Decision.NOT_VERIFIED)


def test_glb_13_a_source_that_cannot_exist_any_more_is_unverifiable_not_false():
    a = _facts(
        ClaimType.EMPLOYMENT_ASSERTION,
        source_fields={},
        source=SourceResult.NO_RECORD,
        source_obtainable=False,
    )
    assert a.decision is Decision.UNVERIFIABLE
    assert (
        _facts(source=SourceResult.NO_RECORD).decision is Decision.NOT_VERIFIED
    )  # a reachable source with no record


def test_glb_22_a_same_name_different_person_match_goes_to_a_person():
    f = {"subject_match": "AMBIGUOUS"}
    a = _facts(ClaimType.DISCIPLINARY_FINDING, source_fields=f, subject=SubjectMatch.AMBIGUOUS)
    assert a.decision is Decision.REVIEW
    nm = _facts(ClaimType.DISCIPLINARY_FINDING, source_fields=f, subject=SubjectMatch.NOT_MATCHED)
    assert nm.decision is Decision.NOT_VERIFIED  # the finding does not belong to this person


@pytest.mark.parametrize(
    "status",
    [
        CredentialStatus.SUSPENDED,
        CredentialStatus.REVOKED,
        CredentialStatus.EXPIRED,
        CredentialStatus.UNKNOWN,
    ],
)
def test_glb_04_a_credential_that_is_not_active_is_not_verified(status):
    a = _facts(ClaimType.PROFESSIONAL_LICENSE, source_fields={}, status=status)
    assert a.decision is Decision.NOT_VERIFIED
    assert a.reason_codes == (f"CREDENTIAL_{status.value}",)


def test_a_decision_never_reads_the_holders_own_fields():
    # the holder says CONFERRED, the source says ATTENDED: only source_fields count
    a = _facts(source_fields={"award_status": "ATTENDED"})
    assert a.decision is Decision.NOT_VERIFIED


# --- the five facts stay separate -------------------------------------------------------------------------------------


def test_a_verified_claim_does_not_make_anyone_eligible():
    a = _facts(ClaimType.PROFESSIONAL_LICENSE, source_fields={})
    assert a.decision is Decision.VERIFIED
    assert a.professional_eligibility is Eligibility.NOT_EVALUATED
    assert a.current_consumer_entitlement is Eligibility.NOT_EVALUATED


def _assertion(**over) -> Assertion:
    base = {
        "claim_type": ClaimType.PROFESSIONAL_LICENSE,
        "decision": Decision.VERIFIED,
        "credential_status": CredentialStatus.ACTIVE,
        "issuer_authenticity": Authenticity.CONFIRMED,
        "primary_source_verified": True,
        "professional_eligibility": Eligibility.NOT_EVALUATED,
        "current_consumer_entitlement": Eligibility.NOT_EVALUATED,
        "assurance_tier": PRIMARY,
        "subject_match": SubjectMatch.MATCHED,
    }
    base.update(over)
    return Assertion(**base)


def test_an_assertion_cannot_claim_verified_without_its_basis():
    bad = [
        {"primary_source_verified": False},
        {"assurance_tier": EvidenceTier.DOCUMENT_INTEGRITY, "primary_source_verified": False},
        {"issuer_authenticity": Authenticity.UNKNOWN},
        {"credential_status": CredentialStatus.SUSPENDED},
        {"subject_match": SubjectMatch.AMBIGUOUS},
    ]
    for over in bad:
        with pytest.raises(ClaimError) as e:
            _assertion(**over)
        assert e.value.code == "ASSERTION_VERIFIED_WITHOUT_BASIS", over


def test_a_source_verified_flag_contradicting_a_low_tier_is_refused():
    with pytest.raises(ClaimError) as e:
        _assertion(
            decision=Decision.NOT_VERIFIED,
            assurance_tier=EvidenceTier.DOCUMENT_INTEGRITY,
            primary_source_verified=True,
        )
    assert e.value.code == "ASSERTION_TIER_CONTRADICTS_SOURCE"


def test_nobody_is_eligible_on_a_claim_that_is_not_verified():
    for decision in (
        Decision.NOT_VERIFIED,
        Decision.REVIEW,
        Decision.SOURCE_UNAVAILABLE,
        Decision.UNVERIFIABLE,
    ):
        with pytest.raises(ClaimError) as e:
            _assertion(
                decision=decision,
                professional_eligibility=Eligibility.ELIGIBLE,
                primary_source_verified=False,
            )
        assert e.value.code == "ASSERTION_ELIGIBLE_WITHOUT_VERIFIED_CLAIM"


def test_a_consumer_entitlement_needs_the_professional_eligibility_first():
    with pytest.raises(ClaimError) as e:
        _assertion(current_consumer_entitlement=Eligibility.ELIGIBLE)
    assert e.value.code == "ASSERTION_ENTITLEMENT_WITHOUT_ELIGIBILITY"
    ok = _assertion(
        professional_eligibility=Eligibility.ELIGIBLE,
        current_consumer_entitlement=Eligibility.ELIGIBLE,
    )
    assert ok.current_consumer_entitlement is Eligibility.ELIGIBLE


def test_the_contract_shape_keeps_the_five_facts_as_separate_keys():
    c = _facts().to_contract()
    for key in (
        "credential_status",
        "issuer_authenticity",
        "primary_source_verified",
        "professional_eligibility",
        "current_consumer_entitlement",
    ):
        assert key in c
    assert c["assurance_tier"] == "PRIMARY_SOURCE_CONFIRMED"
    assert c["decision"] == "VERIFIED"
    assert c["subject_match"] == {"decision": "MATCHED"}


def test_t06_a_document_with_no_conferral_source_is_neither_verified_nor_false():
    """ZID-INT-002 T06: a diploma that looks valid but that no source was asked about stays undetermined."""
    a = _facts(
        source=SourceResult.NOT_QUERIED,
        evidence=EvidenceTier.SELF_DECLARED,
        authenticity=Authenticity.UNKNOWN,
        status=CredentialStatus.UNKNOWN,
        subject=SubjectMatch.NOT_CHECKED,
        source_fields={},
    )
    assert a.decision is Decision.INDETERMINATE and a.reason_codes == (
        "PRIMARY_SOURCE_NOT_CONFIRMED",
    )
    assert a.primary_source_verified is False and a.assurance_tier is EvidenceTier.SELF_DECLARED
