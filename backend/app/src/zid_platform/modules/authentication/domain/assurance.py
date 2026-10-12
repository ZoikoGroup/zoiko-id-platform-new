"""Authentication assurance AAL-Z0..Z3 (ZID-AUTH-001 §2, §15; DEC-E1 §3). Pure domain, no I/O.

- Assurance comes only from an approved mapping profile over `acr`/`amr`; an IdP cannot assert
  its way to a higher level than the profile grants, and unknown values map to Z0.
- SMS/email OTP and passwords are never phishing-resistant, whatever else is claimed (AUTH §28).
- AAL-Z3 is never stored or mapped: it is AAL-Z2 *plus freshness*, computed at decision time.
- Authentication is not authority: nothing here grants anything (AUTHZ-10).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import IntEnum


class AAL(IntEnum):
    Z0 = 0  # unverified / anonymous
    Z1 = 1  # basic authenticated
    Z2 = 2  # phishing-resistant
    Z3 = 3  # Z2 + recent step-up (never mapped, only derived)


# RFC 8176 method references. hwk/swk = proof of possession of a key (WebAuthn, smart card).
PHISHING_RESISTANT_AMR = frozenset({"hwk", "swk", "pop"})
FIRST_FACTOR_AMR = frozenset(
    {"pwd", "otp", "sms", "tel", "mfa", "pin", "user", "face", "fpt", "kba"}
)
WEAK_ONLY_AMR = frozenset({"sms", "tel", "kba", "pwd", "otp"})  # never enough for Z2 on their own
Z3_FRESHNESS = timedelta(minutes=5)
REQUIRED_BY_RISK = {0: AAL.Z0, 1: AAL.Z1, 2: AAL.Z2, 3: AAL.Z3}


@dataclass(frozen=True, slots=True)
class MappingProfile:
    """Approved acr/amr -> AAL mapping for one issuer (native or a federated IdP)."""

    profile_id: str
    issuer: str
    acr_levels: dict[str, AAL] = field(
        default_factory=dict
    )  # acr value -> level it may mean (<= Z2)
    trust_amr: bool = True  # whether this issuer's amr values are trusted at all

    def __post_init__(self) -> None:
        if any(v > AAL.Z2 for v in self.acr_levels.values()):
            raise ValueError("a mapping profile can grant at most AAL-Z2; Z3 needs fresh step-up")


def map_assurance(
    profile: MappingProfile, *, issuer: str, acr: str | None, amr: list[str] | None
) -> AAL:
    if issuer != profile.issuer:
        return AAL.Z0  # a profile never applies to another issuer's assertions
    methods = frozenset(amr or ()) if profile.trust_amr else frozenset()
    if methods & PHISHING_RESISTANT_AMR:
        from_amr = AAL.Z2
    elif methods & FIRST_FACTOR_AMR:
        from_amr = AAL.Z1
    else:
        from_amr = AAL.Z0
    level = max(from_amr, profile.acr_levels.get(acr or "", AAL.Z0))
    if level >= AAL.Z2 and methods and methods <= WEAK_ONLY_AMR:
        level = AAL.Z1  # the acr claims Z2 but every method actually used is weak
    return level


def effective_level(
    base: AAL, *, auth_time: datetime, now: datetime, freshness: timedelta = Z3_FRESHNESS
) -> AAL:
    """Z2 authenticated within the freshness window counts as Z3."""
    if base >= AAL.Z2 and timedelta(0) <= now - auth_time <= freshness:
        return AAL.Z3
    return min(base, AAL.Z2)


def step_up_needed(base: AAL, *, auth_time: datetime, now: datetime, risk_class: int) -> str | None:
    """None when the session's assurance is enough for this risk class, else the reason code."""
    required = REQUIRED_BY_RISK.get(risk_class, AAL.Z3)
    if effective_level(base, auth_time=auth_time, now=now) >= required:
        return None
    return "STEP_UP_REQUIRED" if base >= min(required, AAL.Z2) else "ASSURANCE_INSUFFICIENT"
