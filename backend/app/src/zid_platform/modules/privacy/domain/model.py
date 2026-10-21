"""Privacy rules (ZID-PRIV-001; DEC-F3). Pure: no I/O.

- Jurisdiction packs are counsel-approved, versioned configuration (`backend/config/
  jurisdiction-packs`). Engineers never infer the legal answer: a field counsel has not set
  (`COUNSEL`) stays unset here too - deadlines are then reported COUNSEL_PENDING, never guessed.
- The processing check (PRIV-01/02/05/18): an ACTIVE activity, a purpose it declares, a data
  category both allow, consent only where the activity's basis is consent, and no restriction or
  objection for the subject.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
COUNSEL = "COUNSEL"


class PrivacyError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


class DataCategory(StrEnum):
    D0 = "D0"  # non-personal / public technical
    D1 = "D1"  # standard personal data
    D2 = "D2"  # sensitive security / identity data
    D3 = "D3"  # high-sensitivity / special-category / biometric evidence
    D4 = "D4"  # evidence / legal protected


class LawfulBasis(StrEnum):
    CONSENT = "CONSENT"
    CONTRACT = "CONTRACT"
    LEGAL_OBLIGATION = "LEGAL_OBLIGATION"
    LEGITIMATE_INTERESTS = "LEGITIMATE_INTERESTS"
    PUBLIC_TASK = "PUBLIC_TASK"
    VITAL_INTERESTS = "VITAL_INTERESTS"
    US_STATE_PERMISSION = "US_STATE_PERMISSION"


class ZoikoRole(StrEnum):
    CONTROLLER = "CONTROLLER"
    PROCESSOR = "PROCESSOR"
    JOINT_CONTROLLER = "JOINT_CONTROLLER"
    INDEPENDENT_CONTROLLER = "INDEPENDENT_CONTROLLER"


class ConsentStatus(StrEnum):
    ACTIVE = "ACTIVE"
    WITHDRAWN = "WITHDRAWN"
    EXPIRED = "EXPIRED"
    SUPERSEDED = "SUPERSEDED"
    INVALIDATED = "INVALIDATED"


class RightsType(StrEnum):
    ACCESS = "ACCESS"
    RECTIFICATION = "RECTIFICATION"
    ERASURE = "ERASURE"
    RESTRICTION = "RESTRICTION"
    OBJECTION = "OBJECTION"
    PORTABILITY = "PORTABILITY"
    OPT_OUT_SALE_SHARING = "OPT_OUT_SALE_SHARING"
    LIMIT_SENSITIVE_PI = "LIMIT_SENSITIVE_PI"
    ADMT_ACCESS = "ADMT_ACCESS"
    ADMT_OPT_OUT = "ADMT_OPT_OUT"
    COMPLAINT = "COMPLAINT"
    HUMAN_REVIEW = "HUMAN_REVIEW"


class RightsStatus(StrEnum):
    RECEIVED = "RECEIVED"
    VERIFYING = "VERIFYING"
    SCOPING = "SCOPING"
    FULFILLING = "FULFILLING"
    REVIEW = "REVIEW"
    COMPLETED = "COMPLETED"
    PARTIALLY_DENIED = "PARTIALLY_DENIED"
    DENIED = "DENIED"
    WITHDRAWN = "WITHDRAWN"


TERMINAL_RIGHTS = frozenset(
    {RightsStatus.COMPLETED, RightsStatus.PARTIALLY_DENIED, RightsStatus.DENIED, RightsStatus.WITHDRAWN}
)
# PRIV-001 §14, §21; PRIV-15: requests that must not be gated behind identity verification.
NO_VERIFICATION = frozenset(
    {
        RightsType.OPT_OUT_SALE_SHARING,
        RightsType.LIMIT_SENSITIVE_PI,
        RightsType.ADMT_OPT_OUT,
        RightsType.COMPLAINT,
    }
)
# Disclosure requests: a takeover with weak account evidence must never get them (PRIV-14).
DISCLOSING = frozenset({RightsType.ACCESS, RightsType.PORTABILITY, RightsType.ADMT_ACCESS})
PREFERENCE_FOR = {
    RightsType.OPT_OUT_SALE_SHARING: "OPT_OUT_SALE_SHARING",
    RightsType.LIMIT_SENSITIVE_PI: "LIMIT_SENSITIVE_PI",
    RightsType.ADMT_OPT_OUT: "ADMT_OPT_OUT",
}
PREFERENCE_TYPES = frozenset(
    {"OPT_OUT_SALE_SHARING", "LIMIT_SENSITIVE_PI", "DIRECT_MARKETING_OPT_OUT", "ADMT_OPT_OUT"}
)


class RetentionTrigger(StrEnum):
    CREATED_AT = "CREATED_AT"
    LAST_USED = "LAST_USED"
    VERIFIED_AT = "VERIFIED_AT"
    CONTRACT_END = "CONTRACT_END"
    ACCOUNT_CLOSE = "ACCOUNT_CLOSE"
    GRANT_EXPIRY = "GRANT_EXPIRY"
    EVENT_DATE = "EVENT_DATE"


class ExpiryAction(StrEnum):
    DELETE = "DELETE"
    CRYPTO_SHRED = "CRYPTO_SHRED"
    ANONYMIZE = "ANONYMIZE"
    ARCHIVE_RESTRICTED = "ARCHIVE_RESTRICTED"


def check_code(value: str, what: str) -> str:
    if not CODE.match(value):
        raise PrivacyError("PRIVACY_SCHEMA_INVALID", f"{what} must match [A-Z][A-Z0-9_]{{1,63}}")
    return value


# ------------------------------------------------------------------------------ jurisdiction packs


@dataclass(frozen=True, slots=True)
class JurisdictionPack:
    pack_id: str
    version: str
    approval_ref: str | None
    framework: str
    rights: frozenset[str]
    deadlines: Mapping[str, int] | None  # request type (or "default") -> days; None: COUNSEL
    transfer_mechanisms: tuple[str, ...]
    raw: Mapping[str, Any] = field(repr=False, default_factory=dict)

    @property
    def deployable(self) -> bool:
        """Counsel approved it and filled every field it must (DEC-F3 §3)."""
        return self.approval_ref is not None and self.deadlines is not None

    def deadline(self, request_type: RightsType, received_at: datetime) -> tuple[datetime | None, str]:
        """(deadline, source). Never guessed: an unset counsel field gives (None, COUNSEL_PENDING)."""
        if self.deadlines is None:
            return None, "COUNSEL_PENDING"
        days = self.deadlines.get(request_type.value, self.deadlines.get("DEFAULT"))
        if days is None:
            return None, "COUNSEL_PENDING"
        return received_at + timedelta(days=int(days)), f"{self.pack_id}@{self.version}"


# ---------------------------------------------------------------------------- processing check


@dataclass(frozen=True, slots=True)
class Activity:
    processing_activity_id: str
    tenant_id: str
    activity_code: str
    version: int
    purpose_codes: tuple[str, ...]
    data_categories: tuple[str, ...]
    lawful_basis: LawfulBasis
    recipient_classes: tuple[str, ...]
    jurisdiction_pack_id: str
    residency_profile: str | None
    status: str
    launch_flow: bool


@dataclass(frozen=True, slots=True)
class Purpose:
    purpose_id: str
    purpose_code: str
    purpose_version: int
    allowed_data_categories: tuple[str, ...]
    allowed_recipient_classes: tuple[str, ...]
    compatible_secondary_purposes: tuple[str, ...]
    prohibited_uses: tuple[str, ...]
    status: str


@dataclass(frozen=True, slots=True)
class ProcessingDecision:
    allowed: bool
    reason: str
    activity_code: str
    purpose_code: str
    lawful_basis: str | None = None
    consent_id: str | None = None


def check_processing(
    *,
    activity: Activity | None,
    purpose: Purpose | None,
    purpose_code: str,
    data_category: str,
    recipient_class: str | None,
    consent_id: str | None,
    restricted_purposes: Iterable[str | None],
    activity_code: str,
) -> ProcessingDecision:
    """Default deny; the first failing rule is the reason (PRIV-01/02/05/18, PRIV-28)."""

    def no(reason: str) -> ProcessingDecision:
        return ProcessingDecision(False, reason, activity_code, purpose_code)

    if activity is None or activity.status != "ACTIVE":
        return no("PRIVACY_ACTIVITY_NOT_ACTIVE")
    if purpose_code not in activity.purpose_codes:
        return no("PRIVACY_PURPOSE_NOT_DECLARED")  # a service cannot invent a purpose at runtime
    if purpose is None or purpose.status != "ACTIVE":
        return no("PRIVACY_PURPOSE_NOT_ACTIVE")
    if data_category not in activity.data_categories or data_category not in purpose.allowed_data_categories:
        return no("PRIVACY_DATA_CATEGORY_NOT_ALLOWED")
    if recipient_class is not None and (
        recipient_class not in activity.recipient_classes
        or (purpose.allowed_recipient_classes and recipient_class not in purpose.allowed_recipient_classes)
    ):
        return no("PRIVACY_RECIPIENT_NOT_ALLOWED")
    restricted = set(restricted_purposes)
    if None in restricted or purpose_code in restricted:
        return no("PRIVACY_PROCESSING_RESTRICTED")
    if activity.lawful_basis is LawfulBasis.CONSENT and consent_id is None:
        return no("PRIVACY_CONSENT_REQUIRED")
    return ProcessingDecision(
        True,
        "ALLOWED",
        activity_code,
        purpose_code,
        activity.lawful_basis.value,
        consent_id if activity.lawful_basis is LawfulBasis.CONSENT else None,
    )


def check_consent_basis(activity_basis: LawfulBasis) -> None:
    """PRIV-04 no false consent: consent is captured only where it is the actual basis."""
    if activity_basis is not LawfulBasis.CONSENT:
        raise PrivacyError(
            "PRIVACY_FALSE_CONSENT",
            f"the activity's basis is {activity_basis.value}; asking for consent would mislead",
        )


# ---------------------------------------------------------------------------------- retention


def due_at(trigger_at: datetime, retention_days: int) -> datetime:
    return trigger_at + timedelta(days=retention_days)


def retention_outcome(*, due: datetime | None, held: bool, now: datetime) -> str:
    """PRIV-001 §7.1: HELD while a hold covers it; DUE once due_at passes; otherwise RETAIN."""
    if held:
        return "HELD"
    if due is not None and due <= now:
        return "DUE"
    return "RETAIN"


# -------------------------------------------------------------------------------- rights requests


def initial_verification(
    request_type: RightsType, *, self_request: bool, authn_level: int, auth_age: timedelta | None
) -> tuple[str, int]:
    """PRIV-001 §14: proportional verification. A logged-in subject with a fresh strong session
    is verified by that session; opt-outs never need it; everything else waits for verification."""
    if request_type in NO_VERIFICATION:
        return "NOT_REQUIRED", 0
    if self_request and authn_level >= 2 and auth_age is not None and auth_age <= timedelta(minutes=15):
        return "VERIFIED", authn_level
    return "PENDING", 0


def require_disclosure_verification(request_type: RightsType, state: str, level: int) -> None:
    """PRIV-14: disclosure of personal data needs a VERIFIED request at AAL-Z2 or above."""
    if request_type in DISCLOSING and (state != "VERIFIED" or level < 2):
        raise PrivacyError(
            "PRIVACY_VERIFICATION_REQUIRED", "verify the requester before disclosing personal data"
        )


def summarize(items: Sequence[Mapping[str, Any]]) -> RightsStatus:
    """Request status from its fulfilment items: never COMPLETED while a stage is pending
    (PRIV-12: a processor stage cannot be silently marked complete)."""
    states = {i["state"] for i in items}
    if states & {"PENDING", "PENDING_PROCESSOR_ACK", "FAILED"}:
        return RightsStatus.FULFILLING
    if "RETAINED_RESTRICTED" in states or "DENIED" in states:
        return RightsStatus.PARTIALLY_DENIED
    return RightsStatus.COMPLETED


__all__ = [
    "COUNSEL",
    "DISCLOSING",
    "NO_VERIFICATION",
    "PREFERENCE_FOR",
    "PREFERENCE_TYPES",
    "TERMINAL_RIGHTS",
    "Activity",
    "ConsentStatus",
    "DataCategory",
    "ExpiryAction",
    "JurisdictionPack",
    "LawfulBasis",
    "PrivacyError",
    "ProcessingDecision",
    "Purpose",
    "RetentionTrigger",
    "RightsStatus",
    "RightsType",
    "ZoikoRole",
    "check_code",
    "check_consent_basis",
    "check_processing",
    "due_at",
    "initial_verification",
    "require_disclosure_verification",
    "retention_outcome",
    "summarize",
]
