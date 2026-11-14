"""Agent governance service (ZID-AG-WF-002 v1.0; CR-091).

The five control areas of the document, enforced where the work happens and not on a screen:

  permissions   a policy version holds exact, expiring leases; every tool call is checked against the ACTIVE version (`govern_tool_call`)
  spending      a call that costs money reserves against EVERY ceiling above it, atomically, and settles exactly once
  usage         runs, records and time are counted in the same transaction that starts or extends a run
  approvals     a change is effective only after the independent signatures its kind needs, bound to the exact hash
  company       a tenant baseline (tools, regions, providers, quotas) sits above every agent; a freeze stops everything

A refusal is a result, not an exception: the caller persists the evidence row first, then turns the result into an HTTP error, so
a refused call is never lost to a rolled-back transaction. Nothing a model wrote is ever read here.

Switch `ZID_AGENT_GOVERNANCE` (configured at start-up): shadow (default) records what enforce would refuse and lets the call through,
enforce refuses, off skips (refused outside local, test and dev). An emergency freeze is enforced in every mode, always.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection

from zid_platform.modules.commercial.ports import decide_feature_entitlement
from zid_platform.modules.machine.application import agents as agent_service
from zid_platform.modules.machine.domain import governance as g
from zid_platform.modules.machine.domain.agents import AgentStatus, MachineError, Tool, ToolStatus
from zid_platform.modules.machine.infrastructure import agent_store, machine_store
from zid_platform.modules.machine.infrastructure import daara_store as dstore
from zid_platform.modules.machine.infrastructure import governance_store as store
from zid_platform.modules.machine.infrastructure.governance_config import config

_MODE = "shadow"
_COMMERCIAL = "shadow"  # the platform's plan-enforcement mode, so the entitlement answer follows the same switch as every paid feature
AGENT_FEATURE = "advanced.agent-workload-identities"


def configure(mode: str, commercial_enforcement: str = "shadow") -> None:
    global _MODE, _COMMERCIAL
    if mode not in ("off", "shadow", "enforce"):
        raise ValueError("ZID_AGENT_GOVERNANCE is off, shadow or enforce")
    if commercial_enforcement not in ("off", "shadow", "enforce"):
        raise ValueError("the commercial enforcement mode is off, shadow or enforce")
    _MODE, _COMMERCIAL = mode, commercial_enforcement


def mode() -> str:
    return _MODE


class GovernanceError(MachineError):
    """A request the governance plane refuses before anything is stored (bad input, wrong state, missing record)."""


@dataclass(frozen=True, slots=True)
class Outcome:
    """What a decision-bearing operation returns. `ok` False carries the codes that refused it; the evidence row was already written."""

    ok: bool
    codes: tuple[str, ...] = ()
    data: dict[str, Any] = field(default_factory=dict)
    status: int | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _day_start(now: datetime) -> datetime:
    n = now.astimezone(UTC)
    return datetime(n.year, n.month, n.day, tzinfo=UTC)


def _month_start(now: datetime) -> datetime:
    n = now.astimezone(UTC)
    return datetime(n.year, n.month, 1, tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


async def _event(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str | None,
    action: str,
    outcome: str,
    actor: str | None,
    actor_type: str = "HUMAN",
    code: str | None = None,
    run_id: str | None = None,
    tool_id: str | None = None,
    resource: str | None = None,
    region: str | None = None,
    policy_hash: str | None = None,
    cost_receipt: dict[str, Any] | None = None,
    correlation_id: str | None = None,
    now: datetime | None = None,
) -> str:
    return await store.append_event(
        conn,
        tenant_id,
        agent_id=agent_id,
        run_id=run_id,
        actor=actor,
        actor_type=actor_type,
        action=action,
        outcome=outcome,
        code=code,
        tool_id=tool_id,
        resource=resource,
        region=region,
        policy_hash=policy_hash,
        correlation_id=correlation_id or str(uuid.uuid4()),
        cost_receipt=cost_receipt,
        occurred_at=now or _now(),
    )


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    name: str,
    agent_id: str,
    actor: str | None,
    data: dict[str, Any],
) -> None:
    """The platform audit record (evidence ledger) for a lifecycle-affecting governance step."""
    await agent_service._record(
        conn,
        tenant_id,
        name=name,
        event=False,
        aggregate_type="agent",
        aggregate_id=agent_id,
        actor=actor,
        data=data,
    )


async def _agent(conn: AsyncConnection, agent_id: str):  # type: ignore[no-untyped-def]
    agent = await machine_store.load_agent(conn, agent_id)
    if agent is None:
        raise GovernanceError("AGENT_NOT_FOUND", "this tenant has no such agent")
    return agent


# ====================================================================== the context every gate reads


async def effective_approval(
    conn: AsyncConnection, policy: g.GovernancePolicy | None
) -> g.ChangeRequest | None:
    """The newest approved request for exactly this policy version (a hash that differs is not an approval)."""
    if policy is None:
        return None
    rows = await store.list_requests(
        conn, agent_id=policy.agent_id, states=("APPROVED", "APPLIED"), limit=20
    )
    for row in rows:
        if row["subject"] == "POLICY" and str(row["policy_id"]) == policy.policy_id:
            loaded = await store.load_request(conn, str(row["request_id"]))
            return loaded[0] if loaded else None
    return None


async def load_context(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    now: datetime,
    *,
    with_usage: bool = False,
    with_ceilings: bool = False,
) -> g.GovernanceContext:
    agent = await _agent(conn, agent_id)
    policy = await store.active_policy(conn, agent_id)
    baseline = await store.load_baseline(conn, tenant_id)
    usage = await store.usage_snapshot(conn, agent_id, _day_start(now)) if with_usage else None
    ceilings = await _ceilings(conn, agent_id, now) if with_ceilings else ()
    return g.GovernanceContext(
        agent_tenant_id=agent.tenant_id,
        agent_active=agent.status is AgentStatus.ACTIVE,
        sponsor_active=await agent_service.sponsor_is_active(conn, agent.sponsor_principal_id),
        policy=policy,
        baseline=baseline[0] if baseline else None,
        approval=await effective_approval(conn, policy),
        freezes=await store.active_freezes(conn),
        entitled=await _entitled(conn),
        usage=usage,
        ceilings=ceilings,
    )


async def _entitled(conn: AsyncConnection) -> bool | None:
    """Does the plan include agent workload identities right now? Read on every governed call, never cached by this module, so a plan
    downgrade is seen at the next call. An answer that cannot be read is None, which the gates treat as POLICY_UNAVAILABLE (fail closed)."""
    try:
        result = await decide_feature_entitlement(conn, AGENT_FEATURE, enforcement=_COMMERCIAL)
    except Exception:  # noqa: BLE001 - an unreadable entitlement is a refusal, not a crash
        return None
    return bool(result.allowed)


async def _ceilings(conn: AsyncConnection, agent_id: str, now: datetime) -> list[g.Ceiling]:
    out: list[g.Ceiling] = []
    for b in await store.lock_ceilings(conn, agent_id):
        if b["period"] == "CALL":
            committed = reserved = Decimal(0)
        else:
            start = _day_start(now) if b["period"] == "DAY" else _month_start(now)
            committed, reserved = await store.used_in_window(
                conn,
                agent_id=str(b["agent_principal_id"]) if b["scope"] == "AGENT" else None,
                currency=b["currency"],
                window_start=start,
            )
        out.append(
            g.Ceiling(
                name=f"{b['scope'].lower()}_{b['period'].lower()}",
                scope=b["scope"].lower(),
                currency=b["currency"],
                window=b["period"],
                hard_cap=Decimal(b["hard_cap"]),
                committed=committed,
                reserved=reserved,
                warn_pct=(b["warn_pct_1"], b["warn_pct_2"]),
            )
        )
    return out


# ====================================================================== the gateway hook (section 13)


async def govern_tool_call(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    agent_id: str,
    tool: Tool,
    resource_type: str,
    resource_id: str,
    purpose: str | None,
    region: str | None = None,
    data_class: str = "metadata",
    now: datetime | None = None,
) -> tuple[str, ...]:
    """The governance gates for one tool call. Returns the codes that refuse it (empty = proceed) and always leaves an evidence row.
    Shadow records a would-be refusal and returns nothing; the emergency freeze refuses in every mode."""
    now = now or _now()
    if _MODE == "off":
        return ()
    # the emergency freeze is one cheap read and is enforced in every mode, for every company
    if await frozen_codes(conn, [agent_id]):
        await _event(conn, tenant_id, agent_id=agent_id, action="tool_call.governed", outcome="DENY", actor=agent_id, actor_type="AGENT",
                     code=str(g.Code.EMERGENCY_FROZEN), tool_id=tool.tool_id, resource=f"{resource_type}:{resource_id}", now=now)  # fmt: skip
        return (str(g.Code.EMERGENCY_FROZEN),)
    # shadow is for a company that has started to adopt governance (it has a baseline). One that has not would only fill the evidence chain
    # with "would be refused" rows on every call, and every gateway call would then queue behind the chain's write lock.
    if _MODE == "shadow" and await store.load_baseline(conn, tenant_id) is None:
        return ()
    ctx = await load_context(conn, tenant_id, agent_id, now, with_usage=True)
    call_region = region or (ctx.policy.region if ctx.policy else "")
    call = g.GovernedCall(
        agent_id=agent_id,
        session_tenant_id=tenant_id,
        tool_id=tool.tool_id,
        action=tool.canonical_action,
        side_effect=tool.side_effect.value,
        resource_kind=resource_type,
        resource_id=f"{resource_type}:{resource_id}",
        purpose=purpose,
        region=call_region,
        data_class=data_class,
    )
    decision = g.evaluate(call, ctx, now=now)
    resource = f"{resource_type}:{resource_id}"
    frozen = decision.code is g.Code.EMERGENCY_FROZEN
    if decision.allowed:
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="tool_call.governed",
            outcome="ALLOW",
            actor=agent_id,
            actor_type="AGENT",
            tool_id=tool.tool_id,
            resource=resource,
            region=call_region,
            policy_hash=decision.policy_hash,
            now=now,
        )
        return ()
    enforced = _MODE == "enforce" or frozen
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="tool_call.governed",
        outcome="DENY" if enforced else "SHADOW_DENY",
        actor=agent_id,
        actor_type="AGENT",
        code=str(decision.code),
        tool_id=tool.tool_id,
        resource=resource,
        region=call_region,
        policy_hash=decision.policy_hash,
        now=now,
    )
    return (str(decision.code),) if enforced else ()


async def frozen_codes(conn: AsyncConnection, agent_ids: list[str]) -> list[str]:
    """EMERGENCY_FROZEN when any agent in a chain, or the whole tenant, is frozen. Enforced in every mode, including at redemption,
    so a grant signed before the freeze cannot be spent after it."""
    freezes = await store.active_freezes(conn)
    if any(g.freeze_for(freezes, a) is not None for a in agent_ids):
        return [str(g.Code.EMERGENCY_FROZEN)]
    return []


# ====================================================================== company baseline


async def get_baseline(conn: AsyncConnection, tenant_id: str) -> dict[str, Any]:
    loaded = await store.load_baseline(conn, tenant_id)
    if loaded is None:
        return {
            "configured": False,
            "note": "No company baseline: no agent may run until the Owner sets one.",
        }
    b, revision = loaded
    return {"configured": True, "revision": revision, **baseline_view(b)}


def baseline_view(b: g.CompanyBaseline) -> dict[str, Any]:
    return {
        "allowed_tools": sorted(b.allowed_tools),
        "allowed_regions": sorted(b.allowed_regions),
        "ai_providers": sorted(b.ai_providers),
        "quotas": b.quotas.as_dict(),
        "schedules_allowed": b.schedules_allowed,
        "frozen": b.frozen,
    }


async def set_baseline(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    allowed_tools: list[str],
    allowed_regions: list[str],
    ai_providers: list[str],
    quotas: dict[str, Any] | None,
    schedules_allowed: bool,
) -> dict[str, Any]:
    """A company may narrow or set its baseline, never exceed the platform ceiling, never name a prohibited action's tool."""
    registry = await machine_store.load_tools(conn)
    for t in allowed_tools:
        tool = registry.get(t)
        if tool is None:
            raise GovernanceError("TOOL_NOT_REGISTERED", f"{t} is not a registered tool")
        if g.hard_denied(tool.canonical_action, tool.side_effect.value):
            raise GovernanceError("TOOL_HARD_DENIED", f"{t} can never be allowed to an agent")
    for items in (allowed_regions, ai_providers):
        if any(g.has_wildcard(x) for x in items):
            raise GovernanceError("WILDCARD_REFUSED", "an allowlist names exact entries")
    q = g.QuotaLimits.from_dict(quotas or {})
    if q.problems():
        raise GovernanceError("QUOTA_INVALID", ",".join(q.problems()))
    ceiling = config().platform_quota_ceiling
    if g.tightest(q, ceiling) != q:
        raise GovernanceError(
            "QUOTA_ABOVE_PLATFORM_CEILING",
            "a company can lower the platform limits, never raise them",
        )
    b = g.CompanyBaseline(
        allowed_tools=frozenset(allowed_tools),
        allowed_regions=frozenset(allowed_regions),
        ai_providers=frozenset(ai_providers),
        quotas=q,
        schedules_allowed=schedules_allowed and ceiling.schedules_enabled,
    )
    revision = await store.save_baseline(conn, tenant_id, b, actor=actor)
    await _event(conn, tenant_id, agent_id=None, action="baseline.set", outcome="INFO", actor=actor)
    await _audit(
        conn, tenant_id, "agent.governance.baseline_set", tenant_id, actor, {"revision": revision}
    )
    return {"revision": revision, **baseline_view(b)}


