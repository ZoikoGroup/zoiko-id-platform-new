"""The DAARA reference agent service (ZID-AG-WF-001 v1.0; CR-106).

A governed, deterministic runner. A manual run goes: governance preflight (freeze, active agent and sponsor, the approved policy for its
exact hash, run quotas) -> a governed read of mandate metadata and agent identity status -> six versioned rules -> a governed append of
review proposals -> evidence -> a plain-language explanation. The explanation is fixed text; a model may rewrite it only if the company
approved a provider, the policy turned it on, a budget reserve was granted, and the rewrite then passes the faithfulness checker.

The one write DAARA may make is a row in the human review queue. Nothing in this module can change a mandate, a grant, a role or a
credential, and a human who opens a proposal still has to go through the governed access workflow, which asks for its own permissions.

A refusal is a result, not an exception (the evidence row is written first).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.machine.application import agents as agent_service
from zid_platform.modules.machine.application import governance as gov
from zid_platform.modules.machine.application.governance import GovernanceError, Outcome
from zid_platform.modules.machine.domain import daara as d
from zid_platform.modules.machine.domain import governance as g
from zid_platform.modules.machine.domain.agents import AgentStatus, ToolStatus
from zid_platform.modules.machine.infrastructure import agent_store, machine_store
from zid_platform.modules.machine.infrastructure import daara_store as store
from zid_platform.modules.machine.infrastructure import governance_store as gstore
from zid_platform.modules.machine.infrastructure.governance_config import config
from zid_platform.modules.mandate.ports import metadata_projection
from zid_platform.modules.notification.ports import AlertDraft, Severity, raise_alert
from zid_platform.modules.principal.ports import (
    Principal,
    PrincipalStatus,
    PrincipalType,
    create_principal,
)

Explainer = Callable[[dict[str, Any]], Awaitable[str]]
_EXPLAINERS: dict[str, Explainer] = {}
DISMISS_REASONS = frozenset({"NOT_A_RISK", "ALREADY_HANDLED", "DUPLICATE", "ACCEPTED_RISK"})
NOTICE = "No access permissions changed. The governed access workflow needs its own permissions, a fresh policy decision and an explicit human action."


def register_explainer(name: str, fn: Explainer) -> None:
    """Make a model provider callable. None is registered by default, so nothing ever leaves the platform until an operator wires one
    AND a company approves it by name AND the policy turns it on."""
    _EXPLAINERS[name] = fn


def clear_explainers() -> None:
    _EXPLAINERS.clear()


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


# ====================================================================== template and setup


def template_view() -> dict[str, Any]:
    return {
        "template": d.TEMPLATE,
        "purpose": d.PURPOSE,
        "rule_set_version": d.RULE_SET_VERSION,
        "tools": [
            {
                "tool_id": s.tool_id,
                "action": s.action,
                "side_effect": s.side_effect,
                "resource": s.resource_kind,
                "why": s.why,
            }
            for s in d.TOOLS.values()
        ],
        "does": [
            "Reads tenant-approved mandate and agent metadata",
            "Flags expired and expiring mandates, missing sponsors, scope variance and evidence gaps",
            "Adds proposals to a human review queue",
        ],
        "does_not": [
            "Decide KYC or KYB",
            "Make a biometric or qualification judgement",
            "Recover an account",
            "Grant or revoke a role or credential",
            "Edit a policy",
            "Call a privileged tool",
            "Spend money outside its approved budget",
        ],
        "release_default": "OFF until the platform owners switch governance to enforce and a company publishes it",
    }


def _server_principal_id(tenant_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"zoiko-daara-adapter:{tenant_id}"))


async def ensure_tools(
    conn: AsyncConnection, tenant_id: str, actor: str, home_cell: str
) -> list[str]:
    """Register (once) the three template tools under a per-company platform adapter principal, and activate them. Idempotent."""
    server = _server_principal_id(tenant_id)
    if await agent_service._principal(conn, server) is None:
        await create_principal(
            conn,
            Principal(server, tenant_id, PrincipalType.SERVICE, PrincipalStatus.ACTIVE, home_cell),
        )
    registry = await machine_store.load_tools(conn)
    changed: list[str] = []
    for spec in d.TOOLS.values():
        if spec.tool_id not in registry:
            await agent_service.register_tool(
                conn,
                tenant_id,
                tool_id=spec.tool_id,
                version=d.RULE_SET_VERSION,
                server_principal_id=server,
                canonical_action=spec.action,
                default_risk_class=spec.risk,
                side_effect=spec.side_effect,
                requires_mandate=False,
                requires_human_approval=False,
                actor=actor,
            )
            changed.append(spec.tool_id)
        if (await machine_store.load_tools(conn))[spec.tool_id].status is ToolStatus.DRAFT:
            await agent_service.change_tool(
                conn,
                tenant_id,
                spec.tool_id,
                change="activate",
                reason="DAARA_TEMPLATE",
                actor=actor,
            )
    return changed


async def _setup_blockers(
    conn: AsyncConnection, tenant_id: str, agent_id: str, inst: dict[str, Any]
) -> list[str]:
    """Everything the authoritative compiler checks before a setup may be called valid (AT-02, AT-03, AT-04)."""
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        return ["AGENT_NOT_FOUND"]
    out: list[str] = []
    if agent.status in (AgentStatus.REVOKED, AgentStatus.RETIRED):
        out.append("AGENT_NOT_GOVERNABLE")
    if not await agent_service.sponsor_is_active(conn, agent.sponsor_principal_id):
        out.append("SPONSOR_INVALID")  # AT-02: no active accountable sponsor, no setup
    baseline = await gstore.load_baseline(conn, tenant_id)
    if baseline is None:
        out.append("POLICY_UNAVAILABLE")
    else:
        b = baseline[0]
        if inst["region"] not in b.allowed_regions:
            out.append("REGION_DENIED")  # AT-03: a region the company has not allowed
        missing = sorted(set(d.TOOLS) - set(b.allowed_tools))
        if missing:
            out.append("BASELINE_TOOLS_MISSING:" + ",".join(missing))
        if inst["schedule"] == "DAILY" and not b.schedules_allowed:
            out.append(
                "SCHEDULE_NOT_PERMITTED"
            )  # a schedule needs the company's schedule policy as well as the agent's
        if inst["ai_explain"] and not b.ai_providers:
            out.append("AI_PROVIDER_NOT_APPROVED")
    unlisted = sorted(set(d.TOOLS) - set(agent.allowed_tools))
    if unlisted:
        out.append(
            "AGENT_TOOLS_MISSING:" + ",".join(unlisted)
        )  # AT-04: any other tool is denied; these three must be on its list
    registry = await machine_store.load_tools(conn)
    for t in d.TOOLS:
        tool = registry.get(t)
        if (
            tool is None
            or tool.status is not ToolStatus.ACTIVE
            or g.hard_denied(tool.canonical_action, tool.side_effect.value)
        ):
            out.append(f"TOOL_NOT_REGISTERED:{t}")
    return out


def _config_of(i: dict[str, Any]) -> str:
    return d.config_hash(
        label=i["display_label"],
        warn_days=i["warn_days"],
        region=i["region"],
        ai_explain=i["ai_explain"],
        schedule=i["schedule"],
    )


async def get_setup(
    conn: AsyncConnection, tenant_id: str, agent_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise GovernanceError("AGENT_NOT_FOUND", "this tenant has no such agent")
    inst = await store.get_instance(conn, agent_id)
    base: dict[str, Any] = {
        "agent_id": agent_id,
        "template": template_view(),
        "enabled": inst is not None,
    }
    if inst is None:
        return {**base, "stage": "NOT_ENABLED", "blockers": [], "setup": None}
    policy = await gstore.active_policy(conn, agent_id)
    open_request = False
    if inst["policy_id"] is not None:
        for row in await gstore.list_requests(
            conn, agent_id=agent_id, states=("PENDING", "CLARIFICATION", "APPROVED"), limit=20
        ):
            if str(row["policy_id"]) == str(inst["policy_id"]):
                open_request = True
    stage = d.setup_stage(
        config=inst["config_hash"], validated=inst["validated_hash"], sandbox=inst["sandbox_passed_hash"], request_open=open_request,
        policy_active=policy is not None and agent.status is AgentStatus.ACTIVE,
    )  # fmt: skip
    blockers = await _setup_blockers(conn, tenant_id, agent_id, inst)
    gates = d.publish_blockers(
        config=inst["config_hash"],
        validated=inst["validated_hash"],
        sandbox=inst["sandbox_passed_hash"],
    )
    return {
        **base,
        "stage": stage,
        "blockers": blockers,
        "publish_blockers": [*blockers, *gates],
        "setup": {
            "label": inst["display_label"], "rationale": inst["rationale"], "warn_days": inst["warn_days"], "region": inst["region"],
            "ai_explain": inst["ai_explain"], "schedule": inst["schedule"], "config_hash": inst["config_hash"], "validated": inst["validated_hash"] == inst["config_hash"],
            "sandbox_passed": inst["sandbox_passed_hash"] == inst["config_hash"],
        },
        "effective_scope": {
            "permitted": [{"tool": s.tool_id, "resource": s.resource_kind, "purpose": d.PURPOSE, "region": inst["region"]} for s in d.TOOLS.values()],
            "blocked": sorted(g.HARD_DENY_ACTIONS)[:12],
            "note": "Permitted is what the template would request. Effective access only exists after the approved policy is applied.",
        },
        "policy": {"version": policy.version, "hash": g.policy_hash(policy)} if policy else None,
    }  # fmt: skip


async def save_setup(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, *, label: str, rationale: str | None, warn_days: int, region: str,
    ai_explain: bool, schedule: str = "MANUAL", now: datetime | None = None,
) -> dict[str, Any]:  # fmt: skip
    """Create or change a DAARA setup. The identity of the agent is never a field here, and a change voids a passed validation and a passed
    sandbox test (they are bound to the configuration hash)."""
    now = now or _now()
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise GovernanceError("AGENT_NOT_FOUND", "this tenant has no such agent")
    label = label.strip()
    if not 1 <= len(label) <= 80 or any(ord(c) < 32 for c in label):
        raise GovernanceError("LABEL_INVALID", "a descriptive name of 1 to 80 characters")
    if not 1 <= warn_days <= 90:
        raise GovernanceError("WARN_DAYS_INVALID", "the warning window is 1 to 90 days")
    if g.has_wildcard(region):
        raise GovernanceError("WILDCARD_OR_EMPTY", "region must be exact")
    sponsor = await agent_service._principal(conn, agent.sponsor_principal_id or agent_id)
    await ensure_tools(conn, tenant_id, actor, sponsor.home_cell if sponsor else "unknown")
    if schedule not in ("MANUAL", "DAILY"):
        raise GovernanceError("SCHEDULE_INVALID", "MANUAL or DAILY")
    cfg = d.config_hash(
        label=label, warn_days=warn_days, region=region, ai_explain=ai_explain, schedule=schedule
    )
    await store.save_instance(
        conn, tenant_id,
        {"agent_id": agent_id, "label": label, "rationale": (rationale or "").strip()[:500] or None, "warn_days": warn_days, "region": region,
         "ai_explain": ai_explain, "schedule": schedule, "config_hash": cfg, "created_by": actor},
    )  # fmt: skip
    await gov._event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="daara.setup_saved",
        outcome="INFO",
        actor=actor,
        now=now,
    )
    return await get_setup(conn, tenant_id, agent_id, now)


async def validate(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, now: datetime | None = None
) -> Outcome:
    """The authoritative compile: allowed scope, region and read adapter. Passing records the hash it passed for; failing records why."""
    now = now or _now()
    inst = await store.get_instance(conn, agent_id, lock=True)
    if inst is None:
        return Outcome(False, ("DAARA_NOT_ENABLED",))
    blockers = await _setup_blockers(conn, tenant_id, agent_id, inst)
    if blockers:
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.validate",
            outcome="DENY",
            actor=actor,
            code=blockers[0].split(":")[0],
            now=now,
        )
        return Outcome(False, tuple(blockers))
    await store.mark_validated(conn, agent_id, inst["config_hash"])
    await gov._event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="daara.validate",
        outcome="INFO",
        actor=actor,
        policy_hash=inst["config_hash"],
        now=now,
    )
    return Outcome(True, data={"validated": True, "config_hash": inst["config_hash"]})


# ====================================================================== the sandbox


async def dry_run(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, now: datetime | None = None, *,
    writer: Callable[[], None] | None = None,
) -> Outcome:  # fmt: skip
    """Run the real rules over the synthetic fixture. It reads nothing live and writes nothing: no run row, no proposal, no counter. The
    only trace is one evidence row. A write attempt (`writer`, used by the test that proves the guard) raises DryRunWriteAttempt, is recorded
    as a security incident and fails the test (AT-06). Passing binds to the configuration hash."""
    now = now or _now()
    inst = await store.get_instance(conn, agent_id, lock=True)
    if inst is None:
        return Outcome(False, ("DAARA_NOT_ENABLED",))
    if inst["validated_hash"] != inst["config_hash"]:
        return Outcome(False, ("NOT_VALIDATED",))
    mandates, agents, expected = d.synthetic_fixture(now)
    try:
        if writer is not None:
            writer()
        out = d.evaluate_rules(mandates, agents, now=now, warn_days=inst["warn_days"])
        found = {(f.rule_id, f.target_ref) for f in out.findings}
    except d.DryRunWriteAttempt:
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.dry_run",
            outcome="DENY",
            actor=actor,
            code="DRY_RUN_WRITE_ATTEMPT",
            now=now,
        )
        return Outcome(False, ("DRY_RUN_WRITE_ATTEMPT",))
    passed = found == expected and not out.unknown
    result = {
        "passed": passed,
        "synthetic": True,
        "findings": sorted(f"{r}:{t}" for r, t in found),
        "expected": len(expected),
        "wrote": 0,
    }
    await gov._event(
        conn, tenant_id, agent_id=agent_id, action="daara.dry_run", outcome="INFO" if passed else "DENY", actor=actor,
        code=None if passed else "SANDBOX_MISMATCH", cost_receipt={"findings": len(found), "expected": len(expected)}, now=now,
    )  # fmt: skip
    if not passed:
        return Outcome(False, ("SANDBOX_MISMATCH",), data=result)
    await store.mark_sandbox_passed(conn, agent_id, inst["config_hash"])
    return Outcome(True, data=result)


async def request_publish(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, now: datetime | None = None
) -> Outcome:
    """Put the DAARA policy forward for the independent approvals. Disabled until validation and the sandbox test both passed for THIS
    configuration (AT-05). The approval, and applying it, are the governance plane's (CR-105): this only drafts and requests."""
    now = now or _now()
    inst = await store.get_instance(conn, agent_id, lock=True)
    if inst is None:
        return Outcome(False, ("DAARA_NOT_ENABLED",))
    blockers = [
        *await _setup_blockers(conn, tenant_id, agent_id, inst),
        *d.publish_blockers(
            config=inst["config_hash"],
            validated=inst["validated_hash"],
            sandbox=inst["sandbox_passed_hash"],
        ),
    ]
    if blockers:
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.publish_request",
            outcome="DENY",
            actor=actor,
            code=blockers[0].split(":")[0],
            now=now,
        )
        return Outcome(False, tuple(blockers))
    if inst["policy_id"] is not None:
        for row in await gstore.list_requests(
            conn, agent_id=agent_id, states=("PENDING", "CLARIFICATION"), limit=20
        ):
            if str(row["policy_id"]) == str(inst["policy_id"]):
                loaded = await gstore.load_request(conn, str(row["request_id"]))
                assert loaded
                return Outcome(
                    True, data=gov.request_view(loaded[0], loaded[1], now)
                )  # idempotent: one open request
    until = now + timedelta(days=30)
    leases = [
        {"tool_id": s.tool_id, "resource_kind": s.resource_kind, "resource_selector": s.selector, "purpose": d.PURPOSE, "region": inst["region"],
         "data_class": "metadata", "valid_until": until}
        for s in d.TOOLS.values()
    ]  # fmt: skip
    try:
        policy = await gov.draft_policy(
            conn, tenant_id, agent_id, actor, leases=leases, quotas={"schedules_enabled": True} if inst["schedule"] == "DAILY" else None,
            region=inst["region"], ai_enabled=bool(inst["ai_explain"]),
            risk_tier="LOW", expires_at=None, now=now,
        )  # fmt: skip
    except GovernanceError as e:  # for example a schedule the platform ceiling does not allow
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.publish_request",
            outcome="DENY",
            actor=actor,
            code=e.code,
            now=now,
        )
        return Outcome(False, (e.code,))
    await store.set_policy(conn, agent_id, policy["policy_id"])
    request = await gov.request_change(
        conn,
        tenant_id,
        agent_id,
        actor,
        policy_id=policy["policy_id"],
        reason_code="DAARA_PUBLISH",
        now=now,
    )
    return Outcome(True, data=request)


