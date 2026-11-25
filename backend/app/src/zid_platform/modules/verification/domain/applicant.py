"""What the person in a hosted verification may do and see (ZID-CP-WF01 sections 05, 10, S02, S08 to S11). Pure domain, no I/O.

- `eligible_methods`: the routes this market's signed policy allows, computed on the server from the policy, the device's stated capabilities and the market's
  biometric controls. Nothing unavailable is offered, and an alternative never claims a chip's assurance.
- `phase`: the one state of the person's journey (section 05), derived from the session and its proofing case, so the two can never disagree.
- `action`: the single safe code and sentence the person sees (section 10). It never names a fraud signal, a sanctions or PEP finding, a score or a model.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zid_platform.modules.verification.domain.idv_decision import (
    Decision,
    NfcAccess,
    Optical,
    PassiveAuth,
    Signals,
)
from zid_platform.modules.verification.domain.idv_policy import MarketRules


@dataclass(frozen=True, slots=True)
class Method:
    method: str  # CHIP or a signed alternate route id
    kind: str  # CHIP_AND_DOCUMENT | DOCUMENT_REVIEWED | DOCUMENT_AUTOMATIC | DOCUMENT_AND_FACE
    needs_nfc: bool
    needs_camera: bool
    biometric: bool
    handoff: bool  # this device cannot do it; another device can, through a one-use handoff

    def view(self) -> dict[str, object]:
        return {
            "method": self.method, "kind": self.kind, "needs_nfc": self.needs_nfc, "needs_camera": self.needs_camera,
            "biometric": self.biometric, "requires_handoff": self.handoff,
        }  # fmt: skip


def eligible_methods(
    rules: MarketRules, *, nfc: bool, camera: bool, face_route_on: bool
) -> tuple[list[Method], list[dict[str, str]]]:
    """(offered, withheld with the reason). A face route needs the deployment's acknowledgement and the market's lawful basis and impact assessment."""
    offered: list[Method] = []
    withheld: list[dict[str, str]] = []
    # the chip route reads the chip with a phone; without NFC here it is still offered through a handoff to a phone that has it
    offered.append(Method("CHIP", "CHIP_AND_DOCUMENT", True, True, False, not nfc))
    biometric_basis = bool(rules.basis.lawful_basis and rules.basis.impact_assessment_ref)
    for a in rules.policy.alternates:
        if a.face_match:
            if not (face_route_on and biometric_basis):
                withheld.append(
                    {"method": a.route_id, "reason": "BIOMETRICS_NOT_AUTHORIZED_IN_MARKET"}
                )
                continue
            m = Method(a.route_id, "DOCUMENT_AND_FACE", False, True, True, False)
        else:
            m = Method(
                a.route_id,
                "DOCUMENT_AUTOMATIC" if a.automatic else "DOCUMENT_REVIEWED",
                False,
                True,
                False,
                False,
            )
        if not camera:
            m = Method(m.method, m.kind, m.needs_nfc, m.needs_camera, m.biometric, True)
        offered.append(m)
    return offered, withheld


# ------------------------------------------------------------------------------------------------------------------------------ section 05


def phase(
    *, canceled: bool, expires_at: datetime | None, now: datetime, presented: bool, acknowledged: bool, case: Mapping[str, Any] | None,
    submitted: bool,
) -> str:  # fmt: skip
    """The journey's one state. A final outcome is shown only after the person submitted, and REVIEW is never shown as approval."""
    decision = str(case["decision"]) if case else None
    reasons = tuple(str(r) for r in case["reasons"]) if case else ()
    if canceled or decision == Decision.ABANDONED:
        return "CANCELED"
    if submitted and case is not None:
        if decision == Decision.APPROVED:
            return "FINAL_APPROVED"
        if decision == Decision.REJECTED:
            return "FINAL_DECLINED"
        if any(r.startswith("RECAPTURE_REQUESTED") for r in reasons):
            return "RESUBMISSION_REQUESTED"
        if decision == Decision.REVIEW_REQUIRED:
            return "MANUAL_REVIEW"
        return "INCONCLUSIVE"
    if expires_at is not None and expires_at <= now:
        return "EXPIRED"
    if case is not None:
        return "CAPTURING" if case.get("attempts") else "METHOD_SELECTED"
    if presented or acknowledged:
        return "NOTICE_PRESENTED"
    return "CREATED"


TERMINAL = frozenset({"CANCELED", "EXPIRED", "FINAL_APPROVED", "FINAL_DECLINED", "INCONCLUSIVE"})
TAKES_EVIDENCE = frozenset({"METHOD_SELECTED", "CAPTURING", "RESUBMISSION_REQUESTED"})


