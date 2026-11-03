"""The approval service (ZID-ENG-DEC-010, 011, 012; slice L-10, CR-047): one server-authoritative place where a critical or sensitive change
is requested, voted and applied. The API, a job, a script or a support tool all go through it; the UI only shows its state.

Order, as Decision 11 states it: authenticate the actor, resolve tenant and region, apply legal and technical prohibitions, check the
role, classify the risk under a policy version, require step-up, collect the independent votes, revalidate everything at execution, commit
exactly once, and keep the evidence. A refusal that changes something worth keeping (a case that expired, a vote that was denied)
raises with `persist=True`; the API commits and then refuses.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7
from zid_outbox import emit, next_version

from zid_platform.modules.evidence.ports import AuditFacts, EvidenceClass, record_audit
from zid_platform.modules.principal.ports import PrincipalStatus, PrincipalType, load_principal
from zid_platform.modules.tenant.application import access, approval_audit
from zid_platform.modules.tenant.domain.approvals import (
    OPEN,
    ActionPolicy,
    ApprovalError,
    Authority,
    Ballot,
    CaseFacts,
    OrganizationGovernance,
    Scope,
    State,
    Tier,
    Voter,
    canonical_payload,
    governance_hold,
    organization_governance,
    request_hash,
    state_after,
    tally,
    voter_refusals,
)
from zid_platform.modules.tenant.domain.lifecycle import (
    Role,
    RoleStatus,
    TenantError,
    serves_runtime,
)
from zid_platform.modules.tenant.domain.role_matrix import Mode
from zid_platform.modules.tenant.infrastructure import approvals as store
from zid_platform.modules.tenant.infrastructure import tenant_store

log = logging.getLogger("zid.approvals")

_VOTING_ROLES = (Role.OWNER, Role.APPROVER)
_REQUESTING_ROLES = frozenset({Role.OWNER, Role.ADMIN})
SOURCE = "tenant"


# ----------------------------------------------------------------------------------------------- executors


@dataclass(frozen=True, slots=True)
class ExecContext:
    conn: AsyncConnection
    case: store.Case
    tenant_id: str
    executor_principal: str
    approvers: tuple[str, ...]  # principal ids of the counted, distinct approvers
    now: datetime


@dataclass(frozen=True, slots=True)
class Gate:
    """One additional gate of the record, as a fact: `blocking` gates refuse the request (and the run) when `ok` is false; the others
    inform the approver (for example the permissions a role would carry)."""

    code: str
    ok: bool
    note: str
    blocking: bool = True


@dataclass(frozen=True, slots=True)
class Executor:
    """What applies one kind of approved case. `validate` runs when the case is requested (and refuses a payload that can never be
    applied), `beneficiary` names the principal the change is for (who may not vote), `prohibit` runs at execution after quorum and
    returns a reason code when a legal or technical rule forbids the change whatever the votes say, `run` applies it."""

    name: str
    run: Callable[[ExecContext], Awaitable[dict[str, Any]]]
    validate: Callable[[AsyncConnection, str, dict[str, Any]], Awaitable[None]] | None = None
    beneficiary: Callable[[dict[str, Any]], str | None] | None = None
    prohibit: Callable[[ExecContext], Awaitable[str | None]] | None = None
    # machine-checked additional gates (Decision 11): evaluated when the case is requested, shown with it, and again at execution
    check: Callable[[AsyncConnection, str, dict[str, Any]], Awaitable[list[Gate]]] | None = None


_EXECUTORS: dict[str, Executor] = {}


def register_executor(executor: Executor) -> None:
    _EXECUTORS[executor.name] = executor


def executor_names() -> frozenset[str]:
    return frozenset(_EXECUTORS)


# What must still be true of the organization when a customer case runs, whatever the votes say (Decision 11 order: "revalidate at
# execution"; Decision 12 test 07: execute rechecks country and data controls). It returns a reason code when the organization may not
# act now, or None. Composition supplies it (the country registry is not the tenant module's); without one nothing is refused, which
# is the earlier behaviour, and `standing_check_configured` lets a deployed process refuse to start without it.
StandingCheck = Callable[[AsyncConnection, str], Awaitable[str | None]]
_STANDING: StandingCheck | None = None


def configure_standing_check(check: StandingCheck | None) -> None:
    global _STANDING
    _STANDING = check


def standing_check_configured() -> bool:
    return _STANDING is not None


# ------------------------------------------------------------------------------------------------ evidence


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str | None,
    event: str,
    case: store.Case | None,
    outcome: str,
    reason: str | None,
    details: dict[str, Any],
    now: datetime,
    *,
    target: str | None = None,
    actor_type: str = "HUMAN",
) -> None:
    """One E3 record: ids, tier, hash and codes only, never the payload or a secret."""
    await record_audit(
        conn,
        tenant_id,
        AuditFacts(
            event_type=event,
            actor_type=actor_type,
            action=event.rsplit(".", 1)[-1],
            target_type="approval_case",
            target_ref=(case.case_id if case else target or "")[:128],
            outcome=outcome,
            evidence_class=EvidenceClass.E3,
            occurred_at=now,
            actor_principal_id=actor,
            reason_code=reason,
            source_service=SOURCE,
            source_event_id=str(uuid7()),
            details={
                **(
                    {
                        "action_id": case.action_id,
                        "tier": case.tier.value,
                        "request_hash": case.request_hash,
                        "policy_version": case.policy_version,
                        "region": case.region,
                        "state": case.state.value,
                    }
                    if case
                    else {}
                ),
                **details,
            },
        ),
    )


async def _event(conn: AsyncConnection, name: str, case: store.Case, data: dict[str, Any]) -> None:
    version = await next_version(conn, "approval_case", case.case_id)
    await emit(
        conn,
        name=name,
        tenant_id=case.tenant_id,
        aggregate_type="approval_case",
        aggregate_id=case.case_id,
        aggregate_version=version,
        data={
            "case_id": case.case_id,
            "action_id": case.action_id,
            "tier": case.tier.value,
            "state": case.state.value,
            "request_hash": case.request_hash,
            "policy_version": case.policy_version,
            "region": case.region,
            **data,
        },
    )


# ----------------------------------------------------------------------------------------------- people


async def _principal_ok(
    conn: AsyncConnection, principal_id: str, tenant_id: str
) -> tuple[bool, bool]:
    """(human, active) for an account of this tenant. An account that cannot be read is neither."""
    try:
        p, _ = await load_principal(conn, principal_id)
    except Exception:  # noqa: BLE001 - an unreadable principal is simply not a voter
        return False, False
    return (
        p.principal_type is PrincipalType.HUMAN,
        p.status is PrincipalStatus.ACTIVE and p.tenant_id == tenant_id,
    )


async def _authorities(conn: AsyncConnection, principal_id: str) -> tuple[Authority, ...]:
    out: list[Authority] = []
    for a in await tenant_store.roles(conn, principal_id=principal_id):
        if a.status is not RoleStatus.ACTIVE or a.role not in _VOTING_ROLES:
            continue
        out.append(
            Authority(
                a.role.value,
                a.activated_at or a.created_at,
                await tenant_store.person_of(conn, a.granted_by) if a.granted_by else None,
                await tenant_store.person_of(conn, a.approved_by) if a.approved_by else None,
            )
        )
    return tuple(out)


async def voter_for(
    conn: AsyncConnection,
    tenant_id: str,
    principal_id: str,
    *,
    region: str,
    now: datetime,
    reviewer_rows: list[store.Reviewer] | None = None,
) -> Voter:
    """Everything the rules need to know about one account as a voter, read afresh (no cached role, no remembered grant)."""
    human, active = await _principal_ok(conn, principal_id, tenant_id)
    person = await tenant_store.person_of(conn, principal_id)
    rows = (
        reviewer_rows if reviewer_rows is not None else await store.reviewers(conn, status="ACTIVE")
    )
    mine = next(
        (
            r
            for r in rows
            if r.principal_id == principal_id and r.expires_at > now and r.region == region
        ),
        None,
    )
    return Voter(
        principal_id,
        person,
        human and active,
        await _authorities(conn, principal_id) if active else (),
        reviewer=mine is not None and active,
        reviewer_conflicts=frozenset(mine.declared_conflicts) if mine else frozenset(),
    )


async def candidate_voters(
    conn: AsyncConnection, tenant_id: str, *, region: str, now: datetime
) -> list[Voter]:
    """Every account that holds a voting role or an active reviewer appointment, one per account."""
    reviewer_rows = await store.reviewers(conn, status="ACTIVE")
    ids = {a.principal_id for a in await tenant_store.roles(conn) if a.role in _VOTING_ROLES}
    ids |= {r.principal_id for r in reviewer_rows}
    return [
        await voter_for(conn, tenant_id, p, region=region, now=now, reviewer_rows=reviewer_rows)
        for p in sorted(ids)
    ]


def _facts(case: store.Case, policy: ActionPolicy) -> CaseFacts:
    return CaseFacts(
        case.requester_person_id,
        frozenset(case.editor_persons),
        case.beneficiary_person_id,
        case.created_at,
        policy,
    )


async def independent_people(
    conn: AsyncConnection, case: store.Case, policy: ActionPolicy, now: datetime
) -> list[str]:
    """The distinct people who could vote on this case today."""
    people: set[str] = set()
    for v in await candidate_voters(conn, case.tenant_id, region=case.region, now=now):
        if not voter_refusals(_facts(case, policy), v):
            people.add(v.person_id)
    return sorted(people)


# ------------------------------------------------------------------------------------------------ creation


@dataclass(frozen=True, slots=True)
class CaseView:
    case: store.Case
    policy: ActionPolicy
    hold: (
        str | None
    )  # BLOCKED_INSUFFICIENT_APPROVERS or PENDING_INDEPENDENT_REVIEW while nobody independent can decide it
    approvals: int
    required: int
    votes: list[store.VoteRow] = field(default_factory=list)
    independent_available: int = 0
    gates: list[Gate] = field(default_factory=list)
    execution_hold: str | None = (
        None  # why an approved case cannot run now (policy, region or trust changed), derived, never stored
    )


async def _refuse_audited(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    event: str,
    code: str,
    action_id: str,
    now: datetime,
    detail: str = "",
) -> ApprovalError:
    await _audit(
        conn,
        tenant_id,
        actor,
        event,
        None,
        "DENIED",
        code,
        {"action_id": action_id},
        now,
        target=action_id,
    )
    return ApprovalError(code, detail, persist=True)


async def classify(action_id: str) -> ActionPolicy:
    return store.registry().classify(action_id)


async def _requester_allowed(
    conn: AsyncConnection, tenant_id: str, actor: str, policy: ActionPolicy, now: datetime
) -> None:
    human, active = await _principal_ok(conn, actor, tenant_id)
    if not (human and active):
        raise ApprovalError("APPROVAL_REQUESTER_NOT_HUMAN", "only a person can request an approval")
    if policy.request_matrix:
        d = await access.evaluate(
            conn, tenant_id, actor, policy.request_matrix, Mode.PREPARE,
            object_id=f"tenant:{tenant_id}", session_ok=True, now=now,
        )  # fmt: skip
        if not d.allowed:
            raise ApprovalError("APPROVAL_REQUEST_NOT_PERMITTED", ",".join(d.reasons))
        return
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=actor)
        if a.status is RoleStatus.ACTIVE
    }
    if not held & _REQUESTING_ROLES:
        raise ApprovalError(
            "APPROVAL_REQUEST_NOT_PERMITTED", "an Owner or Administrator requests this"
        )


async def create_case(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    action_id: str,
    target_id: str,
    payload: dict[str, Any],
    reason: str,
    ttl_seconds: int | None = None,
    evidence_ref: str | None = None,
    supersedes: store.Case | None = None,
    platform_tenant_id: str | None = None,
    now: datetime | None = None,
) -> CaseView:
    now = now or datetime.now(UTC)
    reg = store.registry()
    policy = reg.classify(action_id)
    if policy.tier is Tier.FORBIDDEN:  # whatever signatures exist: no case, no vote, no execution
        raise await _refuse_audited(
            conn,
            tenant_id,
            actor,
            approval_audit.REFUSED,
            "APPROVAL_ACTION_FORBIDDEN",
            action_id,
            now,
        )
    if policy.tier is Tier.A0:
        raise ApprovalError(
            "APPROVAL_NOT_REQUIRED",
            "this action is routine: the authorized actor performs it, no vote",
        )
    if policy.tier is Tier.CONTAINMENT:
        raise ApprovalError(
            "APPROVAL_USE_CONTAINMENT",
            "an immediate defensive action is not queued behind an approval",
        )
    if policy.scope is Scope.PLATFORM and (
        platform_tenant_id is None or tenant_id != platform_tenant_id
    ):
        raise await _refuse_audited(conn, tenant_id, actor, approval_audit.REFUSED, "APPROVAL_PLATFORM_ACTION", action_id, now,
                                    "platform actions are approved in the platform operations organization, not by a customer tenant")  # fmt: skip
    ex = _EXECUTORS.get(policy.executor or "")
    if ex is None:  # nothing is approved that cannot be applied
        raise await _refuse_audited(conn, tenant_id, actor, approval_audit.REFUSED, "APPROVAL_ACTION_NOT_EXECUTABLE", action_id, now,
                                    "no executor applies this action yet")  # fmt: skip
    try:
        await _requester_allowed(conn, tenant_id, actor, policy, now)
    except ApprovalError as e:
        raise await _refuse_audited(
            conn, tenant_id, actor, approval_audit.REFUSED, e.code, action_id, now, e.detail
        ) from e
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise ApprovalError("TENANT_NOT_FOUND")
    if not target_id or not reason.strip():
        raise ApprovalError("VALIDATION_FAILED", "a target and a reason are required")
    payload_json = canonical_payload(payload)
    if ex.validate is not None:
        await ex.validate(conn, tenant_id, payload)
    if ex.check is not None:
        failed = next(
            (g for g in await ex.check(conn, tenant_id, payload) if g.blocking and not g.ok), None
        )
        if (
            failed is not None
        ):  # a gate the record states and the platform can check, not met: no case
            raise await _refuse_audited(
                conn,
                tenant_id,
                actor,
                approval_audit.REFUSED,
                failed.code,
                action_id,
                now,
                failed.note,
            )
    ttl = min(
        int(ttl_seconds or store.policy_settings().get("default_ttl_seconds", 259200)),
        int(store.policy_settings().get("max_ttl_seconds", 1209600)),
    )
    requester_person = await tenant_store.person_of(conn, actor)
    ben_principal = ex.beneficiary(payload) if ex.beneficiary else None
    ben_person = await tenant_store.person_of(conn, ben_principal) if ben_principal else None
    editors = {requester_person}
    if supersedes is not None:
        editors |= set(supersedes.editor_persons) | {await tenant_store.person_of(conn, actor)}
        requester_person = supersedes.requester_person_id
    case_id = str(uuid7())
    h = request_hash(tenant_id=tenant_id, action_id=action_id, target_id=target_id, payload_json=payload_json,
                     policy_version=reg.policy_version, region=tenant.home_cell_id)  # fmt: skip
    summary = f"{action_id} on {target_id}"[:500]
    await store.insert_case(
        conn,
        case_id=case_id,
        tenant_id=tenant_id,
        version=1 if supersedes is None else supersedes.version + 1,
        supersedes=None if supersedes is None else supersedes.case_id,
        action_id=action_id,
        tier=policy.tier,
        target_id=target_id,
        payload_json=payload_json,
        request_hash=h,
        policy_version=reg.policy_version,
        region=tenant.home_cell_id,
        requester_principal_id=supersedes.requester_principal_id if supersedes else actor,
        requester_person_id=requester_person,
        editor_persons=sorted(editors),
        beneficiary_principal_id=ben_principal,
        beneficiary_person_id=ben_person,
        summary=summary,
        reason=reason.strip()[:500],
        evidence_ref=evidence_ref,
        expires_at=now + timedelta(seconds=ttl),
        now=now,
    )
    case = await store.load_case(conn, case_id)
    assert case is not None
    await _audit(conn, tenant_id, actor, "governance.approval.case_created", case, "SUCCESS", None,
                 {"version": case.version, "supersedes": case.supersedes_case_id, "expires_at": case.expires_at.isoformat(),
                  "gate": policy.gate}, now)  # fmt: skip
    await _event(conn, "governance.approval.case_created", case, {"version": case.version})
    return await view(conn, case, now=now)


async def revise_case(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    payload: dict[str, Any],
    reason: str,
    platform_tenant_id: str | None = None,
    now: datetime | None = None,
) -> CaseView:
    """A material change is a new case version. Every vote on the old one is void and the person who changed it can never vote."""
    now = now or datetime.now(UTC)
    old = await store.load_case(conn, case_id, lock=True)
    if old is None:
        raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
    old = await _lapse(conn, old, now)
    if old.state not in OPEN and old.state is not State.NEEDS_CHANGES:
        raise ApprovalError("APPROVAL_STATE_INVALID", f"a {old.state.value} case cannot be revised")
    new = await create_case(
        conn, tenant_id, actor, action_id=old.action_id, target_id=old.target_id, payload=payload,
        reason=reason, supersedes=old, platform_tenant_id=platform_tenant_id, now=now,
    )  # fmt: skip
    if (
        old.state is not State.NEEDS_CHANGES
    ):  # a version that already ended for changes stays as it is
        await store.set_state(
            conn, old, State.INVALIDATED, actor=actor, reason_code="MATERIAL_CHANGE", now=now
        )
    refreshed = await store.load_case(conn, old.case_id)
    assert refreshed is not None
    await _audit(conn, tenant_id, actor, "governance.approval.invalidated", refreshed, "SUCCESS", "MATERIAL_CHANGE",
                 {"replaced_by": new.case.case_id}, now)  # fmt: skip
    await _event(
        conn,
        "governance.approval.invalidated",
        refreshed,
        {"reason": "MATERIAL_CHANGE", "replaced_by": new.case.case_id},
    )
    return new


async def cancel_case(
    conn: AsyncConnection, tenant_id: str, actor: str, case_id: str, *, now: datetime | None = None
) -> CaseView:
    now = now or datetime.now(UTC)
    case = await store.load_case(conn, case_id, lock=True)
    if case is None:
        raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
    case = await _lapse(conn, case, now)
    if await tenant_store.person_of(conn, actor) != case.requester_person_id:
        raise ApprovalError("APPROVAL_CANCEL_NOT_PERMITTED", "only the requester cancels a request")
    if case.state not in OPEN:
        raise ApprovalError(
            "APPROVAL_STATE_INVALID", f"a {case.state.value} case cannot be cancelled"
        )
    case = await store.set_state(
        conn, case, State.CANCELLED, actor=actor, reason_code="CANCELLED_BY_REQUESTER", now=now
    )
    await _audit(
        conn, tenant_id, actor, "governance.approval.cancelled", case, "SUCCESS", None, {}, now
    )
    await _event(conn, "governance.approval.cancelled", case, {})
    return await view(conn, case, now=now)


# ----------------------------------------------------------------------------------------------- reading


async def _lapse(conn: AsyncConnection, case: store.Case, now: datetime) -> store.Case:
    """An open case past its expiry becomes EXPIRED the first time anything touches it (the move is logged and cannot be undone)."""
    if case.state in OPEN and now >= case.expires_at:
        case = await store.set_state(
            conn, case, State.EXPIRED, actor=None, reason_code="EXPIRED", now=now
        )
        await _audit(
            conn,
            case.tenant_id,
            None,
            "governance.approval.expired",
            case,
            "SUCCESS",
            "EXPIRED",
            {},
            now,
        )
        await _event(conn, "governance.approval.expired", case, {})
    return case


async def _quorum(
    conn: AsyncConnection, case: store.Case, policy: ActionPolicy, now: datetime
) -> tuple[Any, list[store.VoteRow], dict[str, Voter]]:
    votes = await store.votes(conn, case.case_id)
    facts = _facts(case, policy)
    voters: dict[str, Voter] = {}
    eligible: dict[str, bool] = {}
    for v in votes:
        voter = await voter_for(
            conn, case.tenant_id, v.approver_principal_id, region=case.region, now=now
        )
        voters[v.approver_person_id] = voter
        eligible[v.approver_person_id] = not voter_refusals(facts, voter)
    return (
        tally([Ballot(v.approver_person_id, v.vote == "APPROVE") for v in votes], eligible),
        votes,
        voters,
    )


async def view(conn: AsyncConnection, case: store.Case, *, now: datetime | None = None) -> CaseView:
    now = now or datetime.now(UTC)
    policy = store.registry().actions.get(case.action_id)
    if policy is None:  # the action left the registry: the case can never run
        raise ApprovalError("APPROVAL_ACTION_UNKNOWN")
    t, votes, _ = await _quorum(conn, case, policy, now)
    available = len(await independent_people(conn, case, policy, now)) if case.state in OPEN else 0
    hold = governance_hold(policy, available) if case.state in OPEN else None
    ex = _EXECUTORS.get(policy.executor or "")
    gates = (
        await ex.check(conn, case.tenant_id, case.payload)
        if ex and ex.check and case.state in OPEN
        else []
    )
    return CaseView(
        case, policy, hold, t.approvals, policy.required_votes, votes, available, gates,
        await _execution_hold(conn, case, ex, gates),
    )  # fmt: skip


async def _execution_hold(
    conn: AsyncConnection, case: store.Case, ex: Executor | None, gates: list[Gate]
) -> str | None:
    """Why an APPROVED case cannot run right now: the policy it was classified under changed, its region changed, the organization is not
    serving, or a gate the platform checks no longer holds. The votes stay; nothing is applied until the cause ends or a new request is made."""
    if case.state not in (State.APPROVED_READY, State.FAILED):
        return None
    reg = store.registry()
    if reg.policy_version != case.policy_version:
        return "POLICY_CHANGED"
    tenant = await tenant_store.load_tenant(conn, case.tenant_id)
    if tenant is None or tenant.home_cell_id != case.region:
        return "REGION_CHANGED"
    if not serves_runtime(tenant.status) and (ex is None or ex.name != "lifecycle.resume"):
        return "TENANT_NOT_SERVING"
    failed = next((g for g in gates if g.blocking and not g.ok), None)
    return failed.code if failed else None


async def my_refusals(
    conn: AsyncConnection, case: store.Case, actor: str, now: datetime
) -> tuple[str, ...]:
    """Why the caller may not vote on this case (empty if they may). The reason the UI shows beside a disabled Approve."""
    policy = store.registry().classify(case.action_id)
    voter = await voter_for(conn, case.tenant_id, actor, region=case.region, now=now)
    reasons = list(voter_refusals(_facts(case, policy), voter))
    if any(v.approver_person_id == voter.person_id for v in await store.votes(conn, case.case_id)):
        reasons.append("VOTER_ALREADY_VOTED")
    return tuple(reasons)


async def governance_status(
    conn: AsyncConnection, tenant_id: str, *, now: datetime | None = None
) -> OrganizationGovernance:
    """The computed state of Decision 12: how many eligible human members, whether the organization is solo, the region and policy
    version. Nothing in it is client-controlled, and being solo never lowers a requirement."""
    now = now or datetime.now(UTC)
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise ApprovalError("TENANT_NOT_FOUND")
    voters = await candidate_voters(conn, tenant_id, region=tenant.home_cell_id, now=now)
    people = [
        v.person_id
        for v in voters
        if v.human and any(a.role in {"OWNER", "APPROVER"} for a in v.authorities)
    ]
    reviewers = [v for v in voters if v.reviewer and v.human]
    return organization_governance(
        eligible_persons=people,
        region=tenant.home_cell_id,
        policy_version=store.registry().policy_version,
        reviewers=len(reviewers),
    )


# --------------------------------------------------------------------------------------------------- voting


StepUp = Callable[[store.Case], Awaitable[dict[str, Any] | None]]


async def cast_vote(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    vote: str,
    presented_hash: str,
    step_up: StepUp,
    idempotency_key: str | None = None,
    reason: str | None = None,
    now: datetime | None = None,
) -> CaseView:
    """Record one person's vote on one immutable version. The case row is locked, so concurrent votes meet one authoritative state."""
    now = now or datetime.now(UTC)
    if vote not in ("APPROVE", "REJECT"):
        raise ApprovalError("VALIDATION_FAILED", "vote is APPROVE or REJECT")
    case = await store.load_case(conn, case_id, lock=True)
    if case is None:
        raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
    key_hash = (
        hashlib.sha256(idempotency_key.encode()).hexdigest()[:32] if idempotency_key else None
    )
    if key_hash:  # a retry of the same decision returns what it already did and records nothing new
        who = await tenant_store.person_of(conn, actor)
        if any(
            v.approver_person_id == who and v.auth_context.get("idempotency_key") == key_hash
            for v in await store.votes(conn, case.case_id)
        ):
            return await view(conn, case, now=now)
    case = await _lapse(conn, case, now)
    if case.state is State.EXPIRED:
        raise ApprovalError("APPROVAL_EXPIRED", persist=True)
    if case.state not in (State.PENDING_APPROVAL, State.PARTIALLY_APPROVED):
        raise ApprovalError("APPROVAL_STATE_INVALID", f"a {case.state.value} case takes no vote")
    reg = store.registry()
    policy = reg.actions.get(case.action_id)
    if policy is None:
        raise ApprovalError("APPROVAL_ACTION_UNKNOWN")
    await _stale_check(conn, case, now, actor=actor)
    if (
        presented_hash != case.request_hash
    ):  # the approver reviewed something other than what is being decided
        raise await _deny_vote(conn, case, actor, "APPROVAL_HASH_MISMATCH", now)
    refusals = await my_refusals(conn, case, actor, now)
    if refusals:
        raise await _deny_vote(conn, case, actor, refusals[0], now, refusals)
    ctx = await step_up(
        case
    )  # fresh, action-bound authentication for this approver and this request (Decision 9)
    voter = await voter_for(conn, tenant_id, actor, region=case.region, now=now)
    basis = next((a.role for a in voter.authorities if a.role in policy.approvers), "REVIEWER")
    await store.insert_vote(
        conn, tenant_id=tenant_id, case=case, principal_id=actor, person_id=voter.person_id,
        authority_basis=basis, vote=vote,
        step_up_proof_ref=(ctx or {}).get("proof_id"),
        auth_context={
            "tier": (ctx or {}).get("tier", "NONE"),
            "required": bool((ctx or {}).get("required", False)),
            **({"idempotency_key": key_hash} if key_hash else {}),
        },
        now=now,
    )  # fmt: skip
    t, _, _ = await _quorum(conn, case, policy, now)
    new = state_after(policy, t)
    if new is not case.state:
        case = await store.set_state(
            conn, case, new, actor=actor, reason_code=f"VOTE_{vote}", now=now
        )
    await _audit(conn, tenant_id, actor, "governance.approval.vote_recorded", case, "SUCCESS", None,
                 {"vote": vote, "authority_basis": basis, "approvals": t.approvals, "required": policy.required_votes,
                  "step_up_tier": (ctx or {}).get("tier", "NONE"), "step_up_proof_ref": (ctx or {}).get("proof_id")}, now)  # fmt: skip
    await _event(
        conn,
        "governance.approval.vote_recorded",
        case,
        {"vote": vote, "approvals": t.approvals, "required": policy.required_votes},
    )
    return await view(conn, case, now=now)