# ====================================================================== the run


async def _facts(
    conn: AsyncConnection, cap: int
) -> tuple[list[d.MandateFact], list[d.AgentFact], int]:
    """The only reads. Returns (mandates, agents, total read). One more than `cap` is asked for so an overflow is detected, never trimmed."""
    mandates = [
        d.MandateFact(
            p["mandate_id"], p["status"], p["valid_to"], frozenset(p["actions"]),
            frozenset(p["approved_actions"]) if p["approved_actions"] is not None else None, p["grant_found"], p["grant_status"],
        )
        for p in await metadata_projection(conn, limit=cap + 1)
    ]  # fmt: skip
    agents: list[d.AgentFact] = []
    for row in await agent_store.list_agents(conn, limit=cap + 1):
        aid = str(row["agent_principal_id"])
        sponsor: bool | None = None
        has_policy = False
        approval: bool | None = None
        if row["status"] == "ACTIVE":
            profile = await machine_store.load_agent(conn, aid)
            sponsor = await agent_service.sponsor_is_active(
                conn, profile.sponsor_principal_id if profile else None
            )
            policy = await gstore.active_policy(conn, aid)
            has_policy = policy is not None
            if policy is not None:
                req = await gov.effective_approval(conn, policy)
                approval = bool(
                    req
                    and g.is_effective_approval(req, current_hash=g.policy_hash(policy), now=_now())
                )
        agents.append(d.AgentFact(aid, row["status"], sponsor, has_policy, approval))
    return mandates, agents, len(mandates) + len(agents)