# ------------------------------------------------------------------------------------------------------------------------------ section 10

# code -> (category, what the person reads). Categories: USER_CORRECTABLE, PROVIDER_DOWN, UNSUPPORTED, INDETERMINATE, SUSPICIOUS.
MESSAGES: dict[str, tuple[str, str]] = {
    "CAPTURE_QUALITY": (
        "USER_CORRECTABLE",
        "We could not read the image clearly. Please retake it.",
    ),
    "UNSUPPORTED_DOCUMENT": ("UNSUPPORTED", "This document is not supported for this check."),
    "NFC_UNAVAILABLE": ("UNSUPPORTED", "Your device cannot complete this chip check."),
    "NFC_TRUST_INDETERMINATE": ("INDETERMINATE", "We could not confirm this chip automatically."),
    "DEVICE_RISK": ("SUSPICIOUS", "We need another verification method."),
    "PROVIDER_TEMPORARILY_UNAVAILABLE": (
        "PROVIDER_DOWN",
        "This verification step is unavailable right now.",
    ),
    "SESSION_EXPIRED": ("USER_CORRECTABLE", "This verification link has expired."),
    "LIMIT_REACHED": ("USER_CORRECTABLE", "We could not complete this step. Contact support."),
    "REVIEW_REQUIRED": ("INDETERMINATE", "Your verification needs additional review."),
    "INCONCLUSIVE": ("INDETERMINATE", "We could not verify your identity with this method."),
    "SUSPICIOUS_SIGNAL": ("SUSPICIOUS", "We need an additional verification step."),
}

# the platform's own refusal codes, as the person may be told them
FROM_ERROR: dict[str, str] = {
    "RETRY_CEILING_REACHED": "LIMIT_REACHED",
    "RETRY_CEILING_NOT_CONFIGURED": "LIMIT_REACHED",
    "FACE_ATTEMPTS_USED_UP": "LIMIT_REACHED",
    "ROUTE_HAS_NO_CHIP": "UNSUPPORTED_DOCUMENT",
    "ROUTE_NOT_ALLOWED_BY_POLICY": "UNSUPPORTED_DOCUMENT",
    "METHOD_NOT_ELIGIBLE": "UNSUPPORTED_DOCUMENT",
    "MARKET_POLICY_NOT_PUBLISHED": "UNSUPPORTED_DOCUMENT",
    "COUNTRY_CAPABILITY_UNAVAILABLE": "UNSUPPORTED_DOCUMENT",
    "MARKET_NOT_RELEASED": "UNSUPPORTED_DOCUMENT",
    "PROVIDER_NOT_ENTITLED": "UNSUPPORTED_DOCUMENT",
    "BIOMETRICS_OFF": "UNSUPPORTED_DOCUMENT",
    "SCAN_UNAVAILABLE": "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "IMAGING_UNAVAILABLE": "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "FACE_ENGINE_UNAVAILABLE": "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "PROVIDER_SCOPE_SUSPENDED": "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "IDV_POLICY_FILE_INVALID": "PROVIDER_TEMPORARILY_UNAVAILABLE",
    "SESSION_EXPIRED": "SESSION_EXPIRED",
}


def action(phase_now: str, signals: Signals | None, *, submitted: bool) -> str | None:
    """The one thing to tell the person now, or None when nothing is wrong."""
    if phase_now == "EXPIRED":
        return "SESSION_EXPIRED"
    if signals is None:
        return None
    if signals.injection_risk is True:
        return "SUSPICIOUS_SIGNAL"  # never which detector, never "fraud"
    if phase_now in ("MANUAL_REVIEW",):
        return "REVIEW_REQUIRED"
    if phase_now == "RESUBMISSION_REQUESTED":
        return "CAPTURE_QUALITY"
    if phase_now == "INCONCLUSIVE":
        if signals.passive_auth in (PassiveAuth.CERT_MISSING, PassiveAuth.CERT_UNTRUSTED):
            return "NFC_TRUST_INDETERMINATE"
        return "INCONCLUSIVE"
    if not submitted:
        if signals.nfc is NfcAccess.UNSUPPORTED:
            return "NFC_UNAVAILABLE"
        if signals.optical in (Optical.FAIL, Optical.UNKNOWN) and signals.route != "CHIP":
            return "CAPTURE_QUALITY"
    return None


def message(code: str | None) -> dict[str, str] | None:
    if code is None:
        return None
    category, text = MESSAGES[code]
    return {"code": code, "category": category, "message": text}


def reference(session_ref: str) -> str:
    """The person's own reference for support: short, not guessable into anything, never a case id."""
    return "ZID-" + session_ref.removeprefix("vs_").replace("-", "")[:10].upper()
