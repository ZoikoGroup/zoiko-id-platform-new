"""AI agent, workload and tool identity (ZID-MACHINE-001 MACH-01..21; FR-MND-001)."""

import inspect
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from zid_platform import api_auth
from zid_platform.modules.machine.domain.agents import (
    ActivationEvidence,
    AgentProfile,
    AgentStatus,
    Attestation,
    BindingStatus,
    BlueprintRelease,
    Compromise,
    CredentialRequest,
    MachineError,
    MachineState,
    ReviewStatus,
    SideEffect,
    Tool,
    ToolStatus,
    WorkloadBinding,
    activation_blockers,
    agent_transition,
    credential_blockers,
    register_child_agent,
    respond,
    tool_invocation_blockers,
    tool_requires_mandate,
)
from zid_platform.settings import Settings

NOW = datetime(2026, 10, 12, tzinfo=UTC)
BP = BlueprintRelease(
    "bp_ap", "1.4.0", "sha256:approved", "sha256:tools", "prn_publisher", ReviewStatus.APPROVED
)
AGENT = AgentProfile(
    agent_principal_id="agt_1",
    tenant_id="ten_1",
    agent_name="AP Assistant",
    blueprint_id="bp_ap",
    blueprint_version="1.4.0",
    publisher_principal_id="prn_publisher",
    operator_principal_id="prn_ops",
    sponsor_principal_id="prn_sponsor",
    owning_organization_id="org_1",
    model_provider="any",
    allowed_tools=frozenset({"tool_search", "tool_pay"}),
    max_subagent_depth=2,
    status=AgentStatus.ACTIVE,
)
EVIDENCE = ActivationEvidence(True, BP, True, True, True, True, True)
BINDING = WorkloadBinding(
    "bnd_1",
    "agt_1",
    "spiffe://uk-1.prod.id.zoiko/agents/ap/replica-a",
    "1.4.0",
    "sha256:approved",
    "prod-uk-1",
    Attestation.ATTEST_2,
    BindingStatus.ACTIVE,
    NOW - timedelta(days=1),
    NOW + timedelta(days=30),
)


def creq(**kw: object) -> CredentialRequest:
    base: dict[str, object] = {
        "workload_identity_id": BINDING.workload_identity_id,
        "presented_artifact_digest": "sha256:approved",
        "observed_attestation": Attestation.ATTEST_2,
        "environment_id": "prod-uk-1",
        "at": NOW,
        "risk_level": 2,
    } | kw
    return CredentialRequest(**base)  # type: ignore[arg-type]


def test_activation_needs_every_gate() -> None:
    """MACH-02: no sponsor, no activation; every §12.1 gate blocks."""
    pending = replace(AGENT, status=AgentStatus.PENDING_APPROVAL)
    assert (
        agent_transition(pending, AgentStatus.ACTIVE, evidence=EVIDENCE).status
        is AgentStatus.ACTIVE
    )
    assert "AGENT_SPONSOR_REQUIRED" in activation_blockers(
        replace(pending, sponsor_principal_id=None), EVIDENCE
    )
    assert "AGENT_BLUEPRINT_NOT_APPROVED" in activation_blockers(
        pending, replace(EVIDENCE, blueprint=replace(BP, review=ReviewStatus.DRAFT))
    )
    assert "AGENT_KILL_PATH_UNTESTED" in activation_blockers(
        pending, replace(EVIDENCE, kill_path_tested=False)
    )
    with pytest.raises(MachineError):
        agent_transition(pending, AgentStatus.ACTIVE)  # no evidence, no activation


def test_revoked_is_terminal_and_retired_never_runs() -> None:
    revoked = agent_transition(AGENT, AgentStatus.REVOKED)
    with pytest.raises(MachineError):
        agent_transition(revoked, AgentStatus.ACTIVE, evidence=EVIDENCE)
    retired = agent_transition(AGENT, AgentStatus.RETIRED)
    assert credential_blockers(creq(), BINDING, retired, MachineState()) == ["AGENT_NOT_ACTIVE"]


def test_healthy_workload_gets_a_credential() -> None:
    assert credential_blockers(creq(), BINDING, AGENT, MachineState()) == []