def result_view(r: dict[str, Any]) -> dict[str, Any]:
    summary = r["summary"] if isinstance(r["summary"], dict) else {}
    return {
        "run_id": str(r["run_id"]), "agent_id": str(r["agent_principal_id"]), "mode": r["mode"], "state": r["state"], "incomplete": r["incomplete"],
        "checked": r["checked"], "issues": r["issues"], "proposals": r["proposals"], "privileges_changed": r["privileges_changed"],
        "rule_set_version": r["rule_set_version"], "policy_hash": r["policy_hash"], "summary": summary, "explanation": r["explanation"],
        "explanation_source": r["explanation_source"], "reason_code": r["reason_code"], "completed_at": _iso(r["completed_at"]),
    }  # fmt: skip


async def _fail(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, run_id: str, *, state: str, code: str, policy_hash: str | None, now: datetime,
    status: int | None = None,
) -> Outcome:  # fmt: skip
    summary = {"checked": 0, "issues": 0, "proposals": 0, "unknown": 0}
    await store.insert_result(
        conn, tenant_id,
        {"run_id": run_id, "agent_id": agent_id, "mode": "LIVE", "state": state, "incomplete": True, "checked": 0, "issues": 0, "proposals": 0,
         "rule_set_version": d.RULE_SET_VERSION, "policy_hash": policy_hash, "summary": summary, "explanation": d.fixed_explanation(summary),
         "reason_code": code, "completed_at": now},
    )  # fmt: skip
    await gov.finish_run(conn, tenant_id, run_id, actor, state="FAILED", reason=code, now=now)
    return Outcome(False, (code,), status=status)


