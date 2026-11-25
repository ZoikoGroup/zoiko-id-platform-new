"""The live check for a partner's customer (ZID-EXT-IDV-001; docs/specs/verify-api-live-check-product-definition.md; CR-164). Pure rules, no I/O.

What the live check is: a photograph of an identity document and a live photograph of a face, compared by Zoiko iD's own engine (the automatic face route). What it is NOT: it runs no liveness test and does not check
that the document is genuine. So the result carries the lowest grade the platform has, says exactly that, and lists the ONE check that ran. It never lists a document-authenticity or a liveness check.

A face that does not match is never an accusation: the engine cannot tell a different person from a poor photograph, so it is a retake and, after the allowed tries, `inconclusive`, never `not_verified`.
Partners are shown a short fixed list of reasons. Engine codes that would read as an accusation (a manipulated document, an attack on the camera) are folded into a neutral one, so a partner can never be told that
a customer is a fraudster by this check.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.verify_api.domain.model import PartnerError

LIVE_WORKFLOW = "wf_document_selfie"
LIVE_METHOD = "face_match"
CHECK_NAME = "face_match"
LIVE_ASSURANCE = {"scheme": "zoikoid-proofing", "level": "FACE_MATCH_NO_LIVENESS"}
LIVE_POLICY_NOTE = "A photograph of the document and a face match. No liveness test, and the document is not checked to be genuine."

# the only reasons a partner is ever shown for a live result
PARTNER_REASONS = (
    "FACE_MATCHES_DOCUMENT_PHOTO",
    "FACE_NOT_FOUND_OR_UNCLEAR",
    "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO",
    "DOCUMENT_EXPIRED",
    "DOCUMENT_UNREADABLE",
    "ATTEMPTS_USED_UP",
    "ENGINE_UNAVAILABLE",
)

# engine code -> the partner-facing reason it is shown as. Anything not here is not shown at all.
_FOLDED: dict[str, str] = {
    "FACE_MATCHES_DOCUMENT_PHOTO_NO_LIVENESS": "FACE_MATCHES_DOCUMENT_PHOTO",
    "FACE_NOT_FOUND_OR_UNCLEAR": "FACE_NOT_FOUND_OR_UNCLEAR",
    "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO": "FACE_DOES_NOT_MATCH_DOCUMENT_PHOTO",
    "FACE_MATCH_INCONCLUSIVE": "FACE_NOT_FOUND_OR_UNCLEAR",
    "DOCUMENT_EXPIRED": "DOCUMENT_EXPIRED",
    "DOCUMENT_EXPIRED_POLICY_REVIEW": "DOCUMENT_EXPIRED",
    "DOCUMENT_READING_NOT_RELIABLE": "DOCUMENT_UNREADABLE",
    "DOCUMENT_DATE_NOT_ESTABLISHED": "DOCUMENT_UNREADABLE",
    "MRZ_CHECK_DIGITS_FAILED": "DOCUMENT_UNREADABLE",
    "DOCUMENT_MANIPULATION_INDICATED": "DOCUMENT_UNREADABLE",  # never shown as what it says: that would be an accusation
    "CAPTURE_INJECTION_RISK": "DOCUMENT_UNREADABLE",
    "PRESENTATION_ATTACK_DETECTED": "FACE_NOT_FOUND_OR_UNCLEAR",
}


def partner_reasons(engine_reasons: Iterable[str]) -> list[str]:
    """The fixed reasons a partner sees, in a stable order, with nothing the engine said that is not on the list."""
    shown = {_FOLDED[r] for r in engine_reasons if r in _FOLDED}
    return [r for r in PARTNER_REASONS if r in shown]


class Result(StrEnum):
    VERIFIED = "verified"
    RETRY = "retry"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class LiveOutcome:
    result: Result
    reasons: tuple[str, ...]


def outcome_of(
    decision: str, engine_reasons: Iterable[str], *, attempts_left: int, exhausted: bool = False
) -> LiveOutcome:
    """The engine's decision as a result for the partner.

    APPROVED is verified. Anything the engine could not approve is a retake while tries remain and `inconclusive` when they are used up. A rejection, a reviewer referral and an abandoned case are never
    `not_verified`: this check cannot say that a person is not who they claim, only that it could not confirm it."""
    reasons = partner_reasons(engine_reasons)
    if decision == "APPROVED":
        return LiveOutcome(Result.VERIFIED, ("FACE_MATCHES_DOCUMENT_PHOTO",))
    if attempts_left > 0 and not exhausted:
        return LiveOutcome(Result.RETRY, tuple(reasons) or ("ENGINE_UNAVAILABLE",))
    final = [r for r in reasons if r != "FACE_MATCHES_DOCUMENT_PHOTO"] + ["ATTEMPTS_USED_UP"]
    return LiveOutcome(Result.INCONCLUSIVE, tuple(final))


def checks_for(result: Result) -> list[dict[str, str]]:
    """The one check that ran, and only that one."""
    return [
        {"method": CHECK_NAME, "result": "pass" if result is Result.VERIFIED else "inconclusive"}
    ]


def attempts_left(used: int, allowed: int) -> int:
    return max(0, allowed - used)


def check_live_eligible(
    *,
    country: str,
    workflow_id: str,
    grant_markets: Collection[str],
    grant_methods: Collection[str],
) -> None:
    """A production session needs a workflow the live check can serve, a check the company's grant names, and a country the grant names. Refused before the customer is shown anything."""
    if workflow_id != LIVE_WORKFLOW:
        raise PartnerError(
            "PRODUCT_NOT_ENTITLED",
            "Only the document and selfie workflow is available for live verification. Use wf_document_selfie.",
        )
    if LIVE_METHOD not in grant_methods:
        raise PartnerError(
            "PRODUCT_NOT_ENTITLED",
            "Your production approval does not include the face match check.",
        )
    if country.upper() not in {c.upper() for c in grant_markets}:
        raise PartnerError(
            "MARKET_NOT_ENABLED", f"Your production approval does not include {country.upper()}."
        )
