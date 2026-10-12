"""External identity keys for overlay tenants (DEC-A1 rule 2; INV-01; ZID-ARCH-002 §7).

An external subject is keyed by `issuer + sub` (SAML: IdP entityID + NameID). Email, UPN and
username are attributes, never keys, even when an IdP happens to put an email in `sub`.
Pure domain rules, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ExternalSubjectError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ExternalSubjectKey:
    issuer: str  # OIDC `iss` or SAML IdP entityID, exactly as configured for the connection
    subject: str  # OIDC `sub` or SAML NameID, compared exactly (no case folding)

    def __post_init__(self) -> None:
        if not self.issuer.strip() or not self.subject.strip():
            raise ExternalSubjectError("external subject key needs both issuer and subject")
        if self.issuer != self.issuer.strip() or self.subject != self.subject.strip():
            raise ExternalSubjectError("issuer and subject are compared exactly; strip at the edge")


class LinkBasis(StrEnum):
    """Why an external subject is being linked to an existing principal."""

    SAME_SUBJECT_KEY = "SAME_SUBJECT_KEY"  # the same issuer + sub was seen before
    ADMIN_APPROVED = "ADMIN_APPROVED"  # a tenant admin linked it explicitly
    VERIFIED_PROOF = "VERIFIED_PROOF"  # the user proved control of both accounts
    EMAIL_MATCH = "EMAIL_MATCH"  # only the email attribute matches


def auto_link_allowed(basis: LinkBasis, *, high_assurance: bool) -> bool:
    """Automatic linking on email equality is prohibited for high-assurance accounts.

    SAME_SUBJECT_KEY always links. EMAIL_MATCH may auto-link only low-assurance accounts (tenant
    policy may forbid even that). ADMIN_APPROVED and VERIFIED_PROOF are explicit, not automatic.
    """
    if basis is LinkBasis.SAME_SUBJECT_KEY:
        return True
    if basis is LinkBasis.EMAIL_MATCH:
        return not high_assurance
    return False
