"""Running the agent: a bounded, server-owned state machine with a preflight and one idempotent write (ZID-AG-WF-001 sections 04, 09, 12; CR-091).

QUEUED -> PREFLIGHT -> RUNNING -> COMPLETED | NEEDS_REVIEW | FAILED | CANCELED, or DENIED at the preflight. No model plans anything: the steps are
fixed (preflight, read the metadata page, apply the rules, append proposals), each read and the write are authorized again against tenant,
actor, purpose, resource and policy, and every outcome, including a refusal, is a recorded policy decision and an evidence envelope that holds
digests and references only. The one write is a new review proposal; this module has no path that grants, revokes or edits anything (AT-10).
"""

from __future__ import annotations

import functools
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
from zid_platform.modules.access_readiness.application.explain import (
    PROMPT_VERSION,
    explain,
    facts_of,
)
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
from zid_platform.modules.machine.ports import (
    AgentIdentityStatus,
    MachineError,
    WorkloadLease,
    agent_identity_status,
    issue_workload_lease,
    lease_problems,
    revoke_workload_lease,
)
from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert

log = logging.getLogger("zid.access_readiness")

# who the run's short-lived credential is for: the read-only metadata adapter and the proposal write of this module, nothing else
CREDENTIAL_AUDIENCE = "zid:access-readiness:metadata-adapter"

# Whether the policy service permits the run now. It may raise (the service is down): that is a refusal, never a permit.
PolicyGate = Callable[[AsyncConnection, str, str], Awaitable[bool]]


