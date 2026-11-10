"""Fraud, velocity and adverse-media signals (CR-068; ZID-GAP-001 WP-14). Pure domain, no I/O.

Rules that hold for every signal here:
- a signal only ADDS: a review requirement or a higher risk tier. Nothing here clears, approves or rejects anyone, and a result that
  says "nothing found" is not a clearance (it just adds nothing);
- a provider that fails, times out or answers something unreadable is a review, never a pass (fail safe);
- identifiers are hashed before they reach this code: a device, a contact or a network key is a digest, never a raw email, phone or
  address;
- every threshold below is a PROPOSED default that the owners ratify against real traffic. Nobody has validated them.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class RiskTier(StrEnum):
    LOW = "LOW"
    ELEVATED = "ELEVATED"
    HIGH = "HIGH"


_ORDER = {RiskTier.LOW: 0, RiskTier.ELEVATED: 1, RiskTier.HIGH: 2}


def raise_tier(current: RiskTier, proposed: RiskTier) -> RiskTier:
    """A tier only ever goes up here; lowering one is a person's decision elsewhere."""
    return proposed if _ORDER[proposed] > _ORDER[current] else current


# ---- hashed identifiers ----------------------------------------------------------------------------------------------


def hash_identifier(value: str, *, tenant_id: str, kind: str, pepper: bytes = b"") -> str:
    """The digest a signal store keeps instead of a raw identifier: HMAC-SHA256 over tenant, kind and the normalized value. The
    `pepper` is a deployment secret; without one the digest is still tenant-scoped, but a short value (a phone number) could be
    guessed by brute force, so production sets it. The same value in another tenant gives another digest."""
    normalized = value.strip().casefold()
    msg = f"{tenant_id}\x1f{kind}\x1f{normalized}".encode()
    return hmac.new(pepper or b"zid-screening-velocity", msg, hashlib.sha256).hexdigest()


# ---- velocity --------------------------------------------------------------------------------------------------------


class Dimension(StrEnum):
    DEVICE = "DEVICE"  # a device fingerprint
    CONTACT = "CONTACT"  # a hashed email or phone
    NETWORK = "NETWORK"  # a hashed network key (an address or its prefix)


class Action(StrEnum):
    SIGNUP = "SIGNUP"
    VERIFICATION = "VERIFICATION"
    CLAIM = "CLAIM"


@dataclass(frozen=True, slots=True)
class VelocityRule:
    dimension: Dimension
    action: Action
    window: timedelta
    threshold: int  # a breach is a count at or above this
    tier: RiskTier = RiskTier.ELEVATED

    @property
    def window_seconds(self) -> int:
        return int(self.window.total_seconds())

    @property
    def name(self) -> str:
        return f"VELOCITY_{self.dimension.value}_{self.action.value}_{self.window_seconds}S"


# PROPOSED defaults. Not validated against real traffic; the owners set them.
DEFAULT_RULES: tuple[VelocityRule, ...] = (
    VelocityRule(Dimension.DEVICE, Action.SIGNUP, timedelta(hours=24), 3),
    VelocityRule(Dimension.DEVICE, Action.VERIFICATION, timedelta(hours=1), 5),
    VelocityRule(Dimension.DEVICE, Action.CLAIM, timedelta(hours=24), 3),
    VelocityRule(Dimension.CONTACT, Action.SIGNUP, timedelta(hours=24), 2),
    VelocityRule(Dimension.CONTACT, Action.VERIFICATION, timedelta(hours=1), 5),
    VelocityRule(Dimension.CONTACT, Action.CLAIM, timedelta(hours=24), 3),
    VelocityRule(Dimension.NETWORK, Action.SIGNUP, timedelta(hours=1), 10),
    VelocityRule(Dimension.NETWORK, Action.VERIFICATION, timedelta(hours=1), 20),
    VelocityRule(Dimension.NETWORK, Action.CLAIM, timedelta(hours=24), 10),
)


def in_window(event_at: datetime, now: datetime, window: timedelta) -> bool:
    """An event counts when it is newer than `now - window` and not in the future: exactly `window` old has aged out."""
    return now - window < event_at <= now