async def _notify(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    run_id: str,
    out: d.RuleOutcome,
    new_count: int,
    now: datetime,
) -> None:
    """Tell the company a person has something to review, through the approved channel (the alerts and tasks inbox). One open alert per agent:
    a later run folds into it and counts. A failure to notify never changes a run: it is attempted in a savepoint and only recorded."""
    if not out.findings and not out.unknown:
        return
    worst = max((d.SEVERITY_RANK[f.severity] for f in out.findings), default=0)
    severity = Severity.HIGH if worst == d.SEVERITY_RANK["HIGH"] else Severity.MEDIUM
    body = f"{len(out.findings)} issue(s) found ({new_count} new proposal(s))" + (
        f", and {len(out.unknown)} item(s) could not be established" if out.unknown else ""
    )
    draft = AlertDraft(
        severity=severity, kind="agent.review.proposals", title="An agent proposed an access review", body=body + ". No access was changed.",
        object_type="agent", object_ref=agent_id, href="/admin/agents/review", recommended_action="Open the review queue and decide each proposal.",
        is_task=True, mandatory=False, correlation_key=f"daara-review:{agent_id}",
    )  # fmt: skip
    try:
        async with conn.begin_nested():
            await raise_alert(conn, tenant_id, draft, now=now)
    except Exception:  # noqa: BLE001 - notification is best effort and must not change a result
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.notify_failed",
            outcome="INFO",
            actor=agent_id,
            actor_type="AGENT",
            run_id=run_id,
            now=now,
        )