# ====================================================================== delegates (signer slots)


async def grant_slot(
    conn: AsyncConnection, tenant_id: str, actor: str, principal_id: str, slot: str
) -> dict[str, Any]:
    """Name who may sign as Security, Authority, Privacy or Finance. A person may not grant a slot to themselves."""
    if slot not in g.SLOTS:
        raise GovernanceError("SLOT_INVALID", "slot is SECURITY, AUTHORITY, PRIVACY or FINANCE")
    if principal_id == actor:
        raise GovernanceError(
            "SEPARATION_OF_DUTIES", "nobody grants an approval slot to themselves"
        )
    if not await agent_service.sponsor_is_active(
        conn, principal_id
    ):  # an ACTIVE principal of this tenant
        raise GovernanceError("PRINCIPAL_NOT_ACTIVE", "the person must be an active member")
    created = await store.grant_slot(conn, tenant_id, principal_id, slot, by=actor)
    await _event(
        conn,
        tenant_id,
        agent_id=None,
        action="delegate.granted",
        outcome="INFO",
        actor=actor,
        resource=f"{slot}:{principal_id}",
    )
    return {"principal_id": principal_id, "slot": slot, "created": created}


async def revoke_slot(
    conn: AsyncConnection, tenant_id: str, actor: str, principal_id: str, slot: str
) -> dict[str, Any]:
    done = await store.revoke_slot(conn, principal_id, slot, by=actor)
    if not done:
        raise GovernanceError("DELEGATE_NOT_FOUND", "that person does not hold the slot")
    await _event(
        conn,
        tenant_id,
        agent_id=None,
        action="delegate.revoked",
        outcome="INFO",
        actor=actor,
        resource=f"{slot}:{principal_id}",
    )
    return {"principal_id": principal_id, "slot": slot, "revoked": True}


# ====================================================================== policy versions


def lease_view(x: g.GrantLease, now: datetime) -> dict[str, Any]:
    state = "EXPIRED" if x.valid_until <= now else ("EFFECTIVE" if x.approval_ref else "REQUESTED")
    return {
        "grant_id": x.grant_id,
        "tool_id": x.tool_id,
        "action": x.action,
        "resource_kind": x.resource_kind,
        "resource_selector": x.resource_selector,
        "purpose": x.purpose,
        "region": x.region,
        "data_class": x.data_class,
        "valid_until": x.valid_until.isoformat(),
        "approval_ref": x.approval_ref,
        "state": state,
    }


def policy_view(p: g.GovernancePolicy, now: datetime) -> dict[str, Any]:
    return {
        "policy_id": p.policy_id,
        "agent_id": p.agent_id,
        "version": p.version,
        "state": p.state,
        "risk_tier": p.risk_tier,
        "region": p.region,
        "policy_hash": g.policy_hash(p),
        "ai_enabled": p.ai_enabled,
        "quotas": p.quotas.as_dict(),
        "expires_at": _iso(p.expires_at),
        "grants": [lease_view(x, now) for x in p.grants],
    }


