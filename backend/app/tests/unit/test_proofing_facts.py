"""WF01's assertion vocabulary and result facts, derived from recorded facts only (ZID-DTV-WF-01 sections 7, 14, 15; CR-107)."""

from __future__ import annotations

from typing import Any

from zid_platform.modules.verification.domain import proofing as pf
from zid_platform.modules.verification.domain.proofing import Assertion as A
from zid_platform.modules.verification.domain.proofing import CheckResult as R
from zid_platform.modules.verification.domain.verification import Profile
from zid_platform.modules.verification.domain.verification import SessionStatus as S


def profile(**over: Any) -> Profile:
    req = {
        "required_claims": ["legal_name", "birthdate"],
        "accepted_evidence": ["GOVERNMENT_ID", "BIOMETRIC_BINDING"],
        "binding_required": True,
        "liveness_required": True,
        "biometric_jurisdictions": ["GB"],
        "jurisdictions": ["GB"],
        **over,
    }
    return Profile.from_json("p1", "t1", "idv", 3, "IAL-Z2", req, "ACTIVE")


def claim(
    name: str, *, evidence=("GOVERNMENT_ID",), method="provider", verified=True
) -> dict[str, Any]:
    return {
        "claim": name,
        "verified": verified,
        "method": method,
        "evidence": list(evidence),
        "value_commitment": "x" * 64,
    }


def basis(
    *, binding="BIOMETRIC", liveness=True, codes=(), claims=None, status="COMPLETED"
) -> dict[str, Any]:
    return {
        "status": status,
        "binding": binding,
        "liveness_passed": liveness,
        "fraud_signals": [],
        "fraud_check": None,
        "reason_codes": list(codes),
        "evidence_commitment": "c" * 64,
        "claims": claims if claims is not None else [claim("legal_name"), claim("birthdate")],
    }


def facts(b, *, status=S.VERIFIED, outcome="VERIFIED", **kw) -> pf.ProofingFacts:
    return pf.derive(kw.pop("profile", profile()), b, status=status, outcome=outcome, **kw)


def check(f: pf.ProofingFacts, kind: str) -> R:
    return next(c.result for c in f.checks if c.type == kind)


def test_a_fully_evidenced_biometric_session_holds_the_distinct_assertions_but_never_a_chip_one() -> (
    None
):
    f = facts(basis())
    assert set(f.assertions) == {
        A.DOCUMENT_COLLECTED,
        A.DOCUMENT_VALIDATED,
        A.PRESENCE_CONFIRMED,
        A.FACE_MATCH_CONFIRMED,
        A.IDENTITY_VERIFIED_FOR_SCOPE,
    }
    assert (
        f.state == "VERIFIED_FOR_SCOPE"
        and f.decision == "verified_for_scope"
        and not f.indeterminate
    )
    assert (
        check(f, "chip_crypto") is R.NOT_REQUESTED
    )  # a provider's own chip read is not ours to restate as cryptographic proof


def test_chip_crypto_valid_needs_this_platforms_own_passive_authentication() -> None:
    chip_only = basis(
        binding=None,
        liveness=None,
        codes=["CHIP_PASSIVE_AUTH_ONLY"],
        claims=[claim("legal_name", method="chip"), claim("birthdate", method="chip")],
    )
    p = profile(binding_required=False, liveness_required=False)
    f = facts(chip_only, profile=p)
    assert A.DOCUMENT_CHIP_CRYPTO_VALID in f.assertions and check(f, "chip_crypto") is R.PASS
    assert (
        A.PRESENCE_CONFIRMED not in f.assertions and A.FACE_MATCH_CONFIRMED not in f.assertions
    )  # a chip alone never binds the holder
    assert check(f, "face_match") is R.NOT_REQUESTED
    # the same chip when the profile needs binding: not verified for scope, and the missing binding is an indeterminate check
    needs = facts(chip_only, status=S.FAILED, outcome="ASSURANCE_NOT_MET")
    assert (
        A.IDENTITY_VERIFIED_FOR_SCOPE not in needs.assertions
        and check(needs, "face_match") is R.INDETERMINATE
    )
    assert A.DOCUMENT_CHIP_CRYPTO_VALID in needs.assertions


def test_a_failed_liveness_keeps_the_face_match_and_loses_presence_and_the_scope() -> None:
    f = facts(basis(liveness=False), status=S.MANUAL_REVIEW, outcome="FRAUD_SUSPECTED")
    assert A.FACE_MATCH_CONFIRMED in f.assertions and A.PRESENCE_CONFIRMED not in f.assertions
    assert A.IDENTITY_VERIFIED_FOR_SCOPE not in f.assertions
    assert check(f, "liveness_pad") is R.FAIL and f.decision == "review_required"


def test_an_unjudgeable_chip_is_indeterminate_and_never_a_pass() -> None:
    f = pf.derive(
        profile(),
        None,
        status=S.PENDING_EXTERNAL,
        outcome="PENDING_EXTERNAL",
        pending_codes=["SELF_RUN_CHIP_INDETERMINATE", "NO_CSCA"],
    )
    assert f.state == "INDETERMINATE" and f.decision == "indeterminate" and f.indeterminate
    assert check(f, "chip_crypto") is R.INDETERMINATE
    assert not f.assertions  # nothing is held for a session nothing was proved in
    assert (f.missing_method, f.failure_class, f.retriable, f.next_action) == (
        "chip_trust",
        "TRUST_UNAVAILABLE",
        False,
        "USE_APPROVED_ALTERNATIVE",
    )


