"""Approval tiers, independence and quorum (ZID-ENG-DEC-010, 011, 012; slice L-10, CR-047). Pure: no database, no clock of its own.

A0 needs no vote. A1 needs the requester and one independent approver. A2 needs the requester and two independent approvers, never fewer.
"Independent" is about natural people, never accounts: the person link of slice L-7 says which accounts are one human, and the same person
cannot fill two slots, however many accounts, roles, devices or delegations they use. The requester, anyone who materially edited the
request and the person it benefits cannot vote, whatever their seniority. A vote belongs to one immutable request version: any material
change is a new case, so no vote is carried over. Unknown, stale, conflicting or unavailable facts are a refusal, never an approval.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class ApprovalError(Exception):
    """A refusal with a stable machine code. `persist` says the service wrote something that must survive the refusal (a case that
    expired or was invalidated, the denial's evidence), so the API commits first and then refuses."""

    def __init__(self, code: str, detail: str = "", *, persist: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail
        self.persist = persist


class Tier(StrEnum):
    A0 = "A0"  # routine: the authorized actor executes, no vote
    A1 = "A1"  # sensitive: requester + one independent approver
    A2 = "A2"  # critical: requester + two independent approvers
    FORBIDDEN = "FORBIDDEN"  # never, even with signatures
    CONTAINMENT = "CONTAINMENT"  # immediate defensive action, no case


class Scope(StrEnum):
    CUSTOMER = "CUSTOMER"
    PLATFORM = "PLATFORM"


class State(StrEnum):
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    PARTIALLY_APPROVED = "PARTIALLY_APPROVED"
    APPROVED_READY = "APPROVED_READY"
    EXECUTED = "EXECUTED"
    FAILED = "FAILED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    CANCELLED = "CANCELLED"


TERMINAL = frozenset(
    {State.EXECUTED, State.REJECTED, State.EXPIRED, State.INVALIDATED, State.CANCELLED}
)
OPEN = frozenset(
    {State.PENDING_APPROVAL, State.PARTIALLY_APPROVED, State.APPROVED_READY, State.FAILED}
)
_ENDINGS = {State.EXPIRED, State.INVALIDATED, State.CANCELLED}
TRANSITIONS: dict[State, frozenset[State]] = {
    State.DRAFT: frozenset({State.SUBMITTED}),
    State.SUBMITTED: frozenset({State.PENDING_APPROVAL}),
    State.PENDING_APPROVAL: frozenset(
        {State.PARTIALLY_APPROVED, State.APPROVED_READY, State.REJECTED, *_ENDINGS}
    ),
    # a counted approver who loses authority takes the case back down: the quorum is recomputed, never remembered
    State.PARTIALLY_APPROVED: frozenset(
        {State.PENDING_APPROVAL, State.APPROVED_READY, State.REJECTED, *_ENDINGS}
    ),
    State.APPROVED_READY: frozenset(
        {State.EXECUTED, State.FAILED, State.PENDING_APPROVAL, State.PARTIALLY_APPROVED, *_ENDINGS}
    ),
    # a failed run is not a success; the unchanged request may be run again until it expires
    State.FAILED: frozenset(
        {State.EXECUTED, State.FAILED, State.PENDING_APPROVAL, State.PARTIALLY_APPROVED, *_ENDINGS}
    ),
}
REQUIRED_VOTES = {Tier.A1: 1, Tier.A2: 2}

# What the decision records name as critical, so a registry that weakens one of them does not load (Decision 11 sections 02 and 03,
# T11 of Decision 11, and the "never" rows of Decision 12).
RECORD_A2 = frozenset(
    {
        "ownership-transfer-or-replacement",
        "removal-of-last-owner-or-control-chain",
        "privileged-owner-or-admin-recovery",
        "privileged-role-grant-or-elevation",
        "weaken-authentication-recovery-or-approval-policy",
        "credential-issuer-or-authority-root-change",
        "manual-reversal-of-high-assurance-trust-decision",
        "organization-wide-sensitive-export-or-impersonation",
        "powerful-agent-or-integration-authority",
        "reinstate-suspended-tenant-access",
        "root-super-admin-or-global-break-glass-grant",
        "manual-replacement-of-signing-keys-or-trust-anchors",
        "security-critical-or-trust-policy-rollback",
        "manual-restore-of-privileged-identity-authority-or-evidence-records",
        "change-of-production-data-home-region-or-restricted-transfer",
        "change-of-immutable-evidence-retention-or-audit-safeguards",
        "unrestricted-bulk-impersonation-or-restricted-data-access",
        "disable-or-re-enable-platform-security-enforcement-gates",
    }
)
RECORD_NEVER = frozenset(
    {
        "disable-core-audit",
        "disable-mandatory-multi-factor",
        "unlawful-cross-border-transfer",
        "illegal-deletion-of-regulated-records",
    }
)
VOTING_ROLES = frozenset({"OWNER", "APPROVER", "REVIEWER"})


# ------------------------------------------------------------------------------------------------ the registry


@dataclass(frozen=True, slots=True)
class ActionPolicy:
    action_id: str
    tier: Tier
    scope: Scope
    executor: str | None
    approvers: frozenset[str]
    request_matrix: str | None
    gate: str
    # why nothing can apply this action today (a platform rule, or no such capability); never "not built yet"
    no_operation: str | None = None

    @property
    def required_votes(self) -> int:
        return REQUIRED_VOTES.get(self.tier, 0)


@dataclass(frozen=True, slots=True)
class Registry:
    version: str
    digest: str  # hash of the file as loaded: the policy version a case is frozen to
    actions: Mapping[str, ActionPolicy]

    @property
    def policy_version(self) -> str:
        return f"{self.version}#{self.digest[:16]}"

    def classify(self, action_id: str) -> ActionPolicy:
        """An action nobody classified is refused, never treated as routine."""
        found = self.actions.get(action_id)
        if found is None:
            raise ApprovalError("APPROVAL_ACTION_UNKNOWN", "this action has no approval class")
        return found


def build_registry(doc: Mapping[str, Any], digest: str) -> Registry:
    """Validate the registry the way the record states it. A file that weakens a critical action, lets a forbidden action run, puts a
    containment action behind a queue or lets a platform action be voted by a customer role does not load."""
    actions: dict[str, ActionPolicy] = {}
    for a in doc.get("actions") or []:
        action_id = str(a["id"])
        if action_id in actions:
            raise ApprovalError("REGISTRY_INVALID", f"{action_id}: listed twice")
        try:
            tier, scope = Tier(str(a["tier"])), Scope(str(a["scope"]))
        except ValueError as e:
            raise ApprovalError("REGISTRY_INVALID", f"{action_id}: unknown tier or scope") from e
        approvers = frozenset(str(r) for r in a.get("approvers") or [])
        if not approvers <= VOTING_ROLES:
            raise ApprovalError("REGISTRY_INVALID", f"{action_id}: unknown approver role")
        executor = a.get("executor")
        if tier in (Tier.A1, Tier.A2) and not approvers:
            raise ApprovalError("REGISTRY_INVALID", f"{action_id}: a voted action has approvers")
        if tier in (Tier.FORBIDDEN, Tier.A0) and executor is not None:
            raise ApprovalError("REGISTRY_INVALID", f"{action_id}: {tier.value} has no executor")
        if tier is Tier.CONTAINMENT and (executor is None or approvers):
            raise ApprovalError(
                "REGISTRY_INVALID", f"{action_id}: containment runs at once, with no vote"
            )
        if scope is Scope.PLATFORM and "REVIEWER" in approvers:
            raise ApprovalError(
                "REGISTRY_INVALID",
                f"{action_id}: a customer reviewer cannot vote a platform action",
            )
        no_op = a.get("no_operation")
        if tier in (Tier.A1, Tier.A2) and executor is None and not no_op:
            raise ApprovalError(
                "REGISTRY_INVALID",
                f"{action_id}: a voted action has an executor or says why none can exist",
            )
        if executor is not None and no_op:
            raise ApprovalError(
                "REGISTRY_INVALID", f"{action_id}: an executor and no_operation disagree"
            )
        actions[action_id] = ActionPolicy(
            action_id,
            tier,
            scope,
            None if executor is None else str(executor),
            approvers,
            None if a.get("request_matrix") is None else str(a["request_matrix"]),
            str(a.get("gate", "")),
            None if not no_op else str(no_op),
        )
    for must in RECORD_A2:
        if actions.get(must) is None or actions[must].tier is not Tier.A2:
            raise ApprovalError("REGISTRY_INVALID", f"{must}: the record makes this action A2")
    for never in RECORD_NEVER:
        if actions.get(never) is None or actions[never].tier is not Tier.FORBIDDEN:
            raise ApprovalError("REGISTRY_INVALID", f"{never}: the record forbids this action")
    return Registry(str(doc.get("version", "")), digest, actions)


# ------------------------------------------------------------------------------------- the immutable request


def _canonical(value: Any, path: str = "$") -> Any:
    """JSON with one reading: sorted keys, no floats, no bytes. A payload that cannot be read one way is refused."""
    if value is None or isinstance(value, bool | str | int):
        return value
    if isinstance(value, float):
        raise ApprovalError("APPROVAL_PAYLOAD_INVALID", f"{path}: floats have no single reading")
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if not isinstance(k, str):
                raise ApprovalError("APPROVAL_PAYLOAD_INVALID", f"{path}: keys are text")
            out[k] = _canonical(v, f"{path}.{k}")
        return out
    if isinstance(value, list | tuple):
        return [_canonical(v, f"{path}[]") for v in value]
    raise ApprovalError("APPROVAL_PAYLOAD_INVALID", f"{path}: unsupported value")


def canonical_payload(payload: Mapping[str, Any]) -> str:
    return json.dumps(_canonical(dict(payload)), sort_keys=True, separators=(",", ":"))


def request_hash(
    *,
    tenant_id: str,
    action_id: str,
    target_id: str,
    payload_json: str,
    policy_version: str,
    region: str,
) -> str:
    """What every vote is bound to: the tenant, the action, the target, every material field, the policy it was classified under and
    the region it will run in. A change to any of them is a different hash, so a vote for the old one means nothing for the new."""
    body = json.dumps(
        {
            "tenant": tenant_id,
            "action": action_id,
            "target": target_id,
            "payload": payload_json,
            "policy": policy_version,
            "region": region,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------------------------------ people and eligibility


@dataclass(frozen=True, slots=True)
class Authority:
    """One live role that could let a person vote. `granted_by` and `approved_by` are people, so a role the requester handed out
    after the request began (privilege manufacture) can be recognised."""

    role: str
    effective_at: datetime
    granted_by_person: str | None = None
    approved_by_person: str | None = None


@dataclass(frozen=True, slots=True)
class Voter:
    principal_id: str
    person_id: str
    human: bool
    authorities: tuple[Authority, ...] = ()
    reviewer: bool = False  # an appointed, active, unexpired independent reviewer for this region
    reviewer_conflicts: frozenset[str] = (
        frozenset()
    )  # persons the reviewer declared a relationship with


@dataclass(frozen=True, slots=True)
class CaseFacts:
    requester_person: str
    editor_persons: frozenset[str]  # everyone who created or materially revised the request
    beneficiary_person: str | None
    created_at: datetime
    policy: ActionPolicy


def voter_refusals(facts: CaseFacts, voter: Voter) -> tuple[str, ...]:
    """Why this voter may not vote on this case. Empty means eligible. Every reason is stated; none is overridden by seniority."""
    out: list[str] = []
    if not voter.human:
        out.append(
            "VOTER_NOT_HUMAN"
        )  # a bot, agent, service account or AI is never a human approval
    person = voter.person_id
    if person == facts.requester_person:
        out.append("VOTER_IS_REQUESTER")
    elif person in facts.editor_persons:
        out.append("VOTER_IS_EDITOR")
    if facts.beneficiary_person is not None and person == facts.beneficiary_person:
        out.append("VOTER_IS_BENEFICIARY")
    allowed = facts.policy.approvers
    counted = [
        a
        for a in voter.authorities
        if a.role in allowed
        and not (
            a.effective_at >= facts.created_at  # no creating an approver and using them at once
            and facts.requester_person in (a.granted_by_person, a.approved_by_person)
        )
    ]
    held = [a for a in voter.authorities if a.role in allowed]
    reviewer_ok = voter.reviewer and "REVIEWER" in allowed
    if not counted and not reviewer_ok:
        out.append("VOTER_AUTHORITY_MANUFACTURED" if held else "VOTER_NO_AUTHORITY")
    conflicted = facts.requester_person in voter.reviewer_conflicts or (
        facts.beneficiary_person is not None
        and facts.beneficiary_person in voter.reviewer_conflicts
    )
    if reviewer_ok and not counted and conflicted:
        out.append("VOTER_REVIEWER_CONFLICT")
    return tuple(out)


@dataclass(frozen=True, slots=True)
class Ballot:
    person_id: str
    approve: bool


@dataclass(frozen=True, slots=True)
class Tally:
    approvals: int  # distinct, still-eligible people who approved
    rejected: bool
    lost: tuple[
        str, ...
    ]  # people who voted and are no longer eligible (their vote no longer counts)


def tally(ballots: Iterable[Ballot], still_eligible: Mapping[str, bool]) -> Tally:
    """Count people, not rows: a person counts once however many ballots exist, and a person who has since lost authority counts for
    nothing (the quorum is recomputed on every decision and at execution)."""
    approving: set[str] = set()
    lost: list[str] = []
    rejected = False
    for b in ballots:
        if not still_eligible.get(b.person_id, False):
            lost.append(b.person_id)
            continue
        if b.approve:
            approving.add(b.person_id)
        else:
            rejected = True
    return Tally(len(approving), rejected, tuple(sorted(set(lost))))


def state_after(policy: ActionPolicy, t: Tally) -> State:
    """The state the votes put the case in. The first A2 vote never makes it ready; a rejection ends it."""
    if t.rejected:
        return State.REJECTED
    need = policy.required_votes
    if t.approvals >= need:
        return State.APPROVED_READY
    return State.PARTIALLY_APPROVED if t.approvals > 0 else State.PENDING_APPROVAL


def move(current: State, to: State) -> State:
    if to not in TRANSITIONS.get(current, frozenset()):
        raise ApprovalError("APPROVAL_STATE_INVALID", f"{current.value} cannot become {to.value}")
    return to


# ----------------------------------------------------------------------------------------- solo organizations


HOLD_NO_INDEPENDENT = "BLOCKED_INSUFFICIENT_APPROVERS"
HOLD_NO_REVIEWER = "PENDING_INDEPENDENT_REVIEW"


def governance_hold(policy: ActionPolicy, independent_available: int) -> str | None:
    """A case that nobody independent can decide is held in a stated state and keeps waiting (never executed, never waived). A2 that
    cannot reach two independent people is BLOCKED_INSUFFICIENT_APPROVERS; an A1 with none is PENDING_INDEPENDENT_REVIEW."""
    need = policy.required_votes
    if need == 0 or independent_available >= need:
        return None
    return HOLD_NO_INDEPENDENT if policy.tier is Tier.A2 else HOLD_NO_REVIEWER


@dataclass(frozen=True, slots=True)
class OrganizationGovernance:
    """The computed state Decision 12 lists. `solo_mode` is a fact about eligible people today, recomputed on every read; it never
    lowers a requirement. Legal form is not decided here: one member is not automatically a sole proprietorship."""

    eligible_human_member_count: int
    solo_mode: bool
    region: str
    policy_version: str
    legal_form_resolved: bool = False
    independent_routes: tuple[str, ...] = field(default_factory=tuple)


def organization_governance(
    *, eligible_persons: Sequence[str], region: str, policy_version: str, reviewers: int
) -> OrganizationGovernance:
    count = len(set(eligible_persons))
    routes = ["ORGANIZATION_APPOINTED_REVIEWER"] if reviewers else []
    return OrganizationGovernance(count, count <= 1, region, policy_version, False, tuple(routes))