async def draft_policy(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    *,
    leases: list[dict[str, Any]],
    quotas: dict[str, Any] | None,
    region: str,
    ai_enabled: bool,
    risk_tier: str,
    expires_at: datetime | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A new DRAFT version: requested rights only. Nothing here is effective, and the tool's action and side effect come from the
    registry, never from the request. Drafting voids any approval still open for an older draft."""
    now = now or _now()
    agent = await _agent(conn, agent_id)
    if agent.status in (AgentStatus.REVOKED, AgentStatus.RETIRED):
        raise GovernanceError("AGENT_NOT_GOVERNABLE", "a revoked or retired agent has no policy")
    registry = await machine_store.load_tools(conn)
    built: list[g.GrantLease] = []
    for raw in leases:
        tool = registry.get(raw["tool_id"])
        if tool is None or tool.status is not ToolStatus.ACTIVE:
            raise GovernanceError(
                "TOOL_NOT_REGISTERED", f"{raw['tool_id']} is not an active registered tool"
            )
        if raw["tool_id"] not in agent.allowed_tools:
            raise GovernanceError(
                "MANDATE_TOOL_NOT_ALLOWED", f"{raw['tool_id']} is not on this agent's tool list"
            )
        lease = g.GrantLease(
            grant_id=str(uuid.uuid4()),
            tool_id=tool.tool_id,
            action=tool.canonical_action,
            side_effect=tool.side_effect.value,
            resource_kind=raw["resource_kind"],
            resource_selector=raw["resource_selector"],
            purpose=raw["purpose"],
            region=raw.get("region") or region,
            valid_until=raw["valid_until"],
            data_class=raw.get("data_class", "metadata"),
        )
        problems = g.grant_problems(lease, now=now)
        if problems:
            raise GovernanceError("GRANT_INVALID", f"{tool.tool_id}: {', '.join(problems)}")
        built.append(lease)
    q = g.QuotaLimits.from_dict(quotas or {})
    if q.problems():
        raise GovernanceError("QUOTA_INVALID", ",".join(q.problems()))
    if g.tightest(q, config().platform_quota_ceiling) != q:
        raise GovernanceError(
            "QUOTA_ABOVE_PLATFORM_CEILING", "above what the platform allows any company"
        )
    if g.has_wildcard(region):
        raise GovernanceError("WILDCARD_OR_EMPTY", "region must be exact")
    policy = g.GovernancePolicy(
        policy_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        agent_id=agent_id,
        version=await store.next_policy_version(conn, agent_id),
        risk_tier=risk_tier,
        state="DRAFT",
        region=region,
        grants=tuple(built),
        quotas=q,
        ai_enabled=ai_enabled,
        expires_at=expires_at,
    )
    await store.insert_policy(conn, tenant_id, policy, created_by=actor)
    for row in await store.list_requests(
        conn, agent_id=agent_id, states=("PENDING", "CLARIFICATION", "APPROVED"), limit=50
    ):
        if row["subject"] == "POLICY":
            loaded = await store.load_request(conn, str(row["request_id"]), lock=True)
            if (
                loaded
                and g.invalidated_by(loaded[0], "POLICY_MODIFIED").state
                is g.RequestState.INVALIDATED
            ):
                await store.set_request_state(
                    conn,
                    str(row["request_id"]),
                    g.RequestState.INVALIDATED,
                    reason="POLICY_MODIFIED",
                )
                if row["policy_id"]:
                    await store.set_policy_state(conn, str(row["policy_id"]), "REJECTED")
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="policy.drafted",
        outcome="INFO",
        actor=actor,
        policy_hash=g.policy_hash(policy),
        now=now,
    )
    return policy_view(policy, now)


_RISK = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}


def derive_kind(
    old: g.GovernancePolicy | None, new: g.GovernancePolicy
) -> tuple[g.ChangeKind, dict[str, bool], dict[str, Any]]:
    """What kind of change a version is, from the diff alone: the requester does not choose the lighter path."""
    delta = g.policy_delta(old, new)
    flags = {
        "data_class_changed": bool(
            {x.data_class for x in new.grants}
            - ({x.data_class for x in old.grants} if old else set())
        ),
        "risk_increases": bool(old and _RISK[new.risk_tier] > _RISK[old.risk_tier]),
        "above_baseline": any(
            isinstance(v["to"], int)
            and not isinstance(v["to"], bool)
            and v["to"] > getattr(g.QuotaLimits(), k)
            for k, v in delta["quotas"].items()
        ),
    }
    if new.ai_enabled and not (old and old.ai_enabled):
        return (
            g.ChangeKind.AI_ENABLE,
            flags,
            delta,
        )  # turning AI on is its own approval, including in a first publish
    if old is None:
        return g.ChangeKind.PUBLISH, flags, delta
    if delta["added_grants"] or delta["changed_grants"]:
        return g.ChangeKind.SCOPE_EXTENSION, flags, delta
    if g.delta_widens(delta):
        return g.ChangeKind.QUOTA_INCREASE, flags, delta
    return (
        g.ChangeKind.PUBLISH,
        flags,
        delta,
    )  # narrowing keeps the full publish approval: conservative


def request_view(
    req: g.ChangeRequest, row: dict[str, Any] | None = None, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    view = {
        "request_id": req.request_id,
        "agent_id": req.agent_id,
        "kind": req.kind.value,
        "state": req.state.value,
        "requested_by": req.requested_by,
        "policy_hash": req.policy_hash,
        "base_policy_hash": req.base_policy_hash,
        "delta_hash": req.delta_hash,
        "required_slots": list(req.required),
        "open_slots": list(g.open_slots(req)),
        "signatures": [
            {"slot": s.slot, "signer": s.signer, "signed_at": s.signed_at.isoformat()}
            for s in req.signatures
        ],
        "created_at": req.created_at.isoformat(),
        "expires_at": req.expires_at.isoformat(),
        "expired": now >= req.expires_at and req.state is g.RequestState.PENDING,
    }
    if row is not None:
        view.update(
            ref=f"AGP-{row['ref_no']}",
            subject=row["subject"],
            reason_code=row["reason_code"],
            delta=row["delta"],
            evidence_ref=row["evidence_ref"],
            decided_reason=row["decided_reason"],
            policy_id=str(row["policy_id"]) if row["policy_id"] else None,
        )
    return view


async def request_change(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    *,
    policy_id: str,
    reason_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    agent = await _agent(conn, agent_id)
    policy = await store.load_policy(conn, policy_id)
    if policy is None or policy.agent_id != agent_id:
        raise GovernanceError("POLICY_NOT_FOUND", "no such draft for this agent")
    for row in await store.list_requests(
        conn, agent_id=agent_id, states=("PENDING", "CLARIFICATION", "APPROVED"), limit=50
    ):
        if str(row["policy_id"]) == policy_id:
            loaded = await store.load_request(conn, str(row["request_id"]))
            assert loaded
            return request_view(
                loaded[0], loaded[1], now
            )  # idempotent: the same draft has one open request
    if policy.state != "DRAFT":
        raise GovernanceError("POLICY_NOT_DRAFT", f"{policy.state} cannot be put forward")
    base = await store.active_policy(conn, agent_id)
    kind, flags, delta = derive_kind(base, policy)
    required = g.required_slots(kind, **flags)
    if base is None and kind is g.ChangeKind.AI_ENABLE:
        required = (
            "SECURITY",
            "AUTHORITY",
            "PRIVACY",
            "FINANCE",
        )  # a first publish with AI on needs the publish signers AND the AI ones
    evidence = await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="policy.compiled",
        outcome="INFO",
        actor=actor,
        policy_hash=g.policy_hash(policy),
        now=now,
    )
    # Everyone who wrote, built or deployed the change is an author: none of them may be the only signer.
    authors = tuple(
        dict.fromkeys([actor, agent.operator_principal_id, agent.publisher_principal_id])
    )
    req = g.ChangeRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        agent_id=agent_id,
        kind=kind,
        requested_by=actor,
        authors=authors,
        policy_hash=g.policy_hash(policy),
        base_policy_hash=g.policy_hash(base) if base else None,
        delta_hash=g.digest(delta),
        required=required,
        created_at=now,
        expires_at=now + config().approval_ttl,
    )
    ref = await store.insert_request(
        conn,
        tenant_id,
        req,
        subject="POLICY",
        policy_id=policy_id,
        delta=delta,
        payload={},
        reason_code=reason_code,
        evidence_ref=evidence,
    )
    await store.set_policy_state(conn, policy_id, "PENDING_APPROVAL")
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="approval.requested",
        outcome="INFO",
        actor=actor,
        policy_hash=req.policy_hash,
        resource=f"AGP-{ref}",
        now=now,
    )
    loaded = await store.load_request(conn, req.request_id)
    assert loaded
    return request_view(loaded[0], loaded[1], now)


_REASONS = frozenset(
    {
        "RISK_TOO_HIGH",
        "SCOPE_TOO_WIDE",
        "EVIDENCE_INSUFFICIENT",
        "NOT_NEEDED",
        "POLICY_CONFLICT",
        "OTHER",
    }
)


async def decide_request(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    actor: str,
    *,
    decision: str,
    presented_hash: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> Outcome:
    """Approve, reject or ask for clarification. Everything about the signer is read here, now: their slots, their standing, the
    hash they were shown, whether the evidence still exists. The page's memory of any of it counts for nothing. The step-up proof
    is enforced by the route guard in front of this call (config/step-up/route-actions.yaml)."""
    now = now or _now()
    loaded = await store.load_request(conn, request_id, lock=True)
    if loaded is None:
        raise GovernanceError("REQUEST_NOT_FOUND", "no such request")
    req, row = loaded
    agent_id = req.agent_id

    async def refuse(codes: list[str], state: g.RequestState | None = None) -> Outcome:
        if state is not None:
            await store.set_request_state(conn, request_id, state)
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action=f"approval.{decision}",
            outcome="DENY",
            actor=actor,
            code=codes[0],
            resource=f"AGP-{row['ref_no']}",
            policy_hash=req.policy_hash,
            now=now,
        )
        return Outcome(False, tuple(codes))

    if (
        req.state in (g.RequestState.PENDING, g.RequestState.CLARIFICATION)
        and now >= req.expires_at
    ):
        return await refuse(["REQUEST_EXPIRED"], g.RequestState.EXPIRED)
    slots_now = await store.slots_of(conn, actor)
    evidence_ok = bool(row["evidence_ref"]) and (
        row["subject"] != "POLICY"
        or (
            row["policy_id"] is not None
            and await store.load_policy(conn, str(row["policy_id"])) is not None
        )
    )
    signer_active = await agent_service.sponsor_is_active(conn, actor)
    blockers = g.sign_blockers(
        req,
        signer=actor,
        signer_slots_now=slots_now,
        signer_active=signer_active,
        presented_hash=presented_hash,
        step_up_ok=True,
        evidence_available=evidence_ok,
        now=now,
    )
    if row["subject"] == "RESTORE":
        freeze = await store.load_freeze(
            conn, row["payload"]["case_id"] if isinstance(row["payload"], dict) else ""
        )
        if freeze is not None and freeze.initiator == actor:
            blockers.append("RESTORE_NOT_INDEPENDENT")
    if decision in ("reject", "clarify"):
        # a rejection or a question needs a qualified, independent reviewer too, but not step-up to approve or a matching hash
        blockers = [
            b
            for b in blockers
            if b not in ("SCOPE_HASH_MISMATCH", "EVIDENCE_UNAVAILABLE", "STEP_UP_REQUIRED")
        ]
        if not (set(req.required) & slots_now) and "SIGNER_NOT_QUALIFIED" not in blockers:
            blockers.append("SIGNER_NOT_QUALIFIED")
        if reason is None or reason not in _REASONS:
            blockers.append("REASON_REQUIRED")
        blockers = [b for b in blockers if b not in ("ALREADY_SIGNED",)]
    if blockers:
        return await refuse(list(dict.fromkeys(blockers)))

    if decision == "reject":
        await store.set_request_state(
            conn, request_id, g.RequestState.REJECTED, by=actor, reason=reason
        )
        if row["policy_id"]:
            await store.set_policy_state(conn, str(row["policy_id"]), "REJECTED")
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="approval.rejected",
            outcome="INFO",
            actor=actor,
            code=reason,
            resource=f"AGP-{row['ref_no']}",
            policy_hash=req.policy_hash,
            now=now,
        )
        return Outcome(True, data={"state": "REJECTED"})
    if decision == "clarify":
        await store.set_request_state(
            conn, request_id, g.RequestState.CLARIFICATION, by=actor, reason=reason
        )
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="approval.clarification",
            outcome="INFO",
            actor=actor,
            code=reason,
            resource=f"AGP-{row['ref_no']}",
            policy_hash=req.policy_hash,
            now=now,
        )
        return Outcome(True, data={"state": "CLARIFICATION"})

    slot = g.slot_for(req, slots_now)
    assert slot is not None  # sign_blockers proved the signer holds an open slot
    sig = g.Signature(slot, actor, now, req.policy_hash)
    await store.add_signature(conn, tenant_id, request_id, sig)
    signed = g.add_signature(req, sig)
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="approval.signed",
        outcome="INFO",
        actor=actor,
        code=slot,
        resource=f"AGP-{row['ref_no']}",
        policy_hash=req.policy_hash,
        now=now,
    )
    data: dict[str, Any] = {"state": signed.state.value, "open_slots": list(g.open_slots(signed))}
    if signed.state is g.RequestState.APPROVED:
        await store.set_request_state(conn, request_id, g.RequestState.APPROVED)
        if row["subject"] == "BUDGET":
            data.update(await _finish_budget(conn, tenant_id, row, signed, actor, now))
        elif row["subject"] == "RESTORE":
            data.update(await _finish_restore(conn, tenant_id, row, signed, actor, now))
    return Outcome(True, data=data)


async def apply_request(
    conn: AsyncConnection,
    tenant_id: str,
    request_id: str,
    actor: str,
    *,
    now: datetime | None = None,
) -> Outcome:
    """Make an approved policy version the one that governs the agent. Approved is not active: the agent must be ACTIVE (its
    deployment was attested through the activation gates) and the approval must still be fresh and match this exact version."""
    now = now or _now()
    loaded = await store.load_request(conn, request_id, lock=True)
    if loaded is None:
        raise GovernanceError("REQUEST_NOT_FOUND", "no such request")
    req, row = loaded
    if row["subject"] != "POLICY":
        raise GovernanceError("REQUEST_NOT_A_POLICY", "only a policy change is applied here")
    policy = await store.load_policy(conn, str(row["policy_id"]))
    agent = await _agent(conn, req.agent_id)
    problems: list[str] = []
    if policy is None or g.policy_hash(policy) != req.policy_hash:
        problems.append("SCOPE_HASH_MISMATCH")
    elif not g.is_effective_approval(req, current_hash=g.policy_hash(policy), now=now):
        problems.append("APPROVAL_NOT_EFFECTIVE")
    if req.state is g.RequestState.APPLIED:
        problems.append("ALREADY_APPLIED")
    if agent.status is not AgentStatus.ACTIVE:
        problems.append("DEPLOYMENT_NOT_ATTESTED")
    if problems:
        await _event(
            conn,
            tenant_id,
            agent_id=req.agent_id,
            action="policy.apply",
            outcome="DENY",
            actor=actor,
            code=problems[0],
            resource=f"AGP-{row['ref_no']}",
            policy_hash=req.policy_hash,
            now=now,
        )
        return Outcome(False, tuple(problems))
    assert policy is not None
    old = await store.active_policy(conn, req.agent_id)
    if old is not None:
        await store.set_policy_state(conn, old.policy_id, "SUPERSEDED")
    await store.set_policy_state(conn, policy.policy_id, "ACTIVE", approval_request_id=request_id)
    await store.set_request_state(conn, request_id, g.RequestState.APPLIED)
    await _event(
        conn,
        tenant_id,
        agent_id=req.agent_id,
        action="policy.applied",
        outcome="INFO",
        actor=actor,
        resource=f"AGP-{row['ref_no']}",
        policy_hash=req.policy_hash,
        now=now,
    )
    await _audit(
        conn,
        tenant_id,
        "agent.governance.policy_applied",
        req.agent_id,
        actor,
        {"policy_hash": req.policy_hash, "version": policy.version},
    )
    return Outcome(True, data={"policy_hash": req.policy_hash, "version": policy.version})


async def list_change_requests(
    conn: AsyncConnection,
    *,
    agent_id: str | None,
    states: list[str] | None,
    viewer: str,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    now = now or _now()
    slots = await store.slots_of(conn, viewer)
    out = []
    for row in await store.list_requests(conn, agent_id=agent_id, states=states):
        loaded = await store.load_request(conn, str(row["request_id"]))
        assert loaded
        req = loaded[0]
        view = request_view(req, row, now)
        own = viewer == req.requested_by or viewer in req.authors
        view["can_review"] = bool(
            req.state is g.RequestState.PENDING
            and not own
            and (set(g.open_slots(req)) & slots)
            and not any(s.signer == viewer for s in req.signatures)
        )
        view["review_unavailable"] = (
            "SEPARATION_OF_DUTIES" if own and req.state is g.RequestState.PENDING else None
        )
        out.append(view)
    return out


# ====================================================================== effective access and the simulator


async def effective_access(
    conn: AsyncConnection, tenant_id: str, agent_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    ctx = await load_context(conn, tenant_id, agent_id, now)
    policy = ctx.policy
    drafts = [
        p
        for p in await store.list_policies(conn, agent_id)
        if p.state in ("DRAFT", "PENDING_APPROVAL")
    ]
    allowed_tools = ctx.baseline.allowed_tools if ctx.baseline else frozenset()
    rows = []
    for x in policy.grants if policy else ():
        v = lease_view(x, now)
        v["ceiling_source"] = (
            "company baseline"
            if x.tool_id not in allowed_tools
            else "lease (within company baseline)"
        )
        v["effective"] = bool(v["state"] == "EFFECTIVE" and x.tool_id in allowed_tools)
        rows.append(v)
    return {
        "agent_id": agent_id,
        "policy": policy_view(policy, now) if policy else None,
        "approval": request_view(ctx.approval) if ctx.approval else None,
        "effective_leases": rows,
        "requested": [policy_view(p, now) for p in drafts],
        "company_baseline": baseline_view(ctx.baseline) if ctx.baseline else None,
        "frozen": g.freeze_for(ctx.freezes or (), agent_id) is not None,
        "hard_denies": sorted(g.HARD_DENY_ACTIONS),
        "hierarchy": [
            "P0 platform prohibition",
            "P1 region, residency, entitlement",
            "P2 company baseline",
            "P3 workspace policy",
            "P4 agent grant lease",
            "P5 run envelope",
        ],
        "evaluated_at": now.isoformat(),
    }


_SYNTHETIC = re.compile(r"^[a-z_]+:synthetic[-_a-z0-9]*$")


async def simulate(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    *,
    request: dict[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Evaluate a SYNTHETIC request against the live policy. No reservation, no usage, no effect: only an evidence row. A real
    resource id is refused, so customer data never reaches this path."""
    now = now or _now()
    resource_id = request["resource_id"]
    if not _SYNTHETIC.match(resource_id):
        raise GovernanceError(
            "SIMULATION_REAL_DATA_REFUSED",
            "a simulation takes a synthetic resource id such as mandate:synthetic-1",
        )
    registry = await machine_store.load_tools(conn)
    tool = registry.get(request["tool_id"])
    action = request.get("action") or (tool.canonical_action if tool else request["tool_id"])
    side_effect = request.get("side_effect") or (tool.side_effect.value if tool else "NONE")
    ctx = await load_context(conn, tenant_id, agent_id, now)
    call = g.GovernedCall(
        agent_id=agent_id,
        session_tenant_id=tenant_id,
        tool_id=request["tool_id"],
        action=action,
        side_effect=side_effect,
        resource_kind=request["resource_kind"],
        resource_id=resource_id,
        purpose=request.get("purpose"),
        region=request.get("region") or (ctx.policy.region if ctx.policy else ""),
        data_class=request.get("data_class", "metadata"),
    )
    d = g.evaluate(call, ctx, now=now)
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="policy.simulated",
        outcome="INFO",
        actor=actor,
        code=str(d.code) if d.code else "ALLOW",
        tool_id=request["tool_id"],
        resource=resource_id,
        policy_hash=d.policy_hash,
        now=now,
    )
    return {
        "simulation": True,
        "synthetic": True,
        "effective": False,
        "decision": "ALLOW" if d.allowed else "DENY",
        "code": str(d.code) if d.code else None,
        "reason": d.reason,
        "policy_hash": d.policy_hash,
        "trace": [{"gate": a, "result": b} for a, b in d.trace],
        "note": "A simulation result is not production enablement.",
    }


