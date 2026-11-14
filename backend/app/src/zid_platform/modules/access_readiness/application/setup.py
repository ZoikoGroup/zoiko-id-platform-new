"""Setting the agent up: draft, dry run, publish request, approval, pause (ZID-AG-WF-001 sections 04-06, 10; CR-091).

The agent instance is an existing machine agent (its identity, sponsor, blueprint, lifecycle and attestation stay in the machine module);
this keeps the access review's own scope as a draft and binds an approval to its exact digest. Any change after approval, including a change
to the agent's blueprint or tool set, is a different digest, so the approval no longer applies (AT-05).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.access_readiness.application import common
from zid_platform.modules.access_readiness.application.common import Runtime, record, utcnow
from zid_platform.modules.access_readiness.domain.policy import (
    ReadinessError,
    Scope,
    SetupStatus,
    blueprint_allowed,
    policy_hash,
    scope_digest,
    setup_blockers,
    setup_transition,
    validate_name,
    validate_scope,
)
from zid_platform.modules.access_readiness.domain.rules import (
    DEFAULT_WARNING_WINDOW_DAYS,
    FIXTURES,
    POLICY_VERSION,
    PURPOSE,
    RULE_SET_VERSION,
    Outcome,
    evaluate_run,
    synthetic_fixture,
)
from zid_platform.modules.access_readiness.infrastructure import store
from zid_platform.modules.machine.ports import AgentIdentityStatus, agent_identity_status

REQUIRED_RULES = frozenset({"R-01", "R-02", "R-03", "R-04", "R-05", "R-06"})


def scope_of(setup: dict[str, Any]) -> Scope:
    return Scope(
        setup["purpose"],
        tuple(setup["resource_kinds"]),
        int(setup["max_records"]),
        int(setup["warning_window_days"]),
        setup["home_region"],
        bool(setup.get("ai_explanation", False)),
    )


def current_digest(setup: dict[str, Any], agent: AgentIdentityStatus) -> str:
    return scope_digest(
        scope_of(setup),
        agent_id=str(agent.agent_principal_id),
        blueprint=(agent.blueprint_id, agent.blueprint_version),
        allowed_tools=agent.allowed_tools,
    )


async def _identity(conn: AsyncConnection, agent_id: str) -> AgentIdentityStatus | None:
    return await agent_identity_status(conn, agent_id, now=utcnow())


def _blockers(rt: Runtime, agent: AgentIdentityStatus | None) -> list[str]:
    return setup_blockers(
        sponsor_active=bool(agent and agent.sponsor_active),
        agent_found=agent is not None,
        blueprint_ok=bool(agent and blueprint_allowed(agent.blueprint_id)),
        tools=agent.allowed_tools if agent else (),
        region_supported=rt.region_supported,
    )


def _check_ai(rt: Runtime, wanted: bool) -> None:
    """A setup may only ask for the explanation where the deployment allows it (ZID_ACCESS_READINESS_AI_EXPLAIN, default off)."""
    if wanted and not rt.ai_explain:
        raise ReadinessError(
            "AI_EXPLANATION_DISABLED", "the optional AI explanation is switched off in this cell"
        )


async def _load(conn: AsyncConnection, agent_id: str, *, lock: bool = False) -> dict[str, Any]:
    setup = await store.load_setup(conn, agent_id, lock=lock)
    if setup is None:
        raise ReadinessError("SETUP_NOT_FOUND")
    return setup


def view(
    setup: dict[str, Any],
    *,
    agent: AgentIdentityStatus | None = None,
    approval: dict[str, Any] | None = None,
) -> dict[str, Any]:
    out = {
        "setup_id": setup["setup_id"],
        "agent_id": setup["agent_principal_id"],
        "display_name": setup["display_name"],
        "purpose": setup["purpose"],
        "scope": {
            "resource_kinds": list(setup["resource_kinds"]),
            "max_records": setup["max_records"],
            "warning_window_days": setup["warning_window_days"],
            "home_region": setup["home_region"],
            "ai_explanation": bool(setup.get("ai_explanation", False)),
        },
        "rule_set_version": setup["rule_set_version"],
        "policy_version": setup["policy_version"],
        "status": setup["status"],
        "draft_revision": setup["draft_revision"],
        "scope_digest": setup["scope_digest"],
        "validated": setup["validated_digest"] is not None
        and setup["validated_digest"] == setup["scope_digest"],
        "paused_reason": setup["paused_reason"],
        "approval": None
        if approval is None
        else {
            "approval_id": approval["approval_id"],
            "scope_digest": approval["scope_digest"],
            "requested_by": approval["requested_by"],
            "approved_by": approval["approved_by"],
            "expires_at": approval["expires_at"].isoformat(),
        },
        # what the agent is permitted to do and what it never does, shown as labels and never only as a tooltip
        "permitted_tools": sorted(
            ["read_mandate_metadata", "read_agent_identity_status", "create_access_review_proposal"]
        ),
        "blocked": ["grant_role", "revoke_credential", "change_mandate", "edit_policy", "spend"],
    }
    if agent is not None:
        out["agent"] = {
            "status": agent.status,
            "sponsor_active": agent.sponsor_active,
            "attested": agent.attested,
            "blueprint": f"{agent.blueprint_id}@{agent.blueprint_version}",
        }
    return out


async def get_setup(conn: AsyncConnection, agent_id: str) -> dict[str, Any]:
    setup = await _load(conn, agent_id)
    return view(
        setup,
        agent=await _identity(conn, agent_id),
        approval=await store.latest_approval(conn, setup["setup_id"]),
    )


async def create_setup(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    *,
    actor: str,
    agent_id: str,
    display_name: str,
    resource_kinds: tuple[str, ...],
    max_records: int,
    warning_window_days: int,
    ai_explanation: bool = False,
) -> dict[str, Any]:
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    _check_ai(rt, ai_explanation)
    name = validate_name(display_name)
    agent = await _identity(conn, agent_id)
    blockers = _blockers(rt, agent)
    if blockers:
        raise ReadinessError(blockers[0], ", ".join(blockers))
    assert agent is not None
    scope = validate_scope(
        Scope(PURPOSE, resource_kinds, max_records, warning_window_days, rt.region, ai_explanation)
    )
    digest = scope_digest(
        scope,
        agent_id=agent_id,
        blueprint=(agent.blueprint_id, agent.blueprint_version),
        allowed_tools=agent.allowed_tools,
    )
    setup = await store.insert_setup(
        conn,
        tenant_id,
        agent_id=agent_id,
        display_name=name,
        purpose=scope.purpose,
        resource_kinds=scope.resource_kinds,
        max_records=scope.max_records,
        warning_window_days=scope.warning_window_days,
        ai_explanation=scope.ai_explanation,
        home_region=scope.home_region,
        rule_set_version=RULE_SET_VERSION,
        policy_version=POLICY_VERSION,
        scope_digest=digest,
        created_by=actor,
    )
    await record(
        conn,
        tenant_id,
        name=common.SETUP_CREATED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={"agent_id": agent_id, "scope_digest": digest},
    )
    return view(setup, agent=agent)


async def change_setup(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    *,
    actor: str,
    agent_id: str,
    changes: dict[str, Any],
    expected_revision: int | None,
) -> dict[str, Any]:
    """Save a draft change. Whatever was validated or approved before no longer applies: the setup is a DRAFT again (AT-05)."""
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    setup = await _load(conn, agent_id, lock=True)
    if expected_revision is not None and expected_revision != setup["draft_revision"]:
        raise ReadinessError("SETUP_STALE", "the draft changed since it was read")
    if setup["status"] == SetupStatus.PAUSED.value:
        raise ReadinessError("SETUP_PAUSED", "resume the setup first")
    agent = await _identity(conn, agent_id)
    blockers = _blockers(rt, agent)
    if blockers:
        raise ReadinessError(blockers[0], ", ".join(blockers))
    assert agent is not None
    fields: dict[str, Any] = {}
    if "display_name" in changes:
        fields["display_name"] = validate_name(changes["display_name"])
    merged = scope_of(setup)
    merged = Scope(
        merged.purpose,
        tuple(changes.get("resource_kinds", merged.resource_kinds)),
        int(changes.get("max_records", merged.max_records)),
        int(changes.get("warning_window_days", merged.warning_window_days)),
        rt.region,
        bool(changes.get("ai_explanation", merged.ai_explanation)),
    )
    if "ai_explanation" in changes:
        _check_ai(rt, bool(changes["ai_explanation"]))
    scope = validate_scope(merged)
    for key in ("resource_kinds", "max_records", "warning_window_days", "ai_explanation"):
        if key in changes:
            fields[key] = getattr(scope, key)
    digest = scope_digest(
        scope,
        agent_id=agent_id,
        blueprint=(agent.blueprint_id, agent.blueprint_version),
        allowed_tools=agent.allowed_tools,
    )
    invalidated = await store.invalidate_approvals(conn, setup["setup_id"], "DRAFT_CHANGED")
    updated = await store.update_setup(
        conn,
        setup["setup_id"],
        status=SetupStatus.DRAFT.value,
        scope_digest=digest,
        clear_validation=True,
        fields=fields,
        bump_revision=True,
    )
    await record(
        conn,
        tenant_id,
        name=common.SETUP_CHANGED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={"agent_id": agent_id, "scope_digest": digest, "approvals_invalidated": invalidated},
    )
    return view(updated, agent=agent)


async def dry_run(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    *,
    actor: str,
    agent_id: str,
    fixture: str,
    policy_version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Synthetic records and the current policy, in the sandbox: no live data is read and no run, decision, proposal or evidence is written.
    Only the setup's own validation mark moves, and only when every rule shows up in the fixture's outcome and replays identically."""
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    if fixture not in FIXTURES:
        raise ReadinessError("FIXTURE_UNKNOWN")
    if policy_version != POLICY_VERSION:
        raise ReadinessError("POLICY_VERSION_UNKNOWN")
    setup = await _load(conn, agent_id, lock=True)
    if setup["status"] not in (SetupStatus.DRAFT.value, SetupStatus.SANDBOX_READY.value):
        raise ReadinessError(
            "SETUP_INVALID_TRANSITION", f"a dry run is for a draft, not {setup['status']}"
        )
    agent = await _identity(conn, agent_id)
    blockers = _blockers(rt, agent)
    if blockers:
        raise ReadinessError(blockers[0], ", ".join(blockers))
    assert agent is not None
    at = now or utcnow()
    window = timedelta(days=int(setup["warning_window_days"]) or DEFAULT_WARNING_WINDOW_DAYS)
    first = evaluate_run(synthetic_fixture(fixture, at), at, window)
    again = evaluate_run(synthetic_fixture(fixture, at), at, window)
    rules_seen = {f.rule_id for f in first.findings} | {
        f.rule_id for r in first.records if r.outcome is Outcome.UNKNOWN for f in r.findings
    }
    passed = first.result_digest == again.result_digest and REQUIRED_RULES <= rules_seen
    digest = current_digest(setup, agent)
    status = setup["status"]
    if passed:
        status = SetupStatus.SANDBOX_READY.value
        setup = await store.update_setup(
            conn, setup["setup_id"], status=status, validated_digest=digest
        )
    await record(
        conn,
        tenant_id,
        name=common.DRY_RUN_COMPLETED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={
            "agent_id": agent_id,
            "fixture": fixture,
            "passed": passed,
            "result_digest": first.result_digest,
        },
    )
    return {
        "synthetic": True,
        "live_writes": 0,
        "passed": passed,
        "fixture": fixture,
        "policy_version": POLICY_VERSION,
        "policy_hash": policy_hash(),
        "rule_set_version": first.rule_set_version,
        "result_digest": first.result_digest,
        "rules_covered": sorted(rules_seen),
        "findings": [f.as_dict() for f in first.findings],
        "unknown": first.unknown_count,
        "setup_status": status,
    }


