"""Running the agent: a bounded, server-owned state machine with a preflight and one idempotent write (ZID-AG-WF-001 sections 04, 09, 12; CR-091).

QUEUED -> PREFLIGHT -> RUNNING -> COMPLETED | NEEDS_REVIEW | FAILED | CANCELED, or DENIED at the preflight. No model plans anything: the steps are
fixed (preflight, read the metadata page, apply the rules, append proposals), each read and the write are authorized again against tenant,
actor, purpose, resource and policy, and every outcome, including a refusal, is a recorded policy decision and an evidence envelope that holds
digests and references only. The one write is a new review proposal; this module has no path that grants, revokes or edits anything (AT-10).
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.access_readiness.application import common
from zid_platform.modules.access_readiness.application.adapters import (
    LiveMetadataAdapter,
    MetadataAdapter,
)
from zid_platform.modules.access_readiness.application.common import Runtime, record, utcnow
from zid_platform.modules.access_readiness.application.setup import current_digest, scope_of
from zid_platform.modules.access_readiness.domain.policy import (
    ALLOWED_TOOLS,
    BLUEPRINT_ID,
    CREATE_PROPOSAL,
    PreflightFacts,
    ReadContext,
    ReadinessError,
    ReadRequest,
    RunState,
    check_selector,
    policy_hash,
    preflight_blockers,
    read_denial,
    run_definition_hash,
    run_transition,
    terminal_state,
)
from zid_platform.modules.access_readiness.domain.rules import (
    DEFAULT_WARNING_WINDOW_DAYS,
    PURPOSE,
    RULE_SET_VERSION,
    RunResult,
    digest,
    evaluate_run,
    facts_digest,
    proposal_key,
)
from zid_platform.modules.access_readiness.infrastructure import store
from zid_platform.modules.machine.ports import AgentIdentityStatus, agent_identity_status

log = logging.getLogger("zid.access_readiness")

# Whether the policy service permits the run now. It may raise (the service is down): that is a refusal, never a permit.
PolicyGate = Callable[[AsyncConnection, str, str], Awaitable[bool]]


class _Stop(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_view(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": run["run_id"],
        "agent_id": run["agent_principal_id"],
        "state": run["state"],
        "attempt": run["attempt"],
        "mode": run["mode"],
        "purpose": run["purpose"],
        "resource_selector": run["resource_selector"],
        "denial_codes": list(run["denial_codes"] or []),
        "summary": run["summary"],
        "rule_set_version": run["rule_set_version"],
        "policy_hash": run["policy_hash"],
        "run_definition_hash": run["run_definition_hash"],
        "region": run["home_region"],
        "trace_id": run["trace_id"],
        "worker_attestation_id": run["worker_attestation_id"],
        "created_at": run["created_at"].isoformat(),
        "terminal_at": run["terminal_at"].isoformat() if run["terminal_at"] else None,
        "links": {
            "self": f"/v1/access-readiness/agents/{run['agent_principal_id']}/runs/{run['run_id']}"
        },
    }


# ---------------------------------------------------------------- request


async def start_run(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    *,
    actor: str,
    agent_id: str,
    idempotency_key: str | None,
    purpose: str,
    selector: dict[str, Any],
    mode: str,
    trace_id: str,
) -> tuple[dict[str, Any], bool]:
    """Accept a run request and queue it. The same Idempotency-Key returns the same run. Whether it may actually run is the preflight's
    answer, so a refused run is a recorded DENIED run and not a missing one."""
    if not rt.enabled:
        raise ReadinessError("FEATURE_DISABLED")
    if not idempotency_key or not 8 <= len(idempotency_key) <= 128:
        raise ReadinessError(
            "IDEMPOTENCY_KEY_REQUIRED", "send an Idempotency-Key of 8 to 128 characters"
        )
    if purpose != PURPOSE:
        raise ReadinessError("PURPOSE_NOT_ALLOWED")
    if mode != "manual":
        raise ReadinessError("MODE_NOT_ALLOWED", "scheduled runs wait for the Q2 approval")
    setup = await store.load_setup(conn, agent_id)
    if setup is None:
        raise ReadinessError("SETUP_NOT_FOUND")
    run, created = await store.insert_run(
        conn,
        tenant_id,
        agent_id=agent_id,
        setup_id=setup["setup_id"],
        idempotency_key_hash=hashlib.sha256(idempotency_key.encode()).hexdigest(),
        request_hash=digest(["run", agent_id, purpose, selector, mode]),
        run_definition_hash=run_definition_hash(
            tenant_id=tenant_id,
            agent_id=agent_id,
            setup_digest=setup["scope_digest"],
            selector=selector,
            mode=mode,
            warning_window_days=int(setup["warning_window_days"]),
        ),
        policy_hash=policy_hash(),
        rule_set_version=RULE_SET_VERSION,
        purpose=purpose,
        selector=selector,
        warning_window_days=int(setup["warning_window_days"]),
        home_region=setup["home_region"],
        initiated_by=actor,
        trace_id=trace_id,
    )
    if created:
        await record(
            conn,
            tenant_id,
            name=common.RUN_REQUESTED,
            event=False,
            aggregate_type="agent_run",
            aggregate_id=run["run_id"],
            actor=actor,
            data={"agent_id": agent_id, "run_definition_hash": run["run_definition_hash"]},
        )
    return run, created


# ---------------------------------------------------------------- preflight


async def _preflight(
    conn: AsyncConnection, rt: Runtime, tenant_id: str, run: dict[str, Any], gate: PolicyGate | None
) -> tuple[list[str], AgentIdentityStatus | None, dict[str, Any] | None]:
    """Every fact a run depends on, read now from the authoritative records. A fact that cannot be read says no."""
    real = utcnow()
    agent_id = run["agent_principal_id"]
    agent = await agent_identity_status(conn, agent_id, now=real)
    setup = await store.load_setup(conn, agent_id)
    approval = await store.latest_approval(conn, setup["setup_id"]) if setup else None
    extra: list[str] = []
    available = True
    if gate is not None:
        try:
            if not await gate(conn, tenant_id, agent_id):
                extra.append("POLICY_DENIED")
        except Exception:  # noqa: BLE001 - a policy service that cannot answer never permits anything
            available = False
    if run["policy_hash"] != policy_hash():
        extra.append("POLICY_CHANGED")
    if setup is not None:
        try:
            check_selector(
                run["resource_selector"],
                tenant_id=tenant_id,
                home_region=setup["home_region"],
                scope=scope_of(setup),
            )
        except ReadinessError as e:
            extra.append(e.code)
    digest_now = current_digest(setup, agent) if setup and agent else None
    approved = approval is not None and approval["approved_by"] is not None
    facts = PreflightFacts(
        feature_enabled=rt.enabled,
        tenant_match=run["tenant_id"] == tenant_id,
        region_supported=rt.region_supported,
        region_match=setup is not None and setup["home_region"] == rt.region == run["home_region"],
        agent_found=agent is not None,
        agent_status=agent.status if agent else None,
        attested=bool(agent and agent.attested),
        sponsor_active=bool(agent and agent.sponsor_active),
        blueprint_approved=bool(agent and agent.blueprint_approved),
        blueprint_allowed=bool(agent and agent.blueprint_id == BLUEPRINT_ID),
        tools_allowed=bool(
            agent and agent.allowed_tools and set(agent.allowed_tools) <= ALLOWED_TOOLS
        ),
        agent_expired=bool(agent and agent.expires_at is not None and agent.expires_at <= real),
        setup_status=setup["status"] if setup else None,
        approval_valid=(True if approved else None),
        approval_expired=bool(approval and approval["expires_at"] <= real),
        digest_matches=bool(
            approved
            and approval is not None
            and setup is not None
            and digest_now == approval["scope_digest"] == setup["scope_digest"]
        ),
        policy_available=available,
    )
    blockers = preflight_blockers(facts)
    return blockers + [c for c in extra if c not in blockers], agent, setup


# ---------------------------------------------------------------- the run


async def _decide(
    conn: AsyncConnection,
    run: dict[str, Any],
    *,
    action: str,
    resource: str,
    outcome: str,
    reason: str,
    purpose: str | None = None,
    actor: str | None = None,
) -> None:
    await store.append_decision(
        conn,
        run["tenant_id"],
        run_id=run["run_id"],
        actor=actor or run["agent_principal_id"],
        subject=f"tenant:{run['tenant_id']}",
        resource=resource,
        action=action,
        purpose=purpose or run["purpose"],
        outcome=outcome,
        reason_code=reason,
        policy_hash=run["policy_hash"],
        trace_id=run["trace_id"],
    )


async def _evidence(
    conn: AsyncConnection, run: dict[str, Any], event_type: str, source: str, payload: Any
) -> dict[str, Any]:
    return await store.append_evidence(
        conn,
        run["tenant_id"],
        run_id=run["run_id"],
        event_type=event_type,
        source_id=source,
        source_version=RULE_SET_VERSION,
        payload_digest=digest(payload),
        region=run["home_region"],
    )


def _event_data(run: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "agent_id": run["agent_principal_id"],
        "run_id": run["run_id"],
        "policy_hash": run["policy_hash"],
        "event_time": utcnow().isoformat(),
        "trace_id": run["trace_id"],
        "region": run["home_region"],
        "schema_version": 1,
        **extra,
    }


async def _deny(conn: AsyncConnection, run: dict[str, Any], codes: list[str]) -> None:
    for code in codes:
        await _decide(
            conn,
            run,
            action="run.start",
            resource=f"agent:{run['agent_principal_id']}",
            outcome="DENY",
            reason=code,
        )
    await _evidence(conn, run, "preflight_denied", "preflight", {"codes": codes})
    run_transition(RunState.PREFLIGHT, RunState.DENIED)
    await store.set_run_state(
        conn,
        run["run_id"],
        RunState.DENIED.value,
        denial_codes=codes,
        summary={"privileges_changed": 0, "records_read": 0, "proposals_created": 0},
        terminal=True,
    )
    await record(
        conn,
        run["tenant_id"],
        name=common.EVENT_RUN_DENIED,
        event=True,
        aggregate_type="agent_run",
        aggregate_id=run["run_id"],
        actor=run["agent_principal_id"],
        actor_type="AGENT",
        outcome="DENIED",
        reason_code=codes[0],
        data=_event_data(run, denial_codes=",".join(codes)),
    )


async def _end(
    conn: AsyncConnection,
    run: dict[str, Any],
    state: RunState,
    *,
    reason: str,
    summary: dict[str, Any],
    codes: list[str] | None = None,
) -> None:
    run_transition(RunState.RUNNING, state)
    await _evidence(
        conn,
        run,
        "run_" + state.value.lower(),
        "run",
        {"state": state.value, "reason": reason, **summary},
    )
    await store.set_run_state(
        conn, run["run_id"], state.value, denial_codes=codes or [], summary=summary, terminal=True
    )
    await record(
        conn,
        run["tenant_id"],
        name=common.EVENT_RUN_COMPLETED,
        event=True,
        aggregate_type="agent_run",
        aggregate_id=run["run_id"],
        actor=run["agent_principal_id"],
        actor_type="AGENT",
        outcome="SUCCESS" if state in (RunState.COMPLETED, RunState.NEEDS_REVIEW) else "FAILURE",
        reason_code=None if state in (RunState.COMPLETED, RunState.NEEDS_REVIEW) else reason,
        data=_event_data(
            run,
            state=state.value,
            reason=reason,
            **{k: v for k, v in summary.items() if isinstance(v, (int, bool, str))},
        ),
    )


async def execute_run(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    run_id: str,
    *,
    adapter: MetadataAdapter | None = None,
    gate: PolicyGate | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one queued run to its end. `now` is the trusted clock the rules use (the server clock by default)."""
    run = await store.load_run(conn, run_id, lock=True)
    if run is None:
        raise ReadinessError("RUN_NOT_FOUND")
    if run["state"] != RunState.QUEUED.value:
        return run
    run_transition(RunState.QUEUED, RunState.PREFLIGHT)
    await store.set_run_state(conn, run_id, RunState.PREFLIGHT.value, started=True, attempt=True)
    blockers, agent, setup = await _preflight(conn, rt, tenant_id, run, gate)
    if blockers or agent is None or setup is None:
        await _deny(conn, run, blockers or ["AGENT_NOT_FOUND"])
        log.info("access readiness run %s denied", run_id)
        return await _reload(conn, run_id)
    await _decide(
        conn,
        run,
        action="run.start",
        resource=f"agent:{run['agent_principal_id']}",
        outcome="ALLOW",
        reason="PREFLIGHT_PASSED",
    )
    await _evidence(
        conn,
        run,
        "preflight_passed",
        "preflight",
        {"agent": run["agent_principal_id"], "attested": True, "sponsor_active": True},
    )
    run_transition(RunState.PREFLIGHT, RunState.RUNNING)
    await store.set_run_state(
        conn, run_id, RunState.RUNNING.value, attestation_id=agent.attestation_id
    )

    selector = check_selector(
        run["resource_selector"],
        tenant_id=tenant_id,
        home_region=setup["home_region"],
        scope=scope_of(setup),
    )
    ctx = ReadContext(
        run["tenant_id"],
        run["agent_principal_id"],
        run["purpose"],
        frozenset({selector.kind, "agent"}),
        run["policy_hash"],
    )

    async def authorize(tool: str, resource: str) -> None:

        reason = read_denial(
            ctx,
            ReadRequest(
                tenant_id, run["agent_principal_id"], run["purpose"], resource, tool, policy_hash()
            ),
        )
        await _decide(
            conn,
            run,
            action=tool,
            resource=resource,
            outcome="DENY" if reason else "ALLOW",
            reason=reason or "READ_PERMITTED",
        )
        if reason:
            raise ReadinessError(reason)

    try:
        facts = await (adapter or LiveMetadataAdapter()).read_mandates(
            conn,
            authorize=authorize,
            cell_uuid=rt.cell_uuid,
            limit=selector.max_records,
            now=utcnow(),
        )
    except ReadinessError as e:
        await _end(
            conn,
            run,
            RunState.FAILED,
            reason=e.code,
            summary=_summary(0, None, 0, 0),
            codes=[e.code],
        )
        return await _reload(conn, run_id)
    except Exception:  # noqa: BLE001 - the adapter is down or unreadable: fail closed, write nothing
        await _decide(
            conn,
            run,
            action="read_mandate_metadata",
            resource="mandates:page",
            outcome="DENY",
            reason="DEPENDENCY_UNAVAILABLE",
        )
        await _end(
            conn,
            run,
            RunState.FAILED,
            reason="DEPENDENCY_UNAVAILABLE",
            summary=_summary(0, None, 0, 0),
            codes=["DEPENDENCY_UNAVAILABLE"],
        )
        log.warning("access readiness run %s failed: adapter unavailable", run_id)
        return await _reload(conn, run_id)
    await _evidence(
        conn,
        run,
        "metadata_read",
        "mandate-metadata/1",
        {"count": len(facts), "facts": facts_digest(facts)},
    )

    clock = now if now is not None else utcnow()
    result = evaluate_run(
        facts,
        clock,
        timedelta(days=int(run["warning_window_days"]) or DEFAULT_WARNING_WINDOW_DAYS),
        rule_set_version=run["rule_set_version"],
    )
    rules_event = await _evidence(
        conn,
        run,
        "rules_evaluated",
        result.rule_set_version,
        {"result": result.result_digest, "complete": result.complete},
    )

    created = existing = 0
    try:
        async with conn.begin_nested():  # a stopped run discards every proposal it had begun
            for finding in result.findings:
                again, _, _ = await _preflight(
                    conn, rt, tenant_id, run, gate
                )  # the only write is authorized again, each time
                if again:
                    raise _Stop(again[0])
                if (
                    CREATE_PROPOSAL not in ALLOWED_TOOLS
                ):  # pragma: no cover - the allowlist is a constant
                    raise _Stop("TOOL_NOT_ALLOWED")
                key = proposal_key(tenant_id, finding.target_ref, finding)
                proposal, is_new = await store.insert_proposal(
                    conn,
                    tenant_id,
                    agent_id=run["agent_principal_id"],
                    run_id=run_id,
                    run_step_id=f"propose:{key[7:31]}",
                    proposal_key=key,
                    target_ref=finding.target_ref,
                    rule_id=finding.rule_id,
                    rule_version=finding.rule_version,
                    finding_code=finding.finding_code,
                    severity=finding.severity,
                    confidence=finding.confidence,
                    evidence_refs=[*finding.evidence_refs, f"evidence:{rules_event['event_id']}"],
                )
                await _decide(
                    conn,
                    run,
                    action=CREATE_PROPOSAL,
                    resource=f"proposal:{proposal['proposal_id']}",
                    outcome="ALLOW",
                    reason="PROPOSAL_CREATED" if is_new else "PROPOSAL_DUPLICATE",
                )
                if is_new:
                    created += 1
                    await _evidence(
                        conn,
                        run,
                        "proposal_created",
                        "review-proposal",
                        {"proposal": proposal["proposal_id"], "key": key},
                    )
                    await record(
                        conn,
                        tenant_id,
                        name=common.EVENT_PROPOSAL_CREATED,
                        event=True,
                        aggregate_type="agent_review",
                        aggregate_id=proposal["proposal_id"],
                        actor=run["agent_principal_id"],
                        actor_type="AGENT",
                        data=_event_data(
                            run,
                            proposal_id=proposal["proposal_id"],
                            rule_id=finding.rule_id,
                            finding_code=finding.finding_code,
                        ),
                    )
                else:
                    existing += 1
    except _Stop as stop:
        await _decide(
            conn,
            run,
            action=CREATE_PROPOSAL,
            resource="proposal:*",
            outcome="DENY",
            reason=stop.code,
        )
        await _end(
            conn,
            run,
            RunState.CANCELED,
            reason=stop.code,
            summary=_summary(len(facts), result, 0, 0),
            codes=[stop.code],
        )
        return await _reload(conn, run_id)
    except Exception:  # noqa: BLE001 - a write that fails leaves no half-built proposal behind
        await _decide(
            conn,
            run,
            action=CREATE_PROPOSAL,
            resource="proposal:*",
            outcome="DENY",
            reason="DEPENDENCY_UNAVAILABLE",
        )
        await _end(
            conn,
            run,
            RunState.FAILED,
            reason="DEPENDENCY_UNAVAILABLE",
            summary=_summary(len(facts), result, 0, 0),
            codes=["DEPENDENCY_UNAVAILABLE"],
        )
        log.warning("access readiness run %s failed while writing proposals", run_id)
        return await _reload(conn, run_id)
    state = terminal_state(proposals=len(result.findings), complete=result.complete)
    await _end(
        conn,
        run,
        state,
        reason="RULES_APPLIED",
        summary=_summary(len(facts), result, created, existing),
    )
    log.info("access readiness run %s %s", run_id, state.value)
    return await _reload(conn, run_id)