class _Stop(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def run_view(run: dict[str, Any]) -> dict[str, Any]:
    summary = run["summary"]
    explanation = summary.get("explanation") if isinstance(summary, dict) else None
    if isinstance(summary, dict) and "explanation" in summary:
        summary = {k: v for k, v in summary.items() if k != "explanation"}
    return {
        "run_id": run["run_id"],
        "agent_id": run["agent_principal_id"],
        "state": run["state"],
        "attempt": run["attempt"],
        "mode": run["mode"],
        "purpose": run["purpose"],
        "resource_selector": run["resource_selector"],
        "denial_codes": list(run["denial_codes"] or []),
        "summary": summary,
        # present only when the setup opted in and the deployment allows it; never authoritative (the summary is)
        "explanation": explanation,
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
    await _notify_sponsor(conn, run, RunState.DENIED, {})  # the denial envelope stays the last one
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


async def _notify_sponsor(
    conn: AsyncConnection, run: dict[str, Any], state: RunState, summary: dict[str, Any]
) -> None:
    """Section 09 step 9: tell the agent's sponsor the run ended, through the notification module. The run id, its state and counts
    only: no person, no mandate, no finding detail. A notification that cannot be raised never changes the run's result."""
    count = {
        k: int(summary.get(k) or 0)
        for k in ("records_read", "findings", "proposals_created", "privileges_changed")
    }
    body = (
        f"Run {run['run_id']} ended {state.value}. Records checked: {count['records_read']}. "
        f"Findings: {count['findings']}. New review proposals: {count['proposals_created']}. "
        f"Privileges changed: {count['privileges_changed']}."
    )
    attention = state is not RunState.COMPLETED
    draft = AlertDraft(
        severity=Severity.MEDIUM if attention else Severity.INFO,
        kind=common.RUN_FINISHED_ALERT,
        title="An access readiness run finished",
        body=body,
        object_type="agent_run",
        object_ref=run["run_id"],
        href=f"/admin/agents/readiness/{run['agent_principal_id']}/runs/{run['run_id']}",
        recommended_action="For the agent's sponsor: open the run and review its proposals. Nothing was changed.",
        is_task=state is RunState.NEEDS_REVIEW,
        mandatory=False,
        correlation_key=f"{common.RUN_FINISHED_ALERT}:{run['run_id']}:{state.value}:{run['attempt']}",
    )
    try:
        async with conn.begin_nested():
            await raise_alert(conn, run["tenant_id"], draft, now=utcnow())
        outcome = "sponsor_notified"
    except Exception:  # noqa: BLE001 - the result stands; the missed notice is itself evidence
        outcome = "sponsor_notification_failed"
        log.warning("access readiness run %s: the sponsor notification failed", run["run_id"])
    await _evidence(
        conn,
        run,
        outcome,
        "notification",
        {"kind": draft.kind, "state": state.value, "body": body},
    )


async def _release(
    conn: AsyncConnection, run: dict[str, Any], lease: WorkloadLease | None, reason: str
) -> None:
    """Section 09 step 9, 'revoke lease': the run's credential ends with the run, whatever the outcome."""
    if lease is None:
        return
    if await revoke_workload_lease(conn, run["tenant_id"], lease, reason=reason):
        await _evidence(
            conn,
            run,
            "credential_revoked",
            "workload-lease",
            {"lease": lease.lease_id, "reason": reason},
        )


async def _end(
    conn: AsyncConnection,
    run: dict[str, Any],
    state: RunState,
    *,
    reason: str,
    summary: dict[str, Any],
    codes: list[str] | None = None,
    lease: WorkloadLease | None = None,
) -> None:
    run_transition(RunState.RUNNING, state)
    await _release(conn, run, lease, state.value)
    await _notify_sponsor(conn, run, state, summary)  # the run's own envelope stays the last one
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
    # Section 09 step 3 (AT-07): a short-lived, audience-restricted, purpose-bound credential from the machine module's workload
    # issuance decision. An unattested, revoked, quarantined or rebound worker gets none, and the run is denied. Never stored or logged.
    try:
        async with conn.begin_nested():
            lease = await issue_workload_lease(
                conn,
                tenant_id,
                run["agent_principal_id"],
                audience=CREDENTIAL_AUDIENCE,
                purpose=run["purpose"],
                scope=ALLOWED_TOOLS,
            )
    except MachineError as e:
        await _deny(conn, run, list(dict.fromkeys(["CREDENTIAL_DENIED", e.code])))
        log.info("access readiness run %s denied: no workload credential", run_id)
        return await _reload(conn, run_id)
    except Exception:  # noqa: BLE001 - an issuer that cannot answer never issues anything
        await _deny(conn, run, ["CREDENTIAL_DENIED", "DEPENDENCY_UNAVAILABLE"])
        log.warning("access readiness run %s denied: the credential issuer is unavailable", run_id)
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
    await _evidence(
        conn,
        run,
        "credential_issued",
        "workload-lease",
        {
            "lease": lease.lease_id,
            "audience": lease.audience,
            "purpose": lease.purpose,
            "scope": sorted(lease.scope),
            "binding": lease.binding_id,
            "expires_at": lease.expires_at.isoformat(),
        },
    )
    run_transition(RunState.PREFLIGHT, RunState.RUNNING)
    await store.set_run_state(
        conn, run_id, RunState.RUNNING.value, attestation_id=agent.attestation_id
    )
    # every way out of a running run revokes its credential
    end = functools.partial(_end, lease=lease)

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

    async def credential_stop(use: str) -> str | None:
        """None while the run's credential may be used for this step; else why it stopped (revoked or expired: no broader one)."""
        problems = await lease_problems(
            conn, lease, audience=CREDENTIAL_AUDIENCE, purpose=run["purpose"], use=use
        )
        if not problems:
            return None
        return "CREDENTIAL_EXPIRED" if problems == ["CREDENTIAL_EXPIRED"] else "CREDENTIAL_REVOKED"

    async def authorize(tool: str, resource: str) -> None:
        reason = read_denial(
            ctx,
            ReadRequest(
                tenant_id, run["agent_principal_id"], run["purpose"], resource, tool, policy_hash()
            ),
        ) or await credential_stop(tool)
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
        await end(
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
        await end(
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
                stopped = await credential_stop(CREATE_PROPOSAL)
                if stopped:
                    raise _Stop(stopped)
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
        await end(
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
        await end(
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
    summary = _summary(len(facts), result, created, existing)
    if rt.ai_explain and bool(setup.get("ai_explanation")):
        # Section 09 step 8: decided above, explained here. Only codes and counts go out, the text is verified against them, and
        # anything else (off, no gateway, an error, a timeout, an unsupported claim) is the fixed template. The result does not move.
        explanation = await explain(
            facts_of(
                result, state=state.value, records=len(facts), created=created, existing=existing
            ),
            rt.explainer,
            timeout_seconds=rt.ai_timeout_seconds,
        )
        summary["explanation"] = explanation.as_dict()
        await _evidence(
            conn,
            run,
            "explanation_attached",
            PROMPT_VERSION,
            {
                "source": explanation.source,
                "fallback_reason": explanation.fallback_reason,
                "facts": explanation.facts_digest,
                "text": digest(explanation.text),
            },
        )
    await end(conn, run, state, reason="RULES_APPLIED", summary=summary)
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
