"""WP-11 (ZID-GAP-001): the agent-to-tool gateway rules, pure domain.

Each case is an attack or a failure from the plan's test catalog: a forged or stale delegation chain, a widened
tool scope, a parent that was suspended, an orphaned or expired agent, a tool that is not executable, an
irreversible tool without a bound human approval, and a grant that must be bound to one call.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from zid_platform.modules.machine.domain.agents import (
    AgentProfile,
    AgentStatus,
    SideEffect,
    Tool,
    ToolStatus,
)
from zid_platform.modules.machine.domain.tool_gateway import (
    APPROVAL_REQUIRED,
    GRANT_LIFETIME,
    approval_blockers,
    args_digest,
    call_context,
    chain_blockers,
    chain_commitment,
    grant_claims,
    grant_problems,
    mandate_blockers,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
TOOL = "payments.release"


def agent(aid: str, parent: str | None = None, **over: object) -> AgentProfile:
    base: dict[str, object] = {
        "agent_principal_id": aid,
        "tenant_id": "t1",
        "agent_name": aid,
        "blueprint_id": "bp",
        "blueprint_version": "1",
        "publisher_principal_id": "pub",
        "operator_principal_id": "op",
        "sponsor_principal_id": f"sponsor-{aid}",
        "owning_organization_id": "org",
        "model_provider": "",
        "allowed_tools": frozenset({TOOL}),
        "max_subagent_depth": 2,
        "status": AgentStatus.ACTIVE,
        "parent_agent_id": parent,
    }
    return AgentProfile(**{**base, **over})  # type: ignore[arg-type]


def tool(**over: object) -> Tool:
    base: dict[str, object] = {
        "tool_id": TOOL,
        "version": "1",
        "server_principal_id": "server-1",
        "canonical_action": "payment.release",
        "default_risk_class": 3,
        "side_effect": SideEffect.EXTERNAL_TRANSACTION,
        "requires_mandate": True,
        "requires_human_approval": False,
        "status": ToolStatus.ACTIVE,
    }
    return Tool(**{**base, **over})  # type: ignore[arg-type]


REGISTRY = {TOOL: tool()}
ROOT = agent("root", max_subagent_depth=2)
MID = agent("mid", "root", max_subagent_depth=1)
LEAF = agent("leaf", "mid", max_subagent_depth=0)
CHAIN = [LEAF, MID, ROOT]
SPONSORS = {f"sponsor-{n}": True for n in ("root", "mid", "leaf")}


def blockers(chain=CHAIN, sponsors=SPONSORS, registry=REGISTRY, tool_id=TOOL) -> list[str]:  # type: ignore[no-untyped-def]
    return chain_blockers(
        chain, sponsors_active=sponsors, tool_id=tool_id, registry=registry, now=NOW
    )


def test_a_healthy_chain_passes_the_pre_checks() -> None:
    assert blockers() == []
    assert blockers([ROOT]) == []  # a root agent calling for itself


def test_a_suspended_parent_stops_every_agent_below_it() -> None:
    parent_down = [LEAF, replace(MID, status=AgentStatus.SUSPENDED), ROOT]
    assert blockers(parent_down) == ["AGENT_NOT_ACTIVE"]


def test_a_revoked_or_unsubmitted_caller_cannot_call() -> None:
    for status in (AgentStatus.REVOKED, AgentStatus.DRAFT, AgentStatus.RETIRED):
        assert "AGENT_NOT_ACTIVE" in blockers([replace(ROOT, status=status)])


def test_an_orphaned_agent_anywhere_in_the_chain_is_refused() -> None:
    gone = {**SPONSORS, "sponsor-mid": False}
    assert blockers(sponsors=gone) == ["SPONSOR_NOT_ACTIVE"]
    assert blockers(sponsors={}) == [
        "SPONSOR_NOT_ACTIVE"
    ]  # a sponsor nobody could read is a sponsor that is gone


def test_an_expired_agent_is_refused_before_the_sweep_runs() -> None:
    old = replace(ROOT, expires_at=NOW - timedelta(seconds=1))
    assert blockers([LEAF, MID, old]) == ["AGENT_EXPIRED"]


def test_a_tool_a_parent_dropped_is_refused_for_its_children() -> None:
    narrowed = replace(MID, allowed_tools=frozenset({"other.tool"}))
    assert blockers([LEAF, narrowed, ROOT]) == ["MANDATE_TOOL_NOT_ALLOWED"]


def test_a_tool_the_caller_never_listed_is_refused() -> None:
    assert blockers([replace(ROOT, allowed_tools=frozenset())]) == ["MANDATE_TOOL_NOT_ALLOWED"]


def test_a_tool_that_is_not_active_or_not_registered_is_not_executable() -> None:
    assert blockers(registry={}) == ["TOOL_NOT_REGISTERED"]
    suspended = {TOOL: tool(status=ToolStatus.SUSPENDED)}
    draft = {TOOL: tool(status=ToolStatus.DRAFT)}
    assert blockers(registry=suspended) == ["TOOL_NOT_REGISTERED"]
    assert blockers(registry=draft) == ["TOOL_NOT_REGISTERED"]


def test_a_chain_deeper_than_its_root_allows_is_refused() -> None:
    shallow = replace(ROOT, max_subagent_depth=1)
    assert blockers([LEAF, MID, shallow]) == ["GRANT_DEPTH_EXCEEDED"]


def test_a_forged_chain_is_refused() -> None:
    assert blockers([]) == ["AGENT_CHAIN_INVALID"]
    assert blockers([LEAF, ROOT]) == [
        "AGENT_CHAIN_INVALID"
    ]  # leaf claims a parent that is not its parent
    assert blockers([LEAF, MID]) == ["AGENT_CHAIN_INVALID"]  # the walk stopped before the root
    assert blockers([ROOT, ROOT]) == ["AGENT_CHAIN_INVALID"]  # a loop
    foreign = replace(ROOT, tenant_id="t2")
    assert blockers([LEAF, MID, foreign]) == ["AGENT_CHAIN_INVALID"]  # a parent from another tenant
    too_long = [agent(f"a{i}", f"a{i + 1}") for i in range(7)]
    assert blockers(too_long) == ["AGENT_CHAIN_INVALID"]


def test_an_irreversible_tool_needs_an_approval_bound_to_the_call() -> None:
    needs = tool(requires_human_approval=True)
    assert approval_blockers(needs, None) == [APPROVAL_REQUIRED]
    for state in ("PENDING", "DENIED", "EXPIRED", "STALE"):
        assert approval_blockers(needs, state) == [APPROVAL_REQUIRED]
    assert approval_blockers(needs, "APPROVED") == []
    assert approval_blockers(tool(), None) == []


def test_an_approval_is_bound_to_the_exact_arguments() -> None:
    a = call_context("payment.release", "invoice:1", args_digest({"amount": "100.00", "to": "x"}))
    b = call_context("payment.release", "invoice:1", args_digest({"amount": "100.01", "to": "x"}))
    assert a != b  # change one cent and the approval no longer matches


def test_the_argument_digest_is_canonical() -> None:
    assert args_digest({"a": 1, "b": [1, 2]}) == args_digest({"b": [1, 2], "a": 1})
    assert args_digest({"a": 1}).startswith("sha256:") and len(args_digest({})) == 71


def test_the_grant_is_bound_to_one_call_and_one_audience() -> None:
    digest = args_digest({"amount": "5.00"})
    claims = grant_claims(
        issuer="https://id.zoiko.test",
        tenant_id="t1",
        tool=tool(),
        chain=CHAIN,
        resource="invoice:1",
        arguments_digest=digest,
        represented_principal_id="person-1",
        decision_id="dec-1",
        policy_release_id="rel-1",
        nonce="n-1",
        now=NOW,
    )
    assert claims["aud"] == "server-1"  # only the tool's own server may accept it
    assert claims["sub"] == "leaf" and claims["agent_chain"] == ["leaf", "mid", "root"]
    assert claims["args_sha256"] == digest and claims["resource"] == "invoice:1"
    assert claims["action"] == "payment.release" and claims["jti"] == "n-1"
    assert claims["on_behalf_of"] == "person-1"
    assert claims["chain_commitment"] == chain_commitment(CHAIN)
    assert claims["exp"] - claims["iat"] == int(GRANT_LIFETIME.total_seconds())
    other_chain = chain_commitment([MID, ROOT])
    assert (
        other_chain != claims["chain_commitment"]
    )  # the same grant cannot be replayed under another chain


def test_the_grant_never_outlives_the_decision_it_rests_on() -> None:
    claims = grant_claims(
        issuer="i", tenant_id="t1", tool=tool(), chain=CHAIN, resource="r",
        arguments_digest=args_digest({}), represented_principal_id=None, decision_id="d",
        policy_release_id=None, nonce="n", now=NOW, decision_valid_until=NOW + timedelta(seconds=30),
    )  # fmt: skip
    assert claims["exp"] - claims["iat"] == 30
    assert "on_behalf_of" not in claims


def _claims(**over: object) -> dict[str, object]:
    digest = args_digest({"a": 1})
    base = grant_claims(
        issuer="i", tenant_id="t1", tool=tool(), chain=CHAIN, resource="r", arguments_digest=digest,
        represented_principal_id=None, decision_id="d", policy_release_id=None, nonce="n", now=NOW,
    )  # fmt: skip
    return {**base, **over}


def _problems(claims: dict[str, object], **over: object) -> list[str]:
    args = {
        "audience": "server-1",
        "tenant_id": "t1",
        "arguments_digest": args_digest({"a": 1}),
        "now": NOW,
    }
    return grant_problems(claims, **{**args, **over})  # type: ignore[arg-type]


def test_a_grant_is_good_only_for_its_server_tenant_arguments_and_time() -> None:
    assert _problems(_claims()) == []
    assert _problems(_claims(), audience="server-2") == ["TOOL_GRANT_AUDIENCE"]
    assert _problems(_claims(), tenant_id="t2") == ["TOOL_GRANT_TENANT"]
    assert _problems(_claims(), arguments_digest=args_digest({"a": 2})) == ["TOOL_GRANT_ARGUMENTS"]
    assert _problems(_claims(), now=NOW + GRANT_LIFETIME + timedelta(seconds=1)) == [
        "TOOL_GRANT_EXPIRED"
    ]
    assert _problems(_claims(), now=NOW - timedelta(seconds=5)) == ["TOOL_GRANT_NOT_YET_VALID"]


def test_a_grant_missing_its_nonce_or_chain_is_malformed() -> None:
    assert "TOOL_GRANT_MALFORMED" in _problems(_claims(jti=""))
    assert "TOOL_GRANT_MALFORMED" in _problems(_claims(agent_chain="leaf"))
    assert "TOOL_GRANT_EXPIRED" in _problems(
        _claims(exp="soon")
    )  # a non-numeric expiry is never read as "valid"


def test_a_tool_that_needs_a_mandate_is_not_run_on_the_agents_own_account() -> None:
    needs = tool(side_effect=SideEffect.EXTERNAL_TRANSACTION)
    assert mandate_blockers(needs, mandate_id=None, represented_principal_id=None) == [
        "MANDATE_REQUIRED"
    ]
    assert mandate_blockers(needs, mandate_id="m1", represented_principal_id=None) == [
        "MANDATE_REQUIRED"
    ]
    assert mandate_blockers(needs, mandate_id=None, represented_principal_id="p1") == [
        "MANDATE_REQUIRED"
    ]
    assert mandate_blockers(needs, mandate_id="m1", represented_principal_id="p1") == []
    # a side-effecting tool needs one whatever its flag says; a read-only tool without the flag does not
    flagless = tool(side_effect=SideEffect.IRREVERSIBLE, requires_mandate=False)
    assert mandate_blockers(flagless, mandate_id=None, represented_principal_id=None) == [
        "MANDATE_REQUIRED"
    ]
    assert (
        mandate_blockers(
            tool(side_effect=SideEffect.NONE, requires_mandate=False),
            mandate_id=None,
            represented_principal_id=None,
        )
        == []
    )