async def request_publish(
    conn: AsyncConnection, rt: Runtime, tenant_id: str, *, actor: str, agent_id: str
) -> dict[str, Any]:
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    setup = await _load(conn, agent_id, lock=True)
    agent = await _identity(conn, agent_id)
    blockers = _blockers(rt, agent)
    if blockers:
        raise ReadinessError(blockers[0], ", ".join(blockers))
    assert agent is not None
    digest = current_digest(setup, agent)
    if (
        setup["status"] != SetupStatus.SANDBOX_READY.value
        or setup["validated_digest"] != digest
        or setup["scope_digest"] != digest
    ):
        raise ReadinessError("SETUP_NOT_VALIDATED", "pass a dry run on the current scope first")
    approval = await store.insert_approval_request(
        conn,
        tenant_id,
        setup_id=setup["setup_id"],
        agent_id=agent_id,
        scope_digest=digest,
        requested_by=actor,
        expires_at=utcnow() + rt.approval_ttl,
    )
    setup_transition(SetupStatus.SANDBOX_READY, SetupStatus.PENDING_APPROVAL)
    updated = await store.update_setup(
        conn, setup["setup_id"], status=SetupStatus.PENDING_APPROVAL.value
    )
    await record(
        conn,
        tenant_id,
        name=common.PUBLISH_REQUESTED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={"agent_id": agent_id, "scope_digest": digest, "approval_id": approval["approval_id"]},
    )
    return view(updated, agent=agent, approval=approval)


