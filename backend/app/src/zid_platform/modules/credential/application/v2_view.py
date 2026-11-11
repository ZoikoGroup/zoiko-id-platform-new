"""Document 02 v2.0 (ZID-INT-002) views over what the credential module already decides: pure, no I/O.

Section 06.1: a dimension result uses ONE of eight evidence statuses (VERIFIED, VERIFIED_LIMITED, NOT_VERIFIED, INDETERMINATE,
PENDING, REVOKED, EXPIRED, UNAVAILABLE). Engagement eligibility is a separate vocabulary (ELIGIBLE, NOT_ELIGIBLE, REVIEW_REQUIRED,
UNKNOWN). Neither is ever collapsed into one global PASS, and a source that could not be asked is never "FAILED".

The mapping only ever LOWERS or restates what the check decided; it cannot make a check look better than it was
(`test_v2_view.py` proves that for every decision).
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.credential.domain.claims import ClaimType


class Outcome(StrEnum):
    VERIFIED = "VERIFIED"
    VERIFIED_LIMITED = "VERIFIED_LIMITED"
    NOT_VERIFIED = "NOT_VERIFIED"
    INDETERMINATE = "INDETERMINATE"
    PENDING = "PENDING"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"
    UNAVAILABLE = "UNAVAILABLE"


class EngagementEligibility(StrEnum):
    ELIGIBLE = "ELIGIBLE"
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    UNKNOWN = "UNKNOWN"


# section 06 `credential_type` -> the claim class this platform checks
CREDENTIAL_TYPES: Mapping[str, ClaimType] = {
    "academic_degree": ClaimType.ACADEMIC_AWARD,
    "professional_license": ClaimType.PROFESSIONAL_LICENSE,
    "certification": ClaimType.INDUSTRY_CERTIFICATE,
    "employment": ClaimType.EMPLOYMENT_ASSERTION,
    "continuing_education": ClaimType.CPD_RECORD,
    "insurance": ClaimType.INSURANCE_ASSERTION,
}
# the claim field the issuer's name goes into (the v2 request keeps issuer and award claims apart)
_ISSUER_FIELD: Mapping[ClaimType, str] = {
    ClaimType.ACADEMIC_AWARD: "institution",
    ClaimType.INDUSTRY_CERTIFICATE: "issuer",
    ClaimType.INSURANCE_ASSERTION: "insurer",
    ClaimType.PROFESSIONAL_LICENSE: "regulator",
}
_DIMENSION: Mapping[ClaimType, str] = {
    ClaimType.ACADEMIC_AWARD: "degree",
    ClaimType.PROFESSIONAL_LICENSE: "professional_license",
    ClaimType.INDUSTRY_CERTIFICATE: "certification",
    ClaimType.EMPLOYMENT_ASSERTION: "employment",
    ClaimType.CPD_RECORD: "continuing_education",
    ClaimType.INSURANCE_ASSERTION: "insurance",
}


def claim_from_v2(
    claim_type: ClaimType, issuer_claim: Mapping[str, Any], credential_claim: Mapping[str, Any]
) -> dict[str, Any]:
    """Merge the issuer and the award/licence claim into the one claim the check takes. The caller's own field wins."""
    merged = dict(credential_claim)
    field = _ISSUER_FIELD.get(claim_type)
    name = issuer_claim.get("name")
    if field and name and field not in merged:
        merged[field] = name
    return merged


def outcome_of(contract: Mapping[str, Any]) -> tuple[Outcome, list[str]]:
    """The section 06.1 status of one check and its limitations. Never better than the check's own decision."""
    decision = str(contract.get("decision", ""))
    status = str(contract.get("credential_status", ""))
    limits: list[str] = []
    if decision == "VERIFIED":
        limits = [str(x) for x in contract.get("limitations") or ()]
        return (Outcome.VERIFIED_LIMITED if limits else Outcome.VERIFIED), limits
    if decision == "NOT_VERIFIED":
        if status == "REVOKED":
            return Outcome.REVOKED, limits
        if status == "EXPIRED":
            return Outcome.EXPIRED, limits
        if status == "SUSPENDED":
            return Outcome.NOT_VERIFIED, ["CREDENTIAL_SUSPENDED"]
        return Outcome.NOT_VERIFIED, limits
    if decision == "SOURCE_UNAVAILABLE":
        return Outcome.UNAVAILABLE, ["SOURCE_UNAVAILABLE"]
    # REVIEW, UNVERIFIABLE, INDETERMINATE: a person or more evidence decides; this is never a failure of the holder
    return Outcome.INDETERMINATE, limits