# ====================================================================== budgets


def budget_key_cap(b: dict[str, Any]) -> Decimal:
    return Decimal(b["hard_cap"])


async def _active_budget(
    conn: AsyncConnection, scope: str, agent_id: str | None, period: str, currency: str
) -> dict[str, Any] | None:
    for b in await store.list_budgets(conn, agent_id=agent_id, states=("ACTIVE",)):
        if (
            b["scope"] == scope
            and b["period"] == period
            and b["currency"] == currency
            and (scope != "AGENT" or str(b["agent_principal_id"]) == agent_id)
        ):
            return b
    return None


async def _parent_cap(
    conn: AsyncConnection, scope: str, period: str, currency: str, agent_id: str | None
) -> Decimal | None:
    """The ceiling a child must sit under: the agent sits under the company, a call under the agent (or company)."""
    if scope == "COMPANY":
        return None
    if scope == "WORKSPACE":
        b = await _active_budget(conn, "COMPANY", None, period, currency)
    elif scope == "AGENT":
        b = await _active_budget(conn, "WORKSPACE", None, period, currency) or await _active_budget(
            conn, "COMPANY", None, period, currency
        )
    else:  # CALL: under the agent's day cap, else the company's
        b = (
            await _active_budget(conn, "AGENT", agent_id, "DAY", currency) if agent_id else None
        ) or await _active_budget(conn, "COMPANY", None, "DAY", currency)
    if b is None:
        raise GovernanceError(
            "NO_PARENT_CEILING", "set the company ceiling first: a child cap must sit under one"
        )
    return budget_key_cap(b)