async def run(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, *, idempotency_key: str, now: datetime | None = None
) -> Outcome:  # fmt: skip
    now = now or _now()
    inst = await store.get_instance(conn, agent_id)
    if inst is None:
        return Outcome(False, ("DAARA_NOT_ENABLED",))
    started = await gov.start_run(
        conn, tenant_id, agent_id, actor, idempotency_key=idempotency_key, now=now
    )
    if not started.ok:
        return started
    run_id = started.data["run_id"]
    if started.data.get("replayed"):
        existing = await store.get_result(conn, run_id)
        return Outcome(
            True, data={**(result_view(existing) if existing else started.data), "replayed": True}
        )

    return await _execute(conn, tenant_id, agent_id, actor, run_id, inst, now)


async def _execute(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    run_id: str,
    inst: dict[str, Any],
    now: datetime,
) -> Outcome:
    """The governed work of one run, once its row exists and is RUNNING (inline for a person, or claimed from the queue by a worker)."""
    policy = await gstore.active_policy(conn, agent_id)
    baseline = await gstore.load_baseline(conn, tenant_id)
    phash = g.policy_hash(policy) if policy else None
    limits = (
        g.tightest(policy.quotas, baseline[0].quotas) if policy and baseline else g.QuotaLimits()
    )
    registry = await machine_store.load_tools(conn)

    async def event(action: str, **receipt: Any) -> None:
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action=action,
            outcome="INFO",
            actor=agent_id,
            actor_type="AGENT",
            run_id=run_id,
            policy_hash=phash,
            cost_receipt=receipt or None,
            now=now,
        )

    # every tool is checked on its own: lease, region, baseline, approval hash, freeze (AT-09)
    for spec in (d.TOOLS[d.TOOL_READ_MANDATES], d.TOOLS[d.TOOL_READ_AGENTS]):
        codes = await gov.govern_tool_call(
            conn, tenant_id, agent_id=agent_id, tool=registry[spec.tool_id], resource_type=spec.resource_kind, resource_id="batch", purpose=d.PURPOSE,
            region=inst["region"], now=now,
        )  # fmt: skip
        if codes:
            return await _fail(
                conn,
                tenant_id,
                agent_id,
                actor,
                run_id,
                state="DENIED",
                code=codes[0],
                policy_hash=phash,
                now=now,
            )
    await event("daara.authorized")

    mandates, agents, total = await _facts(conn, limits.records_per_run)
    progress = await gov.record_progress(
        conn, tenant_id, run_id, actor, records=total, proposals=0, now=now
    )
    if (
        not progress.ok
    ):  # over the record cap: a refusal, never a partial result called success (section 06)
        return await _fail(
            conn,
            tenant_id,
            agent_id,
            actor,
            run_id,
            state="FAILED",
            code=progress.codes[0],
            policy_hash=phash,
            now=now,
            status=progress.status,
        )
    await event("daara.metadata_read", records=total)

    out = d.evaluate_rules(mandates, agents, now=now, warn_days=inst["warn_days"])
    state, incomplete = d.classify_run(out.findings, out.unknown)
    await event(
        "daara.rules_evaluated",
        issues=len(out.findings),
        unknown=len(out.unknown),
        rule_set=d.RULE_SET_VERSION,
    )

    planned, summary_finding = d.plan_proposals(out.findings, limits.proposals_per_run)
    to_write = [*planned, *([summary_finding] if summary_finding else [])]
    new_count = 0
    if to_write:
        codes = await gov.govern_tool_call(
            conn, tenant_id, agent_id=agent_id, tool=registry[d.TOOL_PROPOSE], resource_type="review_proposal", resource_id="queue", purpose=d.PURPOSE,
            region=inst["region"], now=now,
        )  # fmt: skip
        if codes:
            return await _fail(
                conn,
                tenant_id,
                agent_id,
                actor,
                run_id,
                state="DENIED",
                code=codes[0],
                policy_hash=phash,
                now=now,
            )
        for f in to_write:
            _pid, created = await store.upsert_proposal(
                conn, tenant_id, agent_id=agent_id, run_id=run_id, target_ref=f.target_ref, rule_id=f.rule_id, rule_version=f.rule_version,
                finding_code=f.finding_code, severity=f.severity, evidence_refs=f.evidence_refs, dedupe_key=d.dedupe_key(tenant_id, agent_id, f),
            )  # fmt: skip
            new_count += int(created)
        await gov.record_progress(
            conn, tenant_id, run_id, actor, records=0, proposals=len(to_write), now=now
        )
        await event("daara.proposals_written", written=len(to_write), new=new_count)

    by_rule: dict[str, int] = {}
    for f in out.findings:
        by_rule[f.rule_id] = by_rule.get(f.rule_id, 0) + 1
    summary: dict[str, Any] = {
        "checked": total, "issues": len(out.findings), "proposals": len(to_write), "new_proposals": new_count, "unknown": len(out.unknown),
        "by_rule": by_rule, "privileges_changed": 0, "consolidated": summary_finding is not None,
    }  # fmt: skip
    refs = sorted({f.target_ref for f in out.findings})
    text, source = await _explain(
        conn, tenant_id, agent_id, actor, run_id, inst, summary, out, refs, phash, now
    )
    result = {
        "run_id": run_id, "agent_id": agent_id, "mode": "LIVE", "state": state, "incomplete": incomplete, "checked": total,
        "issues": len(out.findings), "proposals": len(to_write), "rule_set_version": d.RULE_SET_VERSION, "policy_hash": phash, "summary": summary,
        "explanation": text, "explanation_source": source, "reason_code": None, "completed_at": now,
    }  # fmt: skip
    await store.insert_result(conn, tenant_id, result)
    await gov.finish_run(conn, tenant_id, run_id, actor, state="COMPLETED", now=now)
    await _notify(conn, tenant_id, agent_id, run_id, out, new_count, now)
    await event("daara.completed", state=state, incomplete=incomplete)
    stored = await store.get_result(conn, run_id)
    assert stored
    return Outcome(True, data=result_view(stored))