async def _deny_vote(
    conn: AsyncConnection,
    case: store.Case,
    actor: str,
    code: str,
    now: datetime,
    reasons: tuple[str, ...] = (),
) -> ApprovalError:
    await _audit(conn, case.tenant_id, actor, approval_audit.VOTE_DENIED, case, "DENIED", code,
                 {"reasons": ",".join(reasons or (code,))}, now)  # fmt: skip
    return ApprovalError(code, persist=True)


async def _stale_check(
    conn: AsyncConnection, case: store.Case, now: datetime, *, actor: str | None
) -> None:
    """The request is bound to the policy it was classified under and the region it will run in. If either has changed since, no
    earlier vote may be used: the case is invalidated and a new one is needed."""
    tenant = await tenant_store.load_tenant(conn, case.tenant_id)
    reg = store.registry()
    reason = None
    if reg.policy_version != case.policy_version:
        reason = "STALE_POLICY"
    elif tenant is None or tenant.home_cell_id != case.region:
        reason = "REGION_CHANGED"
    else:
        again = request_hash(tenant_id=case.tenant_id, action_id=case.action_id, target_id=case.target_id,
                             payload_json=case.payload_json, policy_version=reg.policy_version, region=case.region)  # fmt: skip
        if again != case.request_hash:
            reason = "PAYLOAD_TAMPERED"
    if reason is None:
        return
    case = await store.set_state(
        conn, case, State.INVALIDATED, actor=actor, reason_code=reason, now=now
    )
    await _audit(
        conn,
        case.tenant_id,
        actor,
        "governance.approval.invalidated",
        case,
        "SUCCESS",
        reason,
        {},
        now,
    )
    await _event(conn, "governance.approval.invalidated", case, {"reason": reason})
    raise ApprovalError("APPROVAL_INVALIDATED", reason, persist=True)


