"""The WF03 signal model and its one deterministic decision (CR-110)."""

from __future__ import annotations

from dataclasses import replace

from zid_platform.modules.verification.domain.idv_decision import (
    AlternateRoute,
    ChipAuth,
    Decision,
    FaceMatch,
    Liveness,
    NfcAccess,
    Optical,
    PassiveAuth,
    Policy,
    Requirement,
    Signals,
    assertions,
    decide,
)

POLICY = Policy(
    version="gb-v1",
    alternates=(AlternateRoute("PASSPORT_OPTICAL_REVIEW", may_approve=True, reviewers_needed=2),),
)
GOOD = Signals(
    optical=Optical.PASS,
    nfc=NfcAccess.READ_OK,
    passive_auth=PassiveAuth.TRUSTED_PASS,
    chip_auth=ChipAuth.PASS,
    face=FaceMatch.MATCH,
    liveness=Liveness.PASS,
    mrz_check_digits_ok=True,
    optical_matches_chip="MATCH",
    reference_source="CHIP_DG2_TRUSTED",
    injection_risk=False,
    document_expired=False,
)


def d(**kw: object) -> Decision:
    return decide(replace(GOOD, **kw), POLICY).decision  # type: ignore[arg-type]


def reasons(**kw: object) -> tuple[str, ...]:
    return decide(replace(GOOD, **kw), POLICY).reasons  # type: ignore[arg-type]


def test_every_signal_good_is_approved_and_pinned_to_the_policy() -> None:
    r = decide(GOOD, POLICY)
    assert r.decision is Decision.APPROVED and r.policy_version == "gb-v1"
    assert (
        "HOLDER_MATCHES_TRUSTED_CHIP_PORTRAIT" in r.allowed_assertions
        and "CHIP_NOT_CLONED" in r.allowed_assertions
    )
    assert decide(GOOD, POLICY) == r  # deterministic


def test_reading_a_chip_is_not_a_verified_chip() -> None:
    for pa in (PassiveAuth.CERT_MISSING, PassiveAuth.CERT_UNTRUSTED, PassiveAuth.NOT_TESTED):
        r = decide(replace(GOOD, passive_auth=pa), POLICY)
        assert r.decision is Decision.INDETERMINATE
        assert (
            "CHIP_DATA_ISSUER_SIGNED" not in r.allowed_assertions
            and "CHIP_DATA_READ" in r.allowed_assertions
        )
    assert "CHIP_TRUST_ROOT_MISSING" in reasons(passive_auth=PassiveAuth.CERT_MISSING)


def test_a_broken_signature_is_the_defined_failure_and_missing_trust_is_not_an_accusation() -> None:
    assert d(passive_auth=PassiveAuth.SIGNATURE_FAIL) is Decision.REJECTED
    assert d(passive_auth=PassiveAuth.CERT_MISSING) is Decision.INDETERMINATE


def test_unknown_revocation_follows_the_policy() -> None:
    assert d(passive_auth=PassiveAuth.REVOCATION_UNKNOWN) is Decision.INDETERMINATE
    r = decide(
        replace(GOOD, passive_auth=PassiveAuth.REVOCATION_UNKNOWN),
        replace(POLICY, on_revocation_unknown="REVIEW"),
    )
    assert r.decision is Decision.REVIEW_REQUIRED


def test_nfc_states_are_separate_and_never_downgrade_a_mandatory_chip() -> None:
    for nfc in (NfcAccess.UNSUPPORTED, NfcAccess.USER_DECLINED, NfcAccess.READ_FAILED):
        r = decide(replace(GOOD, nfc=nfc), POLICY)
        assert r.decision is Decision.INDETERMINATE and f"CHIP_{nfc.value}" in r.reasons


def test_web_capture_never_claims_the_chip() -> None:
    r = decide(replace(GOOD, web_only_capture=True), POLICY)
    assert (
        r.decision is Decision.INDETERMINATE
        and "CHIP_DATA_ISSUER_SIGNED" not in r.allowed_assertions
    )


def test_clone_resistance_is_its_own_claim() -> None:
    assert (
        d(chip_auth=ChipAuth.UNSUPPORTED) is Decision.APPROVED
    )  # optional here, but no claim is made
    assert "CHIP_NOT_CLONED" not in assertions(replace(GOOD, chip_auth=ChipAuth.UNSUPPORTED))
    assert d(chip_auth=ChipAuth.FAIL) is Decision.REVIEW_REQUIRED
    strict = replace(POLICY, clone_check=Requirement.REQUIRED)
    assert (
        decide(replace(GOOD, chip_auth=ChipAuth.UNSUPPORTED), strict).decision
        is Decision.INDETERMINATE
    )


def test_a_failed_or_inconclusive_presentation_check_is_not_repaired_by_a_good_face_score() -> None:
    assert d(liveness=Liveness.FAIL) is Decision.REVIEW_REQUIRED
    assert d(liveness=Liveness.INCONCLUSIVE) is Decision.REVIEW_REQUIRED
    assert "HOLDER_MATCHES_TRUSTED_CHIP_PORTRAIT" not in assertions(
        replace(GOOD, liveness=Liveness.FAIL)
    )
    assert d(liveness=Liveness.NOT_RUN) is Decision.INDETERMINATE


