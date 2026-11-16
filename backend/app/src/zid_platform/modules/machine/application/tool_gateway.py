"""The agent-to-tool gateway (ZID-GAP-001 WP-11; MACHINE-001 §13-15; MACH-13, MACH-15, MACH-17).

One question is answered here: may this agent, acting through this delegation chain, call this tool, on this
resource, with exactly these arguments, right now? The answer is never an agent's own claim. The gateway reads the
chain, the tool and the sponsors from the registry, runs the pre-checks (`domain.tool_gateway`), asks the policy
decision point about the tool's catalogued action (the same decision the AuthZEN endpoint gives), requires a human
approval bound to this exact call when the tool needs one, and only then signs a short-lived grant for the tool's own
server. Every outcome, allow or deny, leaves an audit record that names the whole chain.

A denial is a result, not an exception: the caller records it before turning it into an HTTP error, so a refused
call is never lost to a rolled-back transaction.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncConnection
from zid_context import uuid7

from zid_platform.modules.authority.ports import evaluate_approval
from zid_platform.modules.decision.ports import AccessQuery
from zid_platform.modules.evidence.ports import EvidenceClass
from zid_platform.modules.machine.application import governance
from zid_platform.modules.machine.application.agents import _record, sponsor_is_active
from zid_platform.modules.machine.domain.agents import AgentProfile, MachineError, Tool
from zid_platform.modules.machine.domain.api_agents import api_tool_blockers
from zid_platform.modules.machine.domain.tool_gateway import (
    APPROVAL_REQUIRED,
    ARGS_DIGEST,
    GRANT_TYP,
    MAX_CHAIN,
    approval_blockers,
    call_context,
    chain_blockers,
    chain_commitment,
    grant_claims,
    grant_problems,
    mandate_blockers,
)
from zid_platform.modules.machine.infrastructure import agent_store, grant_store, machine_store

Decide = Callable[[AccessQuery], Awaitable[dict[str, Any]]]
Sign = Callable[[Mapping[str, Any], str], str]


@dataclass(frozen=True, slots=True)
class ToolCall:
    """What the agent asks for. The agent is whoever the gateway authenticated, never a field the caller chose."""

    agent_id: str
    tool_id: str
    resource_type: str
    resource_id: str
    arguments_digest: str
    represented_principal_id: str | None = None
    purpose: str | None = None
    mandate_id: str | None = None
    approval_request_id: str | None = None
    value: tuple[str, str] | None = (
        None  # (amount as a decimal string, ISO currency) when the call spends money
    )


@dataclass(frozen=True, slots=True)
class GatewayResult:
    allowed: bool
    codes: tuple[str, ...] = ()
    decision_id: str | None = None
    grant: str | None = None
    expires_at: datetime | None = None
    chain: tuple[str, ...] = ()
    approval_context: str | None = None  # what an approval must be bound to, when one is required


async def load_chain(conn: AsyncConnection, agent_id: str) -> list[AgentProfile]:
    """The caller and every parent above it, leaf first. The walk is bounded, so a loop in bad data ends it."""
    chain: list[AgentProfile] = []
    current: str | None = agent_id
    while current is not None and len(chain) <= MAX_CHAIN:
        agent = await machine_store.load_agent(conn, current)
        if agent is None:
            if not chain:
                raise MachineError("AGENT_NOT_FOUND", "this principal is not a registered agent")
            break  # an unresolved parent: the pre-checks refuse the chain
        chain.append(agent)
        current = agent.parent_agent_id
    return chain


async def _approval_state(
    conn: AsyncConnection, call: ToolCall, context: str, now: datetime
) -> str | None:
    if call.approval_request_id is None:
        return None
    try:
        outcome = await evaluate_approval(
            conn,
            request_id=call.approval_request_id,
            current_transaction_context=context,
            now=now,
        )
        return str(outcome.state.value)
    except ValueError:
        return "DENIED"


async def _audit(
    conn: AsyncConnection,
    tenant_id: str,
    call: ToolCall,
    tool: Tool | None,
    chain: list[AgentProfile],
    *,
    outcome: str,
    codes: tuple[str, ...],
    decision_id: str | None,
    grant_jti: str | None = None,
) -> None:
    await _record(
        conn,
        tenant_id,
        name=f"tool_call.{outcome}",
        event=False,
        aggregate_type="agent",
        aggregate_id=call.agent_id,
        actor=call.agent_id,
        actor_type="AGENT",
        evidence=EvidenceClass.E3,
        outcome="DENIED" if outcome in ("denied", "refused") else "SUCCESS",
        data={
            "tool_id": call.tool_id,
            "action": tool.canonical_action if tool else "",
            "resource": f"{call.resource_type}:{call.resource_id}",
            "args_sha256": call.arguments_digest,
            "agent_chain": ",".join(a.agent_principal_id for a in chain),
            "chain_commitment": chain_commitment(chain) if chain else "",
            "represented_principal_id": call.represented_principal_id or "",
            "reason_codes": ",".join(codes),
            "decision_id": decision_id or "",
            "grant_jti": grant_jti or "",
        },
    )


async def authorize_tool_call(
    conn: AsyncConnection,
    tenant_id: str,
    call: ToolCall,
    *,
    decide: Decide,
    sign: Sign,
    issuer: str,
    action_catalogued: Callable[[str], bool],
    now: datetime | None = None,
) -> GatewayResult:
    now = now or datetime.now(UTC)
    if not ARGS_DIGEST.match(call.arguments_digest):
        raise MachineError("TOOL_CALL_INVALID", "arguments_digest must be sha256:<64 hex>")
    chain = await load_chain(conn, call.agent_id)
    ids = tuple(a.agent_principal_id for a in chain)
    registry = await machine_store.load_tools(conn)
    tool = registry.get(call.tool_id)
    sponsors = {
        sid: await sponsor_is_active(conn, sid)
        for sid in {a.sponsor_principal_id for a in chain if a.sponsor_principal_id}
    }

    problems = chain_blockers(
        chain, sponsors_active=sponsors, tool_id=call.tool_id, registry=registry, now=now
    )
    if tool is not None and await agent_store.api_bound_agents(conn, list(ids)):
        problems.extend(
            api_tool_blockers(tool.side_effect.value, tool.default_risk_class)
        )  # CR-093: the code behind an API client is unproven
    if tool is not None and not action_catalogued(tool.canonical_action):
        problems.append(
            "ACTION_UNKNOWN"
        )  # a tool whose action is not catalogued has no policy, so no permission
    if tool is not None:
        problems.extend(
            mandate_blockers(
                tool,
                mandate_id=call.mandate_id,
                represented_principal_id=call.represented_principal_id,
            )
        )
    context: str | None = None
    if tool is not None and not problems:
        context = call_context(
            tool.canonical_action, f"{call.resource_type}:{call.resource_id}", call.arguments_digest
        )
        problems.extend(approval_blockers(tool, await _approval_state(conn, call, context, now)))
    if tool is not None and not problems:
        # ZID-AG-WF-002: the governance gates (freeze, lease, region, baseline, approval hash, quotas). In shadow they record what
        # enforce would refuse; an emergency freeze refuses in every mode.
        problems.extend(
            await governance.govern_tool_call(
                conn,
                tenant_id,
                agent_id=chain[0].agent_principal_id,
                tool=tool,
                resource_type=call.resource_type,
                resource_id=call.resource_id,
                purpose=call.purpose,
                now=now,
            )
        )
    if problems:
        codes = tuple(problems)
        await _audit(
            conn, tenant_id, call, tool, chain, outcome="denied", codes=codes, decision_id=None
        )
        return GatewayResult(
            False,
            codes,
            chain=ids,
            approval_context=context if APPROVAL_REQUIRED in codes else None,
        )

    assert tool is not None  # no problems means the tool resolved
    answer = await decide(
        AccessQuery(
            subject_type="principal",
            subject_id=chain[0].agent_principal_id,
            action=tool.canonical_action,
            resource_type=call.resource_type,
            resource_id=call.resource_id,
            purpose=call.purpose,
            risk_class=f"R{min(3, max(0, tool.default_risk_class))}",
            represented_principal_id=call.represented_principal_id,
            mandate_id=call.mandate_id,
            tool_id=tool.tool_id,
            value=call.value,
        )
    )
    verdict = answer.get("context", {})
    decision_id = verdict.get("decision_id")
    if answer.get("decision") is not True:
        codes = tuple(verdict.get("reason_codes") or ["POLICY_DENIED"])
        await _audit(
            conn,
            tenant_id,
            call,
            tool,
            chain,
            outcome="denied",
            codes=codes,
            decision_id=decision_id,
        )
        return GatewayResult(False, codes, decision_id=decision_id, chain=ids)

    valid_until = verdict.get("valid_until")
    nonce = str(uuid7())
    claims = grant_claims(
        issuer=issuer,
        tenant_id=tenant_id,
        tool=tool,
        chain=chain,
        resource=f"{call.resource_type}:{call.resource_id}",
        arguments_digest=call.arguments_digest,
        represented_principal_id=call.represented_principal_id,
        decision_id=str(decision_id),
        policy_release_id=verdict.get("policy_release_id"),
        nonce=nonce,
        now=now,
        decision_valid_until=datetime.fromisoformat(valid_until) if valid_until else None,
    )
    grant = sign(claims, GRANT_TYP)
    await grant_store.save_grant(
        conn,
        tenant_id,
        grant_id=nonce,
        agent_id=chain[0].agent_principal_id,
        tool_id=tool.tool_id,
        audience=tool.server_principal_id,
        resource=claims["resource"],
        args_sha256=call.arguments_digest,
        decision_id=str(decision_id),
        issued_at=now,
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
    )
    await _audit(
        conn,
        tenant_id,
        call,
        tool,
        chain,
        outcome="authorized",
        codes=(),
        decision_id=decision_id,
        grant_jti=nonce,
    )
    return GatewayResult(
        True,
        decision_id=decision_id,
        grant=grant,
        expires_at=datetime.fromtimestamp(claims["exp"], UTC),
        chain=ids,
    )


Verify = Callable[[str], Mapping[str, Any]]


@dataclass(frozen=True, slots=True)
class RedeemResult:
    ok: bool
    code: str | None = None
    call: dict[str, Any] | None = None


async def redeem_grant(
    conn: AsyncConnection,
    tenant_id: str,
    *,
    grant: str,
    server_id: str,
    arguments_digest: str,
    verify: Verify,
    now: datetime | None = None,
) -> RedeemResult:
    """The tool's server spends a grant, once, at the moment it is about to act (ZID-GAP-001 §3.1: revalidate at execution,
    commit once). The signature is verified, the grant must be for this server, tenant and the arguments the server is about
    to run, and the delegation chain is checked again NOW: a sponsor who left, a suspended parent or an expired agent since
    the grant was signed stops the call. Only then is the grant marked used, in one atomic step."""
    now = now or datetime.now(UTC)
    if not ARGS_DIGEST.match(arguments_digest):
        raise MachineError("TOOL_CALL_INVALID", "arguments_digest must be sha256:<64 hex>")
    try:
        claims = verify(grant)
    except Exception as e:
        raise MachineError("TOOL_GRANT_INVALID", "this is not a grant this service signed") from e
    problems = grant_problems(
        claims, audience=server_id, tenant_id=tenant_id, arguments_digest=arguments_digest, now=now
    )
    if problems:
        raise MachineError(problems[0], ", ".join(problems))
    record = await grant_store.load_grant(conn, str(claims["jti"]))
    if record is None or str(record["audience"]) != server_id:
        raise MachineError("TOOL_GRANT_INVALID", "this grant is not on record")
    registry = await machine_store.load_tools(conn)
    chain = await load_chain(conn, str(claims["agent_chain"][0]))
    sponsors = {
        sid: await sponsor_is_active(conn, sid)
        for sid in {a.sponsor_principal_id for a in chain if a.sponsor_principal_id}
    }
    current = chain_blockers(
        chain,
        sponsors_active=sponsors,
        tool_id=str(claims["tool_id"]),
        registry=registry,
        now=now,
    )
    ids = [a.agent_principal_id for a in chain]
    frozen = await governance.frozen_codes(
        conn, ids
    )  # a freeze issued after the grant was signed still stops it
    if frozen:
        current = [*frozen, *current]
    call = ToolCall(
        agent_id=ids[0],
        tool_id=str(claims["tool_id"]),
        resource_type=str(claims["resource"]).split(":", 1)[0],
        resource_id=str(claims["resource"]).split(":", 1)[-1],
        arguments_digest=arguments_digest,
        represented_principal_id=claims.get("on_behalf_of"),
    )
    refusal: str | None = None
    if (
        current
        or ids != claims["agent_chain"]
        or chain_commitment(chain) != claims["chain_commitment"]
    ):
        refusal = current[0] if current else "AGENT_CHAIN_INVALID"
    elif await grant_store.consume(conn, str(claims["jti"]), by=server_id, now=now) is None:
        refusal = "TOOL_GRANT_USED"
    if refusal is not None:
        await _audit(
            conn,
            tenant_id,
            call,
            registry.get(call.tool_id),
            chain,
            outcome="refused",
            codes=(refusal,),
            decision_id=str(claims["decision_id"]),
            grant_jti=str(claims["jti"]),
        )
        return RedeemResult(False, refusal)
    await _audit(
        conn,
        tenant_id,
        call,
        registry.get(call.tool_id),
        chain,
        outcome="redeemed",
        codes=(),
        decision_id=str(claims["decision_id"]),
        grant_jti=str(claims["jti"]),
    )
    return RedeemResult(
        True,
        call={
            "tool_id": claims["tool_id"],
            "action": claims["action"],
            "resource": claims["resource"],
            "arguments_digest": arguments_digest,
            "agent_chain": ids,
            "on_behalf_of": claims.get("on_behalf_of"),
            "decision_id": claims["decision_id"],
        },
    )