# ------------------------------------------------------------------------------------------------ execution


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    view: CaseView
    outcome: str  # EXECUTED or FAILED
    replayed: bool
    attempt: int
    reason_code: str | None
    result: dict[str, Any]
    once: dict[
        str, Any
    ]  # a secret the executor produced (an enrollment ticket): returned to the first caller only, never stored


async def execute_case(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    idempotency_key: str,
    now: datetime | None = None,
) -> ExecutionResult:
    """Apply an approved case: separate from voting, atomic, and once. Everything is checked again against the state of the world now."""
    now = now or datetime.now(UTC)
    case = await store.load_case(conn, case_id, lock=True)
    if case is None:
        raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
    prior = await store.executions(conn, case.case_id)
    same = next((p for p in prior if p.idempotency_key == idempotency_key), None)
    done = next((p for p in prior if p.outcome == "EXECUTED"), None)
    if (
        same is not None or done is not None
    ):  # a retry returns the one authoritative outcome and runs nothing
        chosen = done or same
        assert chosen is not None
        return ExecutionResult(
            await view(conn, case, now=now),
            chosen.outcome,
            True,
            chosen.attempt,
            chosen.reason_code,
            {},
            {},
        )
    case = await _lapse(conn, case, now)
    if case.state is State.EXPIRED:
        raise ApprovalError("APPROVAL_EXPIRED", persist=True)
    if case.state not in (State.APPROVED_READY, State.FAILED):
        raise ApprovalError("APPROVAL_NOT_READY", f"the case is {case.state.value}")
    reg = store.registry()
    policy = reg.actions.get(case.action_id)
    ex = _EXECUTORS.get(policy.executor or "") if policy else None
    if policy is None or ex is None or policy.tier not in (Tier.A1, Tier.A2):
        raise ApprovalError("APPROVAL_ACTION_NOT_EXECUTABLE")
    await _stale_check(conn, case, now, actor=actor)
    # who may run it: the requester, or a live Owner or Administrator; never an agent or service
    human, active = await _principal_ok(conn, actor, tenant_id)
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=actor)
        if a.status is RoleStatus.ACTIVE
    }
    is_requester = actor == case.requester_principal_id
    if not (human and active and (held & _REQUESTING_ROLES or is_requester)):
        raise await _deny_execute(conn, case, actor, "APPROVAL_EXECUTE_NOT_PERMITTED", now)
    req_roles = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=case.requester_principal_id)
        if a.status is RoleStatus.ACTIVE
    }
    if not req_roles & _REQUESTING_ROLES:  # the requester lost their authority since asking
        case = await store.set_state(
            conn,
            case,
            State.INVALIDATED,
            actor=actor,
            reason_code="REQUESTER_AUTHORITY_LOST",
            now=now,
        )
        await _audit(
            conn,
            tenant_id,
            actor,
            "governance.approval.invalidated",
            case,
            "SUCCESS",
            "REQUESTER_AUTHORITY_LOST",
            {},
            now,
        )
        raise ApprovalError("APPROVAL_INVALIDATED", "REQUESTER_AUTHORITY_LOST", persist=True)
    t, votes, _ = await _quorum(conn, case, policy, now)
    if (
        t.rejected or t.approvals < policy.required_votes
    ):  # an approver was revoked: the quorum is not remembered
        back = state_after(policy, t)
        if back is not State.REJECTED:
            case = await store.set_state(
                conn, case, back, actor=actor, reason_code="QUORUM_LOST", now=now
            )
        await _audit(conn, tenant_id, actor, approval_audit.EXECUTION_REFUSED, case, "DENIED", "APPROVAL_QUORUM_LOST",
                     {"approvals": t.approvals, "required": policy.required_votes, "lost": len(t.lost)}, now)  # fmt: skip
        raise ApprovalError("APPROVAL_QUORUM_LOST", persist=True)
    counted = [v for v in votes if v.vote == "APPROVE" and v.approver_person_id not in t.lost]
    approvers = tuple(dict.fromkeys(v.approver_principal_id for v in counted))
    quorum = {
        "request_hash": case.request_hash,
        "required": policy.required_votes,
        "approvals": [
            {"principal": v.approver_principal_id, "person": v.approver_person_id, "basis": v.authority_basis,
             "step_up_proof_ref": v.step_up_proof_ref, "decided_at": v.decided_at.isoformat()}
            for v in counted
        ],
    }  # fmt: skip
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None or (not serves_runtime(tenant.status) and ex.name != "lifecycle.resume"):
        raise ApprovalError(
            "APPROVAL_TENANT_NOT_SERVING", "the organization is suspended or closed", persist=True
        )
    ctx = ExecContext(conn, case, tenant_id, actor, approvers, now)
    attempt = len(prior) + 1
    if _STANDING is not None and policy.scope is Scope.CUSTOMER:
        standing = await _STANDING(conn, tenant_id)
        if standing:  # the country (or its data controls) closed after the vote; two valid votes cannot reopen it
            return await _record_failure(
                conn, case, policy, actor, idempotency_key, attempt, standing, quorum, now
            )
    if ex.check is not None:
        failed = next(
            (g for g in await ex.check(conn, tenant_id, case.payload) if g.blocking and not g.ok),
            None,
        )
        if failed is not None:  # the gate held when it was requested and does not now
            return await _record_failure(
                conn, case, policy, actor, idempotency_key, attempt, failed.code, quorum, now
            )
    if ex.prohibit is not None:
        why = await ex.prohibit(ctx)
        if why:  # two valid votes cannot authorize what the law or the platform forbids (Decision 11 T13)
            return await _record_failure(
                conn, case, policy, actor, idempotency_key, attempt, why, quorum, now
            )
    try:
        async with conn.begin_nested():
            produced = await ex.run(ctx)
    except (TenantError, ApprovalError) as e:
        return await _record_failure(
            conn, case, policy, actor, idempotency_key, attempt, e.code, quorum, now
        )
    once = dict(produced.get("_once") or {})
    public = {k: v for k, v in produced.items() if k != "_once"}
    await store.insert_execution(conn, tenant_id=tenant_id, case=case, attempt=attempt, idempotency_key=idempotency_key,
                                 outcome="EXECUTED", reason_code=None, actor=actor, quorum=quorum, now=now)  # fmt: skip
    case = await store.set_state(
        conn, case, State.EXECUTED, actor=actor, reason_code="EXECUTED", now=now
    )
    await _audit(conn, tenant_id, actor, "governance.approval.executed", case, "SUCCESS", None,
                 {"attempt": attempt, "approvers": len(approvers), "result": public}, now)  # fmt: skip
    await _event(conn, "governance.approval.executed", case, {"attempt": attempt})
    return ExecutionResult(
        await view(conn, case, now=now), "EXECUTED", False, attempt, None, public, once
    )