def test_an_ordinary_provider_wait_is_pending_not_indeterminate() -> None:
    f = pf.derive(profile(), None, status=S.PENDING_EXTERNAL, outcome="PENDING_EXTERNAL")
    assert f.state == "PROVIDER_PENDING" and f.decision == "pending" and not f.indeterminate


def test_a_check_that_could_not_be_made_is_indeterminate_and_retriable() -> None:
    f = facts(basis(), status=S.FAILED, outcome="FRAUD_CHECK_UNAVAILABLE")
    assert f.state == "INDETERMINATE" and f.decision == "indeterminate" and f.retriable
    assert A.IDENTITY_VERIFIED_FOR_SCOPE not in f.assertions
    assert f.next_action == "RETRY_LATER"


def test_a_resubmission_request_is_more_evidence() -> None:
    f = pf.derive(profile(), None, status=S.RESUBMISSION_REQUIRED, outcome="RESUBMISSION_REQUIRED")
    assert (
        f.state == "RESUBMIT_REQUIRED"
        and f.decision == "more_evidence"
        and f.retriable
        and f.indeterminate
    )


def test_a_reviewer_approval_is_recorded_as_a_review_and_still_needs_the_required_claims() -> None:
    no_binding = basis(binding=None, liveness=None)
    reviewed = facts(no_binding, reviewed=True)
    assert (
        A.DOCUMENT_REVIEWED in reviewed.assertions
        and A.IDENTITY_VERIFIED_FOR_SCOPE in reviewed.assertions
    )
    unreviewed = facts(
        no_binding, reviewed=False
    )  # the same facts with no review: the profile's binding is not met
    assert (
        A.IDENTITY_VERIFIED_FOR_SCOPE not in unreviewed.assertions
        and A.DOCUMENT_REVIEWED not in unreviewed.assertions
    )
    missing = facts(
        basis(binding=None, liveness=None, claims=[claim("legal_name")]), reviewed=True
    )  # a reviewer cannot make a claim appear
    assert A.IDENTITY_VERIFIED_FOR_SCOPE not in missing.assertions
    assert (
        A.DOCUMENT_REVIEWED
        not in facts(
            no_binding, status=S.FAILED, outcome="MANUAL_REVIEW_REJECTED", reviewed=True
        ).assertions
    )


def test_review_never_implies_validation() -> None:
    f = facts(
        basis(
            binding=None,
            liveness=None,
            claims=[claim("legal_name", evidence=("IN_PERSON_ATTESTATION",))],
        ),
        reviewed=True,
    )
    assert A.DOCUMENT_VALIDATED not in f.assertions


def test_evidence_the_profile_does_not_accept_does_not_count() -> None:
    f = facts(
        basis(
            claims=[
                claim("legal_name", evidence=("ADDRESS_EVIDENCE",)),
                claim("birthdate", evidence=("ADDRESS_EVIDENCE",)),
            ]
        )
    )
    assert A.IDENTITY_VERIFIED_FOR_SCOPE not in f.assertions


def test_company_assertions_are_never_derived_here_and_every_state_has_a_name() -> None:
    assert not {a.value for a in A} & set(pf.NOT_DERIVED_HERE)
    assert all(s in pf._STATE for s in S)
    public = facts(basis()).public()
    assert public["not_derived_here"] == ["COMPANY_DOCS_REVIEWED", "COMPANY_REGISTRY_MATCHED"]
    assert "value_commitment" not in str(public) and "evidence_commitment" not in str(
        public
    )  # facts and words only


def test_a_session_with_no_result_has_no_assertions_and_nothing_passed() -> None:
    f = pf.derive(profile(), None, status=S.CREATED, outcome=None)
    assert f.state == "CREATED" and not f.assertions and f.decision == "pending"
    assert all(c.result is not R.PASS for c in f.checks)


# ------------------------------------------------------------------------------------------------ WF01-014: the attempt cap


def test_an_attempt_cap_refuses_at_the_cap_and_no_cap_means_no_limit() -> None:
    import pytest
    from zid_platform.modules.verification.domain.verification import (
        VerificationError,
        require_attempt_allowed,
        validate_requirements,
    )

    capped = profile(max_attempts=3)
    assert capped.max_attempts == 3
    for n in (0, 1, 2):
        require_attempt_allowed(capped, n)
    for n in (3, 4, 50):
        with pytest.raises(VerificationError) as e:
            require_attempt_allowed(capped, n)
        assert e.value.code == "VERIFICATION_ATTEMPTS_EXCEEDED"
    uncapped = profile()
    assert uncapped.max_attempts is None
    require_attempt_allowed(uncapped, 10_000)

    base = {
        "required_claims": ["legal_name"],
        "accepted_evidence": ["GOVERNMENT_ID"],
        "jurisdictions": ["GB"],
        "binding_required": True,
    }
    validate_requirements("IAL-Z2", {**base, "max_attempts": 1})
    validate_requirements("IAL-Z2", {**base, "max_attempts": 20})
    validate_requirements("IAL-Z2", {**base, "max_attempts": None})
    for bad in (0, -1, 21, True, "3", 2.5):
        with pytest.raises(VerificationError):
            validate_requirements("IAL-Z2", {**base, "max_attempts": bad})