@pytest.mark.parametrize(
    ("change", "code"),
    [
        (
            {"presented_artifact_digest": "sha256:tampered"},
            "WORKLOAD_ARTIFACT_NOT_APPROVED",
        ),  # MACH-03
        (
            {"workload_identity_id": "spiffe://uk-1.prod.id.zoiko/rogue"},
            "WORKLOAD_IDENTITY_UNREGISTERED",
        ),  # MACH-04
        (
            {"observed_attestation": Attestation.ATTEST_1, "risk_level": 3},
            "ATTESTATION_INSUFFICIENT",
        ),  # MACH-12
        ({"environment_id": "dev-laptop"}, "ENVIRONMENT_QUARANTINED"),
        (
            {"at": NOW + timedelta(days=31)},
            "WORKLOAD_BINDING_INACTIVE",
        ),  # MACH-06 expiry not extendable
    ],
)
def test_credential_issuance_blockers(change: dict[str, object], code: str) -> None:
    assert code in credential_blockers(creq(**change), BINDING, AGENT, MachineState())


def test_display_name_and_sponsor_never_identify_an_agent() -> None:
    """MACH-01: another agent with the same name and sponsor can't use this binding."""
    twin = replace(AGENT, agent_principal_id="agt_2")
    assert "WORKLOAD_IDENTITY_UNREGISTERED" in credential_blockers(
        creq(), BINDING, twin, MachineState()
    )


def test_workload_compromise_spares_healthy_replicas() -> None:
    """MACH-10 / MACH-21."""
    replica_b = replace(
        BINDING,
        binding_id="bnd_2",
        workload_identity_id="spiffe://uk-1.prod.id.zoiko/agents/ap/replica-b",
    )
    out = respond(
        Compromise.WORKLOAD,
        BINDING.workload_identity_id,
        agents={"agt_1": AGENT},
        bindings={"bnd_1": BINDING, "bnd_2": replica_b},
        tools={},
        state=MachineState(),
    )
    assert out.bindings["bnd_1"].status is BindingStatus.SUSPENDED
    assert out.bindings["bnd_2"].status is BindingStatus.ACTIVE
    assert (
        credential_blockers(
            creq(workload_identity_id=replica_b.workload_identity_id),
            out.bindings["bnd_2"],
            out.agents["agt_1"],
            out.state,
        )
        == []
    )


def test_agent_compromise_reaches_descendants_and_blocks_mandates() -> None:
    """MACH-09 / §18."""
    child = replace(
        AGENT, agent_principal_id="agt_child", parent_agent_id="agt_1", max_subagent_depth=1
    )
    out = respond(
        Compromise.AGENT,
        "agt_1",
        agents={"agt_1": AGENT, "agt_child": child},
        bindings={"bnd_1": BINDING},
        tools={},
        state=MachineState(),
    )
    assert {a.status for a in out.agents.values()} == {AgentStatus.SUSPENDED}
    assert out.agents_whose_mandates_block == {"agt_1", "agt_child"}
    assert out.bindings["bnd_1"].status is BindingStatus.SUSPENDED


def test_blueprint_and_environment_quarantine() -> None:
    """MACH-11 / MACH-20."""
    out = respond(
        Compromise.BLUEPRINT,
        "sha256:approved",
        agents={"agt_1": AGENT},
        bindings={"bnd_1": BINDING},
        tools={},
        state=MachineState(),
    )
    assert "BLUEPRINT_REVOKED" in credential_blockers(creq(), BINDING, AGENT, out.state)
    env = respond(
        Compromise.ENVIRONMENT, "prod-uk-1", agents={}, bindings={}, tools={}, state=MachineState()
    )
    assert "ENVIRONMENT_QUARANTINED" in credential_blockers(creq(), BINDING, AGENT, env.state)


def test_sponsor_removal_suspends_unless_another_qualifying_sponsor() -> None:
    out = respond(
        Compromise.SPONSOR_REMOVED,
        "prn_sponsor",
        agents={"agt_1": AGENT},
        bindings={},
        tools={},
        state=MachineState(),
    )
    assert out.agents["agt_1"].status is AgentStatus.SUSPENDED


TOOLS = {
    "tool_search": Tool(
        "tool_search",
        "1",
        "srv_mcp",
        "tool.execute",
        1,
        SideEffect.NONE,
        False,
        False,
        ToolStatus.ACTIVE,
    ),
    "tool_pay": Tool(
        "tool_pay",
        "2",
        "srv_mcp",
        "payment.release",
        3,
        SideEffect.EXTERNAL_TRANSACTION,
        False,
        True,
        ToolStatus.ACTIVE,
    ),
}