async def _deny_execute(
    conn: AsyncConnection, case: store.Case, actor: str, code: str, now: datetime
) -> ApprovalError:
    await _audit(
        conn,
        case.tenant_id,
        actor,
        approval_audit.EXECUTION_REFUSED,
        case,
        "DENIED",
        code,
        {},
        now,
    )
    return ApprovalError(code, persist=True)


async def _record_failure(
    conn: AsyncConnection,
    case: store.Case,
    policy: ActionPolicy,
    actor: str,
    key: str,
    attempt: int,
    code: str,
    quorum: dict[str, Any],
    now: datetime,
) -> ExecutionResult:
    """A failed run is a recorded failure, not a success, and nothing the executor did survives (its savepoint rolled back). The
    unchanged request may be run again until it expires."""
    await store.insert_execution(conn, tenant_id=case.tenant_id, case=case, attempt=attempt, idempotency_key=key,
                                 outcome="FAILED", reason_code=code, actor=actor, quorum=quorum, now=now)  # fmt: skip
    case = await store.set_state(conn, case, State.FAILED, actor=actor, reason_code=code, now=now)
    await _audit(
        conn,
        case.tenant_id,
        actor,
        "governance.approval.failed",
        case,
        "FAILURE",
        code,
        {"attempt": attempt},
        now,
    )
    await _event(
        conn, "governance.approval.failed", case, {"attempt": attempt, "reason_code": code}
    )
    return ExecutionResult(await view(conn, case, now=now), "FAILED", False, attempt, code, {}, {})


