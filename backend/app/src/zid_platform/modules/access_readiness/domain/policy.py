"""Setup, run and review rules of the access readiness agent (ZID-AG-WF-001 sections 04, 06, 09, 12; CR-091). Pure domain, no I/O.

Invariant (section 00): the agent may propose a change for a person to review; only the separately authorized identity administration
workflow can execute one. So the tool allowlist holds exactly two reads and one append-only write, a setup and a run are judged by
facts the caller establishes elsewhere, and every unknown or unreadable fact is a refusal (fail closed).
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any

from zid_platform.modules.access_readiness.domain.rules import (
    POLICY_VERSION,
    PURPOSE,
    RESOURCE_KINDS,
    RULE_SET_VERSION,
    digest,
)

READ_MANDATE = "read_mandate_metadata"
READ_AGENT = "read_agent_identity_status"
CREATE_PROPOSAL = "create_access_review_proposal"
ALLOWED_TOOLS = frozenset({READ_MANDATE, READ_AGENT, CREATE_PROPOSAL})
# the only blueprint release DAARA runs as (a locked template: no custom agent, no arbitrary tool)
BLUEPRINT_ID = "daara"
MAX_RECORDS_LIMIT = 500
CLASSIFICATION = "METADATA"
RETENTION_POLICY_REF = "retention/access-readiness/1"


class ReadinessError(ValueError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


def policy_hash() -> str:
    """The identity of the policy a run was evaluated under: allowlist, resource kinds, purpose, rule set."""
    return digest(
        {
            "policy": POLICY_VERSION,
            "tools": sorted(ALLOWED_TOOLS),
            "kinds": sorted(RESOURCE_KINDS),
            "purpose": PURPOSE,
            "rules": RULE_SET_VERSION,
        }
    )


def tool_authorized(tool: str) -> bool:
    return tool in ALLOWED_TOOLS


# ---------------------------------------------------------------- setup (the agent instance's scope, kept as a draft)


class SetupStatus(StrEnum):
    DRAFT = "DRAFT"
    SANDBOX_READY = "SANDBOX_READY"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    PAUSED = "PAUSED"


_SETUP_NEXT: dict[SetupStatus, frozenset[SetupStatus]] = {
    SetupStatus.DRAFT: frozenset({SetupStatus.SANDBOX_READY, SetupStatus.PAUSED}),
    SetupStatus.SANDBOX_READY: frozenset(
        {SetupStatus.PENDING_APPROVAL, SetupStatus.DRAFT, SetupStatus.PAUSED}
    ),
    SetupStatus.PENDING_APPROVAL: frozenset(
        {SetupStatus.APPROVED, SetupStatus.DRAFT, SetupStatus.PAUSED}
    ),
    SetupStatus.APPROVED: frozenset({SetupStatus.DRAFT, SetupStatus.PAUSED}),
    SetupStatus.PAUSED: frozenset({SetupStatus.DRAFT}),  # re-release only through the gates, again
}


def setup_transition(current: SetupStatus, target: SetupStatus) -> SetupStatus:
    if target not in _SETUP_NEXT[current]:
        raise ReadinessError("SETUP_INVALID_TRANSITION", f"{current} -> {target}")
    return target


@dataclass(frozen=True, slots=True)
class Scope:
    """What the agent may read, as one exact value that an approval signs the digest of."""

    purpose: str
    resource_kinds: tuple[str, ...]
    max_records: int
    warning_window_days: int
    home_region: str


NAME = re.compile(r"^[^\x00-\x1f\x7f]{1,80}$")


def validate_scope(scope: Scope) -> Scope:
    if scope.purpose != PURPOSE:
        raise ReadinessError(
            "PURPOSE_NOT_ALLOWED", "only the access posture review purpose is available"
        )
    if not scope.resource_kinds or not set(scope.resource_kinds) <= RESOURCE_KINDS:
        raise ReadinessError("SOURCE_NOT_APPROVED", "unknown data source")
    if not 1 <= scope.max_records <= MAX_RECORDS_LIMIT:
        raise ReadinessError("SCOPE_INVALID", "max_records out of range")
    if not 1 <= scope.warning_window_days <= 90:
        raise ReadinessError("SCOPE_INVALID", "warning window out of range")
    return Scope(
        scope.purpose,
        tuple(sorted(set(scope.resource_kinds))),
        scope.max_records,
        scope.warning_window_days,
        scope.home_region,
    )


def validate_name(name: str) -> str:
    cleaned = name.strip()
    if not NAME.match(cleaned):
        raise ReadinessError("NAME_INVALID", "1 to 80 printable characters")
    return cleaned


def scope_digest(
    scope: Scope, *, agent_id: str, blueprint: tuple[str, str], allowed_tools: Collection[str]
) -> str:
    """The digest an approval is bound to: the scope, the agent, its exact blueprint release and its tool set, the rule set and policy.
    Any change to one of them is a different digest, so a prior validation or approval no longer applies (AT-05)."""
    return digest(
        {
            "agent": agent_id,
            "blueprint": list(blueprint),
            "tools": sorted(allowed_tools),
            "purpose": scope.purpose,
            "kinds": list(scope.resource_kinds),
            "max_records": scope.max_records,
            "window_days": scope.warning_window_days,
            "region": scope.home_region,
            "rules": RULE_SET_VERSION,
            "policy": POLICY_VERSION,
        }
    )


def setup_blockers(
    *,
    sponsor_active: bool,
    agent_found: bool,
    blueprint_ok: bool,
    tools: Collection[str],
    region_supported: bool,
) -> list[str]:
    """Why an agent cannot be set up as DAARA now. An agent without an active accountable sponsor is never saved (AT-02)."""
    out: list[str] = []
    if not agent_found:
        return ["AGENT_NOT_FOUND"]
    if not sponsor_active:
        out.append("AGENT_SPONSOR_REQUIRED")
    if not blueprint_ok:
        out.append("BLUEPRINT_NOT_ALLOWED")
    if not tools or not set(tools) <= ALLOWED_TOOLS:
        out.append("TOOL_NOT_ALLOWED")
    if not region_supported:
        out.append("REGION_UNSUPPORTED")
    return out


# ---------------------------------------------------------------- selector (what a run asks to read)


@dataclass(frozen=True, slots=True)
class Selector:
    kind: str
    max_records: int


def check_selector(
    raw: Mapping[str, Any], *, tenant_id: str, home_region: str, scope: Scope
) -> Selector:
    """A resource selector may only narrow the approved scope. A tenant or region of its own is a probe: denied (AT-03)."""
    unknown = set(raw) - {"kind", "max_records", "tenant_id", "region"}
    if unknown:
        raise ReadinessError("SELECTOR_INVALID", "unknown selector field")
    if "tenant_id" in raw and raw["tenant_id"] != tenant_id:
        raise ReadinessError("SELECTOR_TENANT_MISMATCH")
    if "region" in raw and raw["region"] != home_region:
        raise ReadinessError("SELECTOR_REGION_MISMATCH")
    kind = raw.get("kind", "mandates")
    if kind not in RESOURCE_KINDS or kind not in scope.resource_kinds:
        raise ReadinessError("SOURCE_NOT_APPROVED")
    limit = raw.get("max_records", scope.max_records)
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= scope.max_records:
        raise ReadinessError("SELECTOR_INVALID", "max_records outside the approved scope")
    return Selector(kind, limit)


# ---------------------------------------------------------------- preflight (every fact must say yes; a missing fact says no)


@dataclass(frozen=True, slots=True)
class PreflightFacts:
    feature_enabled: bool
    tenant_match: bool
    region_supported: bool
    region_match: bool
    agent_found: bool
    agent_status: str | None
    attested: bool
    sponsor_active: bool
    blueprint_approved: bool
    blueprint_allowed: bool
    tools_allowed: bool
    agent_expired: bool
    setup_status: str | None
    approval_valid: bool | None  # None: no approval on record
    approval_expired: bool
    digest_matches: bool
    policy_available: bool = True


def preflight_blockers(f: PreflightFacts) -> list[str]:
    """Why a run may not start (section 09 step 2; AT-07, AT-08, AT-18, AT-19, AT-24). Empty list = permitted."""
    out: list[str] = []
    if not f.feature_enabled:
        out.append("FEATURE_DISABLED")
    if not f.tenant_match:
        out.append("TENANT_MISMATCH")
    if not f.region_supported:
        out.append("REGION_UNSUPPORTED")
    elif not f.region_match:
        out.append("REGION_MISMATCH")
    if not f.policy_available:
        out.append("DEPENDENCY_UNAVAILABLE")
    if not f.agent_found:
        out.append("AGENT_NOT_FOUND")
        return out
    if f.agent_status != "ACTIVE":
        out.append("AGENT_NOT_ACTIVE")
    if f.agent_expired:
        out.append("AGENT_EXPIRED")
    if not f.attested:
        out.append("AGENT_NOT_ATTESTED")
    if not f.sponsor_active:
        out.append("SPONSOR_NOT_ACTIVE")
    if not f.blueprint_approved:
        out.append("BLUEPRINT_NOT_APPROVED")
    if not f.blueprint_allowed:
        out.append("BLUEPRINT_NOT_ALLOWED")
    if not f.tools_allowed:
        out.append("TOOL_NOT_ALLOWED")
    if f.setup_status != SetupStatus.APPROVED.value or f.approval_valid is None:
        out.append("SETUP_NOT_APPROVED")
    else:
        if not f.digest_matches:
            out.append("APPROVAL_DIGEST_MISMATCH")
        if f.approval_expired:
            out.append("APPROVAL_EXPIRED")
    return out


def blueprint_allowed(blueprint_id: str | None) -> bool:
    return blueprint_id == BLUEPRINT_ID


# ---------------------------------------------------------------- per-read authorization (AT-09)


@dataclass(frozen=True, slots=True)
class ReadRequest:
    tenant_id: str
    actor: str
    purpose: str
    resource: str
    tool: str
    policy: str


@dataclass(frozen=True, slots=True)
class ReadContext:
    """What the run was authorized for. Every read is judged against it: tenant, actor, purpose, resource and policy."""

    tenant_id: str
    agent_id: str
    purpose: str
    kinds: frozenset[str]
    policy: str


def read_denial(ctx: ReadContext, req: ReadRequest) -> str | None:
    """None when the read is permitted, else the reason code. Order is fixed so the same denial always has the same reason."""
    if not tool_authorized(req.tool):
        return "TOOL_NOT_ALLOWED"
    if req.tenant_id != ctx.tenant_id:
        return "TENANT_MISMATCH"
    if req.actor != ctx.agent_id:
        return "ACTOR_MISMATCH"
    if req.purpose != ctx.purpose:
        return "PURPOSE_MISMATCH"
    kind = req.resource.split(":", 1)[0]
    if kind not in ctx.kinds:
        return "RESOURCE_NOT_APPROVED"
    if req.policy != ctx.policy:
        return "POLICY_CHANGED"
    return None


# ---------------------------------------------------------------- runs


class RunState(StrEnum):
    QUEUED = "QUEUED"
    PREFLIGHT = "PREFLIGHT"
    RUNNING = "RUNNING"
    DENIED = "DENIED"
    COMPLETED = "COMPLETED"
    NEEDS_REVIEW = "NEEDS_REVIEW"
    FAILED = "FAILED"
    CANCELED = "CANCELED"


TERMINAL_RUN_STATES = frozenset(
    {RunState.DENIED, RunState.COMPLETED, RunState.NEEDS_REVIEW, RunState.FAILED, RunState.CANCELED}
)
_RUN_NEXT: dict[RunState, frozenset[RunState]] = {
    RunState.QUEUED: frozenset({RunState.PREFLIGHT, RunState.CANCELED}),
    RunState.PREFLIGHT: frozenset({RunState.RUNNING, RunState.DENIED}),
    RunState.RUNNING: frozenset(
        {RunState.COMPLETED, RunState.NEEDS_REVIEW, RunState.FAILED, RunState.CANCELED}
    ),
}


def run_transition(current: RunState, target: RunState) -> RunState:
    if target not in _RUN_NEXT.get(current, frozenset()):
        raise ReadinessError("RUN_INVALID_TRANSITION", f"{current} -> {target}")
    return target


def terminal_state(*, proposals: int, complete: bool) -> RunState:
    """COMPLETED only when everything was judged and nothing needs a person; otherwise a person is asked."""
    return RunState.COMPLETED if complete and proposals == 0 else RunState.NEEDS_REVIEW


def run_definition_hash(
    *,
    tenant_id: str,
    agent_id: str,
    setup_digest: str,
    selector: Mapping[str, Any],
    mode: str,
    warning_window_days: int,
) -> str:
    """The immutable run definition: what was asked, under which approved scope, rule set and policy (section 09 step 1)."""
    return digest(
        {
            "tenant": tenant_id,
            "agent": agent_id,
            "scope": setup_digest,
            "selector": dict(selector),
            "mode": mode,
            "window_days": warning_window_days,
            "rules": RULE_SET_VERSION,
            "policy": policy_hash(),
        }
    )


# ---------------------------------------------------------------- review proposals


class ProposalStatus(StrEnum):
    OPEN = "OPEN"
    ASSIGNED = "ASSIGNED"
    DISMISSED = "DISMISSED"
    HANDED_OFF = "HANDED_OFF"


_PROPOSAL_NEXT: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.OPEN: frozenset(
        {ProposalStatus.ASSIGNED, ProposalStatus.DISMISSED, ProposalStatus.HANDED_OFF}
    ),
    ProposalStatus.ASSIGNED: frozenset({ProposalStatus.DISMISSED, ProposalStatus.HANDED_OFF}),
}


def proposal_transition(current: ProposalStatus, target: ProposalStatus) -> ProposalStatus:
    """A proposal only records a human review. No status here grants, revokes or edits anything (AT-10, AT-12)."""
    if target not in _PROPOSAL_NEXT.get(current, frozenset()):
        raise ReadinessError("PROPOSAL_INVALID_TRANSITION", f"{current} -> {target}")
    return target


def approval_is_current(
    *, approved_digest: str | None, current_digest: str, expires_at: datetime | None, now: datetime
) -> bool:
    return approved_digest == current_digest and expires_at is not None and expires_at > now