async def _explain(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, run_id: str, inst: dict[str, Any], summary: dict[str, Any], out: d.RuleOutcome,
    refs: list[str], phash: str | None, now: datetime,
) -> tuple[str, str]:  # fmt: skip
    """The fixed text, unless every condition for the optional rewrite holds. Any failure on the way (no provider, no budget, a timeout, an
    error, an unfaithful answer) falls back to the fixed text and is recorded; a model failure never changes a result (AT-15, AT-17)."""
    fixed = d.fixed_explanation(summary)
    if not inst["ai_explain"]:
        return fixed, "FIXED"

    async def fell_back(why: str) -> tuple[str, str]:
        await gov._event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="daara.explain_fallback",
            outcome="INFO",
            actor=agent_id,
            actor_type="AGENT",
            code=why,
            run_id=run_id,
            policy_hash=phash,
            now=now,
        )
        return fixed, "FIXED"

    policy = await gstore.active_policy(conn, agent_id)
    baseline = await gstore.load_baseline(conn, tenant_id)
    if policy is None or not policy.ai_enabled or baseline is None or not baseline[0].ai_providers:
        return await fell_back("AI_NOT_APPROVED")
    names = [n for n in sorted(baseline[0].ai_providers) if n in _EXPLAINERS]
    if not names:
        return await fell_back("NO_PROVIDER")
    amount = config().ai_explain_reservation
    reserved = await gov.reserve(
        conn, tenant_id, agent_id, actor, tool_id=d.TOOL_PROPOSE, resource_kind="review_proposal", resource_id="review_proposal:explanation",
        purpose=d.PURPOSE, spend_class="AI_INFERENCE", amount=amount[0], currency=amount[1], idempotency_key=f"explain-{run_id}", run_id=run_id,
        region=inst["region"], now=now,
    )  # fmt: skip
    if not reserved.ok:
        return await fell_back("BUDGET_DENIED")
    reservation_id = reserved.data["reservation_id"]
    # the model sees counts, rule codes and masked targets: no identifier, no free text, nothing from a record's own fields
    payload = {
        "counts": {k: summary[k] for k in ("checked", "issues", "proposals", "unknown")},
        "findings": [
            {
                "rule": f.rule_id,
                "code": f.finding_code,
                "severity": f.severity,
                "target": d.mask_ref(f.target_ref),
            }
            for f in out.findings[:25]
        ],
    }
    try:
        limits = g.tightest(policy.quotas, baseline[0].quotas)
        text = await asyncio.wait_for(
            _EXPLAINERS[names[0]](payload), timeout=limits.tool_timeout_seconds
        )
    except Exception:  # noqa: BLE001 - a provider failure, a timeout or a refusal all end the same way
        await gov.release(conn, tenant_id, reservation_id, actor, reason="PROVIDER_FAILED", now=now)
        return await fell_back("PROVIDER_FAILED")
    problems = d.explanation_problems(text, summary, refs)
    if problems:
        await gov.release(conn, tenant_id, reservation_id, actor, reason="UNFAITHFUL", now=now)
        return await fell_back("UNFAITHFUL:" + problems[0].split(":")[0])
    await gov.settle(conn, tenant_id, reservation_id, actor, actual=amount[0], now=now)
    return text, "MODEL"


# ====================================================================== queued and scheduled runs