async def propose_budget(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    scope: str,
    agent_id: str | None,
    currency: str,
    period: str,
    hard_cap: str,
    warn_pct: tuple[int, int] = (70, 90),
    reason_code: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Set a cap. A cap that is not higher than the one in force takes effect now (it only lowers risk); a higher one, or the first
    one, is a PENDING revision until the finance delegate and Security sign (never live early, never edited in place)."""
    now = now or _now()
    if (
        scope not in ("COMPANY", "WORKSPACE", "AGENT", "CALL")
        or period not in ("DAY", "MONTH", "CALL")
        or (period == "CALL") != (scope == "CALL")
    ):
        raise GovernanceError(
            "BUDGET_SHAPE_INVALID",
            "COMPANY, WORKSPACE and AGENT caps are DAY or MONTH; a CALL cap is per call",
        )
    if (scope == "AGENT") != bool(agent_id):
        raise GovernanceError(
            "BUDGET_SHAPE_INVALID", "an AGENT cap names its agent; the others do not"
        )
    if agent_id:
        await _agent(conn, agent_id)
    try:
        cap = g.money(hard_cap, currency, approved=config().approved_currencies)
    except g.MoneyError as e:
        raise GovernanceError(
            str(e),
            "an approved currency, a non-negative amount, no more decimals than the currency has",
        ) from e
    if not (0 < warn_pct[0] < warn_pct[1] < 100):
        raise GovernanceError(
            "WARN_THRESHOLDS_INVALID", "warnings go 1-99 and the second is above the first"
        )
    parent = await _parent_cap(conn, scope, period, currency, agent_id)
    if parent is not None and cap > parent:
        raise GovernanceError("BUDGET_ABOVE_PARENT", "a cap cannot exceed the ceiling above it")
    current = await _active_budget(conn, scope, agent_id, period, currency)
    revision = (current["revision"] + 1) if current else 1
    new_id = str(uuid.uuid4())
    base = {
        "budget_id": new_id,
        "scope": scope,
        "agent_id": agent_id,
        "currency": currency,
        "period": period,
        "hard_cap": cap,
        "warn_pct_1": warn_pct[0],
        "warn_pct_2": warn_pct[1],
        "revision": revision,
        "created_by": actor,
        "parent_id": str(current["budget_id"]) if current else None,
    }
    if current is not None and cap <= budget_key_cap(current):
        await store.set_budget_state(conn, str(current["budget_id"]), "SUPERSEDED")
        await store.insert_budget(
            conn, tenant_id, {**base, "state": "ACTIVE", "effective_from": now, "parent_id": None}
        )
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="budget.reduced",
            outcome="INFO",
            actor=actor,
            code=scope,
            cost_receipt={
                "currency": currency,
                "from": str(budget_key_cap(current)),
                "to": str(cap),
                "period": period,
            },
            now=now,
        )
        return {"state": "ACTIVE", "budget_id": new_id, "revision": revision, "hard_cap": str(cap)}
    delta = {
        "before": str(budget_key_cap(current)) if current else None,
        "after": str(cap),
        "currency": currency,
        "period": period,
        "scope": scope,
    }
    evidence = await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="budget.compiled",
        outcome="INFO",
        actor=actor,
        code=scope,
        now=now,
    )
    req = g.ChangeRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        agent_id=agent_id or tenant_id,
        kind=g.ChangeKind.BUDGET_INCREASE,
        requested_by=actor,
        authors=(actor,),
        policy_hash=g.digest({"budget_id": new_id, **delta}),
        base_policy_hash=None,
        delta_hash=g.digest(delta),
        required=g.required_slots(g.ChangeKind.BUDGET_INCREASE, above_baseline=True),
        created_at=now,
        expires_at=now + config().approval_ttl,
    )
    await store.insert_request(
        conn,
        tenant_id,
        req,
        subject="BUDGET",
        policy_id=None,
        delta=delta,
        payload={"budget_id": new_id},
        reason_code=reason_code,
        evidence_ref=evidence,
    )
    await store.insert_budget(
        conn, tenant_id, {**base, "state": "PENDING", "approval_request_id": req.request_id}
    )
    return {
        "state": "PENDING",
        "budget_id": new_id,
        "revision": revision,
        "hard_cap": str(cap),
        "approval_request_id": req.request_id,
        "required_slots": list(req.required),
    }


async def _finish_budget(
    conn: AsyncConnection,
    tenant_id: str,
    row: dict[str, Any],
    req: g.ChangeRequest,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    """The last signature on a budget change: re-check the ceiling above it, then supersede the old revision. Reservations already
    taken keep what they were granted: a cap change is never retroactive."""
    budget = await store.load_budget(conn, row["payload"]["budget_id"], lock=True)
    assert budget is not None
    agent_id = str(budget["agent_principal_id"]) if budget["agent_principal_id"] else None
    try:
        parent = await _parent_cap(
            conn, budget["scope"], budget["period"], budget["currency"], agent_id
        )
    except GovernanceError:
        parent = Decimal(-1)
    if parent is not None and budget_key_cap(budget) > parent:
        await store.set_request_state(
            conn, req.request_id, g.RequestState.INVALIDATED, reason="PARENT_CEILING_CHANGED"
        )
        await store.set_budget_state(conn, str(budget["budget_id"]), "SUPERSEDED")
        return {"state": "INVALIDATED", "reason": "PARENT_CEILING_CHANGED"}
    current = await _active_budget(
        conn, budget["scope"], agent_id, budget["period"], budget["currency"]
    )
    if current is not None:
        await store.set_budget_state(conn, str(current["budget_id"]), "SUPERSEDED")
    await store.set_budget_state(conn, str(budget["budget_id"]), "ACTIVE", effective_from=now)
    await store.set_request_state(conn, req.request_id, g.RequestState.APPLIED)
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="budget.activated",
        outcome="INFO",
        actor=actor,
        code=budget["scope"],
        cost_receipt={
            "currency": budget["currency"],
            "cap": str(budget_key_cap(budget)),
            "period": budget["period"],
        },
        now=now,
    )
    return {"state": "APPLIED", "budget_id": str(budget["budget_id"])}


async def budget_view(
    conn: AsyncConnection, agent_id: str, now: datetime | None = None
) -> dict[str, Any]:
    """Committed, reserved and available shown separately, with a text level and the reset time (GOV03-01, GOV03-04)."""
    now = now or _now()
    await _agent(conn, agent_id)
    rows = []
    for c, b in zip(
        await _ceilings(conn, agent_id, now), await _ordered_budgets(conn, agent_id), strict=False
    ):
        rows.append(
            {
                "budget_id": str(b["budget_id"]),
                "name": c.name,
                "scope": c.scope,
                "currency": c.currency,
                "window": c.window,
                "hard_cap": str(c.hard_cap),
                "committed": str(c.committed),
                "reserved": str(c.reserved),
                "available": str(c.available),
                "level": g.usage_level(c.committed + c.reserved, c.hard_cap, c.warn_pct),
                "warn_pct": list(c.warn_pct),
                "revision": b["revision"],
                "resets_at": _iso(g.reset_at(c.window, now)),
            }
        )
    pending = [
        {
            "budget_id": str(b["budget_id"]),
            "scope": b["scope"].lower(),
            "period": b["period"],
            "currency": b["currency"],
            "hard_cap": str(b["hard_cap"]),
            "approval_request_id": str(b["approval_request_id"])
            if b["approval_request_id"]
            else None,
        }
        for b in await store.list_budgets(conn, agent_id=agent_id, states=("PENDING",))
    ]
    return {
        "agent_id": agent_id,
        "ceilings": rows,
        "pending": pending,
        "ai_spend": "$0 until a model opt-in and finance approval",
        "external_spend": "forbidden",
        "as_of": now.isoformat(),
        "timezone": "UTC",
    }


async def _ordered_budgets(conn: AsyncConnection, agent_id: str) -> list[dict[str, Any]]:
    return await store.lock_ceilings(conn, agent_id)


async def reserve(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    *,
    tool_id: str,
    resource_kind: str,
    resource_id: str,
    purpose: str | None,
    spend_class: str,
    amount: str,
    currency: str,
    idempotency_key: str,
    run_id: str | None = None,
    region: str | None = None,
    now: datetime | None = None,
) -> Outcome:
    """The governed paid step: every gate, then an atomic reservation. The ceilings are row-locked, so two callers for the last of an
    allowance queue and the second sees the first. A replay of the same key returns the first reservation and charges nothing more."""
    now = now or _now()
    replay = await store.reservation_by_key(conn, idempotency_key)
    if replay is not None:
        if str(replay["agent_principal_id"]) != agent_id or replay["tool_id"] != tool_id:
            return Outcome(False, ("IDEMPOTENCY_KEY_REUSED",))
        return Outcome(True, data=_reservation_view(replay, replayed=True))
    registry = await machine_store.load_tools(conn)
    tool = registry.get(tool_id)
    if tool is None:
        return Outcome(False, (str(g.Code.SCOPE_NOT_APPROVED),))
    try:
        cls = g.SpendClass(spend_class)
        cost = g.money(amount, currency, approved=config().approved_currencies)
    except (ValueError, g.MoneyError) as e:
        raise GovernanceError(
            str(e) if isinstance(e, g.MoneyError) else "SPEND_CLASS_INVALID",
            "an exact decimal in an approved currency",
        ) from e
    ctx = await load_context(conn, tenant_id, agent_id, now, with_usage=True, with_ceilings=True)
    call = g.GovernedCall(
        agent_id=agent_id,
        session_tenant_id=tenant_id,
        tool_id=tool_id,
        action=tool.canonical_action,
        side_effect=tool.side_effect.value,
        resource_kind=resource_kind,
        resource_id=resource_id,
        purpose=purpose,
        region=region or (ctx.policy.region if ctx.policy else ""),
        spend_class=cls,
        cost=cost,
        currency=currency,
    )
    d = g.evaluate(call, ctx, now=now)
    if not d.allowed:
        receipt = {"amount": str(cost), "currency": currency, "spend_class": spend_class}
        if d.budget is not None:
            receipt.update(
                ceiling=d.budget.ceiling,
                available=str(d.budget.available) if d.budget.available is not None else None,
                resets_at=_iso(d.budget.reset_at),
            )
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="budget.reserve",
            outcome="DENY",
            actor=actor,
            actor_type="AGENT" if actor == agent_id else "HUMAN",
            code=str(d.code),
            tool_id=tool_id,
            resource=resource_id,
            policy_hash=d.policy_hash,
            cost_receipt=receipt,
            run_id=run_id,
            now=now,
        )
        return Outcome(False, (str(d.code),), data=receipt, status=d.http_status)
    rid = str(uuid.uuid4())
    row = {
        "reservation_id": rid,
        "agent_id": agent_id,
        "run_id": run_id,
        "tool_id": tool_id,
        "spend_class": spend_class,
        "currency": currency,
        "upper_bound": cost,
        "idempotency_key": idempotency_key,
        "price_version": config().price_version,
        "created_at": now,
        "expires_at": now + config().reservation_ttl,
    }
    await store.insert_reservation(conn, tenant_id, row)
    receipt = {
        "reservation_id": rid,
        "amount": str(cost),
        "currency": currency,
        "spend_class": spend_class,
        "price_version": config().price_version,
    }
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="budget.reserve",
        outcome="ALLOW",
        actor=actor,
        tool_id=tool_id,
        resource=resource_id,
        policy_hash=d.policy_hash,
        cost_receipt=receipt,
        run_id=run_id,
        now=now,
    )
    return Outcome(
        True,
        data=_reservation_view(
            {
                **row,
                "state": "RESERVED",
                "committed": Decimal(0),
                "released": Decimal(0),
                "overrun": Decimal(0),
            }
        ),
    )


def _reservation_view(r: dict[str, Any], *, replayed: bool = False) -> dict[str, Any]:
    return {
        "reservation_id": str(r["reservation_id"]),
        "state": r["state"],
        "currency": r["currency"],
        "upper_bound": str(r["upper_bound"]),
        "committed": str(r.get("committed", 0)),
        "released": str(r.get("released", 0)),
        "overrun": str(r.get("overrun", 0)),
        "expires_at": _iso(r["expires_at"]),
        "replayed": replayed,
    }


async def settle(
    conn: AsyncConnection,
    tenant_id: str,
    reservation_id: str,
    actor: str,
    *,
    actual: str,
    now: datetime | None = None,
) -> Outcome:
    """Settle exactly once. Unused allowance goes back; a cost above the reservation commits only what was reserved and records the
    rest as an OVERRUN adjustment, so usage tells the truth without rewriting a committed row."""
    now = now or _now()
    r = await store.load_reservation(conn, reservation_id, lock=True)
    if r is None:
        raise GovernanceError("RESERVATION_NOT_FOUND", "no such reservation")
    if r["state"] != "RESERVED":
        return Outcome(False, ("RESERVATION_CLOSED",))
    try:
        cost = g.money(actual, r["currency"], approved=config().approved_currencies)
    except g.MoneyError as e:
        raise GovernanceError(str(e), "an exact decimal in the reservation's currency") from e
    s = g.settle(Decimal(r["upper_bound"]), cost)
    await store.close_reservation(
        conn,
        reservation_id,
        state="SETTLED",
        committed=s.committed,
        released=s.released,
        overrun=s.overrun,
        at=now,
    )
    if s.overrun > 0:
        await store.add_adjustment(
            conn,
            tenant_id,
            reservation_id=reservation_id,
            agent_id=str(r["agent_principal_id"]),
            currency=r["currency"],
            amount=s.overrun,
            reason="OVERRUN",
            by=actor,
        )
    await _event(
        conn,
        tenant_id,
        agent_id=str(r["agent_principal_id"]),
        action="budget.settle",
        outcome="INFO",
        actor=actor,
        cost_receipt={
            "reservation_id": reservation_id,
            "committed": str(s.committed),
            "released": str(s.released),
            "overrun": str(s.overrun),
            "currency": r["currency"],
            "price_version": r["price_version"],
        },
        now=now,
    )
    return Outcome(
        True,
        data={
            "state": "SETTLED",
            "committed": str(s.committed),
            "released": str(s.released),
            "overrun": str(s.overrun),
        },
    )


async def release(
    conn: AsyncConnection,
    tenant_id: str,
    reservation_id: str,
    actor: str,
    *,
    reason: str,
    now: datetime | None = None,
) -> Outcome:
    """A step that failed or never ran gives its whole reservation back (T30)."""
    now = now or _now()
    r = await store.load_reservation(conn, reservation_id, lock=True)
    if r is None:
        raise GovernanceError("RESERVATION_NOT_FOUND", "no such reservation")
    if r["state"] != "RESERVED":
        return Outcome(False, ("RESERVATION_CLOSED",))
    await store.close_reservation(
        conn,
        reservation_id,
        state="RELEASED",
        committed=Decimal(0),
        released=Decimal(r["upper_bound"]),
        overrun=Decimal(0),
        at=now,
    )
    await _event(
        conn,
        tenant_id,
        agent_id=str(r["agent_principal_id"]),
        action="budget.release",
        outcome="INFO",
        actor=actor,
        code=reason,
        cost_receipt={
            "reservation_id": reservation_id,
            "released": str(r["upper_bound"]),
            "currency": r["currency"],
        },
        now=now,
    )
    return Outcome(True, data={"state": "RELEASED", "released": str(r["upper_bound"])})


_ADJUST_SIGN = {"LATE_INVOICE": 1, "PRICE_REVISION": 0, "REFUND": -1, "REVERSAL": -1}


async def adjust(
    conn: AsyncConnection,
    tenant_id: str,
    reservation_id: str,
    actor: str,
    *,
    reason: str,
    amount: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A late invoice, price revision, refund or reversal is a new signed ledger entry that references the reservation. The reservation
    itself is never edited (T31)."""
    now = now or _now()
    r = await store.load_reservation(conn, reservation_id)
    if r is None:
        raise GovernanceError("RESERVATION_NOT_FOUND", "no such reservation")
    if reason not in _ADJUST_SIGN or _ADJUST_SIGN[reason] == 0:
        raise GovernanceError("ADJUSTMENT_REASON_INVALID", "LATE_INVOICE, REFUND or REVERSAL")
    try:
        value = g.money(amount, r["currency"], approved=config().approved_currencies)
    except g.MoneyError as e:
        raise GovernanceError(str(e), "an exact decimal in the reservation's currency") from e
    aid = await store.add_adjustment(
        conn,
        tenant_id,
        reservation_id=reservation_id,
        agent_id=str(r["agent_principal_id"]),
        currency=r["currency"],
        amount=value * _ADJUST_SIGN[reason],
        reason=reason,
        by=actor,
    )
    await _event(
        conn,
        tenant_id,
        agent_id=str(r["agent_principal_id"]),
        action="budget.adjust",
        outcome="INFO",
        actor=actor,
        code=reason,
        cost_receipt={
            "adjustment_id": aid,
            "amount": str(value * _ADJUST_SIGN[reason]),
            "currency": r["currency"],
            "reservation_id": reservation_id,
        },
        now=now,
    )
    return {"adjustment_id": aid, "amount": str(value * _ADJUST_SIGN[reason])}


# ====================================================================== runs and usage


def _run_view(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "run_id": str(r["run_id"]),
        "agent_id": str(r["agent_principal_id"]),
        "state": r["state"],
        "started_at": _iso(r["started_at"]),
        "deadline_at": _iso(r["deadline_at"]),
        "ended_at": _iso(r["ended_at"]),
        "records": r["records"],
        "proposals": r["proposals"],
        "reason_code": r["reason_code"],
        "policy_hash": r["policy_hash"],
    }


def _quota_data(v: g.QuotaVerdict | None) -> dict[str, Any]:
    if v is None:
        return {}
    return {
        "quota": v.quota,
        "window": v.window,
        "limit": v.limit,
        "used": v.used,
        "retry_after": _iso(v.retry_after),
        "waiting_capacity": v.waiting_capacity,
    }


async def start_run(
    conn: AsyncConnection,
    tenant_id: str,
    agent_id: str,
    actor: str,
    *,
    idempotency_key: str,
    now: datetime | None = None,
    queued: bool = False,
    mode: str = "MANUAL",
) -> Outcome:
    """Open a governed run. The concurrency and daily counters are read under advisory locks inside this transaction, so two starts
    cannot both pass the last slot; a replay of the key returns the same run. With `queued` the run is accepted and counted now but
    only starts when a worker claims it (`claim_run`), which re-checks the freeze, the agent, the sponsor and the approval."""
    now = now or _now()
    await store.lock_agent_runs(conn, agent_id)
    replay = await store.run_by_key(conn, idempotency_key)
    if replay is not None:
        if str(replay["agent_principal_id"]) != agent_id:
            return Outcome(False, ("IDEMPOTENCY_KEY_REUSED",))
        return Outcome(True, data={**_run_view(replay), "replayed": True})
    ctx = await load_context(conn, tenant_id, agent_id, now, with_usage=True)
    assert ctx.usage is not None
    d = g.evaluate_run_start(
        ctx,
        ctx.usage,
        agent_id=agent_id,
        now=now,
        agent_already_running=ctx.usage.active_runs_agent > 0,
    )
    enforced = _MODE == "enforce" or d.code is g.Code.EMERGENCY_FROZEN
    if not d.allowed and enforced and _MODE != "off":
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="run.start",
            outcome="DENY",
            actor=actor,
            code=str(d.code),
            policy_hash=d.policy_hash,
            now=now,
        )
        return Outcome(False, (str(d.code),), data=_quota_data(d.quota), status=d.http_status)
    if not d.allowed and _MODE == "shadow":
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="run.start",
            outcome="SHADOW_DENY",
            actor=actor,
            code=str(d.code),
            policy_hash=d.policy_hash,
            now=now,
        )
    limits = (
        g.tightest(ctx.policy.quotas, ctx.baseline.quotas)
        if ctx.policy and ctx.baseline
        else g.QuotaLimits()
    )
    run_id = str(uuid.uuid4())
    row = {
        "run_id": run_id,
        "agent_id": agent_id,
        "idempotency_key": idempotency_key,
        "policy_hash": d.policy_hash or "none",
        "started_at": now,
        "deadline_at": g.run_deadline(limits, now),
        "state": "QUEUED" if queued else "RUNNING",
        "mode": mode,
    }
    await store.insert_run(conn, tenant_id, row)
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="run.queued" if queued else "run.start",
        outcome="ALLOW",
        actor=actor,
        run_id=run_id,
        policy_hash=d.policy_hash,
        now=now,
    )
    loaded = await store.load_run(conn, run_id)
    assert loaded
    return Outcome(True, data=_run_view(loaded))


