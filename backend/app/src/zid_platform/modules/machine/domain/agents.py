"""AI agent, workload and tool identity (ZID-MACHINE-001 §0.2, §2-3, §12-18; FR-MND-001; MACH-01..20).

Identity, not authority: a valid agent, workload credential or attestation never acts for a user or
organization by itself (MACH-13, §0.2). This module decides whether an agent may be ACTIVE, whether a
running workload may get a fresh credential for a logical agent, whether a tool may be invoked at all,
and what a compromise does. Authority comes from grants and mandates elsewhere. Pure domain, no I/O.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import IntEnum, StrEnum


class AgentStatus(StrEnum):
    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    RETIRED = "RETIRED"


_AGENT_TRANSITIONS: dict[AgentStatus, frozenset[AgentStatus]] = {
    AgentStatus.DRAFT: frozenset({AgentStatus.PENDING_APPROVAL, AgentStatus.REVOKED}),
    AgentStatus.PENDING_APPROVAL: frozenset({AgentStatus.ACTIVE, AgentStatus.REVOKED}),
    AgentStatus.ACTIVE: frozenset(
        {AgentStatus.SUSPENDED, AgentStatus.REVOKED, AgentStatus.RETIRED}
    ),
    AgentStatus.SUSPENDED: frozenset(
        {AgentStatus.ACTIVE, AgentStatus.REVOKED, AgentStatus.RETIRED}
    ),
}


class MachineError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


class Attestation(IntEnum):
    """Runtime attestation level (§9). Evidence that can satisfy a floor; never authority."""

    NONE = 0
    ATTEST_1 = 1
    ATTEST_2 = 2
    ATTEST_3 = 3


class ReviewStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    REVOKED = "REVOKED"


@dataclass(frozen=True, slots=True)
class BlueprintRelease:
    blueprint_id: str
    version: str
    artifact_digest: str  # signed build/container digest
    tool_manifest_digest: str
    publisher_principal_id: str
    review: ReviewStatus


@dataclass(frozen=True, slots=True)
class AgentProfile:
    """§2. Publisher, operator, sponsor and owning organization are separate principals, never collapsed."""

    agent_principal_id: str
    tenant_id: str
    agent_name: str  # label only, never an identifier (MACH-01)
    blueprint_id: str
    blueprint_version: str
    publisher_principal_id: str
    operator_principal_id: str
    sponsor_principal_id: str | None
    owning_organization_id: str | None
    model_provider: str  # metadata only: changing it never changes the principal (§1.1)
    allowed_tools: frozenset[str]
    max_subagent_depth: int
    status: AgentStatus
    security_revision: int = 0
    parent_agent_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActivationEvidence:
    """The §12.1 blocking gates, as facts established elsewhere (verified sponsor, tested kill path...)."""

    sponsor_verified: bool
    blueprint: BlueprintRelease | None
    runtime_binding_policy: bool
    security_review_passed: bool
    short_lived_credentials_configured: bool
    observability_configured: bool
    kill_path_tested: bool


def activation_blockers(agent: AgentProfile, ev: ActivationEvidence) -> list[str]:
    """Why the agent can't become ACTIVE (MACH-02). Empty list = may activate."""
    problems = []
    if (
        not agent.sponsor_principal_id
        or not agent.owning_organization_id
        or not ev.sponsor_verified
    ):
        problems.append("AGENT_SPONSOR_REQUIRED")
    bp = ev.blueprint
    if (
        bp is None
        or bp.review is not ReviewStatus.APPROVED
        or (bp.blueprint_id, bp.version) != (agent.blueprint_id, agent.blueprint_version)
    ):
        problems.append("AGENT_BLUEPRINT_NOT_APPROVED")
    if not agent.allowed_tools:
        problems.append("AGENT_TOOL_MANIFEST_REQUIRED")
    for ok, code in (
        (ev.runtime_binding_policy, "AGENT_RUNTIME_BINDING_REQUIRED"),
        (ev.security_review_passed, "AGENT_SECURITY_REVIEW_REQUIRED"),
        (ev.short_lived_credentials_configured, "AGENT_CREDENTIALS_NOT_SHORT_LIVED"),
        (ev.observability_configured, "AGENT_OBSERVABILITY_REQUIRED"),
        (ev.kill_path_tested, "AGENT_KILL_PATH_UNTESTED"),
    ):
        if not ok:
            problems.append(code)
    return problems