def test_discovered_but_unregistered_or_suspended_tools_never_run() -> None:
    """MACH-15."""
    assert tool_invocation_blockers(AGENT, "tool_listed_by_mcp_only", TOOLS) == [
        "TOOL_NOT_REGISTERED"
    ]
    out = respond(
        Compromise.TOOL, "tool_search", agents={}, bindings={}, tools=TOOLS, state=MachineState()
    )
    assert tool_invocation_blockers(AGENT, "tool_search", out.tools) == ["TOOL_NOT_REGISTERED"]
    assert tool_invocation_blockers(
        replace(AGENT, allowed_tools=frozenset({"tool_search"})), "tool_pay", TOOLS
    ) == ["MANDATE_TOOL_NOT_ALLOWED"]
    assert tool_invocation_blockers(AGENT, "tool_pay", TOOLS) == []


def test_side_effecting_tools_always_need_a_mandate() -> None:
    assert tool_requires_mandate(TOOLS["tool_pay"]) and not tool_requires_mandate(
        TOOLS["tool_search"]
    )


def test_parent_cannot_mint_its_own_trusted_child() -> None:
    """MACH-17 / MACH-18."""
    child = replace(
        AGENT,
        agent_principal_id="agt_child",
        parent_agent_id="agt_1",
        max_subagent_depth=1,
        allowed_tools=frozenset({"tool_search"}),
    )
    assert register_child_agent(AGENT, child, issued_by_identity_service=True) == []
    assert "AGENT_CHILD_SELF_DECLARED" in register_child_agent(
        AGENT, child, issued_by_identity_service=False
    )
    wider = replace(child, allowed_tools=frozenset({"tool_search", "tool_wire"}))
    assert "MANDATE_TOOL_NOT_ALLOWED" in register_child_agent(
        AGENT, wider, issued_by_identity_service=True
    )
    deep = replace(child, max_subagent_depth=2)
    assert "GRANT_DEPTH_EXCEEDED" in register_child_agent(
        AGENT, deep, issued_by_identity_service=True
    )


@pytest.mark.parametrize("risk", [2, 3])
@pytest.mark.parametrize(
    "change",
    [
        {"workload_identity_id": "spiffe://uk-1.prod.id.zoiko/unknown"},
        {"observed_attestation": Attestation.NONE},
        {"presented_artifact_digest": "sha256:tampered"},
        {"environment_id": "dev-laptop"},
        {"at": NOW + timedelta(days=31)},
    ],
)
def test_a_trust_failure_gives_no_credential_and_names_no_fallback(
    risk: int, change: dict[str, object]
) -> None:
    """MACH-25: at R2 and R3 an identity, attestation or trust failure ends in refusal codes, never in a lesser credential."""
    blockers = credential_blockers(creq(risk_level=risk, **change), BINDING, AGENT, MachineState())
    assert blockers and all(b.isupper() for b in blockers)
    assert credential_blockers(
        creq(risk_level=risk), None, AGENT, MachineState()
    )  # no binding at all


def test_no_static_bearer_or_api_key_exists_to_fall_back_to() -> None:
    """MACH-25: the API accepts a verified access token and nothing else, and no setting holds a static credential."""
    assert not [f for f in Settings.model_fields if "api_key" in f or "static" in f]
    source = inspect.getsource(api_auth).lower()
    assert "x-api-key" not in source and "api_key" not in source


def test_a_rotated_workload_binding_overlaps_and_the_old_one_stops_after_the_overlap() -> None:
    """MACH-08: both identities issue during the overlap (no downtime) and only the new one after it."""
    overlap_end = NOW + timedelta(hours=1)
    old = replace(BINDING, valid_to=overlap_end)
    new = replace(
        BINDING,
        binding_id="bnd_2",
        workload_identity_id="spiffe://uk-1.prod.id.zoiko/agents/ap/replica-b",
        valid_from=NOW,
    )
    state = MachineState()

    def issues(binding: WorkloadBinding, at: datetime) -> bool:
        request = creq(workload_identity_id=binding.workload_identity_id, at=at)
        return not credential_blockers(request, binding, AGENT, state)

    for at in (NOW, NOW + timedelta(minutes=30), overlap_end - timedelta(seconds=1)):
        assert issues(old, at) and issues(new, at)
    after = overlap_end + timedelta(seconds=1)
    assert not issues(old, after) and issues(new, after)
    assert "WORKLOAD_BINDING_INACTIVE" in credential_blockers(
        creq(at=after), old, AGENT, state
    )  # the reason is the expired binding, not a silent fallback
