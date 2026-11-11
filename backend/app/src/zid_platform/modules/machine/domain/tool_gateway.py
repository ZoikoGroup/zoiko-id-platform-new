"""The agent-to-tool gateway: what must hold before a tool call may run (ZID-GAP-001 WP-11; MACHINE-001 §13-15).

A tool call is the point where an agent's identity becomes an effect in the world, so it is checked at that point and
not earlier: every agent in the delegation chain (the caller, its parent, ... up to the root that a person sponsors)
must be ACTIVE, unexpired and accountable to an active sponsor; the tool must be registered and ACTIVE; every agent in
the chain must list the tool; and the chain may not be deeper than its root allows. These are pre-checks only. They grant
nothing: the policy decision and the mandate still decide (MACH-13), and an irreversible tool also needs a human
approval that is bound to this exact call. What the gateway finally hands out is a short-lived grant, bound to one
tool, one resource, one set of arguments (by digest), one audience and one chain, with a nonce.

Model output is never policy: nothing here reads free text from a model. Pure domain, no I/O.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from datetime import datetime, timedelta
from typing import Any

from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    Tool,
    lifecycle_breach,
    tool_invocation_blockers,
    tool_requires_mandate,
)

GRANT_TYP = "action-grant+jwt"
GRANT_LIFETIME = timedelta(
    seconds=120
)  # short enough that a leaked grant is nearly worthless, long enough to call
MAX_CHAIN = 6  # a root and up to five levels of sub-agents (the registry caps depth at 5)
ARGS_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
MANDATE_REQUIRED = "MANDATE_REQUIRED"


def args_digest(arguments: Mapping[str, Any]) -> str:
    """The digest a caller binds a call to: canonical JSON (sorted keys, no spaces), SHA-256."""
    canonical = json.dumps(arguments, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def chain_commitment(chain: Sequence[AgentProfile]) -> str:
    """One value that names the whole delegation chain, leaf to root, so a grant cannot be replayed under another."""
    return (
        "sha256:"
        + hashlib.sha256("|".join(a.agent_principal_id for a in chain).encode()).hexdigest()
    )


def call_context(action: str, resource: str, arguments_digest: str) -> str:
    """What a human approval is bound to for a tool call: this action, on this resource, with exactly these arguments
    (the shape the approval service already uses: action|resource|change)."""
    return f"{action}|{resource}|{arguments_digest}"


def chain_blockers(
    chain: Sequence[AgentProfile],
    *,
    sponsors_active: Mapping[str, bool],
    tool_id: str,
    registry: Mapping[str, Tool],
    now: datetime,
) -> list[str]:
    """Why this call may not proceed, most basic reason first; empty means the pre-checks pass. `chain` runs leaf
    (the caller) to root. A sponsor missing from `sponsors_active` counts as not active (fail closed)."""
    if not chain or len(chain) > MAX_CHAIN:
        return ["AGENT_CHAIN_INVALID"]
    ids = [a.agent_principal_id for a in chain]
    linked = all(
        chain[i].parent_agent_id == chain[i + 1].agent_principal_id for i in range(len(chain) - 1)
    )
    if (
        len(set(ids)) != len(ids)
        or not linked
        or chain[-1].parent_agent_id
        is not None  # the walk stopped before the root: an unresolved parent
        or len({a.tenant_id for a in chain}) != 1
    ):
        return ["AGENT_CHAIN_INVALID"]
    problems: list[str] = []
    if len(chain) - 1 > chain[-1].max_subagent_depth:
        problems.append("GRANT_DEPTH_EXCEEDED")
    for agent in chain:
        if agent.status is not AgentStatus.ACTIVE:
            problems.append("AGENT_NOT_ACTIVE")
            continue
        breach = lifecycle_breach(
            agent,
            sponsor_active=sponsors_active.get(agent.sponsor_principal_id or "", False),
            now=now,
        )
        if breach is not None:
            problems.append(breach)
    problems.extend(tool_invocation_blockers(chain[0], tool_id, registry))
    if any(tool_id not in a.allowed_tools for a in chain):
        problems.append(
            "MANDATE_TOOL_NOT_ALLOWED"
        )  # a parent that dropped the tool takes it from its children
    return list(dict.fromkeys(problems))


def approval_blockers(tool: Tool, approval_state: str | None) -> list[str]:
    """A tool that needs a human approval is not run on the agent's word: the approval must be APPROVED and bound to
    this exact call (`call_context`)."""
    if tool.requires_human_approval and approval_state != "APPROVED":
        return [APPROVAL_REQUIRED]
    return []


def mandate_blockers(
    tool: Tool, *, mandate_id: str | None, represented_principal_id: str | None
) -> list[str]:
    """A tool that needs a mandate (every irreversible or external-transaction tool does) is not run on the agent's own
    account. The caller must name the mandate and the person it acts for; that makes the policy decision validate the
    mandate (active, in scope, tool allowed). Without both, the decision would never look at a mandate at all."""
    if tool_requires_mandate(tool) and not (mandate_id and represented_principal_id):
        return [MANDATE_REQUIRED]
    return []


def grant_claims(
    *,
    issuer: str,
    tenant_id: str,
    tool: Tool,
    chain: Sequence[AgentProfile],
    resource: str,
    arguments_digest: str,
    represented_principal_id: str | None,
    decision_id: str,
    policy_release_id: str | None,
    nonce: str,
    now: datetime,
    decision_valid_until: datetime | None = None,
) -> dict[str, Any]:
    """The claims of the action grant. The audience is the tool's own server, so the grant is useless anywhere else;
    it never outlives the policy decision it rests on."""
    expires = now + GRANT_LIFETIME
    if decision_valid_until is not None:
        expires = min(expires, decision_valid_until)
    caller = chain[0]
    claims: dict[str, Any] = {
        "iss": issuer,
        "aud": tool.server_principal_id,
        "sub": caller.agent_principal_id,
        "tenant_id": tenant_id,
        "jti": nonce,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int(expires.timestamp()),
        "tool_id": tool.tool_id,
        "tool_version": tool.version,
        "action": tool.canonical_action,
        "resource": resource,
        "args_sha256": arguments_digest,
        "side_effect": tool.side_effect.value,
        "agent_chain": [a.agent_principal_id for a in chain],
        "chain_commitment": chain_commitment(chain),
        "decision_id": decision_id,
        "policy_release_id": policy_release_id,
    }
    if represented_principal_id:
        claims["on_behalf_of"] = represented_principal_id
    return claims


def grant_problems(
    claims: Mapping[str, Any],
    *,
    audience: str,
    tenant_id: str,
    arguments_digest: str,
    now: datetime,
) -> list[str]:
    """What is wrong with a grant a tool's server is about to act on (after its signature was verified). Pure, so a
    server can run it without any Zoiko service: audience is its own principal id, the digest is of the arguments it
    will actually execute. A grant made for another server, another tenant, other arguments or an earlier time is refused."""
    problems = []
    if claims.get("aud") != audience:
        problems.append("TOOL_GRANT_AUDIENCE")
    if claims.get("tenant_id") != tenant_id:
        problems.append("TOOL_GRANT_TENANT")
    if claims.get("args_sha256") != arguments_digest:
        problems.append(
            "TOOL_GRANT_ARGUMENTS"
        )  # the approved payload and the executed payload must be one
    exp, nbf = claims.get("exp"), claims.get("nbf")
    stamp = int(now.timestamp())
    if not isinstance(exp, int) or exp <= stamp:
        problems.append("TOOL_GRANT_EXPIRED")
    if not isinstance(nbf, int) or nbf > stamp:
        problems.append("TOOL_GRANT_NOT_YET_VALID")
    if not claims.get("jti") or not isinstance(claims.get("agent_chain"), list):
        problems.append("TOOL_GRANT_MALFORMED")
    return problems