def agent_transition(
    agent: AgentProfile, target: AgentStatus, *, evidence: ActivationEvidence | None = None
) -> AgentProfile:
    """Lifecycle (§12): REVOKED is terminal; RETIRED keeps history but never runs again."""
    if target not in _AGENT_TRANSITIONS.get(agent.status, frozenset()):
        raise MachineError("AGENT_INVALID_TRANSITION", f"{agent.status} -> {target}")
    if target is AgentStatus.ACTIVE:
        problems = (
            activation_blockers(agent, evidence)
            if evidence
            else ["AGENT_ACTIVATION_EVIDENCE_REQUIRED"]
        )
        if problems:
            raise MachineError(problems[0], ", ".join(problems))
    bump = 1 if target in (AgentStatus.SUSPENDED, AgentStatus.REVOKED, AgentStatus.RETIRED) else 0
    return replace(agent, status=target, security_revision=agent.security_revision + bump)


# ---------------------------------------------------------------- runtime binding and credentials (§16)


class BindingStatus(StrEnum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True, slots=True)
class WorkloadBinding:
    binding_id: str
    logical_principal_id: str
    workload_identity_id: str  # e.g. a registered SPIFFE ID
    blueprint_version: str
    artifact_digest: str
    environment_id: str
    attestation_required: Attestation
    status: BindingStatus
    valid_from: datetime
    valid_to: datetime


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    workload_identity_id: str
    presented_artifact_digest: str
    observed_attestation: Attestation
    environment_id: str
    at: datetime
    risk_level: int


@dataclass(frozen=True, slots=True)
class MachineState:
    """Fleet-wide security facts consulted on every issuance."""

    quarantined_environments: frozenset[str] = frozenset()
    revoked_blueprint_digests: frozenset[str] = frozenset()
    attestation_floor_by_risk: Mapping[int, Attestation] = field(
        default_factory=lambda: {3: Attestation.ATTEST_3, 2: Attestation.ATTEST_2}
    )


def credential_blockers(
    req: CredentialRequest,
    binding: WorkloadBinding | None,
    agent: AgentProfile | None,
    state: MachineState,
) -> list[str]:
    """Whether a running workload may receive a fresh short-lived credential for its logical agent.

    Both the logical principal and the binding are checked (§16). An unregistered workload identity
    maps to nothing, and the workload can never rebind itself (MACH-03/04/09/10/11/12/20).
    """
    if binding is None or binding.workload_identity_id != req.workload_identity_id:
        return ["WORKLOAD_IDENTITY_UNREGISTERED"]
    problems = []
    if agent is None or agent.agent_principal_id != binding.logical_principal_id:
        problems.append("WORKLOAD_IDENTITY_UNREGISTERED")
    elif agent.status is not AgentStatus.ACTIVE:
        problems.append("AGENT_NOT_ACTIVE")
    if binding.status is not BindingStatus.ACTIVE or not (
        binding.valid_from <= req.at < binding.valid_to
    ):
        problems.append("WORKLOAD_BINDING_INACTIVE")
    if req.presented_artifact_digest != binding.artifact_digest:
        problems.append("WORKLOAD_ARTIFACT_NOT_APPROVED")
    if binding.artifact_digest in state.revoked_blueprint_digests:
        problems.append("BLUEPRINT_REVOKED")
    if (
        req.environment_id != binding.environment_id
        or req.environment_id in state.quarantined_environments
    ):
        problems.append("ENVIRONMENT_QUARANTINED")
    floor = max(
        binding.attestation_required,
        state.attestation_floor_by_risk.get(req.risk_level, Attestation.NONE),
    )
    if req.observed_attestation < floor:
        problems.append("ATTESTATION_INSUFFICIENT")
    return list(dict.fromkeys(problems))


# ---------------------------------------------------------------- tools (§14-15)


class ToolStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    RETIRED = "RETIRED"


class SideEffect(StrEnum):
    NONE = "NONE"
    REVERSIBLE = "REVERSIBLE"
    IRREVERSIBLE = "IRREVERSIBLE"
    EXTERNAL_TRANSACTION = "EXTERNAL_TRANSACTION"


@dataclass(frozen=True, slots=True)
class Tool:
    tool_id: str
    version: str
    server_principal_id: str
    canonical_action: str
    default_risk_class: int
    side_effect: SideEffect
    requires_mandate: bool
    requires_human_approval: bool
    status: ToolStatus


def tool_invocation_blockers(
    agent: AgentProfile, tool_id: str, registry: Mapping[str, Tool]
) -> list[str]:
    """Pre-PDP checks at the tool gateway (§14.1). Passing them grants nothing; the PDP and the
    mandate still decide. Discovery (tools/list) never implies execution (MACH-15)."""
    tool = registry.get(tool_id)
    if tool is None or tool.status is not ToolStatus.ACTIVE:
        return ["TOOL_NOT_REGISTERED"]
    if agent.status is not AgentStatus.ACTIVE:
        return ["AGENT_NOT_ACTIVE"]
    if tool_id not in agent.allowed_tools:
        return ["MANDATE_TOOL_NOT_ALLOWED"]
    return []


