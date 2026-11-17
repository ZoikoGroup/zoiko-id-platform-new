"""WF01's assertion vocabulary and result facts, derived from what a verification session RECORDED (ZID-DTV-WF-01 sections 7, 14 and 15; CR-107). Pure domain.

This is a view over the existing decision, not a second state machine. Nothing here decides: `decide()` still decides, and this only says, in WF01's words,
which distinct things the recorded facts support, so a relying party and a reviewer never read "verified" as more than it is.

The rules, each with a test:
1. IDENTITY_VERIFIED_FOR_SCOPE is held only when the session is VERIFIED, every claim the pinned profile requires is verified from accepted evidence, and
   either the binding and liveness the profile demands are present or distinct reviewers approved it through the profile's review path (which is also
   recorded as DOCUMENT_REVIEWED). A session that is anything else holds it never.
2. DOCUMENT_CHIP_CRYPTO_VALID is held only when THIS platform passively authenticated the chip (`CHIP_PASSIVE_AUTH_ONLY`). A provider saying it read a chip is
   not that: its own check is not ours to restate as cryptographic proof.
3. PRESENCE_CONFIRMED and FACE_MATCH_CONFIRMED need a biometric binding; presence also needs the liveness result to be a pass. Neither follows from the other.
4. DOCUMENT_REVIEWED means a person reviewed and approved. It never implies DOCUMENT_VALIDATED, and a reviewer cannot make a missing claim appear.
5. The COMPANY_* assertions belong to the company verification (WF02) and are never derived here.
6. An indeterminate or pending check is reported as such. It is never `pass`, and `not_requested` is used only when the profile did not ask for the check.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from zid_platform.modules.verification.domain.verification import Profile, SessionStatus

IDENTITY_DOCUMENT_EVIDENCE = frozenset({"GOVERNMENT_ID", "DIGITAL_CREDENTIAL"})
CHIP_VERIFIED_CODE = "CHIP_PASSIVE_AUTH_ONLY"
CHIP_INDETERMINATE_CODES = frozenset({"SELF_RUN_CHIP_INDETERMINATE", "SELF_RUN_SCORE_INVALID"})
CHIP_FAILED_CODE = "SELF_RUN_CHIP_NOT_VERIFIED"
INDETERMINATE_OUTCOMES = frozenset(
    {"FRAUD_CHECK_UNAVAILABLE"}
)  # a decided FAILED that is really "could not check", never a pass


class Assertion(StrEnum):
    DOCUMENT_COLLECTED = "DOCUMENT_COLLECTED"
    DOCUMENT_REVIEWED = "DOCUMENT_REVIEWED"
    DOCUMENT_VALIDATED = "DOCUMENT_VALIDATED"
    DOCUMENT_CHIP_CRYPTO_VALID = "DOCUMENT_CHIP_CRYPTO_VALID"
    PRESENCE_CONFIRMED = "PRESENCE_CONFIRMED"
    FACE_MATCH_CONFIRMED = "FACE_MATCH_CONFIRMED"
    IDENTITY_VERIFIED_FOR_SCOPE = "IDENTITY_VERIFIED_FOR_SCOPE"


NOT_DERIVED_HERE = ("COMPANY_DOCS_REVIEWED", "COMPANY_REGISTRY_MATCHED")  # WF02


class CheckResult(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INDETERMINATE = "indeterminate"
    NOT_REQUESTED = "not_requested"


# the platform's SessionStatus in WF01's state names (section 15); INDETERMINATE is decided from the recorded reasons, below
_STATE = {
    SessionStatus.CREATED: "CREATED",
    SessionStatus.IN_PROGRESS: "CAPTURE_IN_PROGRESS",
    SessionStatus.PENDING_EXTERNAL: "PROVIDER_PENDING",
    SessionStatus.RESUBMISSION_REQUIRED: "RESUBMIT_REQUIRED",
    SessionStatus.MANUAL_REVIEW: "MANUAL_REVIEW",
    SessionStatus.VERIFIED: "VERIFIED_FOR_SCOPE",
    SessionStatus.FAILED: "REJECTED",
    SessionStatus.CANCELED: "CANCELED",
    SessionStatus.EXPIRED: "EXPIRED",
}


@dataclass(frozen=True, slots=True)
class Check:
    type: str
    result: CheckResult
    method: str | None = None

    def public(self) -> dict[str, Any]:
        return {"type": self.type, "result": self.result.value, "method": self.method}


@dataclass(frozen=True, slots=True)
class ProofingFacts:
    state: str
    assertions: tuple[Assertion, ...]
    checks: tuple[Check, ...]
    decision: str  # WF01 decision words: verified_for_scope | review_required | more_evidence | indeterminate | rejected | pending
    indeterminate: bool
    missing_method: str | None = None
    failure_class: str | None = None
    retriable: bool = False
    next_action: str | None = None

    def public(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "decision": self.decision,
            "assertions": [a.value for a in self.assertions],
            "checks": [c.public() for c in self.checks],
            "not_derived_here": list(NOT_DERIVED_HERE),
            "indeterminate": self.indeterminate,
        }


def _claims(basis: Mapping[str, Any]) -> Sequence[Mapping[str, Any]]:
    got = basis.get("claims") or ()
    return [c for c in got if isinstance(c, Mapping)]


def _codes(basis: Mapping[str, Any]) -> frozenset[str]:
    return frozenset(str(c) for c in (basis.get("reason_codes") or ()))


def derive(
    profile: Profile,
    basis: Mapping[str, Any] | None,
    *,
    status: SessionStatus,
    outcome: str | None,
    reviewed: bool = False,
    pending_codes: Sequence[str] = (),
) -> ProofingFacts:
    """The WF01 facts for one session. `basis` is the recorded normalized result (None before any result); `reviewed` is true when a person approved it;
    `pending_codes` are reason codes of a result that was not recorded as a basis (a session still waiting)."""
    b: Mapping[str, Any] = basis or {}
    codes = _codes(b) | frozenset(pending_codes)
    claims = _claims(b)
    verified_claims = [
        c
        for c in claims
        if c.get("verified") and set(c.get("evidence") or ()) & profile.accepted_evidence
    ]
    have = {str(c.get("claim")) for c in verified_claims}
    binding, liveness = b.get("binding"), b.get("liveness_passed")
    biometric = binding == "BIOMETRIC"
    chip_ok = CHIP_VERIFIED_CODE in codes and CHIP_FAILED_CODE not in codes
    chip_indeterminate = bool(codes & CHIP_INDETERMINATE_CODES)

    held: list[Assertion] = []
    if claims:
        held.append(Assertion.DOCUMENT_COLLECTED)
    if reviewed and status is SessionStatus.VERIFIED:
        held.append(Assertion.DOCUMENT_REVIEWED)
    if (
        any(
            set(c.get("evidence") or ()) & IDENTITY_DOCUMENT_EVIDENCE and c.get("verified")
            for c in claims
        )
        and b.get("status") == "COMPLETED"
    ):
        held.append(Assertion.DOCUMENT_VALIDATED)
    if chip_ok:
        held.append(Assertion.DOCUMENT_CHIP_CRYPTO_VALID)
    if biometric and liveness is True:
        held.append(Assertion.PRESENCE_CONFIRMED)
    if biometric:
        held.append(Assertion.FACE_MATCH_CONFIRMED)
    satisfied = (
        status is SessionStatus.VERIFIED
        and outcome == "VERIFIED"
        and all(c in have for c in profile.required_claims)
        and (
            reviewed  # a person approved it through the profile's own review path: recorded as DOCUMENT_REVIEWED, and the claims were still required
            or (
                (not profile.binding_required or binding in ("BIOMETRIC", "NON_BIOMETRIC"))
                and (not profile.liveness_required or not biometric or liveness is True)
            )
        )
    )
    if satisfied:
        held.append(Assertion.IDENTITY_VERIFIED_FOR_SCOPE)

    document = (
        CheckResult.PASS
        if Assertion.DOCUMENT_VALIDATED in held
        else CheckResult.FAIL
        if b.get("status") == "FAILED" or status is SessionStatus.FAILED and claims
        else CheckResult.INDETERMINATE
        if claims
        or status
        in (
            SessionStatus.PENDING_EXTERNAL,
            SessionStatus.MANUAL_REVIEW,
            SessionStatus.RESUBMISSION_REQUIRED,
        )
        else CheckResult.NOT_REQUESTED
    )
    if biometric:
        presence = CheckResult.PASS if liveness is True else CheckResult.FAIL
        face = CheckResult.PASS
    else:
        presence = (
            CheckResult.INDETERMINATE if profile.liveness_required else CheckResult.NOT_REQUESTED
        )
        face = (
            CheckResult.INDETERMINATE
            if profile.binding_required and binding not in ("NON_BIOMETRIC",)
            else CheckResult.NOT_REQUESTED
        )
    chip = (
        CheckResult.PASS
        if chip_ok
        else CheckResult.FAIL
        if CHIP_FAILED_CODE in codes
        else CheckResult.INDETERMINATE
        if chip_indeterminate
        else CheckResult.NOT_REQUESTED
    )
    checks = (
        Check("document_validity", document, "optical_or_provider"),
        Check("liveness_pad", presence, "engine" if biometric else None),
        Check("face_match", face, "engine" if biometric else None),
        Check(
            "chip_crypto",
            chip,
            "passive_authentication" if chip is not CheckResult.NOT_REQUESTED else None,
        ),
    )

    indeterminate = (
        chip_indeterminate
        or outcome in INDETERMINATE_OUTCOMES
        or status is SessionStatus.RESUBMISSION_REQUIRED
    )
    state = (
        "INDETERMINATE"
        if chip_indeterminate and status is SessionStatus.PENDING_EXTERNAL
        else _STATE[status]
    )
    if outcome in INDETERMINATE_OUTCOMES:
        state = "INDETERMINATE"
    if satisfied:
        decision = "verified_for_scope"
    elif status is SessionStatus.MANUAL_REVIEW:
        decision = "review_required"
    elif status is SessionStatus.RESUBMISSION_REQUIRED:
        decision = "more_evidence"
    elif indeterminate:
        decision = "indeterminate"
    elif status in (SessionStatus.FAILED, SessionStatus.CANCELED, SessionStatus.EXPIRED):
        decision = "rejected"
    else:
        decision = "pending"

    missing: str | None = None
    failure: str | None = None
    retriable = False
    action: str | None = None
    if chip_indeterminate:
        missing, failure, retriable, action = (
            "chip_trust",
            "TRUST_UNAVAILABLE",
            False,
            "USE_APPROVED_ALTERNATIVE",
        )
    elif outcome == "FRAUD_CHECK_UNAVAILABLE":
        missing, failure, retriable, action = (
            "fraud_check",
            "CHECK_UNAVAILABLE",
            True,
            "RETRY_LATER",
        )
    elif status is SessionStatus.RESUBMISSION_REQUIRED:
        missing, failure, retriable, action = (
            "document_capture",
            "RESUBMISSION_REQUIRED",
            True,
            "SEND_EVIDENCE_AGAIN",
        )
    return ProofingFacts(
        state, tuple(held), checks, decision, indeterminate, missing, failure, retriable, action
    )


__all__ = [
    "IDENTITY_DOCUMENT_EVIDENCE",
    "INDETERMINATE_OUTCOMES",
    "NOT_DERIVED_HERE",
    "Assertion",
    "Check",
    "CheckResult",
    "ProofingFacts",
    "derive",
]
