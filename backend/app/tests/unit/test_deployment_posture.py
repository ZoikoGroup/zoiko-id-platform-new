"""DEC-A1 deployment posture and external identity keys (ZID-ARCH-002 §6-7; INV-01, INV-10)."""

import itertools

import pytest
from zid_platform.modules.principal.domain.external_subject import (
    ExternalSubjectError,
    ExternalSubjectKey,
    LinkBasis,
    auto_link_allowed,
)
from zid_platform.modules.tenant.domain.deployment import (
    DeploymentMode,
    Enforcement,
    OnboardingState,
    OverlaySplit,
    PostureError,
    advance_onboarding,
    default_split,
    enforcement_for,
    requires_native_identity,
    validate_posture,
)


@pytest.mark.parametrize("mode", list(DeploymentMode))
def test_every_mode_has_a_valid_default_split(mode: DeploymentMode) -> None:
    validate_posture(mode, default_split(mode))


def test_overlay_defaults_to_authorization_overlay() -> None:
    assert default_split(DeploymentMode.ENTERPRISE_OVERLAY) is OverlaySplit.AUTHORIZATION


def test_overlay_mode_never_requires_native_identity() -> None:
    for split in (OverlaySplit.AUTHORIZATION, OverlaySplit.ASSURANCE, OverlaySplit.FEDERATED_TRUST):
        assert not requires_native_identity(DeploymentMode.ENTERPRISE_OVERLAY, split)
    with pytest.raises(PostureError):
        validate_posture(DeploymentMode.ENTERPRISE_OVERLAY, OverlaySplit.NATIVE)


def test_native_and_relying_party_splits_are_fixed() -> None:
    assert requires_native_identity(DeploymentMode.NATIVE_IDENTITY, OverlaySplit.NATIVE)
    with pytest.raises(PostureError):
        validate_posture(DeploymentMode.NATIVE_IDENTITY, OverlaySplit.AUTHORIZATION)
    with pytest.raises(PostureError):
        validate_posture(DeploymentMode.RELYING_PARTY_ONLY, OverlaySplit.AUTHORIZATION)


def test_onboarding_moves_forward_one_step_and_shadows_before_enforcing() -> None:
    states = list(OnboardingState)
    for current, target in itertools.pairwise(states):
        assert advance_onboarding(current, target) is target
    with pytest.raises(PostureError):
        advance_onboarding(OnboardingState.INTEGRATING, OnboardingState.ACTIVE)
    with pytest.raises(PostureError):
        advance_onboarding(OnboardingState.ACTIVE, OnboardingState.ACTIVE)
    first_enforcing = next(s for s in states if enforcement_for(s) is Enforcement.ENFORCE)
    assert states[states.index(first_enforcing) - 1] is OnboardingState.SHADOW
    assert enforcement_for(OnboardingState.SHADOW) is Enforcement.EVALUATE_ONLY


def test_external_subject_key_is_issuer_plus_subject() -> None:
    a = ExternalSubjectKey("https://login.example.com/t1", "00u1abc")
    assert a == ExternalSubjectKey("https://login.example.com/t1", "00u1abc")
    assert a != ExternalSubjectKey("https://login.example.com/t2", "00u1abc")
    assert a != ExternalSubjectKey("https://login.example.com/t1", "00U1ABC")  # exact match
    for issuer, subject in (("", "x"), ("https://idp", ""), (" https://idp", "x")):
        with pytest.raises(ExternalSubjectError):
            ExternalSubjectKey(issuer, subject)


def test_email_match_never_auto_links_high_assurance_accounts() -> None:
    assert not auto_link_allowed(LinkBasis.EMAIL_MATCH, high_assurance=True)
    assert auto_link_allowed(LinkBasis.SAME_SUBJECT_KEY, high_assurance=True)
    for explicit in (LinkBasis.ADMIN_APPROVED, LinkBasis.VERIFIED_PROOF):
        assert not auto_link_allowed(explicit, high_assurance=False)