def tool_requires_mandate(tool: Tool) -> bool:
    """Side-effecting tools always need a mandate for represented execution, whatever the flag says."""
    return tool.requires_mandate or tool.side_effect in (
        SideEffect.IRREVERSIBLE,
        SideEffect.EXTERNAL_TRANSACTION,
    )


# ---------------------------------------------------------------- sub-agents (§13)


def register_child_agent(
    parent: AgentProfile, child: AgentProfile, *, issued_by_identity_service: bool
) -> list[str]:
    """Child identity is issued by the identity service, never minted by the parent (MACH-17)."""
    problems = []
    if not issued_by_identity_service:
        problems.append("AGENT_CHILD_SELF_DECLARED")
    if parent.status is not AgentStatus.ACTIVE:
        problems.append("AGENT_NOT_ACTIVE")
    if parent.max_subagent_depth <= 0 or child.max_subagent_depth >= parent.max_subagent_depth:
        problems.append("GRANT_DEPTH_EXCEEDED")
    if child.parent_agent_id != parent.agent_principal_id:
        problems.append("AGENT_PARENT_MISMATCH")
    if not child.allowed_tools <= parent.allowed_tools:
        problems.append("MANDATE_TOOL_NOT_ALLOWED")
    if (
        child.sponsor_principal_id is None
        or child.owning_organization_id != parent.owning_organization_id
    ):
        problems.append("AGENT_SPONSOR_REQUIRED")  # accountability chain stays resolvable
    if child.agent_principal_id == parent.agent_principal_id:
        problems.append("AGENT_IDENTITY_REUSED")
    return problems


# ---------------------------------------------------------------- compromise response (§18)


class Compromise(StrEnum):
    CREDENTIAL = "CREDENTIAL"
    WORKLOAD = "WORKLOAD"
    AGENT = "AGENT"
    BLUEPRINT = "BLUEPRINT"
    ENVIRONMENT = "ENVIRONMENT"
    SPONSOR_REMOVED = "SPONSOR_REMOVED"
    TOOL = "TOOL"


@dataclass(frozen=True, slots=True)
class CompromiseOutcome:
    agents: dict[str, AgentProfile]
    bindings: dict[str, WorkloadBinding]
    tools: dict[str, Tool]
    state: MachineState
    agents_whose_mandates_block: frozenset[str]


def respond(
    kind: Compromise,
    target: str,
    *,
    agents: Mapping[str, AgentProfile],
    bindings: Mapping[str, WorkloadBinding],
    tools: Mapping[str, Tool],
    state: MachineState,
    qualifying_sponsors: Iterable[str] = (),
) -> CompromiseOutcome:
    """Apply one compromise trigger. A workload compromise touches only that workload (MACH-10/21);
    an agent compromise reaches its descendants and blocks their mandates (MACH-09)."""
    ag, bd, tl, st = dict(agents), dict(bindings), dict(tools), state
    blocked: set[str] = set()

    def suspend_agent(aid: str) -> None:
        a = ag[aid]
        if a.status in (AgentStatus.ACTIVE, AgentStatus.PENDING_APPROVAL, AgentStatus.DRAFT):
            ag[aid] = replace(
                a, status=AgentStatus.SUSPENDED, security_revision=a.security_revision + 1
            )
        blocked.add(aid)

    if kind in (Compromise.CREDENTIAL, Compromise.WORKLOAD):
        for bid, b in bd.items():
            if b.workload_identity_id == target and b.status is BindingStatus.ACTIVE:
                bd[bid] = replace(b, status=BindingStatus.SUSPENDED)
    elif kind is Compromise.AGENT:
        stack = [target]
        while stack:
            aid = stack.pop()
            if aid in blocked or aid not in ag:
                continue
            suspend_agent(aid)
            stack.extend(a.agent_principal_id for a in ag.values() if a.parent_agent_id == aid)
        for bid, b in bd.items():
            if b.logical_principal_id in blocked and b.status is BindingStatus.ACTIVE:
                bd[bid] = replace(b, status=BindingStatus.SUSPENDED)
    elif kind is Compromise.BLUEPRINT:
        st = replace(st, revoked_blueprint_digests=st.revoked_blueprint_digests | {target})
        for bid, b in bd.items():
            if b.artifact_digest == target and b.status is BindingStatus.ACTIVE:
                bd[bid] = replace(b, status=BindingStatus.SUSPENDED)
    elif kind is Compromise.ENVIRONMENT:
        st = replace(st, quarantined_environments=st.quarantined_environments | {target})
    elif kind is Compromise.SPONSOR_REMOVED:
        sponsors = set(qualifying_sponsors)
        for aid, a in list(ag.items()):
            if a.sponsor_principal_id == target and target not in sponsors:
                suspend_agent(aid)
    elif kind is Compromise.TOOL and target in tl:
        tl[target] = replace(tl[target], status=ToolStatus.SUSPENDED)
    return CompromiseOutcome(ag, bd, tl, st, frozenset(blocked))
