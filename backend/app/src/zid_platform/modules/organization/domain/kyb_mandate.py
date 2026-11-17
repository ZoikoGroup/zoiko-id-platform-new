"""A representative's mandate to act for a company: what may be recorded and who may record it (ZID-DTV-WF-02 K05; acceptance T15, T16, T22). Pure domain, no I/O.

A company Owner, a platform administrator or a support agent is never the legal representative by that fact, and a mandate is not self-declared:

- the person who records a mandate is never the representative it names (an Owner cannot certify their own authority);
- it names the acts it covers, from the binding acts only; an act no mandate lists is not allowed;
- an act that moves money, transfers ownership of the account or passes authority on always needs a second signatory, whatever the person recording it chose;
- it has a start and an end that make sense, and the evidence it rests on (a board minute, a power of attorney, an officer entry): a mandate with no evidence reference is refused;
- the second signatory is a different person from the representative and from the person who recorded it;
- revoking it is a new fact with a reason; the mandate and its history stay.

Whether the representative is who they say is the identity check's job (a current verified assertion for exactly that person), read at the time of the act, never here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from zid_platform.modules.organization.domain.kyb_grade import BINDING_ACTIONS

ALWAYS_SECOND_SIGNATORY = frozenset(
    {"AUTHORIZE_PAYMENT", "TRANSFER_ACCOUNT_OWNERSHIP", "DELEGATE_AUTHORITY"}
)
ROLES = frozenset({"DIRECTOR", "SECRETARY", "EMPLOYEE", "AGENT", "TRUSTEE", "PARTNER"})
SOURCES = frozenset(
    {"REGISTRY_OFFICER", "BOARD_MINUTE", "POWER_OF_ATTORNEY", "AUTHORIZED_MANDATE", "OTHER"}
)
MAX_TERM = timedelta(days=366 * 5)  # a mandate longer than this is renewed, not left to run


class MandateError(Exception):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True, slots=True)
class Clean:
    powers: tuple[str, ...]
    second_signatory_required: bool


def validate(
    *,
    person: str,
    recorder: str,
    role: str,
    source: str,
    powers: list[str],
    valid_from: datetime,
    valid_to: datetime | None,
    evidence_ref: str,
    second_signatory_required: bool,
    now: datetime,
) -> Clean:
    """Raises MandateError for anything that may not be recorded; otherwise the powers (de-duplicated, sorted) and whether a second signatory is required."""
    if person == recorder:
        raise MandateError(
            "MANDATE_SELF_RECORDED", "the representative cannot record their own mandate"
        )
    if role not in ROLES:
        raise MandateError("MANDATE_ROLE_INVALID", role)
    if source not in SOURCES:
        raise MandateError("MANDATE_SOURCE_INVALID", source)
    if not evidence_ref or not evidence_ref.strip() or len(evidence_ref) > 512:
        raise MandateError(
            "MANDATE_EVIDENCE_REQUIRED", "name the document or record the mandate rests on"
        )
    clean = tuple(sorted(set(powers)))
    if not clean:
        raise MandateError("MANDATE_POWERS_EMPTY", "a mandate names the acts it covers")
    unknown = [p for p in clean if p not in BINDING_ACTIONS]
    if unknown:
        raise MandateError("MANDATE_POWERS_INVALID", ", ".join(unknown))
    if valid_to is not None:
        if valid_to <= valid_from:
            raise MandateError("MANDATE_WINDOW_INVALID", "it must end after it starts")
        if valid_to - valid_from > MAX_TERM:
            raise MandateError("MANDATE_TERM_TOO_LONG", "renew it instead of leaving it to run")
        if valid_to <= now:
            raise MandateError("MANDATE_ALREADY_ENDED", "it has already ended")
    return Clean(clean, second_signatory_required or bool(set(clean) & ALWAYS_SECOND_SIGNATORY))


def cosign_problem(*, person: str, recorder: str, cosigner: str) -> str | None:
    if cosigner == person:
        return "COSIGNER_IS_REPRESENTATIVE"
    if cosigner == recorder:
        return "COSIGNER_IS_RECORDER"
    return None