async def approve_publish(
    conn: AsyncConnection, rt: Runtime, tenant_id: str, *, approver: str, agent_id: str
) -> dict[str, Any]:
    """A different person signs the exact digest. A changed scope, an expired request or the requester themselves cannot approve."""
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    setup = await _load(conn, agent_id, lock=True)
    if setup["status"] != SetupStatus.PENDING_APPROVAL.value:
        raise ReadinessError(
            "SETUP_INVALID_TRANSITION", f"nothing is waiting for approval ({setup['status']})"
        )
    approval = await store.latest_approval(conn, setup["setup_id"])
    agent = await _identity(conn, agent_id)
    blockers = _blockers(rt, agent)
    if blockers:
        raise ReadinessError(blockers[0], ", ".join(blockers))
    assert agent is not None
    now = utcnow()
    if approval is None or approval["approved_by"] is not None:
        raise ReadinessError("SETUP_INVALID_TRANSITION", "no open publish request")
    if approval["requested_by"] == approver:
        raise ReadinessError(
            "APPROVAL_NOT_INDEPENDENT", "the requester cannot approve their own request"
        )
    if approval["expires_at"] <= now:
        raise ReadinessError("APPROVAL_EXPIRED")
    if approval["scope_digest"] != current_digest(setup, agent):
        raise ReadinessError("APPROVAL_DIGEST_MISMATCH", "the scope changed since it was requested")
    await store.record_approval(conn, approval["approval_id"], approver, now)
    updated = await store.update_setup(conn, setup["setup_id"], status=SetupStatus.APPROVED.value)
    approval = await store.latest_approval(conn, setup["setup_id"])
    await record(
        conn,
        tenant_id,
        name=common.PUBLISH_APPROVED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=approver,
        data={"agent_id": agent_id, "scope_digest": setup["scope_digest"]},
    )
    return view(updated, agent=agent, approval=approval)