def dimension_result(
    contract: Mapping[str, Any], claim_type: ClaimType, *, check_id: str
) -> dict[str, Any]:
    """Section 06.1 normalized result for one check."""
    outcome, limitations = outcome_of(contract)
    pol = contract.get("policy_ref") or {}
    return {
        "dimension": _DIMENSION.get(claim_type, claim_type.value.lower()),
        "outcome": outcome.value,
        "evidence_tier": contract.get("assurance_tier"),
        "source_ref": contract.get("source_id") and f"src_{contract['source_id']}",
        "claim_ref": f"claim_{check_id}",
        "checked_at": contract.get("source_checked_at"),
        "valid_until": contract.get("expires_at"),
        "limitations": limitations,
        "human_review_required": contract.get("decision")
        in {"REVIEW", "INDETERMINATE", "UNVERIFIABLE"},
        "evidence_manifest_ref": f"manifest_{check_id}",
        "policy_pack_version": f"{pol.get('id', '')}/{pol.get('version', '')}".strip("/"),
    }


def credential_response(
    contract: Mapping[str, Any],
    claim_type: ClaimType,
    *,
    check_id: str,
    request_id: str,
    correlation_id: str,
) -> dict[str, Any]:
    """Section 02.1: every decision returns request_id, correlation_id, the pack id and version, a provenance manifest reference,
    checked_at, valid_until and the dimension-level outcome. The five separate facts stay as they are in `assertion`."""
    pol = contract.get("policy_ref") or {}
    dim = dimension_result(contract, claim_type, check_id=check_id)
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "credential_ref": f"cred_{check_id}",
        "policy_pack_id": pol.get("id"),
        "policy_pack_version": str(pol["version"]) if "version" in pol else None,
        "provenance_manifest_ref": dim["evidence_manifest_ref"],
        "checked_at": dim["checked_at"],
        "valid_until": dim["valid_until"],
        "dimensions": [dim],
        "assertion": dict(contract),
        **_retry_guidance(dim["outcome"], contract),
    }


def _retry_guidance(outcome: str, contract: Mapping[str, Any]) -> dict[str, Any]:
    """Section 14 names HTTP 424 `source_unavailable` for a required source that could not be asked. The check IS recorded (a signed
    result with a receipt), so the answer stays 201 with outcome UNAVAILABLE and carries the guidance 424 would have: it is retryable,
    why, and that nothing was decided about the person. Anything else has no retry guidance."""
    if outcome != Outcome.UNAVAILABLE.value:
        return {}
    fault = contract.get("source_fault") or {}
    return {
        "retry_guidance": {
            "retryable": True,
            "reason": "SOURCE_UNAVAILABLE",
            "fault": fault.get("fault"),
            "attempts": fault.get("attempts"),
            "note": "The source could not be asked. Nothing was decided about the person. Repeat the check later.",
        }
    }


# ------------------------------------------------------------------------------------------------ engagement


def engagement_eligibility(
    eligibility: str, engagement: str | None = None
) -> EngagementEligibility:
    """Map this platform's eligibility (and engagement state) to the section 10 vocabulary. Anything not positively READY and
    ELIGIBLE is not ELIGIBLE: missing records are REVIEW_REQUIRED or UNKNOWN, never an approval."""
    if engagement == "BLOCKED" or eligibility == "BLOCKED":
        return EngagementEligibility.NOT_ELIGIBLE
    if eligibility == "ELIGIBLE" and engagement in (None, "READY"):
        return EngagementEligibility.ELIGIBLE
    if eligibility == "NOT_EVALUATED":
        return EngagementEligibility.UNKNOWN
    return EngagementEligibility.REVIEW_REQUIRED


def eligibility_response(
    body: Mapping[str, Any], *, request_id: str, correlation_id: str, checked_at: datetime
) -> dict[str, Any]:
    """Wrap an eligibility or engagement decision in the section 02.1 envelope."""
    pol = body.get("policy_ref") or {}
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "eligibility_ref": f"elig_{body.get('decision_id')}",
        "policy_pack_id": pol.get("id"),
        "policy_pack_version": str(pol["version"]) if "version" in pol else None,
        "provenance_manifest_ref": f"manifest_{body.get('decision_id')}",
        "checked_at": checked_at.isoformat(),
        "valid_until": None,
        "engagement_eligibility": engagement_eligibility(
            str(body.get("eligibility", "NOT_EVALUATED")), body.get("engagement")
        ).value,
        "reason_codes": list(body.get("reason_codes", [])),
        "restrictions": list(body.get("restrictions", [])),
        "dimensions": body.get("dimensions", {}),
    }
