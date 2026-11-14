"""The Delegated Authority and Access Readiness Agent, DAARA (ZID-AG-WF-001 v1.0, CR-092).

A deterministic reference agent. It reads minimal metadata about mandates and agent identities, applies six versioned rules, and
proposes a human review. It never changes a right: nothing in this module can, because it only produces findings and proposals as
values. The projection it reads has no free-text field at all, so a sentence planted in a mandate cannot reach a rule, a proposal
or a model (AT-16 holds by construction, and the explanation checker below is the second wall).

Pure domain, no I/O, no clock (callers pass `now`, or None when the clock cannot be trusted).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from zid_platform.modules.machine.domain.governance import digest

RULE_SET_VERSION = "1"
TEMPLATE = "DAARA"
PURPOSE = "access_posture_review"
DEFAULT_WARN_DAYS = 14
DEFAULT_MAX_PROPOSALS = 25

TOOL_READ_MANDATES = "read_mandate_metadata"
TOOL_READ_AGENTS = "read_agent_identity_status"
TOOL_PROPOSE = "create_access_review_proposal"


@dataclass(frozen=True, slots=True)
class ToolSpec:
    tool_id: str
    action: str
    side_effect: str
    resource_kind: str
    selector: str
    risk: int
    why: str


# The only three tools the template may ever hold. Any other tool name is denied by the gateway, by the worker and by `tool_allowed`.
TOOLS: dict[str, ToolSpec] = {
    TOOL_READ_MANDATES: ToolSpec(
        TOOL_READ_MANDATES,
        "mandate.metadata.read",
        "NONE",
        "mandate",
        "mandate:",
        1,
        "Reads mandate state, expiry and scope: no names, no amounts, no documents.",
    ),
    TOOL_READ_AGENTS: ToolSpec(
        TOOL_READ_AGENTS,
        "workload_identity.status.read",
        "NONE",
        "agent",
        "agent:",
        1,
        "Reads an agent's status and whether its sponsor is active: no credential material.",
    ),
    TOOL_PROPOSE: ToolSpec(
        TOOL_PROPOSE,
        "access_review.proposal.create",
        "REVERSIBLE",
        "review_proposal",
        "review_proposal:",
        2,
        "Appends one proposal to the human review queue. It changes no access.",
    ),
}


def tool_allowed(tool_id: str) -> bool:
    return tool_id in TOOLS


# ---------------------------------------------------------------- facts (the whole of what DAARA may read)


@dataclass(frozen=True, slots=True)
class MandateFact:
    mandate_id: str
    status: str
    valid_to: datetime | None
    actions: frozenset[str]
    approved_actions: (
        frozenset[str] | None
    )  # None: the source grant allows any action, so no variance can be shown
    grant_found: bool
    grant_status: str | None


@dataclass(frozen=True, slots=True)
class AgentFact:
    agent_id: str
    status: str
    sponsor_active: bool | None  # None: the registry could not say
    has_policy: bool
    approval_effective: bool | None


KNOWN_MANDATE_STATUSES = frozenset(
    {"PENDING_APPROVAL", "ACTIVE", "PAUSED", "EXHAUSTED", "COMPLETED", "EXPIRED", "REVOKED"}
)
KNOWN_AGENT_STATUSES = frozenset(
    {"DRAFT", "PENDING_APPROVAL", "ACTIVE", "SUSPENDED", "REVOKED", "RETIRED"}
)
HEALTHY_GRANT_STATUS = "ACTIVE"

# ---------------------------------------------------------------- findings (section 09 rules R-01..R-06)

RULES: dict[str, tuple[str, str, str]] = {
    # rule id: (finding code, severity, what the rule means)
    "R-01": ("EXPIRED_MANDATE", "HIGH", "A mandate is still active past its end date."),
    "R-02": (
        "EXPIRING_SOON",
        "LOW",
        "A mandate ends within the warning window. This is not a revocation order.",
    ),
    "R-03": (
        "SPONSOR_MISSING",
        "HIGH",
        "An active agent has no active accountable sponsor. It is a governance exception; nothing is inherited.",
    ),
    "R-04": (
        "SCOPE_VARIANCE",
        "HIGH",
        "A mandate allows an action its source grant does not. It is never corrected automatically.",
    ),
    "R-05": (
        "EVIDENCE_GAP",
        "MEDIUM",
        "The evidence a mandate or policy needs is missing or cannot be verified. This is a review request, not a denial.",
    ),
    "R-06": (
        "UNKNOWN_STATE",
        "MEDIUM",
        "State could not be established. The run is incomplete; a healthy status is never invented.",
    ),
}
SEVERITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


@dataclass(frozen=True, slots=True)
class Finding:
    rule_id: str
    rule_version: str
    finding_code: str
    severity: str
    target_ref: str
    evidence_refs: tuple[str, ...]
    facts_digest: str


@dataclass(frozen=True, slots=True)
class Unknown:
    target_ref: str
    reason: str


@dataclass(frozen=True, slots=True)
class RuleOutcome:
    findings: tuple[Finding, ...]
    unknown: tuple[Unknown, ...]
    checked: int


def _finding(rule: str, target: str, evidence: Sequence[str], facts: dict[str, Any]) -> Finding:
    code, severity, _ = RULES[rule]
    return Finding(rule, RULE_SET_VERSION, code, severity, target, tuple(evidence), digest(facts))


def evaluate_rules(
    mandates: Sequence[MandateFact],
    agents: Sequence[AgentFact],
    *,
    now: datetime | None,
    warn_days: int = DEFAULT_WARN_DAYS,
) -> RuleOutcome:
    """Apply R-01..R-06. Pure and deterministic: the same facts and the same `now` always give the same findings, in the same order
    (AT-13). With no trusted clock nothing time-based is judged and the run is incomplete (R-06)."""
    findings: list[Finding] = []
    unknown: list[Unknown] = []
    if now is None:
        unknown.append(Unknown("clock:trusted_now", "CLOCK_UNVERIFIABLE"))
    window = timedelta(days=warn_days)
    for m in sorted(mandates, key=lambda x: x.mandate_id):
        target = f"mandate:{m.mandate_id}"
        facts = {
            "status": m.status,
            "valid_to": m.valid_to.isoformat() if m.valid_to else None,
            "actions": sorted(m.actions),
            "approved": sorted(m.approved_actions) if m.approved_actions is not None else None,
            "grant": m.grant_status,
        }
        if m.status not in KNOWN_MANDATE_STATUSES or m.valid_to is None:
            unknown.append(Unknown(target, "BAD_METADATA"))
            continue
        if m.status != "ACTIVE":
            continue  # a mandate that is not active holds no live authority to review
        grant_ref = f"grant_of:{m.mandate_id}"
        if now is not None:
            if m.valid_to < now:
                findings.append(_finding("R-01", target, (target,), facts))
            elif m.valid_to - now <= window:
                findings.append(_finding("R-02", target, (target,), facts))
        if not m.grant_found or m.grant_status != HEALTHY_GRANT_STATUS:
            findings.append(_finding("R-05", target, (target, grant_ref), facts))
        if m.approved_actions is not None and not m.actions <= m.approved_actions:
            findings.append(_finding("R-04", target, (target, grant_ref), facts))
    for a in sorted(agents, key=lambda x: x.agent_id):
        target = f"agent:{a.agent_id}"
        facts = {
            "status": a.status,
            "sponsor": a.sponsor_active,
            "policy": a.has_policy,
            "approval": a.approval_effective,
        }
        if a.status not in KNOWN_AGENT_STATUSES:
            unknown.append(Unknown(target, "BAD_METADATA"))
            continue
        if a.status != "ACTIVE":
            continue
        if a.sponsor_active is None:
            unknown.append(Unknown(target, "SPONSOR_UNVERIFIABLE"))
        elif not a.sponsor_active:
            findings.append(_finding("R-03", target, (target,), facts))
        if a.has_policy and a.approval_effective is False:
            findings.append(_finding("R-05", target, (target, "policy:active"), facts))
    ordered = sorted(findings, key=lambda f: (f.rule_id, f.target_ref, f.finding_code))
    return RuleOutcome(tuple(ordered), tuple(unknown), len(mandates) + len(agents))


# ---------------------------------------------------------------- proposals (the one write)


def dedupe_key(tenant_id: str, agent_id: str, f: Finding) -> str:
    """One proposal per (target, rule, version, finding, facts). A retry, a webhook replay or tomorrow's run over the same facts finds
    the same key; changed facts make a new proposal (AT-11, AT-23)."""
    return digest(
        {
            "t": tenant_id,
            "a": agent_id,
            "target": f.target_ref,
            "rule": f.rule_id,
            "v": f.rule_version,
            "code": f.finding_code,
            "facts": f.facts_digest,
        }
    )


def plan_proposals(
    findings: Sequence[Finding], limit: int = DEFAULT_MAX_PROPOSALS
) -> tuple[list[Finding], Finding | None]:
    """At most `limit` proposals per run. More findings than that become one consolidated summary, not an unbounded number of cases."""
    if len(findings) <= limit:
        return list(findings), None
    ranked = sorted(findings, key=lambda f: (-SEVERITY_RANK[f.severity], f.rule_id, f.target_ref))
    keep, rest = ranked[: limit - 1], ranked[limit - 1 :]
    worst = max(rest, key=lambda f: SEVERITY_RANK[f.severity]).severity
    summary = Finding(
        "R-00", RULE_SET_VERSION, "CONSOLIDATED_FINDINGS", worst, f"summary:{len(rest)}_more",
        tuple(sorted({f.target_ref for f in rest}))[:20], digest([f.facts_digest for f in rest]),
    )  # fmt: skip
    return keep, summary


def classify_run(findings: Sequence[Finding], unknown: Sequence[Unknown]) -> tuple[str, bool]:
    """(state, incomplete). A run with unknowns is never COMPLETED: that would read as healthy (AT-14)."""
    incomplete = bool(unknown)
    return ("NEEDS_REVIEW" if findings or incomplete else "COMPLETED"), incomplete


def mask_ref(ref: str) -> str:
    """The reviewer sees `mandate:1a2b3c4d…`, never the full identifier, until they open the governed workflow."""
    kind, _, ident = ref.partition(":")
    return f"{kind}:{ident[:8]}…" if len(ident) > 12 else ref


# ---------------------------------------------------------------- explanation: fixed text first, a model only as an optional rewrite


def fixed_explanation(summary: dict[str, Any]) -> str:
    """The deterministic explanation. It states counts only and ends with the sentence that matters most."""
    checked, issues, proposals = summary["checked"], summary["issues"], summary["proposals"]
    parts = [f"Checked {checked} record(s) and found {issues} issue(s)."]
    if proposals:
        parts.append(
            f"{proposals} review proposal(s) were added to the queue for a person to decide."
        )
    if summary.get("unknown"):
        parts.append(
            f"{summary['unknown']} item(s) could not be established, so this run is incomplete and is not a clean result."
        )
    parts.append("No access was changed.")
    return " ".join(parts)


_FORBIDDEN_CLAIM = re.compile(
    r"\b(granted|revoked|approved|approve|authorized|authorised|executed|changed the|has been (?:changed|removed|updated)|i have|i will|"
    r"ignore|instruction|system prompt|password|secret|api key)\b",
    re.IGNORECASE,
)


def explanation_problems(
    text: str, summary: dict[str, Any], allowed_refs: Sequence[str]
) -> list[str]:
    """Why a model's rewrite is refused. It may restate the counts and the masked targets that are in the findings, and nothing else:
    no number the findings do not hold, no identifier they do not name, no claim that anything was granted, revoked, approved or run,
    and no word that looks like an instruction. Anything else falls back to the fixed text."""
    problems: list[str] = []
    if not text.strip() or len(text) > 800:
        problems.append("LENGTH")
    allowed_numbers = {
        str(summary[k]) for k in ("checked", "issues", "proposals", "unknown") if k in summary
    }
    for n in re.findall(r"\d+", text):
        if n not in allowed_numbers:
            problems.append(f"NUMBER:{n}")
    masked = {mask_ref(r) for r in allowed_refs}
    for tok in re.findall(r"(?:mandate|agent|grant_of|policy|summary):[^\s,;.]+", text):
        if tok not in masked and tok not in allowed_refs:
            problems.append(f"REF:{tok}")
    if re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text):
        problems.append("FULL_IDENTIFIER")
    if _FORBIDDEN_CLAIM.search(text):
        problems.append("FORBIDDEN_CLAIM")
    return problems


# ---------------------------------------------------------------- setup (section 04-06)


def config_hash(
    *, label: str, warn_days: int, region: str, ai_explain: bool, schedule: str = "MANUAL"
) -> str:
    """What a publish approval and a passed sandbox test are bound to. Any change to the setup changes it, so a changed scope voids
    both (AT-05)."""
    return digest({"template": TEMPLATE, "purpose": PURPOSE, "rules": RULE_SET_VERSION, "label": label, "warn_days": warn_days,
                   "region": region, "ai_explain": ai_explain, "schedule": schedule, "tools": sorted(TOOLS)})  # fmt: skip


def setup_stage(
    *,
    config: str,
    validated: str | None,
    sandbox: str | None,
    request_open: bool,
    policy_active: bool,
) -> str:
    """The state of the setup wizard, derived from the hashes and never stored as a claim."""
    if policy_active:
        return "ACTIVE"
    if request_open:
        return "PENDING_APPROVAL"
    if sandbox == config:
        return "SANDBOX_READY"
    if validated == config:
        return "READY_TO_RUN_SANDBOX"
    return "DRAFT"


def publish_blockers(*, config: str, validated: str | None, sandbox: str | None) -> list[str]:
    out: list[str] = []
    if validated != config:
        out.append("NOT_VALIDATED")
    if sandbox != config:
        out.append("SANDBOX_NOT_PASSED")
    return out


# ---------------------------------------------------------------- the sandbox fixture (synthetic only)


def synthetic_fixture(
    now: datetime,
) -> tuple[list[MandateFact], list[AgentFact], set[tuple[str, str]]]:
    """Synthetic records with a known answer: each rule fires once on its own record and the healthy records raise nothing. The dry run
    must reproduce this set exactly; a different answer is a failed sandbox test."""
    acts = frozenset({"docs.read"})
    ok = "ACTIVE"
    m = [
        MandateFact("m-synth-expired", "ACTIVE", now - timedelta(days=2), acts, acts, True, ok),
        MandateFact("m-synth-expiring", "ACTIVE", now + timedelta(days=5), acts, acts, True, ok),
        MandateFact("m-synth-healthy", "ACTIVE", now + timedelta(days=90), acts, acts, True, ok),
        MandateFact("m-synth-variance", "ACTIVE", now + timedelta(days=90), frozenset({"docs.read", "docs.sign"}), acts, True, ok),
        MandateFact("m-synth-nogrant", "ACTIVE", now + timedelta(days=90), acts, None, False, None),
        MandateFact("m-synth-closed", "REVOKED", now - timedelta(days=30), acts, acts, True, "REVOKED"),
    ]  # fmt: skip
    a = [
        AgentFact("a-synth-orphan", "ACTIVE", False, True, True),
        AgentFact("a-synth-healthy", "ACTIVE", True, True, True),
        AgentFact("a-synth-unapproved", "ACTIVE", True, True, False),
        AgentFact("a-synth-suspended", "SUSPENDED", False, False, None),
    ]
    expected = {
        ("R-01", "mandate:m-synth-expired"),
        ("R-02", "mandate:m-synth-expiring"),
        ("R-04", "mandate:m-synth-variance"),
        ("R-05", "mandate:m-synth-nogrant"),
        ("R-03", "agent:a-synth-orphan"),
        ("R-05", "agent:a-synth-unapproved"),
    }
    return m, a, expected


class DryRunWriteAttempt(Exception):
    """A dry run reached for a real write. That is a security incident and a failed test, never a quiet no-op."""