# ------------------------------------------------------------------------------------------------ containment


async def contain(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    action_id: str,
    payload: dict[str, Any],
    reason: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Immediate defensive action (Decision 11 sections 03 and 08, Decision 12 flow C): it never waits for a vote, only names exactly
    what it does, and records who, what, why, when and where, and that a review is due. It can suspend or revoke; it can never grant,
    restore or confer authority. Restoring what it stopped is an A2 case."""
    now = now or datetime.now(UTC)
    policy = store.registry().classify(action_id)
    ex = _EXECUTORS.get(policy.executor or "")
    if policy.tier is not Tier.CONTAINMENT or ex is None:
        raise ApprovalError("CONTAINMENT_ACTION_UNKNOWN", "this is not a defensive action")
    human, active = await _principal_ok(conn, actor, tenant_id)
    held = {
        a.role
        for a in await tenant_store.roles(conn, principal_id=actor)
        if a.status is RoleStatus.ACTIVE
    }
    if not (human and active and held & _REQUESTING_ROLES):
        raise await _refuse_audited(
            conn,
            tenant_id,
            actor,
            approval_audit.CONTAINMENT_REFUSED,
            "CONTAINMENT_NOT_PERMITTED",
            action_id,
            now,
        )
    if not reason.strip():
        raise ApprovalError("VALIDATION_FAILED", "say why")
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    region = tenant.home_cell_id if tenant else ""
    pseudo = store.Case(
        "", tenant_id, 1, None, action_id, Tier.A1, "", "{}", "0" * 64, store.registry().policy_version, region,
        actor, actor, (), None, None, "", reason, None, State.EXECUTED, now, now, now, now,
    )  # fmt: skip
    produced = await _run_containment(ex, conn, pseudo, tenant_id, actor, payload, reason, now)
    await emit(
        conn, name="governance.containment.executed", tenant_id=tenant_id, aggregate_type="containment_action", aggregate_id=str(uuid7()),
        aggregate_version=1,
        data={"action_id": action_id, "region": region, "post_incident_review": "REQUIRED"},
    )  # fmt: skip
    await _audit(conn, tenant_id, actor, "governance.containment.executed", None, "SUCCESS", None,
                 {"action_id": action_id, "region": region, "reason": reason.strip()[:200], "post_incident_review": "REQUIRED",
                  "result": produced}, now, target=action_id)  # fmt: skip
    return {
        "action_id": action_id,
        "executed_at": now.isoformat(),
        "region": region,
        "post_incident_review": "REQUIRED",
        **produced,
    }


async def _run_containment(
    ex: Executor,
    conn: AsyncConnection,
    pseudo: store.Case,
    tenant_id: str,
    actor: str,
    payload: dict[str, Any],
    reason: str,
    now: datetime,
) -> dict[str, Any]:
    from dataclasses import replace

    case = replace(
        pseudo, payload_json=canonical_payload({**payload, "reason": reason.strip()[:200]})
    )
    try:
        async with conn.begin_nested():
            return await ex.run(ExecContext(conn, case, tenant_id, actor, (), now))
    except TenantError as e:
        raise ApprovalError(e.code, e.detail) from e


def legacy_path_gate(action_id: str) -> None:
    """The earlier two-person approval (an authority approval bound to one change) is still how owner changes, privileged role grants and
    resumption work until the owners switch `governance_enforcement` to ENFORCE. In SHADOW it is allowed and reported; in ENFORCE a
    change the registry classifies A2 is refused unless it comes through an approval case."""
    policy = store.registry().actions.get(action_id)
    if policy is None or policy.tier is not Tier.A2:
        return
    if store.enforcement() == "ENFORCE":
        raise ApprovalError(
            "APPROVAL_CASE_REQUIRED",
            "This is a critical (A2) change: request it as an approval case with two independent approvers.",
        )
    log.warning("governance shadow: %s would require an A2 approval case", action_id)


from zid_platform.modules.tenant.application import approval_executors  # noqa: F401  (registers the executors)


async def consume_authorization(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    action_id: str,
    target_id: str,
    payload: dict[str, Any],
    consumer: str,
    now: datetime | None = None,
) -> store.Case:
    """The guard a CLI, job or support tool calls before it does a platform operation (Decision 11 T09). It needs an EXECUTED platform
    case (two independent approvers, each with a fresh proof) for exactly this action, target and payload, under the current policy
    version, inside its validity window, and it uses that authorization up: the same approval cannot authorize a second operation.
    Without one it raises and the operation must not run. Nothing here trusts a flag, a role or a console: the approval is the proof."""
    now = now or datetime.now(UTC)
    reg = store.registry()
    policy = reg.classify(action_id)
    if policy.scope is not Scope.PLATFORM or policy.tier is not Tier.A2:
        raise ApprovalError(
            "APPROVAL_NOT_PLATFORM_ACTION", "this guard is for critical platform operations"
        )
    window = timedelta(
        seconds=int(store.policy_settings().get("authorization_valid_seconds", 3600))
    )
    wanted = canonical_payload(payload)
    usable = [
        case
        for case in await store.executed_cases(conn, action_id, target_id)
        if case.tenant_id == tenant_id
        and case.payload_json == wanted
        and case.policy_version == reg.policy_version
        and case.executed_at is not None
        and now - case.executed_at <= window
    ]
    for case in usable:
        if await store.consume(conn, tenant_id, case.case_id, consumer):
            await _audit(conn, tenant_id, None, approval_audit.AUTHORIZATION_CONSUMED, case, "SUCCESS", None,
                         {"consumer": consumer[:128]}, now, actor_type="OPERATOR")  # fmt: skip
            return case
    await _audit(conn, tenant_id, None, approval_audit.AUTHORIZATION_REFUSED, None, "DENIED", "APPROVAL_AUTHORIZATION_MISSING",
                 {"action_id": action_id, "consumer": consumer[:128]}, now, target=action_id, actor_type="OPERATOR")  # fmt: skip
    raise ApprovalError(
        "APPROVAL_AUTHORIZATION_MISSING",
        "no unused, current, executed approval covers this exact operation",
        persist=True,
    )


# --------------------------------------------------------------------------------- request changes (WF-13C-03D)


async def request_changes(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    case_id: str,
    *,
    reason: str,
    now: datetime | None = None,
) -> CaseView:
    """An eligible approver asks for changes: this version ends and every vote on it is void; the requester revises it into a new version
    (and the votes of the old one are never reused). It needs a reason, an eligible approver and no step-up, because it grants nothing."""
    now = now or datetime.now(UTC)
    if not reason.strip():
        raise ApprovalError("VALIDATION_FAILED", "say what needs to change")
    case = await store.load_case(conn, case_id, lock=True)
    if case is None:
        raise ApprovalError("APPROVAL_CASE_NOT_FOUND")
    case = await _lapse(conn, case, now)
    if case.state is State.EXPIRED:
        raise ApprovalError("APPROVAL_EXPIRED", persist=True)
    if case.state not in (State.PENDING_APPROVAL, State.PARTIALLY_APPROVED, State.APPROVED_READY):
        raise ApprovalError(
            "APPROVAL_STATE_INVALID", f"a {case.state.value} case cannot be sent back"
        )
    refusals = [r for r in await my_refusals(conn, case, actor, now) if r != "VOTER_ALREADY_VOTED"]
    if refusals:
        raise await _deny_vote(conn, case, actor, refusals[0], now, tuple(refusals))
    case = await store.set_state(
        conn, case, State.NEEDS_CHANGES, actor=actor, reason_code="CHANGES_REQUESTED", now=now
    )
    await _audit(conn, tenant_id, actor, "governance.approval.changes_requested", case, "SUCCESS", "CHANGES_REQUESTED",
                 {"reason": reason.strip()[:300]}, now)  # fmt: skip
    await _event(conn, "governance.approval.changes_requested", case, {})
    return await view(conn, case, now=now)


# ------------------------------------------------------------------------------------- who may see what (WF-13C-02)


@dataclass(frozen=True, slots=True)
class Listing:
    items: list[CaseView]
    counts: dict[
        str, int
    ]  # per tab, over what this caller may see: a caller who may see nothing gets zeros, not other people's numbers
    next_cursor: str | None


async def _governor(
    conn: AsyncConnection, tenant_id: str, actor: str, region: str, now: datetime
) -> Voter:
    return await voter_for(conn, tenant_id, actor, region=region, now=now)


def _is_governor(v: Voter) -> bool:
    """Someone who holds, or has been appointed to, an approving role: the people the inbox is for."""
    return v.human and (any(a.role in ("OWNER", "APPROVER") for a in v.authorities) or v.reviewer)


async def can_view(
    conn: AsyncConnection, case: store.Case, actor: str, now: datetime | None = None
) -> bool:
    """Object-level read authorization. The requester sees their own request, anyone who voted sees it, and the organization's approving
    roles see the organization's requests. Everyone else sees nothing, and is told the request does not exist."""
    now = now or datetime.now(UTC)
    person = await tenant_store.person_of(conn, actor)
    if person == case.requester_person_id or person in case.editor_persons:
        return True
    if any(v.approver_person_id == person for v in await store.votes(conn, case.case_id)):
        return True
    return _is_governor(await _governor(conn, case.tenant_id, actor, case.region, now))


@dataclass(frozen=True, slots=True)
class CaseAccess:
    """What one caller may do with the documents on one request (WF-013C AT-12). Built for the attachment service, which lives in
    another module and must not read approval tables."""

    case_id: str
    state: str
    expires_at: datetime
    may_attach: bool  # the requester or an editor, while the request is open and no vote has been cast on it
    may_open: bool  # someone who holds or was appointed to an approving role, or who has voted


async def case_access(
    conn: AsyncConnection, case_id: str, actor: str, now: datetime | None = None
) -> CaseAccess | None:
    """None when the case does not exist for this organization or the caller may not see it: the same answer for both."""
    now = now or datetime.now(UTC)
    case = await store.load_case(conn, case_id)
    if case is None or not await can_view(conn, case, actor, now):
        return None
    person = await tenant_store.person_of(conn, actor)
    votes = await store.votes(conn, case.case_id)
    mine = person == case.requester_person_id or person in case.editor_persons
    voted = any(v.approver_person_id == person for v in votes)
    governor = _is_governor(await _governor(conn, case.tenant_id, actor, case.region, now))
    return CaseAccess(
        case_id=case.case_id,
        state=case.state.value,
        expires_at=case.expires_at,
        may_attach=mine and case.state is State.PENDING_APPROVAL and not votes,
        may_open=voted or governor,
    )


def _cursor(case: store.Case) -> str:
    return f"{case.created_at.isoformat()}|{case.case_id}"


async def list_visible(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    scope: str = "review",
    states: tuple[State, ...] | None = None,
    tier: str | None = None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
    query: str | None = None,
    cursor: str | None = None,
    limit: int = 25,
    now: datetime | None = None,
) -> Listing:
    """The inbox, built from object-level authorization first and filters second, so a filter can never reveal a row or a count that the
    caller may not see. Tabs: `review` (open requests this person may vote on now), `mine` (submitted by this person, any state),
    `history` (ended requests this person made, voted on, or, for approving roles, any in the organization)."""
    if scope not in ("review", "mine", "history"):
        raise ApprovalError("VALIDATION_FAILED", "scope is review, mine or history")
    now = now or datetime.now(UTC)
    tenant = await tenant_store.load_tenant(conn, tenant_id)
    if tenant is None:
        raise ApprovalError("TENANT_NOT_FOUND")
    person = await tenant_store.person_of(conn, actor)
    voter = await _governor(conn, tenant_id, actor, tenant.home_cell_id, now)
    governor = _is_governor(voter)
    reg = store.registry()
    seen: dict[str, list[store.Case]] = {"review": [], "mine": [], "history": []}
    for case in await store.list_cases(conn, limit=500):
        case = await _lapse(conn, case, now)
        policy = reg.actions.get(case.action_id)
        voted = any(v.approver_person_id == person for v in await store.votes(conn, case.case_id))
        mine = person == case.requester_person_id or person in case.editor_persons
        is_open = case.state in OPEN
        if mine:
            seen["mine"].append(case)
        voteable = case.state in (State.PENDING_APPROVAL, State.PARTIALLY_APPROVED)
        eligible = (
            policy is not None and not voter_refusals(_facts(case, policy), voter)
            if governor
            else False
        )
        if is_open and voteable and not mine and not voted and eligible:
            seen["review"].append(case)
        if not is_open and (mine or voted or governor):
            seen["history"].append(case)
    counts = {k: len(v) for k, v in seen.items()}

    def keep(c: store.Case) -> bool:
        return (
            (not states or c.state in states)
            and (not tier or c.tier.value == tier)
            and (created_from is None or c.created_at >= created_from)
            and (created_to is None or c.created_at <= created_to)
            and (not query or c.case_id.startswith(query.strip().lower()))
        )

    rows = sorted(
        (c for c in seen[scope] if keep(c)), key=lambda c: (c.created_at, c.case_id), reverse=True
    )
    if cursor:
        rows = [c for c in rows if _cursor(c) < cursor]
    limit = max(1, min(limit, 100))
    page = rows[:limit]
    items = [await view(conn, c, now=now) for c in page]
    return Listing(items, counts, _cursor(page[-1]) if len(rows) > limit else None)