async def pause_setup(
    conn: AsyncConnection, tenant_id: str, *, actor: str, agent_id: str, reason: str
) -> dict[str, Any]:
    """The kill switch of this feature for one agent: its approval is withdrawn and its queued runs are canceled (AT-19)."""
    from zid_platform.modules.access_readiness.application import runs

    setup = await _load(conn, agent_id, lock=True)
    if setup["status"] == SetupStatus.PAUSED.value:
        return view(setup)
    invalidated = await store.invalidate_approvals(conn, setup["setup_id"], "PAUSED")
    updated = await store.update_setup(
        conn,
        setup["setup_id"],
        status=SetupStatus.PAUSED.value,
        paused=(actor, reason),
        clear_validation=True,
    )
    canceled = await runs.cancel_queued(conn, tenant_id, agent_id, "SETUP_PAUSED")
    await record(
        conn,
        tenant_id,
        name=common.SETUP_PAUSED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={
            "agent_id": agent_id,
            "reason": reason,
            "approvals_invalidated": invalidated,
            "queued_runs_canceled": canceled,
        },
    )
    return view(updated)


async def resume_setup(
    conn: AsyncConnection, rt: Runtime, tenant_id: str, *, actor: str, agent_id: str
) -> dict[str, Any]:
    """A paused setup goes back to DRAFT: it must pass the dry run and a new approval again before any run."""
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    setup = await _load(conn, agent_id, lock=True)
    setup_transition(SetupStatus(setup["status"]), SetupStatus.DRAFT)
    updated = await store.update_setup(
        conn, setup["setup_id"], status=SetupStatus.DRAFT.value, clear_validation=True
    )
    await record(
        conn,
        tenant_id,
        name=common.SETUP_RESUMED,
        event=False,
        aggregate_type="agent_setup",
        aggregate_id=setup["setup_id"],
        actor=actor,
        data={"agent_id": agent_id},
    )
    return view(updated)