async def claim_run(
    conn: AsyncConnection, tenant_id: str, run_id: str, *, now: datetime | None = None
) -> Outcome:
    """A worker takes one queued run. Everything that could have changed while it waited is checked again NOW: a freeze, an agent that is
    no longer active, a sponsor who left, an approval that no longer matches the live policy. A run that fails the re-check is ended
    FAILED with the code and never starts; the quota was already counted when it was queued, so it is not counted twice."""
    now = now or _now()
    run = await store.load_run(conn, run_id, lock=True)
    if run is None or run["state"] != "QUEUED":
        return Outcome(False, ("RUN_NOT_QUEUED",))
    agent_id = str(run["agent_principal_id"])
    ctx = await load_context(conn, tenant_id, agent_id, now)
    d = g.evaluate_run_start(
        ctx, g.UsageSnapshot(), agent_id=agent_id, now=now, agent_already_running=True
    )
    if (
        not d.allowed
        and (_MODE == "enforce" or d.code is g.Code.EMERGENCY_FROZEN)
        and _MODE != "off"
    ):
        await store.finish_run(conn, run_id, state="FAILED", at=now, reason=str(d.code))
        await _event(conn, tenant_id, agent_id=agent_id, action="run.dequeued", outcome="DENY", actor=None, actor_type="SYSTEM",
                     code=str(d.code), run_id=run_id, policy_hash=d.policy_hash, now=now)  # fmt: skip
        return Outcome(False, (str(d.code),), status=d.http_status)
    limits = (
        g.tightest(ctx.policy.quotas, ctx.baseline.quotas)
        if ctx.policy and ctx.baseline
        else g.QuotaLimits()
    )
    if not await store.mark_running(conn, run_id, now=now, deadline=g.run_deadline(limits, now)):
        return Outcome(False, ("RUN_NOT_QUEUED",))
    await _event(conn, tenant_id, agent_id=agent_id, action="run.dequeued", outcome="ALLOW", actor=None, actor_type="SYSTEM",
                 run_id=run_id, policy_hash=d.policy_hash, now=now)  # fmt: skip
    loaded = await store.load_run(conn, run_id)
    assert loaded
    return Outcome(True, data=_run_view(loaded))


async def _release_run_reservations(
    conn: AsyncConnection, tenant_id: str, run_id: str, actor: str | None, now: datetime
) -> int:
    n = 0
    for r in await store.open_reservations_for_run(conn, run_id):
        await store.close_reservation(
            conn,
            str(r["reservation_id"]),
            state="RELEASED",
            committed=Decimal(0),
            released=Decimal(r["upper_bound"]),
            overrun=Decimal(0),
            at=now,
        )
        n += 1
    return n


async def record_progress(
    conn: AsyncConnection,
    tenant_id: str,
    run_id: str,
    actor: str,
    *,
    records: int,
    proposals: int,
    now: datetime | None = None,
) -> Outcome:
    """Count what a run reads and proposes. Past a cap the answer is a refusal, never a quietly truncated success; past the deadline
    the run is ended, its reservations released and the evidence written (T36)."""
    now = now or _now()
    run = await store.load_run(conn, run_id, lock=True)
    if run is None:
        raise GovernanceError("RUN_NOT_FOUND", "no such run")
    agent_id = str(run["agent_principal_id"])
    if run["state"] != "RUNNING":
        return Outcome(False, ("RUN_NOT_RUNNING",))
    ctx = await load_context(conn, tenant_id, agent_id, now, with_usage=True)
    limits = (
        g.tightest(ctx.policy.quotas, ctx.baseline.quotas)
        if ctx.policy and ctx.baseline
        else g.QuotaLimits()
    )
    freeze_live = ctx.freezes is not None and g.freeze_for(ctx.freezes, agent_id) is not None
    if freeze_live:
        await store.finish_run(conn, run_id, state="CANCELED", at=now, reason="EMERGENCY_FROZEN")
        await _release_run_reservations(conn, tenant_id, run_id, actor, now)
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="run.progress",
            outcome="DENY",
            actor=actor,
            code="EMERGENCY_FROZEN",
            run_id=run_id,
            now=now,
        )
        return Outcome(False, (str(g.Code.EMERGENCY_FROZEN),), status=423)
    if g.run_timed_out(limits, run["started_at"], now):
        await store.finish_run(conn, run_id, state="TIMED_OUT", at=now, reason="run_seconds")
        released = await _release_run_reservations(conn, tenant_id, run_id, actor, now)
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="run.timeout",
            outcome="DENY",
            actor=actor,
            code=str(g.Code.USAGE_LIMIT),
            run_id=run_id,
            cost_receipt={"released_reservations": released},
            now=now,
        )
        return Outcome(
            False,
            (str(g.Code.USAGE_LIMIT),),
            data={"quota": "run_seconds", "released_reservations": released},
            status=429,
        )
    assert ctx.usage is not None
    total = run["records"] + records
    verdict = g.records_check(limits, ctx.usage, requested=total, now=now) if records else None
    if verdict is None and run["proposals"] + proposals > limits.proposals_per_run:
        verdict = g.QuotaVerdict(
            "proposals_per_run", "RUN", limits.proposals_per_run, run["proposals"] + proposals, None
        )
    if verdict is not None and verdict.quota == "records_per_tenant_day":
        # records_check compared tenant-day usage plus the whole run total; the usage already counts this run's earlier records
        verdict = (
            g.records_check(limits, ctx.usage, requested=records, now=now) if records else None
        )
    if verdict is not None and _MODE != "off":
        enforced = _MODE == "enforce"
        await _event(
            conn,
            tenant_id,
            agent_id=agent_id,
            action="run.progress",
            outcome="DENY" if enforced else "SHADOW_DENY",
            actor=actor,
            code=str(g.Code.USAGE_LIMIT),
            run_id=run_id,
            resource=verdict.quota,
            now=now,
        )
        if enforced:
            return Outcome(False, (str(g.Code.USAGE_LIMIT),), data=_quota_data(verdict), status=429)
    await store.add_run_records(conn, run_id, records, proposals)
    return Outcome(True, data={"records": total, "proposals": run["proposals"] + proposals})