def _summary(read: int, result: RunResult | None, created: int, existing: int) -> dict[str, Any]:
    return {
        "records_read": read,
        "findings": len(result.findings) if result else 0,
        "unknown": result.unknown_count if result else 0,
        "complete": bool(result and result.complete),
        "proposals_created": created,
        "proposals_existing": existing,
        "privileges_changed": 0,
        "result_digest": result.result_digest if result else "",
    }


async def _reload(conn: AsyncConnection, run_id: str) -> dict[str, Any]:
    run = await store.load_run(conn, run_id)
    assert run is not None
    return run


# ---------------------------------------------------------------- retry, cancel, drain


async def retry_run(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    run_id: str,
    *,
    adapter: MetadataAdapter | None = None,
    gate: PolicyGate | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A FAILED run is tried again under the same run id with a full preflight: a stopped agent, a changed policy or a missing approval
    is a denial, never a blind replay. Proposals are idempotent, so a retry cannot duplicate one."""
    run = await store.load_run(conn, run_id, lock=True)
    if run is None:
        raise ReadinessError("RUN_NOT_FOUND")
    if run["state"] != RunState.FAILED.value:
        raise ReadinessError("RUN_NOT_RETRYABLE", f"a {run['state']} run is final")
    await store.set_run_state(conn, run_id, RunState.QUEUED.value)
    return await execute_run(conn, rt, tenant_id, run_id, adapter=adapter, gate=gate, now=now)


async def cancel_queued(conn: AsyncConnection, tenant_id: str, agent_id: str, reason: str) -> int:
    """Queued work of an agent is discarded, not run later under an authorization that has been withdrawn (AT-19)."""
    n = 0
    for run in await store.list_runs(conn, agent_id, limit=500):
        if run["state"] != RunState.QUEUED.value:
            continue
        run_transition(RunState.QUEUED, RunState.CANCELED)
        await _decide(
            conn,
            run,
            action="run.start",
            resource=f"agent:{agent_id}",
            outcome="DENY",
            reason=reason,
        )
        await store.set_run_state(
            conn,
            run["run_id"],
            RunState.CANCELED.value,
            denial_codes=[reason],
            terminal=True,
            summary={"privileges_changed": 0, "records_read": 0, "proposals_created": 0},
        )
        n += 1
    return n


async def execute_queued(
    conn: AsyncConnection,
    rt: Runtime,
    tenant_id: str,
    *,
    adapter: MetadataAdapter | None = None,
    gate: PolicyGate | None = None,
) -> list[dict[str, Any]]:
    """The worker's pass over this tenant's queued runs. Each one gets its own preflight, so a run queued before a pause is denied now."""
    return [
        await execute_run(conn, rt, tenant_id, run_id, adapter=adapter, gate=gate)
        for run_id in await store.queued_run_ids(conn)
    ]


async def invoke_tool(
    conn: AsyncConnection, tenant_id: str, run_id: str, tool: str, resource: str
) -> None:
    """The gateway check for any tool name the agent asks for (AT-04): only the three allowlisted tools are ever permitted, and a refusal
    is a recorded policy decision."""
    run = await store.load_run(conn, run_id)
    if run is None:
        raise ReadinessError("RUN_NOT_FOUND")

    ctx = ReadContext(
        run["tenant_id"],
        run["agent_principal_id"],
        run["purpose"],
        frozenset({"mandates", "agent", "proposal"}),
        run["policy_hash"],
    )
    reason = read_denial(
        ctx,
        ReadRequest(
            tenant_id, run["agent_principal_id"], run["purpose"], resource, tool, policy_hash()
        ),
    )
    await _decide(
        conn,
        run,
        action=tool,
        resource=resource,
        outcome="DENY" if reason else "ALLOW",
        reason=reason or "TOOL_PERMITTED",
    )
    if reason:
        raise ReadinessError(reason)