@dataclass(frozen=True, slots=True)
class Breach:
    rule: str
    dimension: Dimension
    action: Action
    count: int
    threshold: int
    window_seconds: int
    tier: RiskTier

    def detail(self) -> dict[str, object]:
        return {
            "rule": self.rule,
            "dimension": self.dimension.value,
            "action": self.action.value,
            "count": self.count,
            "threshold": self.threshold,
            "window_seconds": self.window_seconds,
        }


def evaluate_velocity(
    counts: Mapping[tuple[Dimension, Action, int], int],
    rules: tuple[VelocityRule, ...] = DEFAULT_RULES,
) -> list[Breach]:
    """`counts` maps (dimension, action, window seconds) to the attempts inside that window, counting the current one."""
    out: list[Breach] = []
    for r in rules:
        n = counts.get((r.dimension, r.action, r.window_seconds), 0)
        if n >= r.threshold:
            out.append(
                Breach(r.name, r.dimension, r.action, n, r.threshold, r.window_seconds, r.tier)
            )
    return out


# ---- adverse media ---------------------------------------------------------------------------------------------------


class MediaOutcome(StrEnum):
    NO_HITS = "NO_HITS"
    HITS = "HITS"
    ERROR = "ERROR"
    TIMEOUT = "TIMEOUT"


@dataclass(frozen=True, slots=True)
class AdverseMediaResult:
    """What a provider answered, as recorded: counts, a severity and a rationale. No search text and no article text."""

    provider: str
    queried_at: datetime
    outcome: MediaOutcome
    hit_count: int = 0
    severity: RiskTier = RiskTier.ELEVATED  # the provider's own reading of the worst hit
    rationale: str = ""


@dataclass(frozen=True, slots=True)
class MediaEffect:
    review_required: bool
    tier: RiskTier
    reasons: tuple[str, ...]
    cleared: bool = False  # always False: adverse media never clears anyone


def apply_adverse_media(current_tier: RiskTier, result: AdverseMediaResult) -> MediaEffect:
    """NO_HITS adds nothing. HITS adds a review and raises the tier to the hit's severity (at least ELEVATED). ERROR and TIMEOUT add a
    review and raise to at least ELEVATED, because "not checked" must never read as "checked and clean". The tier never goes down."""
    if result.outcome is MediaOutcome.NO_HITS:
        return MediaEffect(False, current_tier, ("ADVERSE_MEDIA_NO_HITS",))
    elevated = raise_tier(current_tier, RiskTier.ELEVATED)
    if result.outcome is MediaOutcome.HITS:
        if result.hit_count < 1:  # a hit that says it has no hits is unreadable: fail safe
            return MediaEffect(True, elevated, ("ADVERSE_MEDIA_RESULT_INVALID",))
        return MediaEffect(True, raise_tier(elevated, result.severity), ("ADVERSE_MEDIA_HIT",))
    code = (
        "ADVERSE_MEDIA_TIMEOUT" if result.outcome is MediaOutcome.TIMEOUT else "ADVERSE_MEDIA_ERROR"
    )
    return MediaEffect(True, elevated, (code, "ADVERSE_MEDIA_NOT_CHECKED"))


# ---- review of a signal ----------------------------------------------------------------------------------------------

SIGNAL_OUTCOMES = frozenset({"CLEARED_BY_REVIEWER", "CONFIRMED_RISK", "ESCALATED"})


def check_signal_resolution(
    outcome: str, rationale: str, evidence_ref: str, status: str
) -> str | None:
    """The reason a resolution is refused, or None when it may go ahead. A person's ruling needs an outcome, a recorded reason of at
    least a sentence, and a reference to the evidence they relied on."""
    if status == "RESOLVED":
        return "SIGNAL_ALREADY_RESOLVED"
    if outcome not in SIGNAL_OUTCOMES:
        return "RULING_OUTCOME_INVALID"
    if len(rationale.strip()) < 20:
        return "RATIONALE_REQUIRED"
    if not evidence_ref.strip():
        return "EVIDENCE_REF_REQUIRED"
    return None