async def finish_run(
    conn: AsyncConnection,
    tenant_id: str,
    run_id: str,
    actor: str,
    *,
    state: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now = now or _now()
    if state not in ("COMPLETED", "FAILED", "CANCELED"):
        raise GovernanceError("RUN_STATE_INVALID", "COMPLETED, FAILED or CANCELED")
    run = await store.load_run(conn, run_id, lock=True)
    if run is None:
        raise GovernanceError("RUN_NOT_FOUND", "no such run")
    done = await store.finish_run(conn, run_id, state=state, at=now, reason=reason)
    released = (
        await _release_run_reservations(conn, tenant_id, run_id, actor, now)
        if state != "COMPLETED"
        else 0
    )
    if done:
        await _event(
            conn,
            tenant_id,
            agent_id=str(run["agent_principal_id"]),
            action="run.finish",
            outcome="INFO",
            actor=actor,
            code=state,
            run_id=run_id,
            cost_receipt={"released_reservations": released},
            now=now,
        )
    loaded = await store.load_run(conn, run_id)
    assert loaded
    return {**_run_view(loaded), "changed": done}


async def governance_sweep(conn: AsyncConnection, tenant_id: str, now: datetime) -> dict[str, int]:
    """Worker pass for one tenant: free allowances a crashed run left reserved, end runs past their deadline, expire stale requests.
    Idempotent. Without it a dead worker would hold budget and a concurrency slot forever."""
    released = 0
    for r in await store.stale_reservations(conn, now):
        await store.close_reservation(
            conn,
            str(r["reservation_id"]),
            state="RELEASED",
            committed=Decimal(0),
            released=Decimal(r["upper_bound"]),
            overrun=Decimal(0),
            at=now,
        )
        await _event(
            conn,
            tenant_id,
            agent_id=str(r["agent_principal_id"]),
            action="budget.release",
            outcome="INFO",
            actor=None,
            actor_type="SYSTEM",
            code="EXPIRED",
            cost_receipt={
                "reservation_id": str(r["reservation_id"]),
                "released": str(r["upper_bound"]),
            },
            now=now,
        )
        released += 1
    ended = 0
    for run in await store.overdue_runs(conn, now):
        await store.finish_run(
            conn, str(run["run_id"]), state="TIMED_OUT", at=now, reason="run_seconds"
        )
        await _release_run_reservations(conn, tenant_id, str(run["run_id"]), None, now)
        await _event(
            conn,
            tenant_id,
            agent_id=str(run["agent_principal_id"]),
            action="run.timeout",
            outcome="DENY",
            actor=None,
            actor_type="SYSTEM",
            code=str(g.Code.USAGE_LIMIT),
            run_id=str(run["run_id"]),
            now=now,
        )
        ended += 1
    expired = await store.expire_open_requests(conn, now)
    tracked = len(await attention(conn, tenant_id, now, sync=True))
    return {
        "reservations_released": released,
        "runs_timed_out": ended,
        "requests_expired": expired,
        "alerts_tracked": tracked,
    }


# ====================================================================== emergency stop and restore


async def pause(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    *,
    scope: str,
    agent_id: str | None,
    reason: str,
    severity: str = "P1",
    now: datetime | None = None,
) -> dict[str, Any]:
    """Stop now. No approval is asked for (T24): the freeze row is written first and is the source of truth; the agent kill path,
    the cancellation of running work and the release of reserved allowance follow in the same transaction. A repeat is idempotent."""
    now = now or _now()
    if scope not in ("AGENT", "TENANT") or (scope == "AGENT") != bool(agent_id):
        raise GovernanceError(
            "FREEZE_SHAPE_INVALID", "an AGENT freeze names its agent; a TENANT freeze names none"
        )
    for f in await store.active_freezes(conn):
        if f.scope == scope and f.agent_id == agent_id:
            return {
                "case_id": f.case_id,
                "generation": f.generation,
                "scope": scope,
                "agent_id": agent_id,
                "already_frozen": True,
            }
    agents = (
        [agent_id]
        if agent_id
        else [
            str(a["agent_principal_id"])
            for a in await agent_store.list_agents(conn, status="ACTIVE", limit=1000)
        ]
    )
    if agent_id:
        await _agent(conn, agent_id)
    case_id = str(uuid.uuid4())
    generation = await store.insert_freeze(
        conn,
        tenant_id,
        {
            "case_id": case_id,
            "scope": scope,
            "agent_id": agent_id,
            "initiator": actor,
            "reason": reason,
            "severity": severity,
            "issued_at": now,
        },
    )
    await _event(
        conn,
        tenant_id,
        agent_id=agent_id,
        action="freeze.issued",
        outcome="INFO",
        actor=actor,
        code=scope,
        resource=case_id,
        cost_receipt={"generation": generation, "severity": severity},
        now=now,
    )
    suspended = 0
    for aid in agents:
        agent = await machine_store.load_agent(conn, aid)
        if agent is not None and agent.status is AgentStatus.ACTIVE:
            await agent_service.suspend(
                conn, tenant_id, aid, reason="EMERGENCY_FROZEN", actor=actor
            )
            suspended += 1
        for run in await store.running_runs(conn, aid):
            await store.finish_run(
                conn, str(run["run_id"]), state="CANCELED", at=now, reason="EMERGENCY_FROZEN"
            )
            await _release_run_reservations(conn, tenant_id, str(run["run_id"]), actor, now)
    await _audit(
        conn,
        tenant_id,
        "agent.governance.freeze",
        agent_id or tenant_id,
        actor,
        {"scope": scope, "generation": generation, "severity": severity},
    )
    return {
        "case_id": case_id,
        "generation": generation,
        "scope": scope,
        "agent_id": agent_id,
        "agents_suspended": suspended,
        "already_frozen": False,
    }


async def restore_health(
    conn: AsyncConnection, tenant_id: str, freeze: g.Freeze, now: datetime
) -> dict[str, Any]:
    """The fresh checks a restore needs. They are measured now, on the server, and recorded; the requester cannot supply them."""
    chain = await store.verify_chain(conn)
    checks = {"evidence_chain_intact": bool(chain["intact"])}
    if freeze.agent_id:
        agent = await _agent(conn, freeze.agent_id)
        checks["sponsor_active"] = await agent_service.sponsor_is_active(
            conn, agent.sponsor_principal_id
        )
        policy = await store.active_policy(conn, freeze.agent_id)
        approval = await effective_approval(conn, policy)
        checks["policy_bound_to_approval"] = bool(
            policy
            and approval
            and g.is_effective_approval(approval, current_hash=g.policy_hash(policy), now=now)
        )
    else:
        checks["company_baseline_present"] = await store.load_baseline(conn, tenant_id) is not None
    return {"checks": checks, "all_ok": all(checks.values()), "measured_at": now.isoformat()}


async def request_restore(
    conn: AsyncConnection, tenant_id: str, actor: str, case_id: str, *, now: datetime | None = None
) -> Outcome:
    """Never immediate (T25): a request that needs an independent Security signer and an independent Authority signer, neither of them
    the person who froze, and only if fresh health checks pass."""
    now = now or _now()
    freeze = await store.load_freeze(conn, case_id, lock=True)
    if freeze is None:
        raise GovernanceError("FREEZE_NOT_FOUND", "no such emergency case")
    if not freeze.active:
        return Outcome(False, ("ALREADY_RESTORED",))
    health = await restore_health(conn, tenant_id, freeze, now)
    evidence = await _event(
        conn,
        tenant_id,
        agent_id=freeze.agent_id,
        action="restore.health_checked",
        outcome="INFO" if health["all_ok"] else "DENY",
        actor=actor,
        code="OK" if health["all_ok"] else "RESTORE_HEALTH_CHECK_FAILED",
        resource=case_id,
        cost_receipt=health["checks"],
        now=now,
    )
    if not health["all_ok"]:
        return Outcome(False, ("RESTORE_HEALTH_CHECK_FAILED",), data=health)
    for row in await store.list_requests(
        conn, agent_id=freeze.agent_id, states=("PENDING", "APPROVED"), limit=20
    ):
        if (
            row["subject"] == "RESTORE"
            and isinstance(row["payload"], dict)
            and row["payload"].get("case_id") == case_id
        ):
            loaded = await store.load_request(conn, str(row["request_id"]))
            assert loaded
            return Outcome(True, data=request_view(loaded[0], loaded[1], now))
    delta = {"case_id": case_id, "generation": freeze.generation, "scope": freeze.scope}
    req = g.ChangeRequest(
        request_id=str(uuid.uuid4()),
        tenant_id=tenant_id,
        agent_id=freeze.agent_id or tenant_id,
        kind=g.ChangeKind.RESTORE,
        requested_by=actor,
        authors=(actor, freeze.initiator),
        policy_hash=g.digest(delta),
        base_policy_hash=None,
        delta_hash=g.digest(delta),
        required=g.required_slots(g.ChangeKind.RESTORE),
        created_at=now,
        expires_at=now + config().approval_ttl,
    )
    await store.insert_request(
        conn,
        tenant_id,
        req,
        subject="RESTORE",
        policy_id=None,
        delta=delta,
        payload={"case_id": case_id},
        reason_code="RESTORE",
        evidence_ref=evidence,
    )
    await store.attach_restore_request(conn, case_id, req.request_id)
    loaded = await store.load_request(conn, req.request_id)
    assert loaded
    return Outcome(True, data=request_view(loaded[0], loaded[1], now))


async def _finish_restore(
    conn: AsyncConnection,
    tenant_id: str,
    row: dict[str, Any],
    req: g.ChangeRequest,
    actor: str,
    now: datetime,
) -> dict[str, Any]:
    case_id = row["payload"]["case_id"]
    freeze = await store.load_freeze(conn, case_id, lock=True)
    assert freeze is not None
    signers = [s.signer for s in req.signatures]
    held = await store.slots_of_many(conn, signers)
    problems = g.restore_blockers(
        freeze, approvers=signers, approver_slots=held, health_checks_fresh=True
    )
    if problems:
        return {"state": "APPROVED", "restore_blocked": problems}
    health = await restore_health(conn, tenant_id, freeze, now)
    if not health[
        "all_ok"
    ]:  # re-measured at the moment of restore, not at the time it was asked for
        return {"state": "APPROVED", "restore_blocked": ["RESTORE_HEALTH_CHECK_FAILED"]}
    await store.restore_freeze(conn, case_id, by=signers, at=now)
    await store.set_request_state(conn, req.request_id, g.RequestState.APPLIED)
    await _event(
        conn,
        tenant_id,
        agent_id=freeze.agent_id,
        action="freeze.restored",
        outcome="INFO",
        actor=actor,
        resource=case_id,
        now=now,
    )
    return {
        "state": "APPLIED",
        "restored": case_id,
        "note": "The agent stays SUSPENDED until it is activated again through its gates.",
    }


def freeze_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "case_id": str(row["case_id"]),
        "scope": row["scope"],
        "agent_id": str(row["agent_principal_id"]) if row["agent_principal_id"] else None,
        "generation": row["generation"],
        "initiator": str(row["initiator"]),
        "reason": row["reason"],
        "severity": row["severity"],
        "issued_at": _iso(row["issued_at"]),
        "restored_at": _iso(row["restored_at"]),
        "restore_request_id": str(row["restore_request_id"]) if row["restore_request_id"] else None,
        "state": "FROZEN" if row["restored_at"] is None else "RESTORED",
    }


# ====================================================================== monitoring (section 15) and the overview


async def attention(
    conn: AsyncConnection,
    tenant_id: str,
    now: datetime,
    *,
    agent_id: str | None = None,
    sync: bool = False,
) -> list[dict[str, Any]]:
    """The 'Requires attention' list, computed from live state each time (nothing is cached, so a stale list cannot read as healthy)."""
    items: list[dict[str, Any]] = []
    agents = [
        a
        for a in await agent_store.list_agents(conn, limit=500)
        if not agent_id or a["agent_principal_id"] == agent_id
    ]
    freezes = await store.active_freezes(conn)
    for f in freezes:
        if not agent_id or f.scope == "TENANT" or f.agent_id == agent_id:
            items.append(
                {
                    "rule": "INCIDENT_OPEN",
                    "severity": g.ALERT_SEVERITY["INCIDENT_OPEN"],
                    "agent_id": f.agent_id,
                    "detail": f"{f.scope} freeze, generation {f.generation}",
                }
            )
    for a in agents:
        aid = str(a["agent_principal_id"])
        if a["status"] != "ACTIVE":
            continue
        agent = await machine_store.load_agent(conn, aid)
        if agent is not None and not await agent_service.sponsor_is_active(
            conn, agent.sponsor_principal_id
        ):
            items.append(
                {
                    "rule": "SPONSOR_INVALID",
                    "severity": g.ALERT_SEVERITY["SPONSOR_INVALID"],
                    "agent_id": aid,
                    "detail": "no active accountable sponsor",
                }
            )
        policy = await store.active_policy(conn, aid)
        approval = await effective_approval(conn, policy)
        if policy is not None and (
            approval is None
            or not g.is_effective_approval(approval, current_hash=g.policy_hash(policy), now=now)
        ):
            items.append(
                {
                    "rule": "POLICY_DRIFT",
                    "severity": g.ALERT_SEVERITY["POLICY_DRIFT"],
                    "agent_id": aid,
                    "detail": "the active policy has no approval bound to its hash",
                }
            )
        for lease_id in g.expiring_leases(policy, now):
            items.append(
                {
                    "rule": "GRANT_EXPIRING",
                    "severity": g.ALERT_SEVERITY["GRANT_EXPIRING"],
                    "agent_id": aid,
                    "detail": f"lease {lease_id[:8]} ends within 7 days",
                }
            )
        for c in await _ceilings(conn, aid, now):
            level = g.usage_level(c.committed + c.reserved, c.hard_cap, c.warn_pct)
            if level in ("WARN_70", "WARN_90", "EXHAUSTED"):
                items.append(
                    {
                        "rule": "CAP_NEAR_LIMIT",
                        "severity": g.ALERT_SEVERITY["CAP_NEAR_LIMIT"],
                        "agent_id": aid,
                        "detail": f"{c.name}: {level}",
                    }
                )
    backlog = await store.list_requests(conn, agent_id=agent_id, states=("PENDING",), limit=500)
    old = [r for r in backlog if now - r["created_at"] > timedelta(hours=12)]
    if old:
        items.append(
            {
                "rule": "APPROVAL_BACKLOG",
                "severity": g.ALERT_SEVERITY["APPROVAL_BACKLOG"],
                "agent_id": agent_id,
                "detail": f"{len(old)} request(s) unsigned for over 12 hours",
            }
        )
    denied = await store.counts_by_outcome(conn, since=now - timedelta(hours=24), agent_id=agent_id)
    if denied.get("DENY", 0) and any(
        e["code"] == str(g.Code.SCOPE_PROHIBITED)
        for e in await store.list_events(
            conn,
            agent_id=agent_id,
            since=now - timedelta(hours=24),
            until=None,
            before_seq=None,
            outcome="DENY",
            limit=200,
        )
    ):
        items.append(
            {
                "rule": "PROHIBITED_ATTEMPT",
                "severity": g.ALERT_SEVERITY["PROHIBITED_ATTEMPT"],
                "agent_id": agent_id,
                "detail": "a prohibited action was attempted",
            }
        )
    if await _telemetry(conn, now, agent_id) != "CURRENT":
        items.append(
            {
                "rule": "TELEMETRY_UNKNOWN",
                "severity": g.ALERT_SEVERITY["TELEMETRY_UNKNOWN"],
                "agent_id": agent_id,
                "detail": "no recent evidence: health is unknown, not good",
            }
        )
    items = sorted(items, key=lambda i: (i["severity"], i["rule"]))
    return await _with_alert_state(conn, tenant_id, items, now, sync=sync and agent_id is None)


def alert_key(rule: str, agent_id: str | None, detail: str) -> str:
    """One key per distinct condition, so a repeat is grouped into one alert with a count and never listed twice."""
    return hashlib.sha256(f"{rule}|{agent_id or ''}|{detail}".encode()).hexdigest()[:32]


async def _with_alert_state(
    conn: AsyncConnection, tenant_id: str, items: list[dict[str, Any]], now: datetime, *, sync: bool
) -> list[dict[str, Any]]:
    """Attach the acknowledge / route / SLA record to each computed alert. With `sync` the conditions that hold now are recorded (a new one
    starts its SLA clock; one that no longer holds is resolved); without it the stored record is only read."""
    sla = config().alert_sla
    stored = {r["alert_key"]: r for r in await dstore.list_alert_states(conn)}
    present: list[str] = []
    out: list[dict[str, Any]] = []
    for item in items:
        key = alert_key(item["rule"], item.get("agent_id"), item["detail"])
        present.append(key)
        row = stored.get(key)
        if sync:
            row = await dstore.see_alert(
                conn, tenant_id, key=key, rule=item["rule"], agent_id=item.get("agent_id"), severity=item["severity"], now=now,
                sla_due=now + sla[item["severity"]],
            )  # fmt: skip
        merged = {**item, "alert_key": key}
        if row is None:
            merged.update(state="OPEN", tracked=False, occurrences=1, sla_due=_iso(now + sla[item["severity"]]), sla_breached=False,
                          acknowledged_by=None, owner=None, route=None, first_seen=None)  # fmt: skip
        else:
            merged.update(
                state=row["state"], tracked=True, occurrences=row["occurrences"], first_seen=_iso(row["first_seen"]), sla_due=_iso(row["sla_due"]),
                sla_breached=row["state"] == "OPEN" and now > row["sla_due"], acknowledged_by=str(row["acknowledged_by"]) if row["acknowledged_by"] else None,
                owner=str(row["owner"]) if row["owner"] else None, route=row["route"],
            )  # fmt: skip
        out.append(merged)
    if sync:
        await dstore.resolve_absent(conn, present)
    return out


