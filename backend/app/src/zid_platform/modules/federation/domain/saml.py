"""SAML 2.0 Web SSO profile rules (ZID-AUTH-001 §10; SAML 2.0 Profiles §4.1.4). Pure domain.

Applied to values extracted *only from the signature-verified element* (the application layer
never reads the unsigned envelope for anything but routing), so signature-wrapping cannot swap
in an unsigned assertion. Exact Destination/Recipient/Audience, InResponseTo for SP-initiated
flows, a strict validity window with small documented skew, and a stable subject: persistent
NameIDs only - transient ones change per login and email ones are not immutable (§10 NameID).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from zid_platform.modules.federation.domain.connection import FederationError

SAML_SKEW = timedelta(seconds=90)
SUCCESS = "urn:oasis:names:tc:SAML:2.0:status:Success"
BEARER = "urn:oasis:names:tc:SAML:2.0:cm:bearer"
PERSISTENT = "urn:oasis:names:tc:SAML:2.0:nameid-format:persistent"
UNSPECIFIED = "urn:oasis:names:tc:SAML:1.1:nameid-format:unspecified"
STABLE_NAMEID_FORMATS = frozenset({PERSISTENT, UNSPECIFIED})


@dataclass(frozen=True, slots=True)
class AssertionFacts:
    response_destination: str | None
    response_in_response_to: str | None
    status: str
    issuer: str
    assertion_id: str
    name_id: str
    name_id_format: str | None
    confirmation_method: str
    recipient: str | None
    confirmation_in_response_to: str | None
    confirmation_not_on_or_after: datetime | None
    not_before: datetime | None
    not_on_or_after: datetime | None
    audiences: tuple[str, ...]
    authn_instant: datetime | None
    authn_context: str | None


def check_assertion(
    f: AssertionFacts,
    *,
    issuer: str,
    acs_url: str,
    sp_entity_id: str,
    request_id: str | None,
    now: datetime,
) -> datetime:
    """Raises FederationError on any violation; returns when the assertion stops being valid."""
    if f.status != SUCCESS:
        raise FederationError("SAML_STATUS_NOT_SUCCESS")
    if f.response_destination is not None and f.response_destination != acs_url:
        raise FederationError("SAML_DESTINATION_MISMATCH")
    if f.issuer != issuer:
        raise FederationError("SAML_ISSUER_MISMATCH")
    if request_id is None:
        raise FederationError("SAML_UNSOLICITED")  # IdP-initiated SSO is disabled (§10)
    if f.response_in_response_to not in (None, request_id):
        raise FederationError("SAML_IN_RESPONSE_TO_MISMATCH")
    if f.confirmation_method != BEARER:
        raise FederationError("SAML_SUBJECT_CONFIRMATION_INVALID")
    if f.recipient != acs_url:
        raise FederationError("SAML_RECIPIENT_MISMATCH")
    if f.confirmation_in_response_to != request_id:
        raise FederationError("SAML_IN_RESPONSE_TO_MISMATCH")
    if f.confirmation_not_on_or_after is None or now >= f.confirmation_not_on_or_after + SAML_SKEW:
        raise FederationError("SAML_ASSERTION_EXPIRED")
    if f.not_before is not None and f.not_before > now + SAML_SKEW:
        raise FederationError("SAML_ASSERTION_NOT_YET_VALID")
    if f.not_on_or_after is not None and now >= f.not_on_or_after + SAML_SKEW:
        raise FederationError("SAML_ASSERTION_EXPIRED")
    if sp_entity_id not in f.audiences:
        raise FederationError("SAML_AUDIENCE_MISMATCH")
    if not f.name_id or len(f.name_id) > 255:
        raise FederationError("SAML_SUBJECT_INVALID")
    if f.name_id_format is not None and f.name_id_format not in STABLE_NAMEID_FORMATS:
        raise FederationError("SAML_NAMEID_NOT_STABLE")
    ends = [t for t in (f.not_on_or_after, f.confirmation_not_on_or_after) if t is not None]
    return min(ends)
