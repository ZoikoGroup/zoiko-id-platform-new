"""Identity proofing: the controls around the decision (ZID-DTV-WF-03 P0 rules, E05, E06, E12; CR-110). Pure domain, no I/O.

- A biometric step is OFF unless the market has a recorded lawful basis, a completed impact assessment, and the applicant has been given the notice and (where the basis is consent) has consented and not withdrawn.
- A self-hosted biometric engine is OFF unless an independent presentation-attack evaluation is recorded for it.
- Trust material (issuer roots, revocation lists) is accepted only from an authority the platform has allowlisted for that country, and a changed trust snapshot names the cases it touches.
- Retries have a ceiling per market and purpose; reaching it ends the attempt, it does not loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class BiometricBasis:
    lawful_basis: (
        str | None
    )  # CONSENT | CONTRACT | LEGAL_OBLIGATION | ... recorded by the owners per market
    impact_assessment_ref: str | None
    notice_version: str | None
    independent_pad_ref: (
        str | None
    )  # for a self-hosted engine; None when a certified provider carries the PAD


def biometrics_allowed(
    b: BiometricBasis,
    *,
    self_hosted: bool,
    notice_shown_version: str | None,
    consent_at: datetime | None,
    withdrawn_at: datetime | None,
) -> tuple[bool, tuple[str, ...]]:
    """(allowed, every reason it is not). A missing control is named, never silently defaulted open."""
    why: list[str] = []
    if not b.lawful_basis:
        why.append("NO_LAWFUL_BASIS_RECORDED")
    if not b.impact_assessment_ref:
        why.append("NO_IMPACT_ASSESSMENT")
    if not b.notice_version:
        why.append("NO_NOTICE_PUBLISHED")
    elif notice_shown_version != b.notice_version:
        why.append("CURRENT_NOTICE_NOT_SHOWN")
    if b.lawful_basis == "CONSENT":
        if consent_at is None:
            why.append("CONSENT_NOT_GIVEN")
        elif withdrawn_at is not None and withdrawn_at >= consent_at:
            why.append("CONSENT_WITHDRAWN")
    if self_hosted and not b.independent_pad_ref:
        why.append("NO_INDEPENDENT_PAD_EVALUATION")
    return (not why), tuple(why)


@dataclass(frozen=True, slots=True)
class TrustSource:
    country: str
    authority: str  # who publishes it, as the owners recorded
    kind: str  # ROOT_LIST | REVOCATION_LIST


def source_accepted(
    allowlist: tuple[TrustSource, ...], country: str, authority: str, kind: str
) -> tuple[bool, str]:
    """Material is imported only from an authority recorded for that country and kind; a bundle that merely claims to be one is refused."""
    for s in allowlist:
        if s.country == country and s.authority == authority and s.kind == kind:
            return True, "ACCEPTED"
    if any(s.country == country and s.kind == kind for s in allowlist):
        return False, "AUTHORITY_NOT_ALLOWLISTED"
    return False, "NO_AUTHORITY_RECORDED_FOR_COUNTRY"


def impacted_cases(
    cases: dict[str, tuple[str, str]], changed_country: str, changed_signers: frozenset[str]
) -> tuple[str, ...]:
    """Cases (id -> (country, signer id)) a trust snapshot change touches, sorted so a reviewer gets a stable list: every case of that country whose signer was added, removed or revoked."""
    return tuple(
        sorted(
            cid
            for cid, (country, signer) in cases.items()
            if country == changed_country and signer in changed_signers
        )
    )


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    market: str
    purpose: str
    max_nfc_attempts: int
    max_capture_attempts: int


def retry_left(p: RetryPolicy, *, kind: str, used: int) -> tuple[bool, str]:
    """Whether another attempt is allowed. A ceiling of zero or less is a configuration fault and allows none."""
    ceiling = p.max_nfc_attempts if kind == "NFC" else p.max_capture_attempts
    if ceiling <= 0:
        return False, "RETRY_CEILING_NOT_CONFIGURED"
    if used >= ceiling:
        return False, "RETRY_CEILING_REACHED"
    return True, "OK"