async def enqueue(
    conn: AsyncConnection, tenant_id: str, agent_id: str, actor: str, *, idempotency_key: str, mode: str = "MANUAL", now: datetime | None = None
) -> Outcome:  # fmt: skip
    """Accept a run now and let a worker start it. The freeze, quota and approval checks happen now AND again when the worker claims it."""
    now = now or _now()
    if await store.get_instance(conn, agent_id) is None:
        return Outcome(False, ("DAARA_NOT_ENABLED",))
    return await gov.start_run(
        conn,
        tenant_id,
        agent_id,
        actor,
        idempotency_key=idempotency_key,
        now=now,
        queued=True,
        mode=mode,
    )


async def execute_queued(
    conn: AsyncConnection, tenant_id: str, run_id: str, now: datetime | None = None
) -> Outcome:
    """A worker takes one queued run: claim it (re-checking everything that may have changed while it waited), then do the work."""
    now = now or _now()
    claimed = await gov.claim_run(conn, tenant_id, run_id, now=now)
    if not claimed.ok:
        return claimed
    agent_id = claimed.data["agent_id"]
    inst = await store.get_instance(conn, agent_id)
    if inst is None:
        return await _fail(
            conn,
            tenant_id,
            agent_id,
            agent_id,
            run_id,
            state="DENIED",
            code="DAARA_NOT_ENABLED",
            policy_hash=None,
            now=now,
        )
    return await _execute(conn, tenant_id, agent_id, agent_id, run_id, inst, now)


async def work_queue(conn: AsyncConnection, now: datetime, limit: int = 20) -> list[dict[str, Any]]:
    """Worker pass (BYPASSRLS): run the oldest queued runs, each under its own company's scope and its own savepoint, so one that fails never
    stops the others. Idempotent: a claimed run is no longer queued."""
    done: list[dict[str, Any]] = []
    for q in await gstore.queued_runs(conn, limit):
        tenant, run_id = str(q["tenant_id"]), str(q["run_id"])
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        try:
            async with conn.begin_nested():
                out = await execute_queued(conn, tenant, run_id, now)
            done.append(
                {"tenant_id": tenant, "run_id": run_id, "ok": out.ok, "codes": list(out.codes)}
            )
        except Exception as e:  # noqa: BLE001 - one run's failure must not stop the pass
            done.append(
                {"tenant_id": tenant, "run_id": run_id, "ok": False, "codes": [type(e).__name__]}
            )
    return done


