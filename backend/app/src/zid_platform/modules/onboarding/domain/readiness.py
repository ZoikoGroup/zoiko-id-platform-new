"""Organization readiness and activation (ZID-UX-WF-013C screen 1; slice L-13, CR-049). Pure: no database, no clock of its own.

Readiness is derived from independent facts, never stored as an editable flag: four trust assertions (legal entity, person identity,
representative authority, screening) each keep their own state, and three required setup items (organizational controls accepted, every
privileged account secured with a passkey, region and policy confirmed) are checked by the server. Approver readiness is recommended and
required only before A1 and A2 changes can run; its absence never blocks ordinary use. A match in a register, a declaration or a payment
never makes any of these true. A provider outage, a held case, a stale check or a missing fact is never "verified".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

# an audit-only record (not an outbox event), named here so the event-catalogue guard does not read it as one
ACTIVATION_REFUSED = "onboarding.organization.activation_refused"


class Readiness(StrEnum):
    DRAFT = "DRAFT"
    LIMITED_SETUP = "LIMITED_SETUP"
    READY_FOR_ACTIVATION = "READY_FOR_ACTIVATION"
    ACTIVE = "ACTIVE"
    ACTIVE_LIMITED = (
        "ACTIVE_LIMITED"  # activated, but a fact that activation rested on no longer holds
    )
    RESTRICTED = "RESTRICTED"  # a trust check was declined or is held in review


# The plain words of WF-013C section 13. "Approved" is never used for a registry match.
VERIFIED, IN_REVIEW, REQUIRED, NEEDS_ACTION, RESTRICTED = (
    "Verified",
    "In review",
    "Required",
    "Needs action",
    "Restricted",
)
_WORDS = {
    "COMPLETED": VERIFIED,
    "PENDING": REQUIRED,
    "HELD": IN_REVIEW,
    "NEEDS_ACTION": NEEDS_ACTION,
    "STALE": NEEDS_ACTION,  # it was complete and has lapsed: it needs fresh evidence, and is never read as verified
    "DECLINED": RESTRICTED,
    "RESTRICTED": RESTRICTED,  # a review suspended it
}

TRUST_CHECKS = ("company", "person_assurance")  # CR-134: the authority and the screening are no longer required for activation
TRUST_TITLES = {
    "company": "Legal entity",
    "person_assurance": "Your identity",
}


@dataclass(frozen=True, slots=True)
class TrustFact:
    """One canonical assertion as the trust services returned it (read-only, never customer-edited)."""

    check: str
    state: str  # COMPLETED, PENDING, HELD, NEEDS_ACTION, STALE, RESTRICTED or DECLINED
    expires_at: datetime | None = None
    started: bool = False  # the person has begun this check

    @property
    def word(self) -> str:
        if self.state == "PENDING" and self.started:
            return IN_REVIEW  # begun and waiting on a source: in review, not "Required" any more
        return _WORDS.get(self.state, REQUIRED)  # an unknown state is never "Verified"


@dataclass(frozen=True, slots=True)
class Setup:
    """The three required items and the recommended one, as the server found them."""

    controls_accepted: bool
    privileged_accounts_secured: bool
    region_policy_confirmed: bool
    independent_approvers: int  # distinct eligible people other than the requester today
    privileged_without_passkey: int = 0


@dataclass(frozen=True, slots=True)
class Item:
    key: str
    title: str
    word: str
    required: bool
    done: bool
    reason: str | None = (
        None  # why a capability stays restricted (the next action, never a shortcut)
    )
    next_action: str | None = None


@dataclass(frozen=True, slots=True)
class Projection:
    state: Readiness
    trust: tuple[Item, ...]
    required: tuple[Item, ...]
    approvers: Item
    can_activate: bool
    blockers: tuple[str, ...]
    solo: bool
    restrictions: tuple[str, ...] = field(default_factory=tuple)


def project(
    trust: list[TrustFact],
    setup: Setup,
    *,
    claim_started: bool,
    activated: bool,
    any_acknowledgement: bool,
    now: datetime,
) -> Projection:
    """Derive the readiness state and the checklist. A trust fact that is missing, expired or in an unknown state is not verified; an expired
    one needs action. Nothing here is read from a client-supplied flag."""
    by = {t.check: t for t in trust}
    trust_items: list[Item] = []
    for key in TRUST_CHECKS:
        f = by.get(key, TrustFact(key, "PENDING", started=claim_started))
        state = f.state
        if state == "COMPLETED" and f.expires_at is not None and f.expires_at <= now:
            state = "NEEDS_ACTION"  # a stale check is not a verified one
        word = TrustFact(key, state, f.expires_at, f.started).word
        done = state == "COMPLETED"
        trust_items.append(
            Item(
                key,
                TRUST_TITLES[key],
                word,
                True,
                done,
                None if done else _trust_reason(key, word),
                None if done else _trust_next(key, word),
            )
        )
    required = (
        Item(
            "controls",
            "Accept the organizational controls",
            VERIFIED if setup.controls_accepted else REQUIRED,
            True,
            setup.controls_accepted,
            None
            if setup.controls_accepted
            else "Required before the organization can be activated.",
            None if setup.controls_accepted else "Read and accept the controls",
        ),
        Item(
            "privileged_account",
            "Secure every privileged account",
            VERIFIED if setup.privileged_accounts_secured else NEEDS_ACTION,
            True,
            setup.privileged_accounts_secured,
            None
            if setup.privileged_accounts_secured
            else f"{setup.privileged_without_passkey} privileged account(s) have no passkey yet.",
            None
            if setup.privileged_accounts_secured
            else "Add a passkey to each Owner and Administrator",
        ),
        Item(
            "region_policy",
            "Confirm the data region and policy",
            VERIFIED if setup.region_policy_confirmed else REQUIRED,
            True,
            setup.region_policy_confirmed,
            None
            if setup.region_policy_confirmed
            else "Your data region and the current policy version must be confirmed.",
            None if setup.region_policy_confirmed else "Review and confirm the region and policy",
        ),
    )
    solo = setup.independent_approvers == 0
    approvers = Item(
        "approvers",
        "Independent approvers",
        VERIFIED if setup.independent_approvers >= 2 else REQUIRED,
        False,  # recommended: required before A1 and A2 changes can run, never for ordinary (A0) use
        setup.independent_approvers >= 2,
        None
        if setup.independent_approvers >= 2
        else (
            "You can manage your organization on your own. Certain high-risk actions need independent approval."
            if solo
            else "A critical (A2) change needs two independent approvers."
        ),
        None
        if setup.independent_approvers >= 2
        else "Invite approvers, or appoint verified independent reviewers",
    )
    trust_ok = all(i.done for i in trust_items)
    required_ok = all(i.done for i in required)
    restricted = any(
        i.word == RESTRICTED for i in trust_items
    )  # only a declined check restricts; a held one is remediation, shown as such
    blockers = tuple(f"{i.title}: {i.reason}" for i in (*trust_items, *required) if not i.done)
    if restricted:
        state = Readiness.RESTRICTED
    elif activated:
        state = Readiness.ACTIVE if trust_ok and required_ok else Readiness.ACTIVE_LIMITED
    elif trust_ok and required_ok:
        state = Readiness.READY_FOR_ACTIVATION
    elif claim_started or any_acknowledgement:
        state = Readiness.LIMITED_SETUP
    else:
        state = Readiness.DRAFT
    return Projection(
        state,
        tuple(trust_items),
        required,
        approvers,
        can_activate=trust_ok and required_ok and not restricted and not activated,
        blockers=blockers,
        solo=solo,
        restrictions=()
        if state in (Readiness.ACTIVE, Readiness.READY_FOR_ACTIVATION)
        else ("PRIVILEGED_ACTIONS_LIMITED",),
    )


def _trust_reason(key: str, word: str) -> str:
    if word == RESTRICTED:
        return "This check was declined or is restricted. Activation is blocked."
    if word == IN_REVIEW:
        return "This check is being reviewed. It is never treated as verified while it is."
    if word == NEEDS_ACTION:
        return "This check needs more evidence or has expired."
    return "This check has not been completed. A register match or a payment does not complete it."


def _trust_next(key: str, word: str) -> str:
    if key == "person_assurance":
        return "Complete your identity check"
    if word == IN_REVIEW:
        return "Wait for the review, or add the evidence requested"
    return "Continue company verification"
