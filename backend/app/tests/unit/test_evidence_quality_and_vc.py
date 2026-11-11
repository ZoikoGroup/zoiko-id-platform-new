"""GID-050 / 051 signed learning credentials and GID-059 evidence quality.

GLB-06  a copied valid badge that belongs to someone else: holder binding fails
GLB-07  a badge signed by an untrusted issuer: integrity passes, verified degree does not
GLB-23  a revoked certificate with a good signature is rejected
GLB-36  a valid EU credential for a rescinded degree: status updated, never a statutory license
GID-059 OCR alone cannot be presented as issuer confirmation
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from zid_platform.modules.credential.domain import evidence_quality as eq
from zid_platform.modules.credential.domain.claims import (
    Authenticity,
    ClaimType,
    CredentialStatus,
    EvidenceTier,
)

NOW = datetime(2026, 11, 11, 12, tzinfo=UTC)
K = eq.EvidenceKind


def test_appearance_evidence_never_rises_above_self_declared_however_much_there_is():
    appearance = [K.OCR_READ, K.DOCUMENT_APPEARANCE, K.WEBSITE_MATCH, K.IMAGE_METADATA]
    assert eq.tier_for(appearance) is EvidenceTier.SELF_DECLARED
    assert eq.tier_for([]) is EvidenceTier.SELF_DECLARED


def test_each_kind_of_evidence_has_its_own_ceiling_and_the_best_one_wins():
    assert eq.tier_for([K.SIGNATURE_VALID]) is EvidenceTier.DOCUMENT_INTEGRITY
    assert (
        eq.tier_for([K.OCR_READ, K.SIGNATURE_FROM_REGISTERED_ISSUER])
        is EvidenceTier.ISSUER_AUTHENTICATED
    )
    assert (
        eq.tier_for([K.OCR_READ, K.PRIMARY_SOURCE_RESPONSE])
        is EvidenceTier.PRIMARY_SOURCE_CONFIRMED
    )


def test_every_evidence_kind_is_ranked():
    assert set(eq.CEILING) == set(K)


def test_the_explanation_states_what_the_tier_proves_and_does_not():
    e = eq.explain(
        ClaimType.ACADEMIC_AWARD,
        EvidenceTier.SELF_DECLARED,
        [K.OCR_READ],
        decision="NOT_VERIFIED",
        checked_at=NOW,
    )
    assert e.confidence == "LOW" and e.source_tier == "SELF_DECLARED"
    text = " ".join(e.limitations)
    assert "not confirmation by the issuer" in text
    assert "attendance is not an earned degree" in text
    assert "ocr read" in e.methodology
    assert e.checked_at == NOW.isoformat()


def test_a_primary_source_explanation_has_no_ocr_warning_but_still_limits_itself():
    e = eq.explain(
        ClaimType.PROFESSIONAL_LICENSE,
        EvidenceTier.PRIMARY_SOURCE_CONFIRMED,
        [K.PRIMARY_SOURCE_RESPONSE],
        decision="VERIFIED",
        checked_at=None,
    )
    assert e.confidence == "HIGH" and e.checked_at is None
    assert not any("appearance" in x for x in e.limitations)
    assert any("good standing after the time shown" in x for x in e.limitations)


def test_every_tier_has_all_three_statements():
    for t in EvidenceTier:
        assert t in eq.PROVES and t in eq.DOES_NOT_PROVE and t in eq.CONFIDENCE


# --- signed credentials ------------------------------------------------------------------------------------------------


def _facts(**over) -> eq.VcFacts:
    base = {
        "kind": eq.Kind.OPEN_BADGE,
        "proof_valid": True,
        "issuer_id": "https://uni.example",
        "issuer_in_registry": True,
        "subject_id": "did:example:alice",
        "presenter_ref": "did:example:alice",
        "status": CredentialStatus.ACTIVE,
    }
    base.update(over)
    return eq.VcFacts(**base)


def test_a_good_badge_is_accepted_as_a_claim_but_never_reaches_primary_source():
    r = eq.evaluate_vc(_facts(), on=NOW)
    assert r.accepted_as_claim and r.reasons == ()
    assert r.tier is EvidenceTier.ISSUER_AUTHENTICATED  # provenance, not truth
    assert r.issuer_authenticity is Authenticity.CONFIRMED and r.holder_bound
    assert r.statutory_license is False


def test_glb_06_a_copied_valid_badge_fails_holder_binding():
    r = eq.evaluate_vc(_facts(presenter_ref="did:example:mallory"), on=NOW)
    assert not r.accepted_as_claim and "HOLDER_BINDING_FAILED" in r.reasons
    assert not eq.evaluate_vc(_facts(subject_id=None, presenter_ref=None), on=NOW).holder_bound


def test_glb_07_an_untrusted_issuer_passes_integrity_but_verifies_nothing():
    r = eq.evaluate_vc(_facts(issuer_in_registry=False), on=NOW)
    assert r.integrity and not r.accepted_as_claim
    assert r.tier is EvidenceTier.DOCUMENT_INTEGRITY
    assert r.issuer_authenticity is Authenticity.NOT_CONFIRMED
    assert "ISSUER_NOT_IN_REGISTRY" in r.reasons


@pytest.mark.parametrize("status", [CredentialStatus.REVOKED, CredentialStatus.SUSPENDED])
def test_glb_23_a_revoked_credential_with_a_good_signature_is_rejected(status):
    r = eq.evaluate_vc(_facts(status=status), on=NOW)
    assert r.integrity and not r.accepted_as_claim and f"CREDENTIAL_{status.value}" in r.reasons


def test_an_unreadable_status_endpoint_is_not_acceptance():
    r = eq.evaluate_vc(_facts(status=CredentialStatus.UNKNOWN), on=NOW)
    assert not r.accepted_as_claim and "STATUS_UNKNOWN" in r.reasons


def test_an_invalid_proof_gives_no_authenticity_and_the_lowest_tier():
    r = eq.evaluate_vc(_facts(proof_valid=False), on=NOW)
    assert not r.integrity and r.issuer_authenticity is Authenticity.UNKNOWN
    assert r.tier is EvidenceTier.SELF_DECLARED


def test_validity_window_is_enforced_at_both_ends():
    early = eq.evaluate_vc(_facts(valid_from=NOW + timedelta(days=1)), on=NOW)
    assert not early.accepted_as_claim and "NOT_YET_VALID" in early.reasons
    late = eq.evaluate_vc(_facts(valid_until=NOW), on=NOW)  # valid_until is exclusive
    assert not late.accepted_as_claim and late.credential_status is CredentialStatus.EXPIRED


def test_glb_36_a_valid_eu_credential_is_never_a_statutory_license():
    ok = eq.evaluate_vc(_facts(kind=eq.Kind.EDC, seal_valid=True), on=NOW)
    assert ok.accepted_as_claim and ok.statutory_license is False
    assert ok.tier is EvidenceTier.ISSUER_AUTHENTICATED
    rescinded = eq.evaluate_vc(
        _facts(kind=eq.Kind.EDC, seal_valid=True, status=CredentialStatus.REVOKED), on=NOW
    )
    assert not rescinded.accepted_as_claim
    bad_seal = eq.evaluate_vc(_facts(kind=eq.Kind.EDC, seal_valid=False), on=NOW)
    assert not bad_seal.integrity and "SEAL_INVALID" in bad_seal.reasons


def test_facts_are_read_from_a_credential_document_and_missing_fields_stay_none():
    doc = {
        "type": ["VerifiableCredential", "OpenBadgeCredential"],
        "issuer": {"id": "https://uni.example", "type": "Profile"},
        "credentialSubject": [{"id": "did:example:alice"}],
        "validFrom": "2026-01-01T00:00:00Z",
        "validUntil": "2030-01-01T00:00:00",
    }
    f = eq.facts_from_json(doc, eq.Kind.OPEN_BADGE)
    assert f["issuer_id"] == "https://uni.example" and f["subject_id"] == "did:example:alice"
    assert f["valid_from"] == datetime(2026, 1, 1, tzinfo=UTC)
    assert f["valid_until"].tzinfo is UTC
    empty = eq.facts_from_json(
        {"issuer": "https://x", "credentialSubject": "not-a-map"}, eq.Kind.VC
    )
    assert empty["issuer_id"] == "https://x" and empty["subject_id"] is None
    assert empty["valid_from"] is None
    assert eq.facts_from_json({"validFrom": "garbage"}, eq.Kind.VC)["valid_from"] is None