ROUTES = frozenset({*g.SLOTS, "SRE"})


async def acknowledge_alert(
    conn: AsyncConnection,
    tenant_id: str,
    actor: str,
    key: str,
    *,
    note: str | None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """A person says they have seen it. It does not clear the condition and it never lifts a limit: an alert is never enforcement."""
    now = now or _now()
    row = await dstore.get_alert(conn, key, lock=True)
    if row is None:
        raise GovernanceError(
            "ALERT_NOT_FOUND", "no such alert is being tracked: open the overview first"
        )
    if row["state"] == "RESOLVED":
        raise GovernanceError("ALERT_RESOLVED", "the condition no longer holds")
    await dstore.acknowledge_alert(
        conn, key, by=actor, note=(note or "").strip()[:500] or None, at=now
    )
    await _event(conn, tenant_id, agent_id=str(row["agent_principal_id"]) if row["agent_principal_id"] else None, action="alert.acknowledged",
                 outcome="INFO", actor=actor, code=row["rule"], resource=key, now=now)  # fmt: skip
    return {
        "alert_key": key,
        "state": "ACKNOWLEDGED" if row["state"] != "ROUTED" else "ROUTED",
        "acknowledged_by": actor,
    }


async def route_alert(
    conn: AsyncConnection, tenant_id: str, actor: str, key: str, *, route: str, owner: str | None, note: str | None, now: datetime | None = None
) -> dict[str, Any]:  # fmt: skip
    """Give an alert an owner and a team. The owner must be an active member and not an agent."""
    now = now or _now()
    if route not in ROUTES:
        raise GovernanceError("ROUTE_INVALID", "SECURITY, AUTHORITY, PRIVACY, FINANCE or SRE")
    row = await dstore.get_alert(conn, key, lock=True)
    if row is None:
        raise GovernanceError(
            "ALERT_NOT_FOUND", "no such alert is being tracked: open the overview first"
        )
    if row["state"] == "RESOLVED":
        raise GovernanceError("ALERT_RESOLVED", "the condition no longer holds")
    if owner is not None and (
        not await agent_service.sponsor_is_active(conn, owner)
        or await machine_store.load_agent(conn, owner) is not None
    ):
        raise GovernanceError("OWNER_INVALID", "the owner must be an active person")
    await dstore.route_alert(
        conn, key, route=route, owner=owner, note=(note or "").strip()[:500] or None, at=now
    )
    await _event(conn, tenant_id, agent_id=str(row["agent_principal_id"]) if row["agent_principal_id"] else None, action="alert.routed",
                 outcome="INFO", actor=actor, code=route, resource=key, now=now)  # fmt: skip
    return {"alert_key": key, "state": "ROUTED", "route": route, "owner": owner}


async def _telemetry(conn: AsyncConnection, now: datetime, agent_id: str | None) -> str:
    return g.telemetry_state(
        await store.last_event_at(conn, agent_id), now, max_age=config().telemetry_max_age
    )


async def overview(
    conn: AsyncConnection, tenant_id: str, viewer: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    agents = await agent_store.list_agents(conn, limit=500)
    pending = await list_change_requests(
        conn, agent_id=None, states=["PENDING"], viewer=viewer, now=now
    )
    since = now - timedelta(hours=24)
    counts = await store.counts_by_outcome(conn, since=since)
    rows = []
    committed_total = Decimal(0)
    for a in agents:
        aid = str(a["agent_principal_id"])
        policy = await store.active_policy(conn, aid)
        spend = Decimal(0)
        for c in await _ceilings(conn, aid, now):
            if c.scope == "agent" and c.window == "DAY":
                spend = c.committed + c.reserved
        committed_total += spend
        last = await store.last_event_at(conn, aid)
        rows.append(
            {
                "agent_id": aid,
                "name": a.get("agent_name"),
                "sponsor": a.get("sponsor_principal_id"),
                "state": a["status"],
                "risk": policy.risk_tier if policy else None,
                "policy_version": policy.version if policy else None,
                "spend_today": str(spend),
                "last_evidence_at": _iso(last),
                "governed": policy is not None,
            }
        )
    freshness = await _telemetry(conn, now, None)
    alerts = await attention(conn, tenant_id, now, sync=True)
    return {
        "kpis": {
            "active_agents": sum(1 for a in agents if a["status"] == "ACTIVE"),
            "pending_approvals": len(pending),
            "denied_calls_24h": counts.get("DENY", 0),
            "shadow_denials_24h": counts.get("SHADOW_DENY", 0),
            "budget_used_today": str(committed_total),
            "risk_signals": len(alerts),
        },
        "agents": rows,
        "requires_attention": alerts,
        "pending": pending[:20],
        "telemetry": {
            "state": freshness,
            "last_ingest": _iso(await store.last_event_at(conn)),
            "message": None
            if freshness == "CURRENT"
            else "Data temporarily unavailable: health is unknown.",
        },
        "mode": _MODE,
        "as_of": now.isoformat(),
        "timezone": "UTC",
        "counts_source": "server-generated, not sampled",
    }


async def agent_monitoring(
    conn: AsyncConnection, tenant_id: str, agent_id: str, now: datetime | None = None
) -> dict[str, Any]:
    now = now or _now()
    agent = await _agent(conn, agent_id)
    since = now - timedelta(hours=24)
    counts = await store.counts_by_outcome(conn, since=since, agent_id=agent_id)
    policy = await store.active_policy(conn, agent_id)
    freezes = [
        f for f in await store.active_freezes(conn) if f.scope == "TENANT" or f.agent_id == agent_id
    ]
    runs = await store.running_runs(conn, agent_id)
    return {
        "agent_id": agent_id,
        "name": agent.agent_name,
        "state": agent.status.value,
        "summary": {
            "allowed_24h": counts.get("ALLOW", 0),
            "denied_24h": counts.get("DENY", 0),
            "shadow_denied_24h": counts.get("SHADOW_DENY", 0),
        },
        "sponsor": {
            "principal_id": agent.sponsor_principal_id,
            "active": await agent_service.sponsor_is_active(conn, agent.sponsor_principal_id),
        },
        "expires_at": _iso(agent.expires_at),
        "policy": policy_view(policy, now) if policy else None,
        "active_runs": [_run_view(r) for r in runs],
        "alerts": await attention(conn, tenant_id, now, agent_id=agent_id),
        "incident": [
            {"case_id": f.case_id, "scope": f.scope, "generation": f.generation, "reason": f.reason}
            for f in freezes
        ],
        "telemetry": {
            "state": await _telemetry(conn, now, agent_id),
            "last_ingest": _iso(await store.last_event_at(conn, agent_id)),
        },
        "as_of": now.isoformat(),
    }


async def events(
    conn: AsyncConnection,
    *,
    agent_id: str | None,
    since: datetime | None,
    until: datetime | None,
    before_seq: int | None,
    outcome: str | None,
    limit: int,
    detail: bool,
) -> dict[str, Any]:
    rows = await store.list_events(
        conn,
        agent_id=agent_id,
        since=since,
        until=until,
        before_seq=before_seq,
        outcome=outcome,
        limit=limit + 1,
    )
    page, more = rows[:limit], len(rows) > limit
    items = []
    for r in page:
        item = {
            **r,
            "event_id": str(r["event_id"]),
            "agent_principal_id": str(r["agent_principal_id"]) if r["agent_principal_id"] else None,
            "run_id": str(r["run_id"]) if r["run_id"] else None,
            "actor": str(r["actor"]) if r["actor"] else None,
            "occurred_at": _iso(r["occurred_at"]),
        }
        items.append(g.redact_event(item, detail=detail))
    return {
        "items": items,
        "next_cursor": page[-1]["seq"] if more and page else None,
        "redacted": not detail,
    }


async def sweep_all_tenants(conn: AsyncConnection, now: datetime) -> list[dict[str, Any]]:
    """Worker pass (BYPASSRLS): run the per-tenant sweep for every tenant that has something overdue."""
    from sqlalchemy import text

    rows = (
        await conn.execute(
            text(
                "SELECT tenant_id FROM agent.budget_reservation WHERE state = 'RESERVED' AND expires_at <= :n "
                "UNION SELECT tenant_id FROM agent.run_ledger WHERE state = 'RUNNING' AND deadline_at <= :n "
                "UNION SELECT tenant_id FROM agent.governance_change_request "
                "WHERE state IN ('PENDING','CLARIFICATION','APPROVED') AND expires_at <= :n"
            ),
            {"n": now},
        )
    ).all()
    out = []
    for (tenant,) in rows:
        await conn.execute(text("SELECT set_config('app.tenant_id', :t, true)"), {"t": str(tenant)})
        out.append({"tenant_id": str(tenant), **await governance_sweep(conn, str(tenant), now)})
    return out


EXPORT_COLUMNS = (
    "seq", "event_id", "occurred_at", "agent_principal_id", "action", "outcome", "code", "digest", "prev_digest",
    "run_id", "actor", "actor_type", "tool_id", "resource", "region", "policy_hash", "correlation_id", "cost_receipt",
)  # fmt: skip
EXPORT_MAX = 5000


def _cell(value: Any) -> str:
    """One CSV cell. A value a spreadsheet would run as a formula (a leading = + - @, tab or carriage return) is made inert."""
    text_ = (
        ""
        if value is None
        else (json.dumps(value, sort_keys=True) if isinstance(value, (dict, list)) else str(value))
    )
    return "'" + text_ if text_[:1] in ("=", "+", "-", "@", "\t", "\r") else text_


async def export_events(
    conn: AsyncConnection, tenant_id: str, actor: str, *, agent_id: str | None, since: datetime | None, until: datetime | None, limit: int,
    detail: bool, now: datetime | None = None,
) -> tuple[str, int]:  # fmt: skip
    """A CSV of the evidence stream, redacted by the caller's role exactly as the page is (a role without detail gets no resource, actor, tool,
    region, policy hash, trace or cost columns at all). Bounded, newest first, and the export itself is an evidence row. Returns (csv, rows)."""
    import csv
    import io

    now = now or _now()
    limit = max(1, min(limit, EXPORT_MAX))
    rows: list[dict[str, Any]] = []
    cursor: int | None = None
    while len(rows) < limit:
        page = await events(
            conn,
            agent_id=agent_id,
            since=since,
            until=until,
            before_seq=cursor,
            outcome=None,
            limit=min(200, limit - len(rows)),
            detail=detail,
        )
        rows.extend(page["items"])
        cursor = page["next_cursor"]
        if cursor is None:
            break
    columns = [c for c in EXPORT_COLUMNS if not rows or any(c in r for r in rows)]
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\n")
    writer.writerow(columns)
    for r in rows:
        writer.writerow([_cell(r.get(c)) for c in columns])
    await _event(
        conn, tenant_id, agent_id=agent_id, action="export.created", outcome="INFO", actor=actor, code="DETAIL" if detail else "REDACTED",
        cost_receipt={"rows": len(rows), "columns": len(columns)}, now=now,
    )  # fmt: skip
    return out.getvalue(), len(rows)
