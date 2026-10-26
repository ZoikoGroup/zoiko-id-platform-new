"""Whether a message may be sent to this address right now (Communication Estate §02, §03, §05). Pure domain.

- An address that hard-bounced receives nothing, whatever the class: it cannot be delivered to.
- Only marketing is optional. A spam complaint, an unsubscribe or a missing consent stops marketing and
  never a security, service, privacy or incident message (§03 MESSAGE CLASSES).
- Flood protection (§03 EMAIL TRANSPORT) caps how often one address gets mail, per template and in total,
  so a bug or an attacker cannot turn the platform into a mail bomb against someone's inbox.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum

from zid_platform.modules.communication.domain.catalogue import MessageClass


class Suppression(StrEnum):
    BOUNCE_HARD = "BOUNCE_HARD"
    COMPLAINT = "COMPLAINT"
    UNSUBSCRIBED = "UNSUBSCRIBED"


class Outcome(StrEnum):
    SEND = "SEND"
    SUPPRESSED = "SUPPRESSED"
    THROTTLED = "THROTTLED"


@dataclass(frozen=True, slots=True)
class Limits:
    per_template_per_hour: int = 5
    per_recipient_per_hour: int = 20


@dataclass(frozen=True, slots=True)
class Decision:
    outcome: Outcome
    reason: str | None = None

    @property
    def send(self) -> bool:
        return self.outcome is Outcome.SEND


def decide(
    *,
    message_class: MessageClass,
    suppressions: Collection[Suppression],
    consent: str | None,
    template_sent_last_hour: int,
    recipient_sent_last_hour: int,
    limits: Limits | None = None,
) -> Decision:
    limits = limits or Limits()
    if Suppression.BOUNCE_HARD in suppressions:
        return Decision(Outcome.SUPPRESSED, "BOUNCE_HARD")
    if message_class is MessageClass.MARKETING:
        if Suppression.UNSUBSCRIBED in suppressions or consent == "WITHDRAWN":
            return Decision(Outcome.SUPPRESSED, "UNSUBSCRIBED")
        if Suppression.COMPLAINT in suppressions:
            return Decision(Outcome.SUPPRESSED, "COMPLAINT")
        if consent != "GRANTED":
            return Decision(Outcome.SUPPRESSED, "NO_CONSENT")
    if template_sent_last_hour >= limits.per_template_per_hour:
        return Decision(Outcome.THROTTLED, "TEMPLATE_LIMIT")
    if recipient_sent_last_hour >= limits.per_recipient_per_hour:
        return Decision(Outcome.THROTTLED, "RECIPIENT_LIMIT")
    return Decision(Outcome.SEND)
