"""Tenant deployment posture: overlay first (DEC-A1; ZID-ARCH-002 §6; ZID-OVERLAY-001 §1-2).

Pure domain rules, no I/O. The same domain APIs serve every mode; sovereign is a deployment
profile, not a code fork. Onboarding rollback and per-state duties are refined by
ZID-OVERLAY-001 in Phase G; this module fixes only what the architecture baseline fixes.
"""

from __future__ import annotations

from enum import StrEnum


class DeploymentMode(StrEnum):
    """§6.1 supported deployment modes."""

    ENTERPRISE_OVERLAY = "A"  # incumbent IdP stays authoritative for authentication and lifecycle
    NATIVE_IDENTITY = "B"  # Zoiko iD is the identity source
    RELYING_PARTY_ONLY = "C"  # wallet/EUDI/verification integrations only
    SOVEREIGN_DEDICATED = "D"  # dedicated cell, keys, operators, lockbox


class OverlaySplit(StrEnum):
    """How control is split with the incumbent estate (ZID-OVERLAY-001)."""

    NATIVE = "O0"
    AUTHORIZATION = "O1"  # incumbent IdP authenticates; Zoiko evaluates protected actions
    ASSURANCE = "O2"  # O1 + step-up, verification, credentials, authority, mandates
    FEDERATED_TRUST = "O3"  # many organizations/IdPs + Zoiko authority/evidence plane


class OnboardingState(StrEnum):
    DRAFT = "DRAFT"
    ORGANIZATION_VERIFYING = "ORGANIZATION_VERIFYING"
    CONFIGURING = "CONFIGURING"
    INTEGRATING = "INTEGRATING"
    SHADOW = "SHADOW"
    PILOT = "PILOT"
    ACTIVE = "ACTIVE"


class Enforcement(StrEnum):
    NONE = "NONE"  # no decisions are served yet
    EVALUATE_ONLY = "EVALUATE_ONLY"  # shadow: decisions are computed and recorded, never enforced
    ENFORCE = "ENFORCE"


class PostureError(ValueError):
    """A deployment posture or onboarding step the baseline does not allow."""


_ALLOWED_SPLITS: dict[DeploymentMode, frozenset[OverlaySplit | None]] = {
    DeploymentMode.ENTERPRISE_OVERLAY: frozenset(
        {OverlaySplit.AUTHORIZATION, OverlaySplit.ASSURANCE, OverlaySplit.FEDERATED_TRUST}
    ),
    DeploymentMode.NATIVE_IDENTITY: frozenset({OverlaySplit.NATIVE}),
    DeploymentMode.RELYING_PARTY_ONLY: frozenset({None}),
    # Sovereign identity is customer-selected or federated, so any split is possible.
    DeploymentMode.SOVEREIGN_DEDICATED: frozenset(OverlaySplit),
}

_ONBOARDING_ORDER = tuple(OnboardingState)


def default_split(mode: DeploymentMode) -> OverlaySplit | None:
    """O1/O2 is the default for overlay tenants; O0 is never a forced destination."""
    return {
        DeploymentMode.ENTERPRISE_OVERLAY: OverlaySplit.AUTHORIZATION,
        DeploymentMode.NATIVE_IDENTITY: OverlaySplit.NATIVE,
        DeploymentMode.RELYING_PARTY_ONLY: None,
        DeploymentMode.SOVEREIGN_DEDICATED: OverlaySplit.AUTHORIZATION,
    }[mode]


def validate_posture(mode: DeploymentMode, split: OverlaySplit | None) -> None:
    if split not in _ALLOWED_SPLITS[mode]:
        raise PostureError(f"split {split} is not allowed in deployment mode {mode.name}")


def requires_native_identity(mode: DeploymentMode, split: OverlaySplit | None) -> bool:
    """INV-10: only an explicit native choice moves authentication into Zoiko iD."""
    validate_posture(mode, split)
    return split is OverlaySplit.NATIVE


def next_onboarding_state(current: OnboardingState) -> OnboardingState:
    """Onboarding moves forward one step at a time; SHADOW always precedes enforcement."""
    i = _ONBOARDING_ORDER.index(current)
    if i == len(_ONBOARDING_ORDER) - 1:
        raise PostureError("onboarding is already ACTIVE")
    return _ONBOARDING_ORDER[i + 1]


def advance_onboarding(current: OnboardingState, target: OnboardingState) -> OnboardingState:
    if target is not next_onboarding_state(current):
        raise PostureError(f"cannot move onboarding from {current} to {target}")
    return target


def enforcement_for(state: OnboardingState) -> Enforcement:
    if state is OnboardingState.SHADOW:
        return Enforcement.EVALUATE_ONLY
    if state in (OnboardingState.PILOT, OnboardingState.ACTIVE):
        return Enforcement.ENFORCE
    return Enforcement.NONE