def test_face_outcomes_never_reject_automatically() -> None:
    assert d(face=FaceMatch.NON_MATCH) is Decision.REVIEW_REQUIRED
    assert d(face=FaceMatch.INCONCLUSIVE) is Decision.REVIEW_REQUIRED
    assert d(face=FaceMatch.NOT_RUN) is Decision.INDETERMINATE
    assert d(reference_source="CHIP_DG2_UNTRUSTED") is Decision.REVIEW_REQUIRED


def test_printed_text_alone_never_approves_and_a_typed_transcription_is_not_authentication() -> (
    None
):
    r = decide(Signals(optical=Optical.PASS, document_expired=False), POLICY)
    assert r.decision is Decision.INDETERMINATE
    typed = decide(replace(GOOD, typed_transcription_only=True), POLICY)
    assert (
        typed.decision is Decision.REVIEW_REQUIRED
        and "DOCUMENT_OPTICALLY_AUTHENTIC" not in typed.allowed_assertions
    )


def test_optical_failures_and_mismatches() -> None:
    assert d(optical=Optical.FAIL) is Decision.REJECTED
    assert d(optical=Optical.REVIEW) is Decision.REVIEW_REQUIRED
    assert d(optical=Optical.UNKNOWN) is Decision.INDETERMINATE
    assert d(mrz_check_digits_ok=False) is Decision.REVIEW_REQUIRED
    assert d(optical_matches_chip="MISMATCH") is Decision.REVIEW_REQUIRED
    assert d(optical_matches_chip="UNKNOWN") is Decision.INDETERMINATE


def test_a_capture_whose_origin_was_not_established_is_indeterminate() -> None:
    assert d(injection_risk=None) is Decision.INDETERMINATE
    relaxed = replace(POLICY, capture_integrity=Requirement.OPTIONAL)
    assert decide(replace(GOOD, injection_risk=None), relaxed).decision is Decision.APPROVED


def test_capture_injection_blocks_automatic_approval() -> None:
    assert d(injection_risk=True) is Decision.REVIEW_REQUIRED
    assert "LIVE_PRESENCE" not in assertions(replace(GOOD, injection_risk=True))


def test_expiry_is_the_markets_policy_and_no_universal_rule() -> None:
    assert d(document_expired=True) is Decision.REJECTED
    review = replace(POLICY, expired_document="REVIEW")
    assert decide(replace(GOOD, document_expired=True), review).decision is Decision.REVIEW_REQUIRED
    accept = replace(POLICY, expired_document="ACCEPT")
    assert decide(replace(GOOD, document_expired=True), accept).decision is Decision.APPROVED
    assert d(document_expired=None) is Decision.INDETERMINATE


def test_the_worst_finding_wins_and_all_reasons_are_kept() -> None:
    r = decide(
        replace(GOOD, passive_auth=PassiveAuth.SIGNATURE_FAIL, liveness=Liveness.FAIL), POLICY
    )
    assert r.decision is Decision.REJECTED
    assert "CHIP_INTEGRITY_FAILED" in r.reasons and "PRESENTATION_ATTACK_DETECTED" in r.reasons


def test_an_alternative_route_exists_only_when_the_signed_policy_names_it() -> None:
    base = replace(
        GOOD,
        nfc=NfcAccess.UNSUPPORTED,
        passive_auth=PassiveAuth.NOT_TESTED,
        chip_auth=ChipAuth.NOT_TESTED,
        reference_source="OPTICAL",
    )
    unknown = decide(replace(base, route="SELFMADE"), POLICY)
    assert unknown.decision is Decision.INDETERMINATE and unknown.reasons == (
        "ROUTE_NOT_ALLOWED_BY_POLICY",
    )
    one = decide(replace(base, route="PASSPORT_OPTICAL_REVIEW", reviewers=("a",)), POLICY)
    assert (
        one.decision is Decision.REVIEW_REQUIRED
        and "ALTERNATE_ROUTE_NEEDS_INDEPENDENT_REVIEWERS" in one.reasons
    )
    same_twice = decide(
        replace(base, route="PASSPORT_OPTICAL_REVIEW", reviewers=("a", "a")), POLICY
    )
    assert same_twice.decision is Decision.REVIEW_REQUIRED
    two = decide(replace(base, route="PASSPORT_OPTICAL_REVIEW", reviewers=("a", "b")), POLICY)
    assert two.decision is Decision.APPROVED
    review_only = replace(
        POLICY, alternates=(AlternateRoute("PASSPORT_OPTICAL_REVIEW", may_approve=False),)
    )
    assert (
        decide(
            replace(base, route="PASSPORT_OPTICAL_REVIEW", reviewers=("a", "b")), review_only
        ).decision
        is Decision.REVIEW_REQUIRED
    )


def test_a_lost_entitlement_or_unreleased_method_disables_the_route_and_abandonment_is_its_own_outcome() -> (
    None
):
    assert d(route_enabled=False) is Decision.INDETERMINATE
    assert d(method_released=False) is Decision.INDETERMINATE
    assert d(abandoned=True) is Decision.ABANDONED