async def schedule_due(conn: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    """Worker pass (BYPASSRLS): queue the daily run of every agent that asks for one AND is allowed one. Allowed means the agent is active,
    its approved policy holds `schedules_enabled` and the company's baseline allows schedules; the platform ceiling ships with schedules
    off, so until an owner raises it nothing here ever queues. One run per agent per UTC day, by key."""
    queued: list[dict[str, Any]] = []
    for row in await store.scheduled_instances(conn):
        tenant, agent_id = str(row["tenant_id"]), str(row["agent_principal_id"])
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": tenant})
        try:
            async with conn.begin_nested():
                agent = await machine_store.load_agent(conn, agent_id)
                policy = await gstore.active_policy(conn, agent_id)
                baseline = await gstore.load_baseline(conn, tenant)
                allowed = (
                    agent is not None and agent.status is AgentStatus.ACTIVE and policy is not None and policy.quotas.schedules_enabled
                    and baseline is not None and baseline[0].schedules_allowed and config().platform_quota_ceiling.schedules_enabled
                )  # fmt: skip
                if not allowed:
                    continue
                out = await enqueue(
                    conn,
                    tenant,
                    agent_id,
                    agent_id,
                    idempotency_key=f"sched-{agent_id}-{now:%Y%m%d}",
                    mode="SCHEDULED",
                    now=now,
                )
                queued.append(
                    {
                        "tenant_id": tenant,
                        "agent_id": agent_id,
                        "ok": out.ok,
                        "codes": list(out.codes),
                        "replayed": bool(out.data.get("replayed")),
                    }
                )
        except Exception as e:  # noqa: BLE001
            queued.append(
                {
                    "tenant_id": tenant,
                    "agent_id": agent_id,
                    "ok": False,
                    "codes": [type(e).__name__],
                }
            )
    return queued


# ====================================================================== reading runs


async def run_detail(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    run_id: str,
    *,
    detail: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    result = await store.get_result(conn, run_id)
    if result is None or str(result["agent_principal_id"]) != agent_id:
        raise GovernanceError("RUN_NOT_FOUND", "no such run")
    view = result_view(result)
    events = [
        e
        for e in await gstore.list_events(
            conn,
            agent_id=agent_id,
            since=None,
            until=None,
            before_seq=None,
            outcome=None,
            limit=500,
        )
        if str(e["run_id"] or "") == run_id
    ]
    events.sort(key=lambda e: e["seq"])
    timeline = [
        {
            "seq": e["seq"],
            "at": _iso(e["occurred_at"]),
            "step": e["action"],
            "outcome": e["outcome"],
            "code": e["code"],
        }
        for e in events
    ]
    decisions = [
        {
            "seq": e["seq"],
            "action": e["action"],
            "outcome": e["outcome"],
            "code": e["code"],
            "policy_hash": e["policy_hash"],
        }
        for e in events
        if e["action"].startswith(("tool_call", "run.", "budget"))
    ]
    expected = {"run.start", "daara.authorized", "daara.metadata_read", "daara.rules_evaluated"}
    have = {e["action"] for e in events}
    complete = view["state"] in ("FAILED", "DENIED") or expected <= have
    proposals = [proposal_view(p) for p in await store.proposals_for_run(conn, run_id)]
    return {
        **view, "timeline": timeline, "policy_decisions": decisions if detail else [],
        "evidence": {"complete": complete, "events": len(events), "missing": sorted(expected - have) if not complete else []},
        "proposals_for_run": proposals, "redacted": not detail, "model_used": view["explanation_source"] == "MODEL",
    }  # fmt: skip


async def list_runs(conn: AsyncConnection, agent_id: str, limit: int = 20) -> list[dict[str, Any]]:
    return [result_view(r) for r in await store.list_results(conn, agent_id, limit=limit)]


# ====================================================================== the review queue


def proposal_view(p: dict[str, Any]) -> dict[str, Any]:
    rule = d.RULES.get(p["rule_id"])
    return {
        "proposal_id": str(p["proposal_id"]), "ref": f"RP-{p['ref_no']}", "agent_id": str(p["agent_principal_id"]), "submitted_by": "AGENT",
        "run_id": str(p["last_run_id"]), "target": d.mask_ref(p["target_ref"]), "rule_id": p["rule_id"], "rule_version": p["rule_version"],
        "finding_code": p["finding_code"], "severity": p["severity"], "confidence_basis": p["confidence_basis"], "meaning": rule[2] if rule else "Consolidated findings.",
        "evidence_refs": [d.mask_ref(r) for r in (p["evidence_refs"] if isinstance(p["evidence_refs"], list) else [])],
        "seen_count": p["seen_count"], "status": p["status"], "created_at": _iso(p["created_at"]), "decided_at": _iso(p["decided_at"]),
        "decision_reason": p["decision_reason"], "has_handoff": p["handoff_digest"] is not None, "notice": NOTICE,
    }  # fmt: skip


async def list_proposals(
    conn: AsyncConnection, *, status: str | None, agent_id: str | None, limit: int = 100
) -> dict[str, Any]:
    items = [
        proposal_view(p)
        for p in await store.list_proposals(conn, status=status, agent_id=agent_id, limit=limit)
    ]
    return {"items": items, "open": await store.count_open(conn), "notice": NOTICE}


async def get_proposal(conn: AsyncConnection, proposal_id: str) -> dict[str, Any]:
    p = await store.get_proposal(conn, proposal_id)
    if p is None:
        raise GovernanceError("PROPOSAL_NOT_FOUND", "no such proposal")
    return proposal_view(p)


async def decide_proposal(
    conn: AsyncConnection, tenant_id: str, actor: str, proposal_id: str, *, action: str, reason: str | None = None, assignee: str | None = None,
    now: datetime | None = None,
) -> Outcome:  # fmt: skip
    """Dismiss, assign or hand off. All three only record a human's decision on the proposal. 'Hand off' makes a digest-bound record for
    the governed access workflow to start from; it changes no access, and an agent can never decide on a proposal (AT-10, AT-12)."""
    now = now or _now()
    if await machine_store.load_agent(conn, actor) is not None:
        return Outcome(False, ("AGENT_CANNOT_REVIEW",))
    p = await store.get_proposal(conn, proposal_id, lock=True)
    if p is None:
        raise GovernanceError("PROPOSAL_NOT_FOUND", "no such proposal")
    if p["status"] in ("DISMISSED", "HANDED_OFF"):
        return Outcome(False, ("PROPOSAL_FINAL",))
    if action == "dismiss":
        if reason not in DISMISS_REASONS:
            raise GovernanceError(
                "REASON_REQUIRED", "choose one of: " + ", ".join(sorted(DISMISS_REASONS))
            )
        status, handoff = "DISMISSED", None
    elif action == "assign":
        if not assignee or not await agent_service.sponsor_is_active(conn, assignee):
            raise GovernanceError("ASSIGNEE_INVALID", "assign to an active member")
        if await machine_store.load_agent(conn, assignee) is not None:
            raise GovernanceError("ASSIGNEE_INVALID", "an agent cannot be assigned a review")
        status, handoff = "ASSIGNED", None
    elif action == "handoff":
        status = "HANDED_OFF"
        handoff = g.digest(
            {
                "proposal": proposal_id,
                "target": p["target_ref"],
                "rule": p["rule_id"],
                "version": p["rule_version"],
                "code": p["finding_code"],
                "by": actor,
                "at": now.isoformat(),
            }
        )
    else:
        raise GovernanceError("ACTION_INVALID", "dismiss, assign or handoff")
    await store.decide_proposal(
        conn,
        proposal_id,
        status=status,
        by=actor,
        reason=reason,
        assignee=assignee,
        handoff_digest=handoff,
        at=now,
    )
    await gov._event(
        conn, tenant_id, agent_id=str(p["agent_principal_id"]), action=f"review.{action}", outcome="INFO", actor=actor, code=reason or "NO_ACCESS_CHANGED",
        resource=f"RP-{p['ref_no']}", cost_receipt={"handoff_digest": handoff} if handoff else None, now=now,
    )  # fmt: skip
    return Outcome(True, data={"status": status, "handoff_digest": handoff, "notice": NOTICE})


_ = Decimal  # money stays Decimal end to end in governance; imported for type clarity of the reservation amount
